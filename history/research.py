"""Chronological learned-minutes and points-calibration research, stdlib only.

See protocol-v2.json. Historical outcomes already inspected are not advertised
as a fresh external holdout. This script never modifies the production model.
"""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import importlib.util
import json
import math
import pathlib
import shutil
import statistics
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


MINUTES = module('research_minutes', ROOT/'history/minutes.py')
OLD = module('research_original', ROOT/'backtests/historical_predictive.py')
REPLAY = module('research_replay', ROOT/'backtests/replay_current.py')
ASSESS = module('research_assess', ROOT/'backtests/assess.py')


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def prepare(panel, archive, output):
    panels = collections.defaultdict(list)
    for row in OLD.read(panel):
        panels[row['season']].append(row)
    datasets = {}; prior_profiles = {}
    for season, rows in sorted(panels.items()):
        year = int(season[:4]); previous = f'{year-1}-{str(year)[-2:]}'
        rawpath = archive/previous/'players_raw.csv'
        priors = {r['code']: float(r['minutes']) for r in OLD.read(rawpath)} if rawpath.exists() else {}
        weeks = collections.defaultdict(list); histories = collections.defaultdict(list)
        for row in rows:
            row['_gw'] = int(row['gw']); row['_minutes'] = float(row['minutes'])
            row['_kickoff'] = OLD.stamp(row['kickoff_time'])
            row['_finished'] = row['_kickoff']+datetime.timedelta(hours=3)
            weeks[row['_gw']].append(row); histories[row['player_id']].append(row)
        dataset = []
        for gw, week in sorted(weeks.items()):
            cutoff = min(r['_kickoff'] for r in week)-datetime.timedelta(minutes=90)
            counts = collections.Counter(r['player_id'] for r in week)
            for row in week:
                if counts[row['player_id']] != 1:
                    continue
                lag = [r for r in histories[row['player_id']] if r['_gw'] < gw and r['_finished'] < cutoff]
                if not lag:
                    continue
                lag.sort(key=lambda r:r['_kickoff'])
                assert max(r['_finished'] for r in lag) < cutoff
                prior = priors.get(row['code']); previous_profile = prior_profiles.get(row['code'])
                recent = lag[-6:]; states = [MINUTES.state(r['_minutes']) for r in recent]
                prior_prob = previous_profile[0] if previous_profile else [.5, .1, .4]
                prior_means = previous_profile[1] if previous_profile else [0, 20, 85]
                probabilities = [(states.count(s)+2*prior_prob[s])/(len(states)+2) for s in range(3)]
                means = []
                for s in range(3):
                    observed = [r['_minutes'] for r in recent if MINUTES.state(r['_minutes']) == s]
                    means.append(statistics.mean(observed) if observed else prior_means[s])
                dataset.append({'season':season, 'gw':gw, 'player_id':int(row['player_id']), 'pos':row['pos'],
                    'minutes':row['_minutes'], 'actual_points':float(row['total_points']), 'prior_minutes':prior,
                    'eligible':int(statistics.mean(r['_minutes'] for r in lag[-3:]) >= 45 or
                                   (gw <= 3 and (prior or 0) >= 900)),
                    'keys':{w:MINUTES.features(row['pos'], prior, lag, w) for w in (3, 6, 10)},
                    'rolling_prediction':(probabilities, means), 'cutoff':cutoff.isoformat(),
                    'latest_feature_time':max(r['kickoff_time'] for r in lag)})
        datasets[season] = dataset
        profiles = collections.defaultdict(list)
        for row in rows:
            profiles[row['code']].append(row['_minutes'])
        prior_profiles = {}
        for code, values in profiles.items():
            states = [MINUTES.state(m) for m in values]
            prior_profiles[code] = ([states.count(s)/len(states) for s in range(3)],
                [statistics.mean([m for m in values if MINUTES.state(m) == s])
                 if s in states else [0, 20, 85][s] for s in range(3)])
        print(json.dumps({'features':season, 'rows':len(dataset), 'active':sum(r['eligible'] for r in dataset)}), flush=True)
    with gzip.open(output/'lagged-features.jsonl.gz', 'wt', encoding='utf-8') as f:
        for season, dataset in datasets.items():
            for row in dataset:
                f.write(json.dumps(row, separators=(',', ':'))+'\n')
    return panels, datasets


def select_and_fit(datasets, target):
    seasons = sorted(s for s in datasets if s < target)
    validation = seasons[-1]
    tuning_train = [r for s in seasons[:-1] for r in datasets[s]]
    grid = []
    if tuning_train:
        valid = [r for r in datasets[validation] if r['eligible']]
        for window in (3, 6, 10):
            trained = MINUTES.fit(tuning_train, window, 50)
            for strength in (10, 50, 200):
                model = MINUTES.Model(window, strength, trained.nodes)
                scores = MINUTES.metrics(valid, [model.predict(r['keys'][window]) for r in valid])
                grid.append({'window':window, 'strength':strength, **scores})
        best = min(grid, key=lambda r:r['multiclass_logloss'])
    else:
        best = {'window':6, 'strength':50}
    full_train = [r for s in seasons for r in datasets[s]]
    trained = MINUTES.fit(full_train, best['window'], best['strength'])
    return trained, {'target':target, 'training_seasons':seasons, 'training_rows':len(full_train),
        'inner_validation':validation if tuning_train else None,
        'inner_training_seasons':seasons[:-1], 'selected':best, 'grid':grid}


def evaluate_minutes(datasets, output):
    models = {}; selections = {}; scores = {}
    targets = sorted(datasets)[1:]+['2026-27']
    for season in targets:
        model, selection = select_and_fit(datasets, season)
        models[season] = model; selections[season] = selection
        if season in datasets:
            scores[season] = {}
            for pool in ('active', 'all_previously_observed'):
                rows = [r for r in datasets[season] if r['eligible'] or pool != 'active']
                learned = [model.predict(r['keys'][model.window]) for r in rows]
                rolling = [r['rolling_prediction'] for r in rows]
                scores[season][pool] = {'learned':MINUTES.metrics(rows, learned),
                                        'rolling6':MINUTES.metrics(rows, rolling)}
            print(json.dumps({'minutes_test':season, 'window':model.window, 'strength':model.strength,
                              **scores[season]['active']}), flush=True)
    dump(output/'nested-selection.json', selections)
    dump(output/'minutes-walk-forward.json', scores)
    dump(output/'minute-model-2026-27.json', models['2026-27'].to_dict())
    return models, selections, scores


def solve(matrix, values):
    augmented = [list(row)+[float(value)] for row,value in zip(matrix, values)]
    n = len(values)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r:abs(augmented[r][col]))
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = augmented[col][col]
        if abs(scale) < 1e-12:
            raise ValueError('Singular calibration matrix')
        augmented[col] = [v/scale for v in augmented[col]]
        for r in range(n):
            if r != col:
                factor = augmented[r][col]
                augmented[r] = [a-factor*b for a,b in zip(augmented[r], augmented[col])]
    return [row[-1] for row in augmented]


def design(xp, probability, pos):
    return [xp]+[probability if pos == p else 0.0 for p in ('GKP', 'DEF', 'MID', 'FWD')]


def fit_points(rows, ridge=1000):
    # Identity prior: slope1, position offsets0; zero appearance still scores0.
    matrix = [[float(ridge) if i == j else 0.0 for j in range(5)] for i in range(5)]
    target = [float(ridge), 0, 0, 0, 0]
    for row in rows:
        x = design(row['xp'], row['p_appear'], row['pos'])
        for i in range(5):
            target[i] += x[i]*row['actual_points']
            for j in range(5):
                matrix[i][j] += x[i]*x[j]
    return {'coefficients':solve(matrix, target), 'ridge':ridge, 'n':len(rows),
            'training_seasons':sorted({r['season'] for r in rows})}


def calibrated(xp, probability, pos, coefficients):
    return max(0.0, sum(a*b for a,b in zip(design(xp, probability, pos), coefficients)))


def point_research(panels, archive, models, output):
    model = OLD.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
    config = json.loads((ROOT/'backtests/baseline-v1/model/config.json').read_text())
    original_minutes = model.minute_states
    previous_predictions = []; combined = []; scores = {}; coefficients = {}
    for season in ('2023-24', '2024-25', '2025-26'):
        cases, _ = OLD.cases_for_season(season, panels, archive, model, config)
        selected = models[season]; coeff = fit_points(previous_predictions); coefficients[season] = coeff
        rows_by_model = collections.defaultdict(list)
        for case in cases:
            if not case['eligible']:
                continue
            base = OLD.model_prediction(case, config, model)
            keys = MINUTES.features(case['pos'], float(case['prior']['mins']) if case['prior'] else None,
                                    case['fixture_rows'], selected.window)
            probs, means = selected.predict(keys)
            def learned_minutes(*unused):
                return [(probs[2], means[2]), (probs[1], means[1]), (0.0, 15.0)], {'minutes_60':means[2]}
            model.minute_states = learned_minutes
            try:
                learned = OLD.model_prediction(case, config, model)
            finally:
                model.minute_states = original_minutes
            shared = {k:case[k] for k in ('season', 'gw', 'player_id', 'name', 'pos', 'actual_points',
                      'actual_minutes', 'appeared', 'reached60')}
            for label, prediction in [('v1', base), ('learned_minutes', learned),
                                       ('rolling10', OLD.rolling_prediction(case, 10))]:
                rows_by_model[label].append({**shared, **prediction, 'model':label})
            corrected = {**shared, **learned, 'model':'learned_plus_points_calibration'}
            corrected['xp'] = calibrated(learned['xp'], learned['p_appear'], case['pos'], coeff['coefficients'])
            rows_by_model['learned_plus_points_calibration'].append(corrected)
        scores[season] = {label:OLD.metric(rows) for label,rows in rows_by_model.items()}
        scores[season]['paired_uncertainty'] = {
            label:ASSESS.paired_bootstrap(rows_by_model['v1'], rows_by_model[label])
            for label in ('learned_minutes', 'learned_plus_points_calibration')}
        previous_predictions += rows_by_model['learned_minutes']
        combined.extend(row for rows in rows_by_model.values() for row in rows)
        print(json.dumps({'points_test':season, 'scores':scores[season]}), flush=True)
    coefficients['2026-27'] = fit_points(previous_predictions)
    dump(output/'point-calibration.json', coefficients)
    dump(output/'points-walk-forward.json', scores)
    OLD.write_csv(output/'historical-player-predictions.csv', combined)
    return coefficients, scores


def wrap_current(model, minute_model, point_model=None):
    def predict_minutes(player, rows, prior, config, override):
        if override:
            raise ValueError('Research replay does not accept untested news overrides')
        single = [r for r in sorted(rows, key=lambda r:int(r['gw'])) if '|' not in r.get('fixture_id', '')]
        prior_minutes = float(prior['mins']) if prior else None
        probs, means = minute_model.predict(MINUTES.features(player['pos'], prior_minutes, single, minute_model.window))
        available = model.number(player, 'chance_of_playing_next_round', 100)/100
        if player.get('st') in ('i', 's', 'u'):
            available = 0
        values = {'method':'historically_learned_minute_states', 'p60':probs[2]*available,
            'p_short':probs[1]*available, 'minutes_60':means[2], 'minutes_short':means[1],
            'window':minute_model.window, 'pseudocount':minute_model.strength}
        return [(probs[2]*available, means[2]), (probs[1]*available, means[1]), (0.0, 15.0)], values
    model.minute_states = predict_minutes
    if point_model:
        original = model.forecast
        def forecast(*args, **kwargs):
            result = original(*args, **kwargs)
            for player in result['players'].values():
                for week in player['weeks'].values():
                    base = week['xp']; value = calibrated(base, week['p_appear'], player['pos'], point_model['coefficients'])
                    week['xp'] = round(value, 5)
                    week['components']['research_calibration_adjustment'] = round(value-base, 5)
            result['research_only'] = True
            return result
        model.forecast = forecast
    return model


def current_research(models, coefficients, data, refs, actuals, squads, output, baseline_predictions):
    original_loader = REPLAY.load_model
    baseline_model_dir = ROOT/'backtests/baseline-v1/model'
    current_scores = {}; all_rows = []
    for label, score_model in [('learned_minutes', None),
                               ('learned_plus_points_calibration', coefficients['2026-27'])]:
        with tempfile.TemporaryDirectory() as temp:
            model_dir = pathlib.Path(temp)
            config = json.loads((baseline_model_dir/'config.json').read_text())
            config['research_variant'] = label+'; not installed in the live advisor'
            config['research_minute_model'] = models['2026-27'].to_dict()
            config['research_point_model'] = score_model
            config['research_code_sha256'] = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
            config['research_minutes_code_sha256'] = hashlib.sha256((ROOT/'history/minutes.py').read_bytes()).hexdigest()
            dump(model_dir/'config.json', config)
            shutil.copyfile(baseline_model_dir/config['prior_file'], model_dir/config['prior_file'])
            wrapped = wrap_current(original_loader(ROOT/'backtests/baseline-v1/fpl_advisor.py'), models['2026-27'], score_model)
            REPLAY.load_model = lambda path:wrapped
            try:
                result = REPLAY.run(data, json.loads(refs.read_text()), actuals, squads,
                                    ROOT/'backtests/baseline-v1/fpl_advisor.py', model_dir, output/label, baseline_predictions)
            finally:
                REPLAY.load_model = original_loader
        current_scores[label] = result
        rows = OLD.read(output/label/'player_predictions.csv')
        for r in rows:
            r['model'] = label
        all_rows += rows
    baseline = OLD.read(baseline_predictions)
    for r in baseline:
        r['model'] = 'v1'
    all_rows += baseline
    prior = {int(r['id']):float(r['mins']) for r in wrapped.read_csv(baseline_model_dir/config['prior_file'])}
    cohorts = {}
    for label in ('v1', 'learned_minutes', 'learned_plus_points_calibration'):
        source = [r for r in all_rows if r['model'] == label]
        pools = {'fixed_candidate_pool':[r for r in source if int(r['likely_player'])],
                 'owned15':[r for r in source if int(r['owned'])],
                 'thin_prior_all_players':[r for r in source if prior.get(int(r['player_id']), 0) < 450]}
        cohorts[label] = {}
        for name, rows in pools.items():
            numeric = [{**r, **{k:float(r[k]) for k in ('xp', 'actual_points', 'p_appear', 'p60', 'appeared', 'reached60')}} for r in rows]
            cohorts[label][name] = OLD.metric(numeric)
    dump(output/'current-cohorts.json', cohorts)
    dump(output/'current-research-summary.json', {k:{'conditional_squad_score':v['conditional_squad_score'],
        'actual_score':v['actual_score'], 'likely_players':v['likely_players'], 'owned_players':v['owned_players'],
        'weekly_scores':[{f:w[f] for f in ('gw', 'conditional_squad_score', 'captain', 'xi', 'bench')} for w in v['weeks']]}
        for k,v in current_scores.items()})
    return current_scores, cohorts


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    panels, datasets = prepare(args.panel, args.archive, args.output)
    models, selections, minute_scores = evaluate_minutes(datasets, args.output)
    coefficients, point_scores = point_research(panels, args.archive, models, args.output)
    if args.current_data:
        current_research(models, coefficients, args.current_data, args.refs, args.actuals,
                         args.squads, args.output, args.baseline_predictions)
    dump(args.output/'experiment.json', {'protocol':'history/protocol-v2.json',
        'source_panel_sha256':hashlib.sha256(args.panel.read_bytes()).hexdigest(),
        'source_code_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        'minutes_code_sha256':hashlib.sha256((ROOT/'history/minutes.py').read_bytes()).hexdigest(),
        'training_is_chronological':True, 'fresh_untouched_holdout':False, 'live_model_changed':False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('panel', 'archive', 'output'):
        parser.add_argument('--'+name, type=pathlib.Path, required=True)
    for name in ('current-data', 'refs', 'actuals', 'squads', 'baseline-predictions'):
        parser.add_argument('--'+name, type=pathlib.Path)
    arguments = parser.parse_args()
    if arguments.current_data and not all((arguments.refs, arguments.actuals, arguments.squads, arguments.baseline_predictions)):
        parser.error('Current replay requires refs, actuals, squads and baseline-predictions')
    run(arguments)

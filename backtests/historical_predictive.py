"""Chronological, lag-only component backtest; Python standard library only.

Old archives are not deadline vintages. This tests statistical components on
single-fixture gameweeks, with realised fixture assignments and no odds/news.
Use replay_current.py for the seven genuine current-season deadline snapshots.
"""
import argparse
import collections
import csv
import datetime
import importlib.util
import json
import math
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parent.parent
POSITIONS = {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}
STATS = ('minutes', 'starts', 'total_points', 'goals_scored', 'assists',
         'expected_goals', 'expected_assists', 'defensive_contribution',
         'bonus', 'saves', 'yellow_cards', 'red_cards', 'own_goals',
         'penalties_missed', 'penalties_saved')
DEVELOPMENT = tuple(f'{y}-{str(y+1)[-2:]}' for y in range(2016, 2023))
VALIDATION = '2023-24'
HOLDOUTS = ('2024-25', '2025-26')


def read(path):
    data = pathlib.Path(path).read_bytes()
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('latin-1')
    return list(csv.DictReader(text.splitlines()))


def load_model(path):
    spec = importlib.util.spec_from_file_location('historical_model', path)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    # Algebraically equivalent Poisson-block expectation, with negligible
    # omitted probability (<1e-12 at model intensities); avoids quadratic tails.
    def blocks(mean, size):
        p = math.exp(-mean)
        result = 0.0
        for k in range(1, 121):
            p *= mean / k
            result += (k // size) * p
        return result
    model.expected_blocks = blocks
    return model


def stamp(value):
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))


def aggregate(rows):
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[int(row['gw'])].append(row)
    output = []
    for gw, items in sorted(grouped.items()):
        r = {'gw': gw, 'fixture_id': '|'.join(i['fixture_id'] for i in items)}
        r.update({key: sum(float(i[key] or 0) for i in items) for key in STATS})
        output.append(r)
    return output


def metric(rows):
    if not rows:
        return {'n': 0}
    errors = [r['xp'] - r['actual_points'] for r in rows]
    return {'n': len(rows), 'mae': statistics.mean(map(abs, errors)),
            'rmse': math.sqrt(statistics.mean(e*e for e in errors)),
            'bias_actual_minus_forecast': -statistics.mean(errors),
            'appearance_brier': statistics.mean((r['p_appear']-r['appeared'])**2 for r in rows),
            'p60_brier': statistics.mean((r['p60']-r['reached60'])**2 for r in rows)}


def variants(base):
    result = {'v1': dict(base)}
    # This finite search is declared before examining validation/holdout scores.
    for window in (3, 6, 10):
        for strength in (0.5, 2, 6):
            if window == 6 and strength == 2:
                continue
            result[f'minutes_w{window}_p{strength}'] = {
                **base, 'minutes_window': window, 'minutes_prior_matches': strength}
    for half in (4, 8, 16):
        for prior in (450, 900, 1800):
            if half == 8 and prior == 900:
                continue
            result[f'rates_h{half}_p{prior}'] = {
                **base, 'history_half_life_gameweeks': half, 'rate_prior_minutes': prior}
    return result


def cases_for_season(season, panels, archive, model, base):
    rows = panels[season]
    year = int(season[:4]); previous = f'{year-1}-{str(year)[-2:]}'
    oldrows = panels.get(previous, [])
    prior_by_code = {}
    oldpath = archive / previous / 'players_raw.csv'
    if oldpath.exists():
        for r in read(oldpath):
            if int(r['element_type']) not in POSITIONS:
                continue
            prior_by_code[r['code']] = {
                'pos': POSITIONS[int(r['element_type'])], 'mins': r['minutes'],
                'a': r['assists'], **{k: r.get(k, '') for k in model.RATE_FIELDS},
                'starts': r.get('starts', '')}
    groups = model.position_priors({i: r for i, r in enumerate(prior_by_code.values())})
    previous_outcomes = collections.defaultdict(list)
    for row in oldrows:
        previous_outcomes[row['code']].append(row)
    fixtures = []
    path = archive / season / 'fixtures.csv'
    if path.exists():
        for f in read(path):
            if f.get('kickoff_time') and f.get('event') and f.get('team_h_score'):
                fixtures.append({'gw': f['event'], 'kickoff': f['kickoff_time'],
                    'home': f['team_h'], 'away': f['team_a'], 'finished': 'true',
                    'team_h_score': f['team_h_score'], 'team_a_score': f['team_a_score']})
    weeks = collections.defaultdict(list)
    histories = collections.defaultdict(list)
    for r in rows:
        weeks[int(r['gw'])].append(r)
        histories[r['player_id']].append(r)
    cases = []; excluded = collections.Counter()
    for gw, weekrows in sorted(weeks.items()):
        # A conservative proxy for an unavailable old deadline. Requiring
        # three hours after kickoff prevents in-progress outcomes entering.
        cutoff = min(stamp(r['kickoff_time']) for r in weekrows) - datetime.timedelta(minutes=90)
        past_fixtures = [f for f in fixtures if stamp(f['kickoff']) + datetime.timedelta(hours=3) < cutoff]
        environment, team_average = model.team_environment(past_fixtures, gw, base)
        target_counts = collections.Counter(r['player_id'] for r in weekrows)
        for r in weekrows:
            if target_counts[r['player_id']] != 1:
                excluded['double_gameweek_target_rows'] += 1
                continue
            lag = [x for x in histories[r['player_id']] if int(x['gw']) < gw and
                   stamp(x['kickoff_time']) + datetime.timedelta(hours=3) < cutoff]
            if not lag:
                excluded['no_previously_observed_archive_row'] += 1
                continue
            assert max(stamp(x['kickoff_time']) + datetime.timedelta(hours=3) for x in lag) < cutoff
            prior = prior_by_code.get(r['code'], {})
            old = previous_outcomes.get(r['code'], [])
            recent = sorted(lag, key=lambda x: x['kickoff_time'])[-3:]
            # One common, entirely lagged candidate pool for every variant.
            eligible = statistics.mean(float(x['minutes']) for x in recent) >= 45 or (
                gw <= 3 and float(prior.get('mins', 0) or 0) >= 900)
            home = r['was_home'] == 'True'
            own = environment(r['team_id'], r['opponent_team'], home)
            opp = environment(r['opponent_team'], r['team_id'], not home)
            scale = max(.4, min(2.5, own / team_average.get(r['team_id'], base['neutral_team_goals'])))
            cases.append({'season': season, 'gw': gw, 'player_id': r['player_id'],
                'name': r['name'], 'pos': r['pos'], 'code': r['code'],
                'cutoff': cutoff.isoformat(), 'latest_feature_time': max(x['kickoff_time'] for x in lag),
                'rows': aggregate(lag), 'fixture_rows': lag, 'prior': prior, 'old': old,
                'group': groups[r['pos']], 'own': own, 'opp': opp, 'scale': scale,
                'actual_points': float(r['total_points']), 'actual_minutes': float(r['minutes']),
                'appeared': int(float(r['minutes']) > 0), 'reached60': int(float(r['minutes']) >= 60),
                'eligible': int(eligible), 'has_xg': int(r['has_xg'])})
    return cases, dict(excluded)


def rolling_prediction(case, window):
    lag = sorted(case['fixture_rows'], key=lambda r: r['kickoff_time'])[-window:]
    prior = case['old']
    # Only last season's outcomes, or a fixed first-season default, supply priors.
    old_points = statistics.mean(float(r['total_points']) for r in prior) if prior else 2.0
    old_appear = statistics.mean(float(r['minutes']) > 0 for r in prior) if prior else .5
    old_long = statistics.mean(float(r['minutes']) >= 60 for r in prior) if prior else .4
    strength = 2
    return {'xp': (sum(float(r['total_points']) for r in lag) + strength*old_points)/(len(lag)+strength),
        'p_appear': (sum(float(r['minutes']) > 0 for r in lag)+strength*old_appear)/(len(lag)+strength),
        'p60': (sum(float(r['minutes']) >= 60 for r in lag)+strength*old_long)/(len(lag)+strength)}


def model_prediction(case, config, model):
    player = {'pos': case['pos'], 'st': 'a'}  # Historical news is unavailable.
    rates = model.player_rates(player, case['rows'], case['prior'], config, case['group'])
    states, values = model.minute_states(player, case['rows'], case['prior'], config, {})
    if case['season'] < '2025-26':
        rates['defensive_contribution'] = 0
    threshold = 10 if case['pos'] == 'DEF' else 12
    eligible = [r for r in case['rows'] if '|' not in r['fixture_id'] and r['minutes'] >= 60]
    prior_hit = model.poisson_tail(rates['defensive_contribution']*values['minutes_60']/90, threshold)
    dc_long = (sum(r['defensive_contribution'] >= threshold for r in eligible) +
               config['dc_prior_matches']*prior_hit)/(len(eligible)+config['dc_prior_matches'])
    parts = model.fixture_points(case['pos'], rates, states, case['own'], case['opp'], case['scale'], dc_long)
    # Keeper goals were six points before the 2025/26 rule change.
    if case['season'] < '2025-26' and case['pos'] == 'GKP':
        parts['goals'] *= .6
    return {'xp': sum(parts.values()), 'p_appear': sum(p for p, _ in states),
            'p60': sum(p for p, m in states if m >= 60)}


def predict(cases, config, model, rolling=None):
    output = []
    for case in cases:
        if not case['eligible']:
            continue
        prediction = rolling_prediction(case, rolling) if rolling else model_prediction(case, config, model)
        output.append({**{k: case[k] for k in ('season', 'gw', 'player_id', 'name', 'pos',
                       'actual_points', 'actual_minutes', 'appeared', 'reached60',
                       'cutoff', 'latest_feature_time')}, **prediction})
    return output


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def run(panel_path, archive, output, phase):
    output.mkdir(parents=True, exist_ok=True)
    panels = collections.defaultdict(list)
    for r in read(panel_path):
        panels[r['season']].append(r)
    model = load_model(ROOT / 'backtests/baseline-v1/fpl_advisor.py')
    base = json.loads((ROOT / 'backtests/baseline-v1/model/config.json').read_text())
    configs = variants(base)
    if phase == 'select':
        # Only development/validation outcomes are opened by selection.
        scores = {}; benchmark_rows = collections.defaultdict(list)
        for season in DEVELOPMENT + (VALIDATION,):
            cases, excluded = cases_for_season(season, panels, archive, model, base)
            result = {'cases': len(cases), 'eligible': sum(c['eligible'] for c in cases), 'excluded': excluded,
                      'rolling_points': {}}
            for window in (3, 6, 10):
                prediction = predict(cases, base, model, rolling=window)
                result['rolling_points'][str(window)] = metric(prediction)
                if season in DEVELOPMENT:
                    benchmark_rows[window] += prediction
            if season == VALIDATION:
                result['variants'] = {}
                for name, config in configs.items():
                    prediction = predict(cases, config, model)
                    result['variants'][name] = metric(prediction)
                    print(json.dumps({'season': season, 'variant': name, **result['variants'][name]}), flush=True)
            scores[season] = result
            print(json.dumps({'completed': season, 'n': result['eligible']}), flush=True)
        development = {str(w): metric(rows) for w, rows in benchmark_rows.items()}
        benchmark_window = min(development, key=lambda w: development[w]['rmse'])
        selected = min(scores[VALIDATION]['variants'], key=lambda n: scores[VALIDATION]['variants'][n]['rmse'])
        selection = {'primary_metric': 'RMSE on the common lag-defined candidate pool',
                     'variant': selected, 'config': configs[selected], 'rolling_window': int(benchmark_window),
                     'development_rolling_metrics': development, 'scores': scores,
                     'promotion_rule': 'Select once on 2023/24; promote only if RMSE improves >=0.5% on EACH held-out season; never select a new variant from holdout results.'}
        (output/'selection.json').write_text(json.dumps(selection, indent=2)+'\n')
    else:
        selection = json.loads((output/'selection.json').read_text())
        scores = {}; predictions = []
        for season in (VALIDATION,) + HOLDOUTS:
            cases, excluded = cases_for_season(season, panels, archive, model, base)
            scores[season] = {'cases': len(cases), 'excluded': excluded, 'models': {}}
            for name, config, window in [('v1', base, None), ('selected', selection['config'], None),
                                        ('rolling', base, selection['rolling_window'])]:
                rows = predict(cases, config, model, rolling=window)
                for r in rows:
                    r['model'] = name
                predictions += rows
                scores[season]['models'][name] = metric(rows)
                print(json.dumps({'season': season, 'model': name, **metric(rows)}), flush=True)
        promote = all(scores[s]['models']['selected']['rmse'] <= .995*scores[s]['models']['v1']['rmse'] for s in HOLDOUTS)
        result = {'selected_variant': selection['variant'], 'promote': promote, 'scores': scores,
                  'limitations': ['Archive component test: no historical odds/news or vintage fixture schedules.',
                                  'Only single-fixture targets after the first observed player row; no transfer-policy simulation.',
                                  'Pre-2025/26 defensive contribution disabled; historical bonus/scoring outcomes retained.',
                                  'Both holdouts and seven current GWs are retrospective tests, not prospective records.']}
        (output/'evaluation.json').write_text(json.dumps(result, indent=2)+'\n')
        write_csv(output/'player_predictions.csv', predictions)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--panel', type=pathlib.Path, required=True)
    p.add_argument('--archive', type=pathlib.Path, required=True)
    p.add_argument('--output', type=pathlib.Path, required=True)
    p.add_argument('--phase', choices=('select', 'evaluate'), required=True)
    a = p.parse_args(); run(a.panel, a.archive, a.output, a.phase)

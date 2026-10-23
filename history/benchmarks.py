"""Diagnostic benchmarks; never used to select the research model parameters."""
import argparse
import collections
import copy
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def point_metric(rows, field):
    error = [r[field]-r['actual_points'] for r in rows]
    return {'n':len(rows), 'rmse':math.sqrt(statistics.mean(e*e for e in error)),
            'mae':statistics.mean(map(abs, error)), 'bias_actual_minus_forecast':-statistics.mean(error)}


def run(research, baseline, data, output):
    output.mkdir(parents=True, exist_ok=True)
    minutes = load('benchmark_minutes', ROOT/'history/minutes.py')
    replay = load('benchmark_replay', ROOT/'backtests/replay_current.py')
    model = replay.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
    rows = list(csv.DictReader((baseline/'player_predictions.csv').open()))
    snapshots = {}; values = {}
    for gw in range(1, 8):
        source = data/f'GW{gw:02d}'
        snapshots[gw] = {int(r['id']):r for r in model.read_csv(source/'players.csv')}
        for pid, row in snapshots[gw].items():
            if row.get('ep_next') not in (None, ''):
                values[gw, pid] = float(row['ep_next'])
    scored = []
    for row in rows:
        key = (int(row['gw']), int(row['player_id']))
        if key not in values:
            continue
        scored.append({**row, 'actual_points':float(row['actual_points']), 'v1':float(row['xp']), 'ep_next':values[key]})
    result = {'scope':'Pre-deadline snapshot ep_next only; archived post-match xP is excluded.', 'pools':{}}
    for pool, subset in [('fixed_candidate_pool',[r for r in scored if int(r['likely_player'])]),
                         ('owned15',[r for r in scored if int(r['owned'])])]:
        result['pools'][pool] = {name:point_metric(subset, name) for name in ('v1', 'ep_next')}
    forecasts = model.read_json(baseline/'forecasts.replay.json')
    actuals = {(int(r['gw']), int(r['player_id'])):{'minutes':float(r['actual_minutes']), 'points':float(r['actual_points'])} for r in rows}
    weeks = []
    for gw, forecast in forecasts.items():
        gw = int(gw); candidate = copy.deepcopy(forecast)
        candidate['model_version'] = 'diagnostic-official-ep_next-with-common-appearance-assumptions'
        candidate['forecast_id'] = hashlib.sha256((forecast['forecast_id']+'official-ep_next-benchmark').encode()).hexdigest()
        squad = model.read_json(ROOT/'backtests/fixtures'/f'squad_gw{gw}.json')
        ids = [p['element'] for p in squad['picks']]
        for pid in ids:
            candidate['players'][str(pid)]['weeks'][str(gw)]['xp'] = values[gw, pid]
        team = model.best_lineup(ids, candidate, gw)
        score = replay.team_score(model, team, candidate['players'], {pid:actuals[gw, pid] for pid in ids})
        weeks.append({'gw':gw, 'score':score, 'captain':candidate['players'][str(team['captain'])]['name']})
    result['conditional_lineup_diagnostic'] = {'weeks':weeks, 'total':sum(w['score'] for w in weeks),
        'caveat':'Ranks the same owned15 by official ep_next but retains common v1 appearance/autosub assumptions. This is not an official FPL recommended strategy or a transfer backtest.'}
    (output/'official-score-benchmark.json').write_text(json.dumps(result, indent=2)+'\n')
    datasets = collections.defaultdict(list)
    with gzip.open(research/'lagged-features.jsonl.gz', 'rt') as f:
        for line in f:
            row = json.loads(line); datasets[row['season']].append(row)
    constants = {}
    for season in sorted(datasets)[1:]:
        train = [r for year, items in datasets.items() if year < season for r in items if r['eligible']]
        by_position = collections.defaultdict(list)
        for row in train:
            by_position[row['pos']].append(row['minutes'])
        prediction = {}
        for pos, values in by_position.items():
            states = [minutes.state(v) for v in values]
            probs = [(states.count(s)+1)/(len(states)+3) for s in range(3)]
            means = [statistics.mean([v for v in values if minutes.state(v) == s])
                     if s in states else [0, 20, 85][s] for s in range(3)]
            prediction[pos] = (probs, means)
        target = [r for r in datasets[season] if r['eligible']]
        constants[season] = minutes.metrics(target, [prediction[r['pos']] for r in target])
    (output/'position-constant-minute-benchmark.json').write_text(json.dumps(constants, indent=2)+'\n')
    print(json.dumps({'official_score_rmse':result['pools']['fixed_candidate_pool']['ep_next']['rmse'],
                      'official_score_conditional_lineup':result['conditional_lineup_diagnostic']['total']}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('research', 'baseline', 'data', 'output'):
        p.add_argument('--'+name, type=pathlib.Path, required=True)
    a = p.parse_args(); run(a.research, a.baseline, a.data, a.output)

"""Independently check published comparison arithmetic and the live contract."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research/results'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    modern = pd.read_csv(OUT / 'extended-modern-predictions.csv.gz')
    current = pd.read_csv(OUT / 'extended-current-predictions.csv.gz')
    table = pd.read_csv(OUT / 'model-comparison.csv')
    require(not table.duplicated(['period', 'pool', 'model']).any(), 'Duplicate score rows')
    baseline = pd.read_csv(ROOT / 'research/work/baselines/current-v1.csv')
    require(len(current) == len(baseline), 'Current comparison dropped baseline cases')
    target = current.merge(baseline[['gw', 'player_id', 'actual_points']],
                           on=['gw', 'player_id'], validate='one_to_one')
    require(np.array_equal(target.y_total_points, target.actual_points_y
                           if 'actual_points_y' in target else target.actual_points),
            'Comparison changed realised outcomes')
    checks = 0
    for row in table.to_dict('records'):
        if row['pool'] == 'matched_modern':
            frame = modern[modern.season == row['period']]
        else:
            frame = current
            if row['pool'] == 'fixed_candidates':
                frame = frame[frame.likely_player == 1]
            elif row['pool'] == 'owned15':
                frame = frame[frame.owned == 1]
            else:
                require(row['pool'] == 'all', 'Unknown comparison pool')
        y = frame.y_total_points.to_numpy(float)
        p = frame[row['model']].to_numpy(float)
        require(len(y) == row['n'] and len(y) > 0, 'Score denominator changed')
        require(np.isfinite(p).all() and np.isfinite(y).all(), 'Missing prediction/target')
        error = p - y
        expected = dict(rmse=float(np.linalg.norm(error) / np.sqrt(len(error))),
                        mae=float(sum(abs(error)) / len(error)),
                        bias_forecast_minus_actual=float(sum(error) / len(error)),
                        mean_prediction=float(sum(p) / len(p)),
                        mean_actual=float(sum(y) / len(y)))
        base = float(np.linalg.norm(frame.v1.to_numpy() - y) / np.sqrt(len(y)))
        expected['improvement_vs_v1_percent'] = (base - expected['rmse']) / base * 100
        for key, value in expected.items():
            require(np.isclose(row[key], value, rtol=1e-10, atol=1e-10),
                    f'Arithmetic mismatch: {row["model"]}/{row["period"]}/{key}')
        checks += 1
    selection = json.loads((OUT / 'nested-selection.json').read_text())
    for year, chosen in selection.items():
        require(all(past < int(year) for past in chosen['selected_using_years']),
                'Selection used outer test outcomes')
        require(abs(sum(chosen['ensemble_weights']) - 1) < 1e-8, 'Invalid ensemble weights')
        require(min(chosen['ensemble_weights']) >= 0, 'Negative ensemble weight')
    trials = list((OUT / 'folds').glob('fold-*/one-week/*-seed1729.json'))
    require(len(trials) == 790, 'Incomplete registered trial population')
    for path in trials:
        record = json.loads(path.read_text())
        require(record['status'] == 'ok', 'Unsuccessful trial')
        require(record['latest_training_outcome'] < record['earliest_test_cutoff'],
                'Training crossed forecast boundary')
        require(all(int(s[:4]) < record['year'] for s in record['training_seasons']),
                'Training used test season')
        require(hashlib.sha256(path.with_suffix('.npz').read_bytes()).hexdigest()
                == record['prediction_sha256'], 'Changed trial forecasts')
    lineups = json.loads((OUT / 'lineup-assessment.json').read_text())
    sums = defaultdict(float)
    weeks = defaultdict(set)
    for row in lineups['weeks']:
        sums[row['model']] += row['points']
        weeks[row['model']].add(row['gw'])
    for name, total in lineups['totals'].items():
        require(total == sums[name] and len(weeks[name]) == 7, 'Lineup total mismatch')
    notebook = json.loads((ROOT / 'research/comparison.ipynb').read_text())
    cells = [c for c in notebook['cells'] if c['cell_type'] == 'code']
    require([c['execution_count'] for c in cells] == list(range(1, len(cells) + 1)),
            'Notebook was not executed in order')
    require(not any(o['output_type'] == 'error' for c in cells for o in c['outputs']),
            'Notebook contains execution errors')
    contract = json.loads((ROOT / 'research/production-contract.json').read_text())
    for name, sha in contract['files'].items():
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha,
                f'Production contract changed: {name}')
    report = {'status': 'passed', 'independently_recomputed_score_rows': checks,
              'chronological_trials_and_hashes_checked': len(trials),
              'executed_code_cells': len(cells),
              'unchanged_production_files': list(contract['files']),
              'limits': 'Arithmetic and artifact checks do not remove retrospective selection, archive vintage or prospective uncertainty.'}
    (OUT / 'independent-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

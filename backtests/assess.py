"""Summarise paired predictions, calibration bins and GW-cluster uncertainty."""
import argparse
import collections
import csv
import json
import math
import pathlib
import random
import statistics


def read(path):
    with pathlib.Path(path).open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def quantile(values, probability):
    values = sorted(values)
    index = probability*(len(values)-1)
    left = int(index); right = min(left+1, len(values)-1)
    return values[left]*(right-index) + values[right]*(index-left) if right != left else values[left]


def paired_bootstrap(left, right, samples=2000):
    keys = lambda r: (r['season'], r['gw'], r['player_id'])
    lookup = {keys(r): r for r in right}
    assert {keys(r) for r in left} == set(lookup), 'Comparison pools must match'
    clusters = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for row in left:
        other = lookup[keys(row)]
        assert row['actual_points'] == other['actual_points']
        cluster = clusters[row['season'], row['gw']]
        cluster[0] += 1
        cluster[1] += (float(row['xp'])-float(row['actual_points']))**2
        cluster[2] += (float(other['xp'])-float(other['actual_points']))**2
    values = list(clusters.values()); rng = random.Random(790889)
    def improvement(items):
        n = sum(x[0] for x in items)
        first = math.sqrt(sum(x[1] for x in items)/n)
        second = math.sqrt(sum(x[2] for x in items)/n)
        return 100*(1-second/first)
    draws = [improvement(rng.choices(values, k=len(values))) for _ in range(samples)]
    return {'n': len(left), 'gw_clusters': len(values), 'resamples': samples,
            'rmse_improvement_percent': improvement(values),
            'bootstrap_95_percent_interval': [quantile(draws, .025), quantile(draws, .975)],
            'interpretation': 'Descriptive paired GW-cluster bootstrap; not adjusted for validation search or season dependence.'}


def calibration(rows, label):
    grouped = collections.defaultdict(list)
    for r in rows:
        value = float(r['p60'])
        grouped[min(4, int(value*5))].append(r)
    output = []
    for index, items in sorted(grouped.items()):
        output.append({'sample': label, 'p60_bin': f'{index/5:.1f}-{(index+1)/5:.1f}',
            'n': len(items), 'mean_p60': statistics.mean(float(r['p60']) for r in items),
            'observed_p60': statistics.mean(float(r['reached60']) for r in items),
            'mean_xp': statistics.mean(float(r['xp']) for r in items),
            'mean_points': statistics.mean(float(r['actual_points']) for r in items)})
    return output


def write(path, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def run(historical, current, candidate, output):
    output.mkdir(parents=True, exist_ok=True)
    rows = read(historical/'player_predictions.csv')
    current_rows = read(current/'player_predictions.csv')
    candidate_rows = read(candidate/'player_predictions.csv')
    result = {}; bins = []; gw_rows = []
    for season in ('2023-24', '2024-25', '2025-26'):
        base = [r for r in rows if r['season'] == season and r['model'] == 'v1']
        selected = [r for r in rows if r['season'] == season and r['model'] == 'selected']
        rolling = [r for r in rows if r['season'] == season and r['model'] == 'rolling']
        result[season] = {'selected_vs_v1': paired_bootstrap(base, selected),
                          'v1_vs_rolling': paired_bootstrap(rolling, base)}
        for name, subset in [('v1', base), ('selected', selected), ('rolling', rolling)]:
            bins += calibration(subset, season+'/'+name)
            for gw in sorted({int(r['gw']) for r in subset}):
                items = [r for r in subset if int(r['gw']) == gw]
                errors = [float(r['xp'])-float(r['actual_points']) for r in items]
                gw_rows.append({'season': season, 'gw': gw, 'model': name, 'n': len(items),
                    'squared_error': sum(e*e for e in errors), 'absolute_error': sum(map(abs, errors)),
                    'bias_forecast_minus_actual': statistics.mean(errors)})
    for label, subset in [('v1', current_rows), ('selected', candidate_rows)]:
        likely = [r for r in subset if int(r['likely_player'])]
        owned = [r for r in subset if int(r['owned'])]
        bins += calibration(likely, '2026-27/GW1-7/'+label+'/fixed-pool')
        bins += calibration(owned, '2026-27/GW1-7/'+label+'/owned')
        for r in subset:
            r['season'] = '2026-27'
    result['2026-27/GW1-7'] = paired_bootstrap(
        [r for r in current_rows if int(r['likely_player'])],
        [r for r in candidate_rows if int(r['likely_player'])])
    (output/'paired-uncertainty.json').write_text(json.dumps(result, indent=2)+'\n')
    write(output/'calibration.csv', bins)
    write(output/'gameweek-errors.csv', gw_rows)
    write(output/'current-owned-predictions.csv', [r for r in current_rows if int(r['owned'])])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('historical', 'current', 'candidate', 'output'):
        p.add_argument('--'+name, type=pathlib.Path, required=True)
    a = p.parse_args(); run(a.historical, a.current, a.candidate, a.output)

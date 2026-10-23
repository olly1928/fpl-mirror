"""Additional paired current-season uncertainty and probability reliability bins."""
import argparse
import collections
import csv
import json
import math
import pathlib
import random
import statistics


def read(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def quantile(values, probability):
    values = sorted(values); i = probability*(len(values)-1); low = int(i); high = min(low+1, len(values)-1)
    return values[low] if high == low else values[low]*(high-i)+values[high]*(i-low)


def paired(base, candidate):
    lookup = {(r['gw'], r['player_id']):r for r in candidate}
    if set(lookup) != {(r['gw'], r['player_id']) for r in base}:
        raise ValueError('Comparison player pools differ')
    output = {}
    for field, target in [('xp', 'actual_points'), ('p60', 'reached60'), ('p_appear', 'appeared')]:
        groups = collections.defaultdict(lambda:[0.0, 0.0])
        for row in base:
            other = lookup[row['gw'], row['player_id']]
            if row[target] != other[target]:
                raise ValueError('Outcomes differ across comparison models')
            groups[row['gw']][0] += (float(row[field])-float(row[target]))**2
            groups[row['gw']][1] += (float(other[field])-float(other[target]))**2
        def improvement(items):
            ratio = sum(x[1] for x in items)/sum(x[0] for x in items)
            return 100*(1-(math.sqrt(ratio) if field == 'xp' else ratio))
        clusters = list(groups.values()); rng = random.Random(790889)
        draws = [improvement(rng.choices(clusters, k=len(clusters))) for _ in range(2000)]
        output[field] = {'relative_error_reduction_percent':improvement(clusters),
            'gw_cluster_bootstrap_95_interval':[quantile(draws, .025), quantile(draws, .975)],
            'clusters':len(clusters), 'n':len(base)}
    return output


def main(research, baseline, output):
    output.mkdir(parents=True, exist_ok=True)
    source = {'v1':read(baseline)}
    for label in ('learned_minutes', 'learned_plus_points_calibration'):
        source[label] = read(research/label/'player_predictions.csv')
    compare = {}; bins = []; means = {}
    for label, rows in source.items():
        rows = [r for r in rows if int(r['likely_player'])]
        means[label] = {k:statistics.mean(float(r[k]) for r in rows)
                        for k in ('xp', 'actual_points', 'p60', 'reached60', 'p_appear', 'appeared')}
        if label != 'v1':
            compare[label] = paired([r for r in source['v1'] if int(r['likely_player'])], rows)
        grouped = collections.defaultdict(list)
        for row in rows:
            grouped[min(4, int(float(row['p60'])*5))].append(row)
        for index, items in sorted(grouped.items()):
            bins.append({'model':label, 'p60_bin':f'{index/5:.1f}-{(index+1)/5:.1f}', 'n':len(items),
                         'predicted':statistics.mean(float(r['p60']) for r in items),
                         'observed':statistics.mean(float(r['reached60']) for r in items)})
    (output/'current-uncertainty.json').write_text(json.dumps({'paired':compare, 'means':means,
        'caveat':'Seven retrospective GW clusters; intervals are descriptive, not independent player-trial confidence or proof of a deployment edge.'}, indent=2)+'\n')
    with (output/'current-probability-bins.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(bins[0])); writer.writeheader(); writer.writerows(bins)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('research', 'baseline', 'output'):
        p.add_argument('--'+name, type=pathlib.Path, required=True)
    a = p.parse_args(); main(a.research, a.baseline, a.output)

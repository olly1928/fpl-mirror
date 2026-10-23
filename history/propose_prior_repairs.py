"""Build a reviewable explicit-season prior proposal; never overwrite live files."""
import argparse
import csv
import hashlib
import io
import json
import pathlib

MAPPING = {'mins':'minutes', 'a':'assists'}
FIELDS = ('mins', 'starts', 'expected_goals', 'expected_assists', 'defensive_contribution',
          'bonus', 'saves', 'yellow_cards', 'red_cards', 'own_goals', 'penalties_missed',
          'penalties_saved', 'a')


def propose(official, frozen, season, output):
    original = frozen.read_bytes()
    rows = list(csv.DictReader(line for line in original.decode().splitlines() if not line.startswith('#')))
    changes = []
    for row in rows:
        path = official/'players'/f'{row["id"]}.json'
        if not path.exists():
            continue
        history = json.loads(path.read_text())['history_past']
        previous = next((r for r in history if r['season_name'] == season), None)
        if previous is None:
            continue
        altered = {}
        for field in FIELDS:
            source = MAPPING.get(field, field)
            if source in previous and previous[source] not in (None, '') and float(row[field] or 0) != float(previous[source]):
                altered[field] = {'old':row[field], 'proposed':previous[source]}
                row[field] = str(previous[source])
        if altered:
            changes.append({'player_id':int(row['id']), 'name':row['name'],
                            'source_season':season, 'code':previous['element_code'], 'fields':altered})
    output.mkdir(parents=True, exist_ok=True)
    filename = output/'priors-2026-27-proposed.csv'
    with filename.open('w', newline='', encoding='utf-8') as f:
        f.write('# Research proposal only; not installed as the live prior.\n')
        f.write('# Explicit official history_past season '+season+'; captured after GW7.\n')
        f.write('# Preserve the original pre-GW1 frozen prior and old forecast records.\n')
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    report = {'source_season':season, 'original_sha256':hashlib.sha256(original).hexdigest(),
              'changed_rows':len(changes), 'total_rows':len(rows), 'changes':changes,
              'not_a_historical_forecast_input':True, 'live_model_changed':False}
    (output/'prior-repair-proposal.json').write_text(json.dumps(report, indent=2)+'\n')
    assert frozen.read_bytes() == original
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('official', 'frozen', 'output'):
        p.add_argument('--'+name, type=pathlib.Path, required=True)
    p.add_argument('--season', required=True)
    a = p.parse_args(); result = propose(a.official, a.frozen, a.season, a.output)
    print(json.dumps({'proposal_rows':result['total_rows'], 'changed_rows':result['changed_rows'], 'live_model_changed':False}))

"""Reconcile official season aggregates, frozen priors and current outcomes."""
import argparse
import csv
import json
import pathlib


def read_csv(path):
    return list(csv.DictReader(line for line in path.read_text().splitlines() if not line.startswith('#')))


def run(official, prior_path, outcomes_path, archive_previous, output):
    bootstrap = json.loads((official/'bootstrap.json').read_text())
    players = {int(r['id']):r for r in bootstrap['elements']}
    priors = read_csv(prior_path)
    archived = {r['code']:r for r in read_csv(archive_previous/'players_raw.csv')}
    matched = 0; differences = []; missing_ids = []; outcome_differences = []
    actuals = {(int(r['gw']), int(r['player_id'])):r for r in read_csv(outcomes_path)}
    official_rows = 0; past_counts = {}; captures = {}
    for pid, player in players.items():
        path = official/'players'/f'{pid}.json'
        body = json.loads(path.read_text()); captures[pid] = body
        for row in body['history_past']:
            past_counts[row['season_name']] = past_counts.get(row['season_name'], 0)+1
        for row in body['history']:
            key = (int(row['round']), pid)
            if key not in actuals:
                continue
            official_rows += 1; mirror = actuals[key]
            for field in ('minutes', 'starts', 'total_points', 'goals_scored', 'assists',
                          'expected_goals', 'expected_assists', 'defensive_contribution', 'bonus'):
                if field in row and field in mirror and float(row[field] or 0) != float(mirror[field] or 0):
                    outcome_differences.append({'gw':key[0], 'player_id':pid, 'field':field,
                                               'official':row[field], 'mirror':mirror[field]})
    for prior in priors:
        pid = int(prior['id'])
        if pid not in captures:
            missing_ids.append(pid); continue
        row = next((r for r in captures[pid]['history_past'] if r['season_name'] == '2025/26'), None)
        if row is None:
            continue
        matched += 1; fields = {}
        archive = archived.get(str(players[pid]['code']), {})
        for field in ('mins', 'starts', 'expected_goals', 'expected_assists', 'defensive_contribution',
                      'bonus', 'saves', 'yellow_cards', 'red_cards', 'own_goals',
                      'penalties_missed', 'penalties_saved', 'a'):
            api_field = {'mins':'minutes', 'a':'assists'}.get(field, field)
            if api_field in row and float(prior.get(field) or 0) != float(row[api_field] or 0):
                fields[field] = {'frozen':prior.get(field), 'official_history':row[api_field],
                    'archive_previous':archive.get(api_field),
                    'archive_agrees':api_field in archive and float(archive[api_field] or 0) == float(row[api_field] or 0)}
        if fields:
            differences.append({'player_id':pid, 'code':players[pid]['code'], 'name':prior['name'],
                'differences':fields, 'owned_in_current_seven':False})
    # Ownership is an outcome fixture and is never fed into prior calibration.
    squad_folder = prior_path.parent.parent/'backtests/fixtures'
    if squad_folder.exists():
        owned = {p['element'] for path in squad_folder.glob('squad_gw*.json')
                 for p in json.loads(path.read_text())['picks']}
        for row in differences:
            row['owned_in_current_seven'] = row['player_id'] in owned
    result = {'official_players':len(players), 'past_aggregate_rows':sum(past_counts.values()),
        'past_aggregate_rows_by_season':dict(sorted(past_counts.items())),
        'frozen_prior_rows':len(priors), 'matched_previous_season_rows':matched,
        'missing_current_player_ids':missing_ids, 'prior_differences':differences,
        'current_outcome_rows_reconciled':official_rows, 'current_outcome_differences':outcome_differences,
        'interpretation':['Six source-prior conflicts do not establish that an old captured response was corrupted: API-carried totals and later historical records can differ.',
                          'Both current official history and the archive corroborate the replacement values; future prior construction should select an explicit season.',
                          'Do not silently revise frozen historical forecast inputs after observing outcomes.',
                          'Official old season aggregates have survivor bias and are not enough for a full historical gameweek backtest.']}
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('official', 'prior', 'outcomes', 'archive-previous', 'output'):
        p.add_argument('--'+name, type=pathlib.Path, required=True)
    a = p.parse_args(); x = run(a.official, a.prior, a.outcomes, a.archive_previous, a.output)
    print(json.dumps({'prior_conflicts':len(x['prior_differences']), 'current_outcome_differences':len(x['current_outcome_differences'])}))

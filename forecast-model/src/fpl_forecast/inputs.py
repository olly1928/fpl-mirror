"""Local official-capture validation and per-fixture history conversion."""
from pathlib import Path
import numpy as np
import pandas as pd
from . import model as modern
from .utils import read_json, sha256, utc, POSITIONS

def read_capture(directory):
    """Validate every bound local capture file before reading forecast inputs."""
    root = Path(directory).resolve()
    manifest = read_json(root / 'manifest.json')
    origin, deadline = utc(manifest['origin_utc']), utc(manifest['target_deadline_utc'])
    if origin >= deadline:
        raise ValueError('Capture origin must precede target deadline')
    if origin > pd.Timestamp.now(tz='UTC'):
        raise ValueError('Capture origin cannot be in the future')
    records, summaries, seen = {}, {}, set()
    for item in manifest['files']:
        path = (root / item['path']).resolve()
        if root not in path.parents or path in seen:
            raise ValueError('Capture path escapes root or is repeated')
        seen.add(path)
        if sha256(path) != item['sha256']:
            raise ValueError('Capture hash mismatch: ' + item['path'])
        if utc(item['retrieved_at']) > origin:
            raise ValueError('Capture file was retrieved after frozen origin')
        role = item['role']
        if role == 'element_summary':
            player = int(item['player_id'])
            if player in summaries:
                raise ValueError('Duplicate element summary')
            summaries[player] = read_json(path)
        elif role in {'bootstrap', 'fixtures'}:
            if role in records:
                raise ValueError('Duplicate capture role: ' + role)
            records[role] = read_json(path)
    if set(records) != {'bootstrap', 'fixtures'}:
        raise ValueError('Bootstrap and fixture capture required')
    bootstrap = records['bootstrap']
    players = bootstrap['elements']
    ids, codes = [int(p['id']) for p in players], [str(p['code']) for p in players]
    if len(set(ids)) != len(ids) or len(set(codes)) != len(codes):
        raise ValueError('Current IDs/codes must be unique')
    if set(summaries) != set(ids):
        raise ValueError('Element summaries must cover exactly the registered population')
    target = int(manifest['target_gameweek'])
    events = {int(e['id']): e for e in bootstrap['events']}
    if target not in events or utc(events[target]['deadline_time']) != deadline:
        raise ValueError('Target deadline differs from official bootstrap')
    future = [gw for gw, e in events.items() if utc(e['deadline_time']) > origin]
    if not future or target != min(future):
        raise ValueError('Target must be the first future official deadline')
    weeks = [gw for gw in range(target, target + 6) if gw in events]
    if not weeks:
        raise ValueError('Empty forecast horizon')
    fixtures = records['fixtures']
    fixture_ids = [int(f['id']) for f in fixtures]
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError('Duplicate official fixture IDs')
    return manifest, bootstrap, fixtures, summaries, weeks

def summary_history(bootstrap, fixtures, summaries, season, origin, target_gameweek):
    """Per-fixture current observations; missing count fields remain unknown."""
    origin = utc(origin)
    players = {int(p['id']): p for p in bootstrap['elements']}
    fixture_lookup = {int(f['id']): f for f in fixtures}
    rows, excluded = [], {'unfinished': 0, 'availability_lag': 0, 'target_or_future_gameweek': 0}
    seen = set()
    for element_id, summary in summaries.items():
        p = players[int(element_id)]
        for raw in summary['history']:
            fixture_id = int(raw['fixture'])
            key = (int(element_id), fixture_id)
            if key in seen:
                raise ValueError('Duplicate player-fixture summary row')
            seen.add(key)
            fixture = fixture_lookup.get(fixture_id)
            if fixture is None:
                raise ValueError('Summary fixture absent from frozen schedule')
            if fixture.get('event') != raw['round']:
                raise ValueError('Summary gameweek disagrees with fixture')
            if raw.get('element', element_id) != element_id:
                raise ValueError('Summary element identity mismatch')
            home = raw['was_home']
            if not isinstance(home, bool):
                raise ValueError('Historical home indicator must be boolean')
            opponent = fixture['team_a'] if home else fixture['team_h']
            if int(raw['opponent_team']) != int(opponent):
                raise ValueError('Summary opponent disagrees with fixture')
            if not fixture.get('finished', False):
                excluded['unfinished'] += 1
                continue
            event_time = utc(fixture['kickoff_time'])
            if utc(raw['kickoff_time']) != event_time:
                raise ValueError('Summary kickoff disagrees with fixture')
            if int(raw['round']) >= target_gameweek:
                excluded['target_or_future_gameweek'] += 1
                continue
            if event_time + pd.Timedelta(hours=48) >= origin:
                excluded['availability_lag'] += 1
                continue
            row = dict(raw)
            row.update(observation_id=f'shadow:{season}:{p["code"]}:{fixture_id}',
                       player_id='fpl-code:' + str(p['code']), fpl_code=str(p['code']),
                       fpl_element_id=int(element_id), fpl_position=POSITIONS[int(p['element_type'])],
                       season=season, gameweek=int(raw['round']), event_time=event_time,
                       source_fixture_id=f'fpl:{season}:{fixture_id}')
            for field in list(modern.BASE_LAG_VALUES) + ['defensive_contribution']:
                value = raw.get(field)
                row[field] = np.nan if value is None else float(value)
                if value is not None and (not np.isfinite(row[field]) or (field != 'total_points' and row[field] < 0)):
                    raise ValueError('Invalid observed count: ' + field)
            if pd.notna(row['minutes']) and row['minutes'] > 90:
                raise ValueError('Observed minutes exceed supported regulation range')
            rows.append(row)
    columns = list(dict.fromkeys(['observation_id', 'player_id', 'fpl_code', 'fpl_element_id',
                                 'fpl_position', 'season', 'gameweek', 'event_time', 'source_fixture_id']
                                + list(modern.BASE_LAG_VALUES) + ['defensive_contribution']))
    history = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
    history['event_time'] = pd.to_datetime(history.event_time, utc=True)
    return modern.masks_and_targets(history), excluded

"""Capture public official FPL responses and checksum-pinned historical panels.

No account login, forecasts, transfers or production-data writes. Raw response
bytes are preserved in tar.gz packages, with provenance and per-file hashes.
"""
import argparse
import collections
import concurrent.futures
import datetime
import gzip
import hashlib
import io
import json
import os
import pathlib
import tarfile
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = 'https://fantasy.premierleague.com/api/'
REQUEST_LOCK = threading.Lock()
NEXT_REQUEST = 0.0


def pace(url):
    global NEXT_REQUEST
    if not url.startswith(API):
        return
    with REQUEST_LOCK:
        current = time.monotonic(); delay = max(0.0, NEXT_REQUEST-current)
        NEXT_REQUEST = max(current, NEXT_REQUEST)+0.3
    if delay:
        time.sleep(delay)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def capture(url, path, expected=None):
    metadata = path.with_suffix(path.suffix+'.meta.json')
    if path.exists() and metadata.exists():
        raw = path.read_bytes(); record = json.loads(metadata.read_text())
        if record['url'] != url or digest(raw) != record['sha256']:
            raise ValueError('Cached source identity/checksum mismatch: '+str(path))
    else:
        for attempt in range(3):
            try:
                pace(url)
                request = urllib.request.Request(url, headers={'User-Agent': 'FPL-historical-research/2.0'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read(); status = response.status
                break
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                    raise
                time.sleep(1+attempt*2)
        record = {'url': url, 'status': status, 'bytes': len(raw), 'sha256': digest(raw),
                  'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); write_json(metadata, record)
    if expected and (digest(raw) != expected['sha256'] or len(raw) != expected['bytes']):
        raise ValueError('Archive bytes differ from pinned source manifest: '+url)
    return raw, record


def pack(destination, root, paths):
    """Deterministic tar metadata; preserve the exact uncompressed source bytes."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix+'.partial')
    with temporary.open('wb') as stream:
        with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as archive:
                for path in sorted(paths):
                    body = path.read_bytes(); info = tarfile.TarInfo(str(path.relative_to(root)))
                    info.size = len(body); info.mode = 0o644; info.mtime = 0
                    archive.addfile(info, io.BytesIO(body))
        stream.flush(); os.fsync(stream.fileno())
    with tarfile.open(temporary, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) != len(paths):
            raise ValueError('Incomplete raw package: '+str(destination))
        for member in members:
            original = root/member.name
            if digest(archive.extractfile(member).read()) != digest(original.read_bytes()):
                raise ValueError('Raw package differs from captured bytes: '+member.name)
    os.replace(temporary, destination)
    return {'file': destination.name, 'bytes': destination.stat().st_size,
            'sha256': digest(destination.read_bytes()), 'members': len(paths)}


def collect_official(output, workers=8, seed=None):
    folder = output/'official'; folder.mkdir(parents=True, exist_ok=True)
    if seed:
        for name, endpoint in [('bootstrap', 'bootstrap-static/'), ('haaland', 'element-summary/411/'),
                               ('player1', 'element-summary/1/'), ('fixtures', 'fixtures/'),
                               ('bootstrap_season_query', 'bootstrap-static/?season=2025-26'),
                               ('fixtures_season_query', 'fixtures/?season=2025-26')]:
            source = seed/(name+'.json')
            if not source.exists():
                continue
            target = folder/(name+'.json')
            if target.exists():
                continue
            raw = source.read_bytes(); target.write_bytes(raw)
            records = json.loads((seed/'probe.json').read_text())
            record = next(r for r in records if r['name'] == name)
            write_json(target.with_suffix('.json.meta.json'), {k:record[k] for k in
                ('url', 'status', 'bytes', 'sha256', 'captured_at')})
    bootstrap, record = capture(API+'bootstrap-static/', folder/'bootstrap.json')
    data = json.loads(bootstrap); elements = data['elements']
    ids = [int(p['id']) for p in elements]
    if len(ids) != len(set(ids)) or len(ids) < 100:
        raise ValueError('Invalid official player universe')
    season_start = min(e['deadline_time'] for e in data['events'])[:4]
    active_season = season_start+'/'+str(int(season_start)+1)[-2:]
    print(json.dumps({'official_players': len(ids), 'active_season': active_season}), flush=True)
    codes = {int(p['id']): int(p['code']) for p in elements}
    for pid, seed_name in [(411, 'haaland'), (1, 'player1')]:
        source = folder/(seed_name+'.json')
        target = folder/'players'/f'{pid}.json'
        if source.exists() and not target.exists():
            target.parent.mkdir(exist_ok=True); target.write_bytes(source.read_bytes())
            target.with_suffix('.json.meta.json').write_bytes(source.with_suffix('.json.meta.json').read_bytes())
    failures = []; counts = collections.Counter(); fields = collections.defaultdict(set)
    captures = []; histories = collections.Counter(); total = 0
    def one(pid):
        raw, metadata = capture(API+f'element-summary/{pid}/', folder/'players'/f'{pid}.json')
        value = json.loads(raw)
        for row in value['history_past']:
            if int(row['element_code']) != codes[pid]:
                raise ValueError('Cross-season player-code mismatch: '+str(pid))
        if any(int(r['element']) != pid for r in value['history']):
            raise ValueError('Current player-id mismatch: '+str(pid))
        return pid, value, metadata
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, pid): pid for pid in ids}
        for future in concurrent.futures.as_completed(futures):
            pid = futures[future]
            try:
                _, value, metadata = future.result(); captures.append(metadata)
                for r in value['history_past']:
                    counts[r['season_name']] += 1; fields[r['season_name']].update(r)
                histories[pid] = len(value['history'])
            except Exception as exc:
                failures.append({'player_id': pid, 'error': str(exc)})
            total += 1
            if total % 50 == 0 or total == len(ids):
                print(json.dumps({'official_completed': total, 'total': len(ids), 'failures': len(failures)}), flush=True)
    probes = []
    for name, endpoint in [('fixtures', 'fixtures/'), ('fixtures_season_query', 'fixtures/?season=2025-26'),
                           ('bootstrap_season_query', 'bootstrap-static/?season=2025-26')]:
        raw, metadata = capture(API+endpoint, folder/(name+'.json')); probes.append(metadata)
    regular = json.loads((folder/'fixtures.json').read_bytes())
    queried = json.loads((folder/'fixtures_season_query.json').read_bytes())
    queried_bootstrap = json.loads((folder/'bootstrap_season_query.json').read_bytes())
    result = {'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'bootstrap_capture': record, 'active_season': active_season, 'players_requested': len(ids),
        'players_captured': len(captures), 'failures': failures,
        'history_current_rows': sum(histories.values()),
        'history_past_rows': sum(counts.values()), 'history_past_rows_by_season': dict(sorted(counts.items())),
        'history_past_fields_by_season': {s:sorted(v) for s,v in sorted(fields.items())},
        'season_query_probe': {'requested': '2025-26', 'fixtures_identical_to_current': regular == queried,
            'returned_first_kickoff': min(f['kickoff_time'] for f in queried if f.get('kickoff_time')),
            'bootstrap_first_deadline': min(e['deadline_time'] for e in queried_bootstrap['events']),
            'responses': probes},
        'interpretation': ['history_past contains season aggregates, not historical gameweek panels.',
            'Only players in the present bootstrap are enumerated: survivor bias excludes retired/departed players.',
            'A tested season query does not switch the fixtures/bootstrap to that old season.',
            'Official responses captured now are outcomes/audit evidence, never pre-deadline inputs for past forecasts.']}
    write_json(folder/'coverage.json', result)
    if failures:
        raise ValueError('Official collection incomplete; inspect coverage.json and retry the same cache')
    package = pack(output/'packages'/('official-fpl-'+active_season.replace('/', '-')+'.tar.gz'), folder,
                   [p for p in folder.rglob('*') if p.is_file()])
    return result, package


def collect_archives(output, cache=None):
    manifest = json.loads((ROOT/'backtests/sources/archive-manifest.json').read_text())
    folders = collections.defaultdict(list); records = []
    for source in manifest['files']:
        if 'error' in source:
            records.append(source); continue
        relative = pathlib.Path(source['path']).relative_to('raw/archive')
        path = output/'archive'/relative
        if cache and (cache/relative).exists() and not path.exists():
            raw = (cache/relative).read_bytes()
            if digest(raw) != source['sha256']:
                raise ValueError('Cached archive differs from manifest: '+str(relative))
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            write_json(path.with_suffix(path.suffix+'.meta.json'), source)
        _, record = capture(source['url'], path, source)
        records.append(record); folders[relative.parts[0]].extend([path, path.with_suffix(path.suffix+'.meta.json')])
    packages = []
    for season, paths in sorted(folders.items()):
        packages.append(pack(output/'packages'/f'archive-{season}.tar.gz', output/'archive'/season, paths))
    write_json(output/'archive-manifest.json', {'archive_commit': manifest['repository_commit'], 'files': records})
    return packages


def main(output, workers, seed, archive_cache, archive_only=False):
    output.mkdir(parents=True, exist_ok=True)
    packages = collect_archives(output, archive_cache)
    official = None
    if not archive_only:
        official, package = collect_official(output, workers, seed); packages.append(package)
    result = {'official': official, 'packages': packages,
              'archive_commit': 'd7b05b51d137bfefef4b692386d18c610975b9b9'}
    write_json(output/'collection-summary.json', result)
    print(json.dumps({'packages': len(packages), 'compressed_bytes': sum(p['bytes'] for p in packages)}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=pathlib.Path, required=True)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--seed', type=pathlib.Path)
    p.add_argument('--archive-cache', type=pathlib.Path)
    p.add_argument('--archive-only', action='store_true')
    a = p.parse_args()
    if not 1 <= a.workers <= 16:
        p.error('--workers must be between1 and16')
    main(a.output, a.workers, a.seed, a.archive_cache, a.archive_only)

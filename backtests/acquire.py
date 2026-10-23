"""Download immutable, checksum-verified backtest sources; never production data."""
import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent


def verify(data, record):
    if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('Source bytes differ from pinned manifest: ' + record['url'])


def fetch(record, output):
    if 'error' in record:
        return {'path': record['path'], 'skipped': record['error']}
    path = output / record['path']
    if path.exists():
        verify(path.read_bytes(), record)
        return {'path': record['path'], 'cached': True}
    request = urllib.request.Request(record['url'], headers={'User-Agent': 'FPL-predictive-backtest/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    verify(data, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {'path': record['path'], 'bytes': len(data)}


def main(output, scope='all'):
    records = []
    filenames = ('current-core-manifest.json', 'outcomes-manifest.json') if scope == 'current' else (
        'archive-manifest.json', 'current-core-manifest.json', 'outcomes-manifest.json')
    for filename in filenames:
        records.extend(json.loads((ROOT/'sources'/filename).read_text())['files'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(lambda r: fetch(r, output), records):
            print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT/'work')
    parser.add_argument('--scope', choices=('all', 'current'), default='all')
    args = parser.parse_args(); main(args.output, args.scope)

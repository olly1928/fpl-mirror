#!/usr/bin/env bash
set -euo pipefail
# One-off source expansion. No cron, no FPL login, no live feed/model writes.
work=history/extended/work
mkdir -p "$work"
cp history/extended/inventories/history.json "$work/history-inventory.json"
cp history/extended/inventories/supplement.json "$work/supplement-inventory.json"
cp history/extended/inventories/understat.json "$work/understat-inventory.json"
cp history/extended/inventories/auxiliary.json "$work/auxiliary-inventory.json"
python history/extend_zip_cache.py --inventory "$work/history-inventory.json" --output "$work/player-histories" --repository vaastav/Fantasy-Premier-League
python history/extend_sources.py --inventory "$work/history-inventory.json" --output "$work/player-histories"
for repo in mvbfontes/premierleaguedatasets barryedmund/gaffer nori/hbv401g-2015v-f1a; do
  python history/extend_zip_cache.py --inventory "$work/supplement-inventory.json" --output "$work/early-archives" --repository "$repo"
done
python history/extend_sources.py --inventory "$work/supplement-inventory.json" --output "$work/early-archives"
python history/extend_zip_cache.py --inventory "$work/understat-inventory.json" --output "$work/understat" --repository vaastav/Fantasy-Premier-League
python history/extend_sources.py --inventory "$work/understat-inventory.json" --output "$work/understat"
python history/extend_sources.py --inventory "$work/auxiliary-inventory.json" --output "$work/auxiliary" --workers 4
python history/probe_extended_api.py
python history/unpack.py --output "$work/original"
python backtests/history_data.py --root "$work/original/archive" --output "$work/original-normalised"
# Optional packages are confined to this one-time R-to-CSV conversion.
# The published CSVs and all production Python remain standard-library readable.
python - <<'PY'
import gzip,json
from pathlib import Path
import pyreadr
root=Path('history/extended/work/early-archives')
out=Path('history/extended/data/r-converted');out.mkdir(parents=True,exist_ok=True)
for record in json.loads((root/'manifest.json').read_text())['records']:
    if record['repository']!='durtal/fantasysocceR' or not record['path'].endswith('.RData'):continue
    name=Path(record['path']).stem
    frame=pyreadr.read_r(str(root/record['object']))[name]
    with (out/(name+'.csv.gz')).open('wb') as stream,gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0) as compressed:
        compressed.write(frame.to_csv(index=False).encode())
PY
python history/extend_dataset.py --work "$work" --output history/extended/data --base-panel "$work/original-normalised/normalised.csv"
python history/package_extended.py --work "$work" --output history/extended/raw
python history/verify_extended.py --root history/extended

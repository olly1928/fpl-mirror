#!/usr/bin/env bash
set -euo pipefail
# Run at repository root in the pinned optional environment. 16 GB RAM is
# recommended; choose FPL_RESEARCH_JOBS=1 on a smaller machine. No live writes.
FPL_RESEARCH_JOBS="${FPL_RESEARCH_JOBS:-2}"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MPLBACKEND=Agg
python history/unpack.py --output research/work/source
python backtests/acquire.py --scope current --output research/work/replay-inputs
python -m research.prepare --output research/work/canonical-features --current research/work/replay-inputs/raw/current-core --actuals research/work/replay-inputs/outcomes/player_history.csv
python -m research.experiment --features research/work/canonical-features --output research/work/remote/experiments --jobs "$FPL_RESEARCH_JOBS"
python -m research.prepare_six_cache
python -m research.six_week --features research/work/canonical-features --work research/work/remote/six-week --reuse-labels --jobs "$FPL_RESEARCH_JOBS"
python backtests/history_data.py --root research/work/source/archive --output history/work/normalised
python backtests/replay_current.py --data-root research/work/replay-inputs/raw/current-core --refs backtests/sources/current-core-refs.json --actuals research/work/replay-inputs/outcomes/player_history.csv --squads backtests/fixtures --output research/work/current-v1
python history/research.py --panel history/work/normalised/normalised.csv --archive research/work/source/archive --output research/work/legacy --current-data research/work/replay-inputs/raw/current-core --refs backtests/sources/current-core-refs.json --actuals research/work/replay-inputs/outcomes/player_history.csv --squads backtests/fixtures --baseline-predictions research/work/current-v1/player_predictions.csv
mkdir -p research/work/baselines
cp research/work/legacy/historical-player-predictions.csv research/work/baselines/historical.csv
cp research/work/current-v1/player_predictions.csv research/work/baselines/current-v1.csv
cp research/work/current-v1/forecasts.replay.json research/work/baselines/current-v1-forecasts.json
cp research/work/current-v1/summary.json research/work/baselines/current-v1-summary.json
cp research/work/legacy/learned_minutes/player_predictions.csv research/work/baselines/current-v2.csv
python -m research.audit
python -m research.finalize --features research/work/canonical-features --experiments research/work/remote/experiments
python -m research.refinements
python -m research.stability
python - <<'PY'
from pathlib import Path
import json, shutil, pandas as pd
for year in range(2016,2026):
    source=Path('research/work/remote/experiments')/str(year)
    target=Path('research/results/folds')/f'fold-{year}'/'one-week'
    target.mkdir(parents=True,exist_ok=True)
    for path in source.glob('*-seed1729.*'):shutil.copy2(path,target/path.name)
    pd.read_pickle(source/'targets.pkl').to_csv(target/'targets.csv.gz',index=False,compression={'method':'gzip','mtime':0})
path=Path('research/results/workflow-run.json')
if not path.exists():path.write_text(json.dumps({'run_id':'local','source_commit':'see experiment.json','trials':790,'live_model_changed':False},indent=2)+'\n')
PY
python -m research.report
python -m unittest research.test_research
python tests/test_offline.py
python tests/test_advisor.py
python tests/test_backtests.py
python tests/test_history.py

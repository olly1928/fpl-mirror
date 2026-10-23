#!/usr/bin/env bash
set -euo pipefail

# Run from the repository root. All writes are confined to ignored work folders.
python history/unpack.py --output history/work/source
python backtests/history_data.py --root history/work/source/archive --output history/work/normalised
python backtests/acquire.py --scope current --output history/work/replay-inputs
python backtests/replay_current.py \
  --data-root history/work/replay-inputs/raw/current-core \
  --refs backtests/sources/current-core-refs.json \
  --actuals history/work/replay-inputs/outcomes/player_history.csv \
  --squads backtests/fixtures --output history/work/current-v1
python history/research.py --panel history/work/normalised/normalised.csv \
  --archive history/work/source/archive --output history/work/research \
  --current-data history/work/replay-inputs/raw/current-core \
  --refs backtests/sources/current-core-refs.json \
  --actuals history/work/replay-inputs/outcomes/player_history.csv \
  --squads backtests/fixtures \
  --baseline-predictions history/work/current-v1/player_predictions.csv
python history/audit_official.py --official history/work/source/official \
  --prior model/priors-2026-27.csv \
  --outcomes history/work/replay-inputs/outcomes/player_history.csv \
  --archive-previous history/work/source/archive/2025-26 \
  --output history/work/research/official-audit.json
python history/summarise.py --research history/work/research \
  --baseline history/work/current-v1/player_predictions.csv --output history/work/research
python history/benchmarks.py --research history/work/research --baseline history/work/current-v1 \
  --data history/work/replay-inputs/raw/current-core --output history/work/research
python history/propose_prior_repairs.py --official history/work/source/official \
  --frozen model/priors-2026-27.csv --season 2025/26 --output history/work/research/proposals

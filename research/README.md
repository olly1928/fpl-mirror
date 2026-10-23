# Projection model laboratory

Optional research, isolated from the standard-library production advisor. The weekly, preseason and smoke-test playbook URLs and the live v1 model are unchanged.

The declared search compares 67 point configurations across 12 families plus 12 learned minute-state configurations. Ten chronological OOF seasons support nested selection for eight outer seasons, followed by the seven archived current-season deadline snapshots. A separate purged six-gameweek experiment tests the longer transfer horizon. See `protocol.json` and `protocol-six.json` for populations, selection rules and caveats.

This research is in progress on the research branch. A completed report will accompany the retained results; incomplete fit logs are not evidence of improvement. Historical outcomes have already been inspected, so these are retrospective tests, not pristine external holdouts.

Use Python 3.12 in a separate virtual environment and install `research/requirements.txt`. The one-off **Projection model research** workflow prepares a single checksum-verified feature cache and fits independent season folds on standard public-repository runners. It has no schedule and does not update production. Production's stdlib import gate continues to cover every Python file outside this dedicated research directory; optional research has its own pinned CI test job.

The model library includes shrinkage/rolling baselines, ridge, elastic net, random forest, extra trees, histogram boosting, LightGBM, XGBoost, CatBoost, minute-state hurdles and Poisson scoring components. The primary ensemble and calibration fit only earlier OOF predictions. An outer season never chooses its own winner.

Inputs are the committed, immutable historical captures under `history/extended`, including players who have left the Premier League. Exact source hashes are recorded with the features. Historical bookmaker prices, post-match archive xP, target-season aggregates and missing provider statistics filled with false zeros are excluded. Unknown identity/target rows from the early partial SQLite archive remain quarantined.

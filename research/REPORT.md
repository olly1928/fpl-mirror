# FPL projection model research

The lowest recent historical error among the evaluated procedures came from **Top-three mean**. The choice uses the three complete modern seasons, without the seven current outcomes. Its annual RMSE improvement against v1 ranges from **+1.34% to +2.29%**; the current fixed-pool comparison is **-0.06%**. A positive value means lower error. These results do not establish perfect calibration or guaranteed extra team points. **No production model or playbook has been changed.**

## What was tested

The original broad run evaluated **67 points configurations across 12 families, plus 12 minutes configurations, over ten chronological folds: 790 candidate/fold evaluations**. Eight outer seasons (2018/19–2025/26) use only preceding OOF seasons for configuration/family selection, nonnegative ensemble weights and calibration. The first two seasons supply selection history. The library covers rolling/shrinkage baselines, ridge, elastic net, random forest, extra trees, histogram boosting, LightGBM, XGBoost, CatBoost, minute hurdles and Poisson scoring components.

Separate refinements test exact Poisson save/conceded blocks, replacing only v1 minutes, and calibrating the disjoint minute-state probabilities. Original source ablations are retained as diagnostic configurations, with two attribution limitations: there is no identical-parameter full-feature anchor, and removing explicit team columns leaves derived team-context components. They cannot establish an isolated causal benefit from an individual source. No further model search was added while recovering the report. The six-week experiment tests eight additional configurations, with labels purged at season boundaries. The 2025 outer ensemble is repeated across three seeds without selecting a seed.

The feature population contains **320,931 historical player-fixture rows**, including 12 complete seasons and partial 2013/14 warm-up, and **4,517 current snapshot/outcome intersections**. There are zero duplicate player-fixtures, unknown point/minute targets or as-of boundary violations in that population. Primary points metrics sum DGWs to player-gameweek. The fixed current v1 pool contains 1,205 observations and the owned-squad pool 105. The larger raw outcome capture contains later registrations absent from earlier snapshots; these are not silently added to a deadline forecast.

## Comparison with the existing model

The following table uses exactly the existing v1 modern benchmark cases. It inherits that old benchmark's exclusions of DGWs and first-observed rows; the broader eight-season tests include those cases.

| Procedure | 2023-24 | 2024-25 | 2025-26 |
| --- | --- | --- | --- |
| Exact Poisson components | 2.965 | 2.881 | 2.997 |
| Live v1 | 3.023 | 2.921 | 3.068 |
| Nested ensemble | 2.964 | 2.882 | 2.996 |
| Top-three mean | 2.962 | 2.881 | 2.997 |
| V1 + boosted minutes | 2.988 | 2.895 | 3.003 |
| V1 + calibrated minutes | 2.988 | 2.895 | 3.003 |

The pooled matched-case RMSE improvement is 1.90%, with a descriptive 95% four-GW-block interval of [1.59%, 2.20%]. Intervals are conditional on this retrospective protocol and available archive population, and do not eliminate method-search or future-regime uncertainty. See [extended-comparison.json](results/extended-comparison.json) for all paired comparisons and [model-comparison.csv](results/model-comparison.csv) for MAE, bias and calibration slope/intercept as well as RMSE.

Current seven-week results on the same fixed candidate pool:

| Procedure | Cases | RMSE | MAE | RMSE improvement % |
| --- | --- | --- | --- | --- |
| Live v1 | 1205 | 3.187 | 2.334 | 0.000 |
| Earlier learned minutes | 1205 | 3.179 | 2.348 | 0.222 |
| Nested ensemble | 1205 | 3.190 | 2.347 | -0.094 |
| Top-three mean | 1205 | 3.188 | 2.345 | -0.059 |
| V1 + boosted minutes | 1205 | 3.172 | 2.359 | 0.462 |
| V1 + calibrated minutes | 1205 | 3.170 | 2.367 | 0.524 |
| Exact Poisson components | 1205 | 3.193 | 2.358 | -0.198 |

On the actual owned 15 over the same seven weeks:

| Procedure | Cases | RMSE | MAE | RMSE improvement % |
| --- | --- | --- | --- | --- |
| Live v1 | 105 | 2.972 | 2.377 | 0.000 |
| Top-three mean | 105 | 3.051 | 2.365 | -2.659 |
| V1 + boosted minutes | 105 | 2.969 | 2.379 | 0.099 |
| V1 + calibrated minutes | 105 | 2.965 | 2.381 | 0.232 |

The seven weeks are too few for a precise independent validation. They were already known during research design, although they did not fit weights, calibrators or hyperparameters. Current previous-season inputs are later-captured completed outcomes and include corrections to six frozen-v1 priors; none of those six players was owned in these weeks. This is not a pristine archived complete feature vintage.

## Decisions and probabilities

The actual team scored **415** points. Numerical policies on the actual owned 15, with legal formations, captain/vice and actual autosubs, scored:

| Procedure | Conditional points |
| --- | --- |
| Live v1 | 422.000 |
| Nested ensemble | 429.000 |
| Top-three mean | 429.000 |
| V1 + boosted minutes | 422.000 |
| V1 + calibrated minutes | 422.000 |

This compares XI/captain/bench decisions conditional on the actual squads. It does not simulate transfers, hits, chips, changing budgets or season rank, and does not reconstruct missing Claude news overrides. All weekly selections are retained in the lineup JSON reports. Probability Brier scores, log loss and mean predicted/observed ≥60 frequencies are in [probability-calibration.json](results/probability-calibration.json). A correct aggregate mean alone is insufficient to establish good individual forecasts.

On the same recent historical cases as the v1 comparison, ≥60-minute Brier scores were as follows (lower is better):

| Season | Live v1 | Boosted minutes | Calibrated minutes |
| --- | --- | --- | --- |
| 2023-24 | 0.187 | 0.149 | 0.149 |
| 2024-25 | 0.185 | 0.154 | 0.154 |
| 2025-26 | 0.190 | 0.149 | 0.149 |

The learned minute-state model improves these probability scores substantially even where the resulting points gain is modest. Extra probability rescaling does not improve every season or metric. Appearance Brier, state log loss and predicted/observed frequencies are retained in [matched-minute-comparison.json](results/matched-minute-comparison.json); there is no claim of perfect calibration.

## Full family comparison

Each family selects its own configuration from preceding seasons. The macro RMSE below is the square root of mean seasonal MSE across eight outer seasons, with equal seasonal influence; the worst seasonal RMSE keeps failures visible.

| family | macro_rmse | worst_rmse |
| --- | --- | --- |
| hurdle | 2.998 | 3.096 |
| poisson_components | 2.999 | 3.095 |
| catboost | 3.000 | 3.101 |
| lightgbm | 3.002 | 3.103 |
| xgboost | 3.002 | 3.103 |
| hist_boost | 3.003 | 3.105 |
| extra_trees | 3.004 | 3.105 |
| random_forest | 3.005 | 3.106 |
| elasticnet | 3.007 | 3.103 |
| ridge | 3.008 | 3.104 |
| empirical | 3.054 | 3.157 |
| rolling | 3.126 | 3.235 |

The notebook includes the full season-by-family heatmap, modern/current comparisons, points and minute calibration, conditional decisions and the six-week experiment. Raw and calibrated variants, source/recency controls, cold starts, positions and DGWs are retained rather than reporting only a winning aggregate.

Some convergence warnings were emitted in the broad parallel fits. Python warning capture is shared across threads, so warning text may be associated with a concurrent trial rather than its emitting estimator; the retained per-trial warning attribution is not definitive. Trials were not removed on this basis. Saved forecasts and trial metrics are independently reconciled by [independent-verification.json](results/independent-verification.json).

## Limits and recommendation

**Keep v1 live while trialling the learned minutes model alongside it.** The historical winning ensemble has no demonstrated current-season points advantage and has worse RMSE on the owned-squad sample. The minutes hybrids show small current points gains; their paired seven-week uncertainty intervals include zero. Their stronger probability results make minutes the most promising incremental change, rather than a reason for an immediate wholesale replacement. Preserve the existing Claude playbook URLs, bash/curl routing, sourced news/role overrides and decision provenance. The research minute hybrid currently rejects manual overrides, so it is not a production drop-in. Confirm a versioned candidate at future unseen deadlines before claiming a reliable edge; do not automatically switch the live team advisor on the basis of these results.

Historical fixtures, registrations, prices and position labels are not complete pre-deadline vintages. Fixture assignments are realised schedules; historical odds without provable deadline timestamps and post-match archive xP are excluded. Provider statistics stay missing when unobserved. Understat's match archive ends **2025-04-07**: none of the current rows has one-year Understat coverage, and only 1,352 has two-year coverage. Source diagnostics therefore do not establish the benefit of a current fresh feed. Very old annual/team records are not falsely converted into per-GW player labels; conflicted early SQLite targets remain quarantined. Fourteen historical zero-minute/nonzero-point records remain in the signed points targets; the basic hurdle approximation does not model their rare bench-card mechanism explicitly.

Six-week labels overlap, require known future coverage and use realised fixture counts. Eight-GW block intervals address overlapping origins descriptively; the two current six-week windows are heavily overlapping and cannot supply strong independent validation. Source exclusions and coverage counts are retained in [six-week-assessment.json](results/six-week-assessment.json).

**Readiness: share with these caveats; research evidence is reviewable, production promotion remains separate.**

## Reproduce and inspect

The [completion workflow](https://github.com/olly1928/fpl-mirror/actions/runs/39960356523) passed report generation, independent score reconciliation and production compatibility tests.

Use Python 3.12 and `research/requirements.txt` in an isolated environment. `research/run.sh` reproduces the pipeline; the one-off GitHub workflows parallelise independent folds and have no schedule. [comparison.ipynb](comparison.ipynb) is an executed, portable companion with saved outputs. Its plain Python cells were executed top-to-bottom in a socket-free in-process engine, explicitly recorded in metadata; normal Jupyter kernel execution is not claimed. Training results and models live only under `research/`; production retains its stdlib-only gate and separate acceptance tests.

Raw inputs and immutable source hashes are under `history/extended` and the feature manifests. The broad fit run is [GitHub Actions 39936706280](https://github.com/olly1928/fpl-mirror/actions/runs/39936706280), from commit `58234487bebf786caed1642cb0b44ee06fec1973`. Every declared fold's parameters, training seasons, runtime, warnings and forecast bytes are retained under [results/folds](results/folds). [artifacts/manifest.json](artifacts/manifest.json) records fitted finalist checksums. The model serialisations are research artifacts, not instructions to execute an unreviewed production rollout.

Method references: [scikit-learn data leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage) and [LightGBM's documented objectives and deterministic settings](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html).

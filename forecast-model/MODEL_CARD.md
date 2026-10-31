# Model card — FPL forecast v2

**Recommendation:** retain the existing advisor as primary. This package is an optional research forecast whose fixed procedure can be run on demand. It does not establish a reliable advantage over the advisor and makes no season-long rank, transfer or chip claim.

## Procedure and intended use

The supported season is **2026–27**. The fixed primary seed is **20261028**. The selected procedure, `blend_direct_football_050`, averages a direct supported-score model and a component-assembled score. The package contains 12 saved artifacts: ten selected scoring/appearance components plus shared rolling references for appearance state and minutes. Learned components use CatBoost, XGBoost, LightGBM or scikit-learn histogram boosting. No model selection, training or weight updates occur during a forecast run.

The selected components predict supported score, goals, appearance state, clean-sheet points, save blocks, conceded-goal deductions, negative scoring events, saved penalties, historical assists and historical bonus. Position-specific structural zeros and scoring transforms are retained. Shared rolling availability is reported separately from the appearance component inside the assembled score.

Inputs combine archived public player-fixture histories through 2025–26, team results, a fixed evidenced current-season club map, and a fresh official bootstrap/fixture/player-summary capture. Named feature sets contain 83 base columns, 129 columns with team/opponent context, or 139 columns with exact rolling targets. Current outcomes must belong to an earlier gameweek and pass the 48-hour availability rule. All six forecast weeks share one origin. The package fails for an unsupported season or mismatched club map.

## Evidence and deployment decision

The qualification run completed 1,002 declared recipe/season evaluations across six development folds, including empirical references. Later validation used the locked selection and preserved earlier-only selection constraints. Evaluation and packaging are distinct from operational promotion. The [research report](research/REPORT.md) provides the full comparisons, chronology, uncertainty and coverage limits.

On seven previously exposed 2026–27 gameweeks, the final fixed procedure had the following retrospective diagnostic results:

| Measure | Candidate | Frozen v1 advisor |
| --- | ---: | ---: |
| Full-population points RMSE, 4,517 player-GW observations | 2.25275 | 2.15945 |
| Full-population points MAE | 1.35003 | 1.13393 |
| Conditional lineup points on the same archived squads | 392 | 422 |

Lower RMSE/MAE is better. The owned-player subset contained 105 observations; no individual squad records are included in this public card. Lineups used the same archived 15-player squads, without transfers or chips. These exposed weeks did not choose the recipe, blend or status policy. They do not constitute prospective validation. The candidate's poorer practical results support retaining the existing advisor.

## Engineering validation

The immutable candidate passed local official-capture parity against the selected lab procedure: 669 players, 4,014 fixture rows and gameweeks 10–15. All named feature values/order/dtypes and complete selected outputs agreed. Published CSV/JSON values were checked against the validated tables with absolute tolerance 1e-10 and relative tolerance 1e-12. A 32-case offline fixture covers all four positions and the actual saved models; local maximum output differences were zero, with declared cross-platform tolerance 1e-10.

A relocated CLI run and its nested replay process both demonstrated blocked original-workspace reads and blocked lab imports. Fresh-process predictions were exactly equal. This is a Python-level isolation check, not an OS sandbox or a guarantee about arbitrary native-library I/O. No fitting occurred during these package checks.

**Hosted Linux offline CI passed.** The [hosted run](https://github.com/olly1928/fpl-mirror/actions/runs/41250558816) reported 30 tests passed and one skipped, plus 32 actual-model cases whose 18 checked output columns matched within the declared 1e-10 tolerance. The largest component difference was 5.55e-17; headline prediction and approximate current total were exact. It performed zero fits and zero network requests. The reviewed PR head `feb525113f76467432b3431cb6a8db64b4ba4e67` has green [model offline CI](https://github.com/olly1928/fpl-mirror/actions/runs/41250558816) and existing repository tests. Python 3.12 and exact runtime dependencies remain required.

The manual forecast and offline-CI workflows are installed in the repository. **The first fresh hosted official-API capture, forecast and publication is still pending** and requires authenticated manual dispatch. Saved-input CI does not prove API availability or successful publication. `release_validation_complete` means package/prepublication engineering clearance; `hosted_execution_validated` remains false until the fresh end-to-end run is evidenced. Neither engineering clearance nor hosted execution implies scientific superiority. There is no automatic forecast schedule or season-long service.

## Limitations

- `approximate_current_rule_expected_total` adds twice a lagged defensive-hit prior to the supported score. This current-rule bridge is imperfect; exact current-rule totals remain unavailable.
- Historical FPL assists, bonus definitions, positions and observed opportunity populations differ across eras. The panel is not a certified historical eligible-player roster.
- Team/opponent history, archived fixture-calendar assumptions and missing identity/context coverage limit generalization. There is no certified joint forecast distribution.
- Shared rolling appearance/minutes estimates are imperfect. Current status/chance adjustments are next-gameweek diagnostics only, not a long-horizon injury model.
- Fixture changes, postponements, transfers and later information are unknown at the shared origin. Rerunning with fresh inputs updates forecasts without retraining.
- Aggregate forecasts do not supply an owned squad, optimize transfers/chips or promise FPL rank improvements.

The publication workflow stores bounded public CSV/JSON outputs, not raw captures or personal account data. Source/model/data hashes and fixed selection metadata are in `manifest.json`; public provenance uses logical artifact identifiers rather than machine-specific paths. Keep the original validated candidate and separately bound release evidence when updating documentation or release metadata.

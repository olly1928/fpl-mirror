# Historical predictive backtest — 22 October 2026

**Scope correction:** this first experiment searched 17 fixed configurations.
Failure to promote one of them does not show that v1 is nearly optimal or well
calibrated. See the [broader follow-up](../history/README.md) for direct official
API collection, learned minute probabilities, explicit source-prior conflicts
and chronological tests that use all ten archived seasons for model fitting.

The live model remains **v1.0.0**. It beats a simple lagged-points benchmark in
the two held-out seasons, but the calibration candidate did not meet the
predeclared promotion rule. There is no reliable evidence here for a dramatic
improvement from changing the live parameters.

The seven current-season replays produce **422 points versus the actual 415**,
conditional on owning the same 15 players each week. This is a seven-point
line-up/captaincy counterfactual, not a backtested transfer strategy or a claim
that Claude actually gave those recommendations before the deadline.

## Seven gameweeks, genuine pre-deadline inputs

| GW | Actual points | Model line-up/captain points | Model captain | Snapshot hours before deadline |
|---|---:|---:|---|---:|
| 1 | 52 | 52 | Haaland | 0.14 |
| 2 | 76 | 76 | Haaland | 4.74 |
| 3 | 44 | 44 | Haaland | 2.83 |
| 4 | 79 | 86 | Haaland | 0.64 |
| 5 | 59 | 59 | Haaland | 2.78 |
| 6 | 38 | 38 | Saka | 2.94 |
| 7 | 67 | 67 | Haaland | 2.61 |
| **Total** | **415** | **422** | | |

All seven points of improvement occurred in GW4: six from the starting XI and
one from captaincy. The model also selected Saka in the disappointing GW6; it
would not have avoided that captaincy outcome. The unpromoted calibration
candidate gives the same 422 points.

A separate simple control—holding the GW1 squad, starting XI, bench order and
Haaland captaincy unchanged, with legal autosubs—scores **420**. Its ownership
differs from the weekly conditional replay, so the two are not a paired
transfer-policy comparison. It does show that these seven results offer little
evidence that the extra decision-making improved on a basic hold strategy.

The replay uses the latest coherent core-build snapshot identified before each
deadline, at its publication time. Predictions use only the frozen pre-GW1
prior, past results and odds/status flags present in that snapshot. No later
news overrides are reconstructed. Some snapshots precede the deadline by several
hours. GW1's warning about unused new price-change columns is explicitly
acknowledged in the numerical replay; other integrity gates remain active.

Ownership is conditioned on the eventual locked squad, including any transfers
between snapshot capture and deadline. It is not an inferred pre-snapshot
transfer recommendation. Actual points apply recorded multipliers; model points
apply its recommended XI, bench, captain/vice and legal autosubs. No chips/hits
occurred in these seven actual weeks.

On a fixed pool of 1,205 player/gameweek observations with baseline projected
minutes of at least 45, v1 has points RMSE **3.187**, MAE **2.334**, and
60-minute Brier score **0.1485**. Lower is better. Mean forecast is 3.014 points
versus 3.315 realised, an underprediction of 0.301 per observation. The owned-15
sample has 105 observations, RMSE 2.972 and MAE 2.377. We do not pad the headline
accuracy with hundreds of zero-minute players.

These results cannot establish that the season was simply unlucky. The model's
recommended teams actually scored above their combined 397.9 projected points,
although an approximate, retrospectively designed mean forecast cannot measure
luck by itself. Seven noisy outcomes and missing historical advice records do
not support a probability-of-bad-luck estimate. They also cannot validate the
old transfer reasoning, a whole-season rank, or the six-week transfer horizon.

## Ten previous seasons found

The [FPL Historical Dataset](https://github.com/vaastav/Fantasy-Premier-League)
provides complete player/gameweek panels for **2016/17–2025/26**, frozen here at
commit `d7b05b51d137bfefef4b692386d18c610975b9b9`. That is every complete previous
season found in this archive, not every FPL season since the game began. We did
not find comparable complete panels before 2016/17.

| Seasons | Normalised player/fixture rows | Use |
|---|---:|---|
| 2016/17–2021/22 | 140,249 | Lagged points/minutes benchmark development; xG/starts unavailable |
| 2022/23 | 26,505 | Benchmark development; first archived xG/starts season |
| 2023/24 | 29,725 | Parameter selection, with previous-season xG/starts priors |
| 2024/25 | 27,283 | First held-out evaluation |
| 2025/26 | 29,747 | Second held-out evaluation; defensive contributions available |
| **Total** | **253,509** | **Ten seasons** |

Quality checks remove 59 duplicated/postponed player-fixture records in the
COVID restart season, ten duplicate rows in 2025/26, and 322 Assistant Manager
rows in 2024/25. Normalised fixtures are unique per player/fixture; player IDs
join within season and immutable player codes join previous-season priors.
Missing xG/starts/DC fields are identified rather than presented as observed
zero outcomes. Five optional old fixture/team metadata files are unavailable;
the older points/minutes benchmark does not require them.

The archive explicitly warns that its `xP` can leak post-match information.
`xP`, same-gameweek performance, end-of-season current-year totals, current-year
status/news and selection data are excluded from predictors. Previous-season
totals are legitimate prior inputs. Source manifests pin URLs and SHA-256
checksums, including the current-season outcome file.

## Method and selection

The [protocol](protocol.json) was saved before scoring the historical candidates.
The model itself was designed after the first seven current-season results had
been observed. This is a retrospective experiment, not pristine prospective
validation. The two historical holdouts were not used to select parameters.

1. Use 2016/17–2022/23 to choose a simple rolling-points benchmark from windows
   of three, six or ten prior fixtures. Each includes two matches of the
   previous-season mean; the first archive year uses fixed initial defaults.
   Ten fixtures wins across the 54,131 eligible development observations.
2. Compare 17 fixed model configurations on 2023/24: the original, eight
   minutes-window/prior-strength alternatives, and eight rate-half-life/prior-
   minutes alternatives. Only one parameter group changes at a time. Select
   lowest points RMSE on the common candidate pool.
3. Freeze that choice before evaluating 2024/25 and 2025/26. Promote only for at
   least 0.5% RMSE improvement in **each** held-out season. Do not choose a new
   winner from the holdouts or the current seven weeks.
4. Apply the frozen candidate to the same current-season snapshots as a final
   diagnostic, using the baseline's unchanged player pool.

Historical targets are single-fixture gameweeks, after at least one archived
row has already been observed for that player. A proxy cutoff is the earliest
GW kickoff minus 90 minutes. A predictor outcome must come from a lower GW and
have kickoff plus three hours before that cutoff. Double-gameweek history
retains its multiple fixture opportunities; DGW target observations are excluded.

Every variant uses the same lag-defined candidate pool: mean minutes in the
previous three fixtures >=45, or GW<=3 and previous-season minutes>=900. The
three modern evaluations contain 24,222 eligible observations, of which 16,346
are in the holdouts. Pools differ from the current snapshot test's fixed
baseline-minutes pool; compare models within each evaluation, not pooled raw
errors across scoring eras.

The older archive is not a vintage news/odds database. These are component
tests using realised fixture assignments and the model's team-results fallback,
not a reconstruction of every historical postponement forecast. They omit
historical availability flags and bookmaker odds. Keeper goal values and DC
availability follow the historical scoring era. Pre-2025/26 DC is disabled;
2025/26 has no previous-season DC prior. Historical bonus awards remain observed
outcomes; the current model's broad bonus approximation is not a replay of old
BPS rules. These differences limit transfer to the current live system.

## Results and calibration decision

Points RMSE, lower is better:

| Evaluation | Observations | Rolling benchmark | Live v1 | Selected candidate | Candidate improvement vs v1 |
|---|---:|---:|---:|---:|---:|
| 2023/24 — selection | 7,876 | 3.0943 | 3.0228 | 3.0049 | 0.59% |
| 2024/25 — held out | 8,213 | 2.9897 | 2.9206 | 2.9181 | 0.09% |
| 2025/26 — held out | 8,133 | 3.1440 | 3.0678 | 3.0346 | 1.08% |
| Current GW1–7 — fixed pool | 1,205 | — | 3.1865 | 3.1978 | **-0.35%** |

The selected candidate changes the minutes window from six observations to
three, and prior weight from two matches to half a match. Its 2024/25 gain is
below the promotion threshold; a GW-cluster bootstrap interval for that gain
is **-0.25% to +0.42%**. In 2025/26 the interval is +0.72% to +1.45%. These are
descriptive paired intervals, not adjusted for parameter search or season
dependence. Current-week improvement has an interval of -3.26% to +1.23%.

The candidate improves the historical appearance/60-minute probabilities, but
MAE worsens from 2.070 to 2.150 in 2024/25 and from 2.222 to 2.269 in 2025/26.
It also worsens current-season owned-player appearance calibration. That is a
tradeoff, not a generally superior calibrated model. **Do not promote it.**

The live model beats the simple rolling benchmark by 2.31% and 2.43% RMSE on
the held-out years. This supports keeping a structured xG/minutes/fixture
baseline, but does not show an edge against strong FPL projection systems or
managers. The test is not an exhaustive model search and cannot certify optimal
calibration. Small transfer gains should still be treated cautiously.

## What updates automatically

New ingested outcomes change rates and minutes estimates whenever Claude runs
the helper. The parameters, formulas and frozen previous-season prior **do not
retrain or retune automatically**. They stay fixed until a reviewed, versioned
change. There is no weekly calibration chore for the user, and no need to change
the existing Claude project routing instructions.

The useful next evidence is prospective: retain the pre-deadline decision packet,
news assumptions and benchmarks, then run `review` after results are complete.
Keep the model stable while collecting that evidence. Revisit minutes/role
uncertainty and calibration across a longer sample; do not tune each week to
the latest captain blank. Archived Claude recommendation records and vintage
odds/news would be needed to evaluate the complete advice/transfer policy.

## Inspect and reproduce

- [Current v1 weekly results](results/current-v1.json)
- [Current owned-player predictions](results/current-owned-predictions.csv)
- [Historical candidate selection](results/selection.json)
- [Held-out evaluation](results/historical-evaluation.json)
- [Calibration bins](results/calibration.csv)
- [Paired bootstrap intervals](results/paired-uncertainty.json)
- [Data quality profile](results/source-quality.json)
- [Executed analysis notebook](analysis.ipynb)
- [Pinned source manifests](sources/)

Raw panels and full player predictions are regenerated locally, rather than
duplicated in Git. Python 3.12, standard library only:

```bash
python backtests/acquire.py --output backtests/work
python backtests/history_data.py --root backtests/work/raw/archive --output backtests/work/normalised
python backtests/historical_predictive.py --panel backtests/work/normalised/normalised.csv \
  --archive backtests/work/raw/archive --output backtests/work/historical --phase select
python backtests/historical_predictive.py --panel backtests/work/normalised/normalised.csv \
  --archive backtests/work/raw/archive --output backtests/work/historical --phase evaluate
python backtests/replay_current.py --data-root backtests/work/raw/current-core \
  --refs backtests/sources/current-core-refs.json --actuals backtests/work/outcomes/player_history.csv \
  --squads backtests/fixtures --output backtests/work/current-v1
```

To reproduce the unpromoted candidate, copy `backtests/baseline-v1/model/` to
`backtests/work/research-model/`, replace its config with `selection.json`'s
`config` object and add `research_variant: "minutes_w3_p0.5; not promoted"`.
Run the same replay with `--model-dir backtests/work/research-model`,
`--pool-from backtests/work/current-v1/player_predictions.csv` and
`--output backtests/work/current-candidate`. Finally:

```bash
python backtests/assess.py --historical backtests/work/historical \
  --current backtests/work/current-v1 --candidate backtests/work/current-candidate \
  --output backtests/work/assessment
python tests/test_backtests.py
```

Replays stay outside `decisions/` and cannot be saved as prospective advice.
Unit/acceptance tests check code behaviour; the outcome comparisons above are
the separate predictive test.

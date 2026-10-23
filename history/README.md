# Historical FPL data and calibration research — 23 October 2026

**The first model was not proved nearly perfect.** The original test compared 17
fixed configurations; its failure to justify one specific parameter change did
not establish optimal calibration. This follow-up collects the official raw
history, uses all ten archived seasons for model fitting and tests a different,
data-trained minutes model.

There is measurable headroom in the minute probabilities. The improvement in
points forecasts is small and mixed, particularly for the user's actual owned
players. The live numerical advisor remains v1.0.0; research candidates and the
prior repair proposal are separate, reviewable files.

## What was actually collected

| Source | Successful coverage | Grain and limitation |
|---|---|---|
| Official FPL API | All 668 currently listed players; 2,110 past-season records spanning 2006/07–2025/26 | Previous-season aggregates for today's players; incomplete historical population |
| Official current history | 4,551 player/fixture rows through GW7 | All 9 audited outcome fields agree with the mirrored outcome file |
| Pinned FPL Historical Dataset | Ten complete season panels, 2016/17–2025/26; 253,509 normalised player/fixture rows | Archived outcomes; does not restore vintage news, odds or registration/schedule changes |

The [completed collection run](https://github.com/olly1928/fpl-mirror/actions/runs/39909712054)
committed 11 lossless raw packages, about 12.7MB compressed, under
[history/raw/39909712054](raw/39909712054/). Source response bytes, capture times,
URLs and SHA-256 checksums are retained. The [collection index](collections/latest.json)
records the packages and their checksums; the [coverage report](results/official-api-coverage.json)
shows every season and field actually retrieved.

The official `/element-summary/{player_id}/` response contains current-season
match history and `history_past` **season totals**. Those totals are useful for
checking priors but cannot be expanded into invented gameweek outcomes. Only
one currently listed player has a 2006/07 record; that is not a complete 2006/07
season panel. Retired/departed players are absent from today's bootstrap.

The tested `season=2025-26` query returned the current 2026/27 fixtures unchanged;
the bootstrap query likewise retained the current first deadline. This establishes
what those tested endpoints did, not a proof that no undocumented historical
endpoint could exist. [Raw probe evidence](results/api-capability-probes.json)
and the official responses are retained.

For complete match-level old seasons, the collector therefore uses Vaastav
Anand's [FPL Historical Dataset](https://github.com/vaastav/Fantasy-Premier-League),
pinned to `d7b05b51d137bfefef4b692386d18c610975b9b9`. We did not find comparable
complete player/gameweek panels before 2016/17. Older years lack xG/starts;
defensive contributions are available only for 2025/26 in these panels. The
archive's potentially post-match `xP` is excluded throughout.

## A real source-prior problem

Comparing the 600 frozen pre-GW1 priors with explicitly selected official
`history_past` records for 2025/26 gives 461 matched players and six conflicting
records. All audited replacement values agree with the previous-season archive:

| Player | ID | Frozen prior minutes | Official 2025/26 minutes |
|---|---:|---:|---:|
| Gomes | 54 | 2,207 | 2,829 |
| Anthony | 105 | 0 | 2,717 |
| Wilson | 108 | 0 | 1,234 |
| Disasi | 153 | 0 | 1,254 |
| Tchaouna | 190 | 0 | 1,259 |
| Florentino | 571 | 0 | 2,100 |

Starts and other rate inputs also differ; the [full audit](results/official-audit.json)
lists them. These players are absent from the seven owned squads, so the conflicts
do not explain the realised 415 points. They can affect forecasts for alternatives.
The audit found no positive-minute frozen prior without a 2025/26 official record.

This does not imply the old captured API response was corrupted. Bootstrap-carried
statistics and later explicit season-history records can differ. The robust fix
is to construct priors from a named prior season and retain their identity/source,
rather than treating a carried bootstrap total as automatically authoritative.

A [six-record repair proposal](proposals/prior-repair-proposal.json) and
[proposed 600-row prior](proposals/priors-2026-27-proposed.csv) are prepared.
They use only explicit 2025/26 records and preserve the other 594 frozen rows.
They are not installed in the live model and are not substituted into historical
replays: these responses were captured after GW7. The original frozen file and
old forecast inputs remain unchanged.

## What the broader test does

The [v2 protocol](protocol-v2.json) was recorded before scoring the new model
family. It distinguishes this research from the original 17-configuration sweep.

- Predict three disjoint minute states: no appearance, 1–59 minutes, and >=60.
  Learn their probabilities and conditional mean minutes from earlier seasons.
- Use only position, previous-season minutes, last observed minute state, recent
  appearance/60-minute frequencies and zero-minute streaks. No same-gameweek
  outcome, current-year final total, post-match `xP`, or hindsight news is a feature.
- For each test season 2017/18–2025/26, choose the window/pseudocount using the
  immediately previous season, with that selection model fitted only on still
  earlier seasons. Refit through the previous season before the target-year test.
  The first fold uses declared defaults because no inner validation year exists.
- Search windows 3/6/10 and hierarchical pseudocounts 10/50/200 using multiclass
  log loss on an unchanged, lag-defined active-candidate pool. All previously
  observed players are also evaluated separately.
- Plug the learned minute states into the unchanged v1 points formula for the
  three modern compatible test years. Compare a further fixed ridge calibration
  of points, fitted only on **earlier-year chronological predictions**.
- For the seven current weeks, fit through 2025/26 and reuse the exact original
  pre-deadline snapshots, original 1,205-player candidate pool and owned squads.
  No current result chooses a parameter. FPL availability flags are applied as
  captured; no historical Claude news overrides are invented.

There are 206,261 chronological minute-test observations across nine seasons,
including 70,492 active-pool observations. The final current-season minute model
fits 228,115 observations across all ten prior seasons. It selects a ten-fixture
window and pseudocount 50, rather than the original six-observation/two-prior-match
formula. Modern points tests use 24,222 active observations.

A predictor outcome must have a lower GW and kickoff plus three hours before
the proxy cutoff of first GW kickoff minus 90 minutes. Historical targets exclude
DGWs and first-observed player rows. Those exclusions avoid false per-fixture
60-minute claims and unsupported registration-vintage assumptions; they also
mean historical preseason builds/new-player first weeks are not fully validated.

The older outcomes and the current seven weeks have already been inspected.
Chronological fitting prevents feature/target leakage, but does not turn a
retrospectively designed experiment into a new pristine external holdout.
Prospective GW8-onward records remain important.

## Minute probabilities: useful gains with a visible failure

Lower log loss is better. The position-constant diagnostic was added after the
initial v2 results and never used for model selection.

| Test season | Active observations | Constant-position log loss | Rolling-six log loss | Learned log loss |
|---|---:|---:|---:|---:|
| 2017/18 | 7,970 | 0.7293 | 0.8016 | 1.0008 |
| 2018/19 | 7,959 | 0.7179 | 0.8189 | 0.6733 |
| 2019/20 | 8,308 | 0.7267 | 0.8445 | 0.6480 |
| 2020/21 | 7,436 | 0.7315 | 0.8466 | 0.6571 |
| 2021/22 | 7,096 | 0.7486 | 0.8507 | 0.6678 |
| 2022/23 | 7,501 | 0.7524 | 0.8138 | 0.6586 |
| 2023/24 | 7,876 | 0.7548 | 0.8799 | 0.6573 |
| 2024/25 | 8,213 | 0.7625 | 0.8272 | 0.6769 |
| 2025/26 | 8,133 | 0.7597 | 0.8800 | 0.6678 |

The learned model beats both simple references in eight of nine test seasons.
**It fails badly in 2017/18.** A likely cause is that the seed 2016/17 training data have no earlier
season's priors; the 2017/18 prior buckets are therefore largely unseen, forcing
an excessively broad position fallback. This cold-start weakness is recorded,
not omitted. A more robust fallback should be developed before production use.

On the current fixed candidate pool, v1 forecasts an average 73.3% chance of
reaching 60 minutes against 82.2% observed. The learned model raises the forecast
to 76.7%: closer, with substantial remaining underprediction. Its 60-minute
Brier score falls from 0.14847 to 0.13023, a 12.29% relative reduction. The seven-GW
cluster bootstrap interval for that reduction is approximately 0.12%–22.50%.
Appearance Brier improves 11.76%, but its interval includes no improvement.

This is evidence of headroom, not near-perfect calibration. A Brier improvement
combines probability calibration and discrimination; it is not a pure measure
of reliability. [Probability bins](results/current-probability-bins.csv),
[means and paired intervals](results/current-uncertainty.json), and
[all-season minute scores](results/minutes-walk-forward.json) are available.

## Points: much smaller, mixed improvements

RMSE is forecast error in points, not a percentage gain in FPL team points.
Lower is better:

| Evaluation | Observations | Live v1 RMSE | Learned minutes RMSE | Learned + points calibration RMSE | Learned minutes improvement |
|---|---:|---:|---:|---:|---:|
| 2023/24 | 7,876 | 3.0228 | 3.0011 | 3.0011 | 0.72% |
| 2024/25 | 8,213 | 2.9206 | 2.9087 | 2.9109 | 0.41% |
| 2025/26 | 8,133 | 3.0678 | 3.0234 | 3.0238 | 1.44% |
| Current GW1–7 | 1,205 | 3.1865 | 3.1794 | 3.1771 | 0.22% |

Historical learned-minutes RMSE improvements have descriptive GW-cluster 95%
intervals of 0.22%–1.26%, 0.05%–0.77% and 1.01%–1.91%, respectively. Current-week
improvement has an interval of **-0.86% to +1.25%**. These intervals do not adjust
for repeated research decisions or correlations across weeks/seasons.

On the actual 105 owned-player observations, learned-minutes points RMSE gets
slightly worse: 2.9845 versus 2.9723. Owned-player60-minute Brier improves, while
appearance Brier slightly worsens. All research variants still choose line-ups
scoring **422**, versus the actual 415 and the separate unchanged-GW1 control 420.
The six-week transfer policy is not tested by this conditional lineup comparison.

The points recalibration layer reduces mean underprediction from 0.301 to 0.145
points on the wider candidate pool, but increases MAE from 2.334 to 2.363. It adds
little RMSE benefit and no realised lineup-score gain. It should not be presented
as a generally superior calibration or automatically adopted.

The thin-prior cohort is also reported separately: 2,340 observations with less
than 450 prior minutes. Learned minutes reduces its points RMSE from 1.688 to 1.617
and 60-minute Brier from 0.09162 to 0.07036, while MAE worsens slightly. The cohort
includes many nonplayers; it is a diagnostic, not inflated headline accuracy.

An independent raw-score reference is possible for these seven weeks because
`ep_next` was captured **before** each deadline. On the same 1,205 observations,
its RMSE is 3.6825 versus 3.1865 for v1. That supports v1 over this simple official
score field, not optimality against strong projection systems. `ep_next` has a
much smaller aggregate mean bias; good average calibration alone does not mean
accurate player rankings. The [benchmark file](results/official-score-benchmark.json)
includes a separately labelled hybrid lineup diagnostic, not an official FPL
recommended strategy.

## What I recommend changing

1. **Repair prior construction by explicit season.** The six corroborated
   conflicts justify a source-data repair for a future version. Use the prepared
   proposal, preserve historical inputs, and review the next forecast for those
   players. This is a data-quality correction, not evidence of a betting edge.
2. **Develop the learned minutes model in parallel.** Its probability scores
   warrant further work, including the cold-start fallback and live news-override
   integration. It is a research replay, not a drop-in live model. Collect
   prospective comparisons before promoting it wholesale.
3. **Keep broader claims narrow.** These results do not quantify the probability
   that the season was unlucky, validate every captain choice, or establish a
   profitable full transfer/chip strategy. Old vintage odds/news and logged
   pre-deadline decisions remain missing inputs for that assessment.

The live advisor, parameters, frozen prior and production `data/` feeds are
unchanged by the research. The original three Claude project URLs still work.
New results update live forecasts when the helper runs; neither a raw-data
collection nor these experiments automatically retunes that live model.

## Reproduce and inspect

The [collection workflow](../.github/workflows/historical-data.yml) runs only
when the collection request changes or when manually invoked; it has no cron.
The [research workflow](../.github/workflows/historical-research.yml) is manual
and writes research artifacts, not live parameters.

From the repository root, with Python 3.12 and the standard library:

```bash
bash history/run_research.sh
python tests/test_history.py
```

The script verifies/unpacks committed raw packages, normalises all seasons,
fetches checksum-pinned current replay inputs, runs the nested research and
produces the audits/diagnostics/proposals under ignored `history/work/` paths.
No pip install, FPL login or new secret is required.

The full numerical results were reproduced independently from the committed
raw GitHub packages. All 4,551 current outcome rows agree across nine audited
fields. See [reproduction verification](results/reproduction-verification.json).

- [Executed companion notebook](research.ipynb)
- [Nested selection and training years](results/nested-selection.json)
- [Modern points scores and paired intervals](results/points-walk-forward.json)
- [Current candidate/owned/thin-prior scores](results/current-cohorts.json)
- [Current lineup results](results/current-research-summary.json)
- [Point recalibration coefficients](results/point-calibration.json)
- [Compiled research minute model](results/minute-model-2026-27.json)
- [Original narrow experiment](../backtests/README.md)

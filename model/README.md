# FPL advisor model v1.0.0

The three project entry points remain `prompts/weekly.md`, `prompts/preseason.md`
and `prompts/smoke-test.md` on `main`. Fetch them with bash + curl. Weekly is the
default; preseason is for a full build/wildcard; the smoke test gives feed health
only. Confirm the loaded playbook/version in the first response line.

This helper adds a consistent baseline, legal team/transfer comparisons and
prospective records. It does not access an FPL account, place transfers, upload
files, fetch the network or change any ingestion job. Python 3.12 standard library
only; no pip install, account connection or new secret is required.

## What the numbers mean

These are **baseline expected-points estimates with limited retrospective
validation**, not a demonstrated edge over other managers or projection providers.
Parameters are fixed in `config.json`. Do not retune them each conversation.
Review prospective results before a versioned change. An optimiser cannot make
poor forecasts accurate.

The [historical backtest](../backtests/README.md) covers the first seven 2026/27
weeks and ten previous seasons, with modern component selection/holdouts where
xG/starts are available. The proposed shorter minutes window failed its
predeclared promotion rule; live v1.0.0 parameters remain unchanged. The current
seven-week result is conditional on actual owned squads, not a transfer-policy
backtest. Do not claim the system is fully calibrated or optimal.

New results update forecasts when this helper runs. Parameters and frozen priors
do not automatically retrain. There is no weekly user calibration task; retain
prospective decision packets so a later versioned change can be assessed.

### Rates and priors

`priors-2026-27.csv` freezes 600 players' previous-season statistics from the
last pre-GW1 snapshot at 2026-08-21 17:21:39 UTC, before the 17:30 deadline:

https://github.com/olly1928/fpl-mirror/blob/62f698b3fc0e8af00998d0cd998bb2ea91b61bd8/data/players.csv

The snapshot's **new-season** club labels do not establish where the player
generated the old statistics. Summer transfers, promoted players and changed
positions/roles need news-based judgement. The file is never refreshed from
current-season outcomes. A season mismatch fails rather than reusing IDs next year.

For each per-90 component, blend current history with at most 900 prior minutes
and 180 minutes of a broad position prior. Current observations decay with an
eight-gameweek half-life. This shrinks small samples instead of chasing recent
goals/points. Goal rates use xG and assist rates use xA separately. Historical
finishing overperformance is not extrapolated as a new skill estimate.

FPL assists and xA have different definitions. A frozen position-wide rate of
FPL assists above xA supplies a simple extra-assist allowance; it is not fitted
to an individual player's recent assist haul. Bonus uses current actual bonus
shrunk toward a position baseline, not the old individual's BPS tally. The
2026/27 BPS change makes that distinction relevant. These remain approximations.

No `form`, `ep_this`, `ep_next`, `value_form`, `value_season`, ownership or future
outcomes enter the model. FPL projections can be an independent **benchmark** only
when their target gameweek and capture time are known, never a hidden input.

### Minutes

The last six history observations estimate starts per fixture opportunity,
shrunk with two matches of the old starting frequency. `starts_per_90` is not
used. A DGW contributes multiple fixture opportunities; its aggregate minutes
cannot identify individual substitutions, so it does not train the conditional
minute distributions or defensive-contribution threshold frequency.

The minute states are: starts and reaches 60, starts but leaves earlier, and
appears as a substitute. The remaining probability means no appearance. A cameo
can itself last 60 minutes. The FPL availability percentage scales the default
start/cameo probabilities; injured/suspended/unavailable status defaults to no
appearance. Sourced overrides replace those estimates.

Current fitness uncertainty persists into future weeks unless explicitly
overridden. This is deliberately conservative, **not a recovery forecast**.
Do not compare a sale against six weeks at 50% availability just because a flag
appears today. Verify the outlook and run alternative recovery scenarios.

### Fixture environment and scoring

Match exact teams and kickoff date. Use a matching odds row only when its own
timestamp is within 12 hours of the forecast clock. Otherwise label the estimate
`shrunk_team_results`: team goals scored/conceded are shrunk with eight matches
at a 1.45-goal neutral prior, with explicit home/away factors. This fallback is
not a bookmaker forecast, and is especially uncertain early in the season.
Player attacking rates are scaled by fixture team goal expectation divided by
that team's shrunk scoring baseline, bounded to avoid extreme extrapolation.

The odds mirror fits independent Poisson rates from bookmaker markets. That
approximation, sparse totals coverage and goal dependence are limitations; it
does not provide player-level anytime-scorer odds. Flag `n_books_totals <= 2`.

Per fixture, project:

- Appearance points from the disjoint minute states and the 60-minute threshold.
- Goals at 10/6/5/4 points for goalkeeper/defender/midfielder/forward; assists at 3.
- Clean-sheet points only in >=60-minute states, using opponent goal intensity
  over that state's exposure. This approximates protection after early substitution.
- Defensive contributions at **two points once** for reaching 10 (defender) or
  12 (midfielder/forward) in a match. Shrink observed single-fixture threshold-hit
  frequency with a Poisson-count prior; use count-tail probabilities for short
  appearances. Never divide a season's raw action total by the threshold and
  multiply by two. Role overrides invalidate the old hit-frequency evidence.
- Empirical shrunk bonus, cards, own goals, missed/saved penalties; goalkeeper
  saves and conceded-goal deductions using expected complete blocks of 3 or 2,
  rather than dividing a mean count and rounding it.

The scoring constants were checked against the official 2026/27 guidance:

https://www.premierleague.com/en/news/2174909/
https://www.premierleague.com/en/news/4679946/whats-new-in-202627-fantasy-changes-to-bonus-points-system

This is not a full scoreline/BPS simulation. Player/team appearances and double-
gameweek availability are treated independently. Team-news correlations, red-
card event interactions, save intensity by opponent and detailed penalty models
are not represented. Do not describe model differences as statistically
significant without further prospective evidence.

## Assumptions file

Start with `assumptions.example.json`. It intentionally contains no invented news.

- `free_transfers`: confirmed integer 0-5, or null to use the labelled feed estimate.
- `free_transfers_confirmed`: true only after actual confirmation.
- `sources`: dated news/source notes retained in the decision record.
- `players`: string player ID -> `all` and/or string gameweek -> override object.
  A gameweek-specific value wins over `all`.
- `plans`: named alternatives, always including `roll`. Each `moves` entry has
  integer `out`, `in`, and optional `gw` (defaults to the next gameweek).
- `notes`: reasons for a horizon, risk objective or scenario choice.

Supported player override keys:

| Key | Meaning |
|---|---|
| `p_start` | Unconditional probability of starting, 0-1 |
| `p_cameo` | Unconditional probability of a substitute appearance, 0-1 |
| `p60_if_start` | Probability of reaching 60 conditional on starting |
| `minutes_60` | Mean minutes in the >=60 starting state, 60-90 |
| `minutes_short_start` | Mean minutes in a short start, 1-59 |
| `minutes_cameo` | Mean minutes when appearing off the bench, 1-90 |
| `goal_rate_multiplier` | Sourced role adjustment to the goal rate, 0-3 |
| `assist_rate_multiplier` | Sourced role adjustment to the xA rate, 0-3 |
| `dc_rate_multiplier` | Sourced role adjustment to raw defensive-action rate, 0-3 |
| `source`, `reason` | Required whenever any override is used |

`p_start + p_cameo` must be <=1. Overriding `p_start` alone does not reset a
default cameo probability; specify both when changing availability. Set both to
zero for a confirmed absence. Put actual dated source URLs in the real file,
not made-up claims. There is no generic instruction to add 20% for a hunch.

Plans may move players of different positions provided the final 15 have the
correct quotas. Transfers in each week are simultaneous for budget purposes.
Price rises use the half-profit sale rule, falls in full. Later-week prices are
held at today's prices, explicitly as an assumption. Recheck them before acting.
The comparison refuses already-made upcoming-GW transfers until the locked
picks/bank have been reconciled. Preserve the original, save a separate
`data/squad-reconciled.json` with current owned IDs, bank and purchase/sale prices,
and add `reconciliation: {"event": 8, "source": "actual confirmation/source", "notes":
"what changed and when"}` using the actual target GW. The free-transfer count
must also be confirmed. Pass that file to `compare --squad`. The comparison embeds
the financial/squad state and final assumptions actually used. Do not add that
marker without doing the reconciliation.

## Commands

Use the fetch commands in the selected playbook to download this helper, config,
prior and data. Prefer a pinned repository SHA for a coherent, recoverable input
packet. The existing project instruction URLs stay on `main`.

```bash
python fpl_advisor.py forecast --data-dir data --model-dir model \
  --assumptions assumptions.json --horizon 6 --source-ref "$REF" --output forecast.json
python fpl_advisor.py compare --forecast forecast.json --squad data/squad.json \
  --assumptions assumptions.json --output comparison.json
```

Six gameweeks is the shared comparison horizon, not six independently chosen
holding periods. A planned wildcard can justify a shorter **common** horizon.
Future scheduled transfers consume the appropriate free transfers and hits.
Remaining transfer flexibility is reported separately; the script assigns it
no invented fixed points value. Chips are not included in transfer-plan gains.

The lineup helper enumerates legal XIs on starter/captain points and checks
expected autosubs for the leading eight. It orders the bench against non-
appearance scenarios and nominal formation rules. A bench cameo qualifies.
Captain/vice expectation includes the vice when the captain has no appearance.
This bounded search and the independence assumptions do not guarantee a global
optimum. Compare close formations and don't claim otherwise.

For a preseason build or wildcard:

```bash
python fpl_advisor.py build --forecast forecast.json --budget-tenths 1000 \
  --beam-width 400 --output build.json
python fpl_advisor.py build --forecast forecast.json --budget-tenths 1000 \
  --beam-width 800 --force 411 --output build-force.json
python fpl_advisor.py build --forecast forecast.json --budget-tenths 1000 \
  --beam-width 800 --exclude 411 --output build-without.json
```

1000 means £100.0m, **not** the wildcard budget: use verified sale proceeds plus
bank for a wildcard. Forced/excluded IDs are scenario controls, not recommended
players. The candidate pool combines high projected points, value and cheap
players; beam search prunes partial squads, then rescores finalists with legal
weekly XIs. It is a bounded heuristic. All returned squads are checked for
budget, club cap, unique players and 2/5/5/3. Repeat at wider width, compare
premium-in/out builds and plausible minutes/recovery scenarios. Inspect the
published candidate pool for a missing role-supported alternative.

The model has no source for next season's priors: refresh the frozen prior and
configuration at the next season boundary. It intentionally fails on a mismatch.

## Save an honest, prospective decision

```bash
python fpl_advisor.py record --forecast forecast.json --comparison comparison.json \
  --plan roll --output decisions/GW08-first.json
# For a full build instead:
python fpl_advisor.py record --forecast forecast.json --build-result build.json \
  --plan build-1 --output decisions/GW01-first.json
```

Use the actual GW and chosen plan/index, with a new filename for revisions.
Writes are exclusive: no overwriting an old forecast after seeing the results.
The record embeds all projections and alternatives and identifies config,
assumptions and inputs by hashes. It says **recommended, not confirmed executed**.
Recording does not send transfers to FPL or upload anything to GitHub.

Attach a packet containing the record and original inputs, not just a prose
summary. For example, after creating the actual `decision.json` file:

```bash
python -m zipfile -c fpl-decision-packet.zip decision.json forecast.json \
  assumptions.json data model fpl_advisor.py
```

Use the real record filename. Attach `comparison.json` or `build.json` as well if
useful; their results are already embedded in the record. Keep these files in
the project/downloads or commit a new JSON record when an authorised repository
write workflow is available. A chat attachment is not automatically a GitHub
commit. Advice must remain usable without a token or upload connection. If the
previous record is unavailable, say a forecast review cannot yet be done.

No production record is fabricated by installing this model. `--as-of` creates
an explicitly labelled historical replay and cannot be saved as a prospective
decision. A decision created after its target deadline is also refused.

## Review without hindsight leakage

```bash
python fpl_advisor.py review --record decisions/GW08-first.json \
  --history data/player_history.csv --output review.json
```

Wait until that GW is completed and captured. The review scores the recommended
team (which might not be what was executed), applying actual appearance,
autosub/formation and captain/vice rules. It reports errors and appearance
Brier scores for the owned/alternative candidate pool, rather than padding
accuracy with hundreds of non-playing players. A DGW's aggregate minutes do not
support a per-fixture 60-minute calibration claim.

Maintain simple, dated benchmarks: highest projected captain, Haaland captain
when owned, and an agreed untouched/roll strategy. Distinguish realised scores
from the forecast that justified a choice. Across multiple prospective records,
inspect bias, minutes calibration, rank ordering and whether gains versus
holding materialise. Goals minus xG, poor captain outcomes or a low weekly score
alone do not quantify luck. Do not optimise parameters on these seven historical
weeks and call that out-of-sample validation.

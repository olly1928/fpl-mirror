# fpl-mirror

A public, unauthenticated mirror of the Fantasy Premier League API, refreshed by
GitHub Actions and committed straight into this repo. The point is reachability:
the FPL API is not resolvable from a Claude sandbox, `raw.githubusercontent.com`
is.

Everything is fetched from public endpoints. No key, no login, no session
cookie, no secret in the tree — the one exception is `ODDS_API_KEY`, which lives
in repository secrets and is never written to disk.

## Fetching

```bash
curl -s https://raw.githubusercontent.com/olly1928/fpl-mirror/main/data/meta.json
```

**Read `meta.json` first, every time.** It carries `fetched_at`, a `stale` flag,
a `warnings[]` array and a per-component freshness breakdown. If `stale` is true,
some part of this mirror has stopped refreshing and the file you are about to
read may be describing last week.

### Beware the CDN cache

`raw.githubusercontent.com` caches **per path**, so one file can be minutes or
hours behind another even though both were written by the same run. A cached
`meta.json` is the dangerous one: it defeats the integrity gate just as
completely as a broken build would, and it looks entirely plausible while doing
it.

Every CSV carries `fetched=<timestamp>` in its first comment line, and within a
single build that is always **identical** to `meta.json`'s `fetched_at`. So the
check is free:

```bash
BASE=https://raw.githubusercontent.com/olly1928/fpl-mirror/main/data
curl -s "$BASE/meta.json?v=$(date +%s)"   | head -2      # "fetched_at": ...
curl -s "$BASE/players.csv?v=$(date +%s)" | head -1      # # season=... fetched=...
```

If those two timestamps disagree, you are holding a cached copy, not a stale
mirror — refetch with the cache-busting query string. If they agree and the
timestamp is old, the mirror really has stopped.

## Files

| File | Contents | Refresh |
|---|---|---|
| `data/meta.json` | Season state, gameweek list, scoring rules, chips, squad limits, freshness and warnings | hourly |
| `data/players.csv` | Every player, season-to-date. Prices, ownership, underlying numbers, set pieces, availability | hourly |
| `data/teams.csv` | FPL's team strength ratings, plus a league table this mirror computes from results | hourly |
| `data/fixtures.csv` | Full season fixture list, with results once played | hourly |
| `data/fdr.csv` | Per-team fixture difficulty over the next six gameweeks | hourly |
| `data/fixture_stats.csv` | Per-fixture, per-player stat lines. Empty until games are played | hourly |
| `data/squad.json` | My squad, transfers, chip usage, past seasons, mini-league standings | hourly |
| `data/snapshots/YYYY-MM.csv` | Price / ownership / transfer / news history | hourly |
| `data/player_history.csv` | Per-player, per-gameweek stats | once per gameweek |
| `data/odds.csv` | Bookmaker consensus, de-vigged, with clean-sheet probabilities | every 6 hours |
| `data/build_status.json` | When each builder last ran and what it complained about | every run |

`meta.json` carries two separate lists and they mean different things. `warnings[]`
is *something changed, go and look*. `known_empty[]` is *we looked, it is upstream,
it is not changing* — read it before calling any column broken.

CSVs carry a leading `#` comment block with the season header and a `fetched=`
timestamp. Drop lines starting with `#`, then parse normally:

```python
body = [ln for ln in text.split("\n") if ln and not ln.startswith("#")]
rows = list(csv.DictReader(io.StringIO("\n".join(body))))
```

### `players.csv`

The first fourteen columns — `id,name,team,pos,price,own,pts,ppg,mins,g,a,cs,bonus,st`
— are a fixed contract and will not move, be renamed, or be reordered. New
columns are only ever appended to the right-hand end, so read by name.

Beyond those: expected goals and assists (plus per-90s), **`starts` and
`starts_per_90`**, `chance_of_playing_this_round` / `_next_round`, defensive
contributions, set-piece order and notes, discipline, ICT, and FPL's own
projections.

`defensive_contribution` is an aggregate and cannot be decomposed after the
fact, so its raw components are mirrored alongside it:
`clearances_blocks_interceptions`, `tackles` and `recoveries`. Defenders and
midfielders cross the DEFCON threshold on different combinations of these, and
`recoveries` counts towards it for midfielders and forwards only — so modelling
the probability of a player clearing the threshold needs the parts, not the
total.

`starts` is the one to reach for first. `minutes` alone cannot separate a player
who started twenty games from one who came off the bench in thirty-eight, and
minutes assumptions move squad selection roughly twice as hard as the entire
bookmaker layer does.

Every cumulative column is **season-to-date**. For per-gameweek splits use
`player_history.csv`.

### FPL's price-change projections

`price_change_proj_pct_d0`, `_d1` and `_d2` are FPL's own projection of a player's
next price change, 0/1/2 days ahead. The number is **signed progress towards that
change**: positive is towards a rise, negative towards a fall, and crossing ±100 is
the change itself. `price_change_proj_likelihood_d0/1/2` is FPL's 1–5 band over the
same figure (±5 meaning projected to cross, 0 meaning nothing projected).

Three columns qualify them. `price_change_hourly_rate` is signed net transfers per
hour. `price_change_locked_until`, when set, is a timestamp before which the price
cannot move whatever the projection says. `price_change_calibrating` is true while
FPL's model has not settled on a player — usually a recent addition — so the
projection is soft.

`price_change_projections` beside them is the verbatim packed mirror of the same
block, kept so that FPL adding a fourth day or a new key needs no code change. The
exploded columns are the ones to do arithmetic on.

This block was found by the shape guard rather than guessed at: the field was
mirrored before anyone had seen it, arrived as a list of objects, was flattened
safely, and reported its own type and a sample in `warnings[]` — which is what
made the proper columns possible on the next pass.

> `form`, `ep_this`, `ep_next`, `value_form` and `value_season` are FPL's own
> projections. They are mirrored for reference and comparison. Do not feed them
> to a model as inputs.

### `teams.csv`

Two halves, and the distinction matters.

The unprefixed columns are FPL's, mirrored verbatim. Ten of them carry nothing
and never have: `strength` comes through empty, the attack/defence breakdowns
come through as zeros, and `played`/`win`/`draw`/`loss`/`points` sit at zero
**all season** — not just pre-season — while `position` right beside them updates
every week. `strength_overall_home` and `strength_overall_away` are the two
ratings with values in them.

**That is upstream behaviour, not a fault, and it is not going to change.** It is
recorded in `meta.json` `known_empty[]` rather than `warnings[]`, precisely so
that `warnings[]` stays a list of things that are *new*. Don't report those ten
columns as a data-quality problem. If FPL ever starts populating one, *that*
appears in `warnings[]` — which is the signal worth acting on.

A league table with a real ordering and nothing to justify it is worse than no
table at all, which is why the computed half exists.

So the `derived_*` columns are computed here from the finished fixtures in
`fixtures.csv`: `derived_played`, `derived_win`, `derived_draw`, `derived_loss`,
`derived_gf`, `derived_ga`, `derived_gd`, `derived_points`, `derived_position`
and `derived_form` (last five results, most recent first). **Use these for the
table.** They are computed, not mirrored, and the prefix is there so the two can
never be confused.

`derived_position` is ranked on points, goal difference, goals for, then club
name, and is cross-checked against FPL's own `position` on every run. Agreement
is silent; any disagreement is raised in `warnings[]` naming the clubs, because
it means either a result is missing or FPL is ranking on something this does not
model.

### `snapshots/YYYY-MM.csv`

The only file here that cannot be rebuilt. Everything else — cumulative stats,
gameweek splits, fixtures, results — can be re-fetched from the API at any later
date. Price, ownership, transfer counts and injury news are overwritten in place,
so once they change the previous value exists nowhere.

Rolled monthly. Written in **delta mode** by default: a full baseline row for
every player on the first snapshot of each UTC day, then rows only for players
whose values actually moved.

```
snapshot_at,player_id,now_cost,...   # to read a player at time T:
                                     # take their most recent row at or before T
```

That is lossless — forward-filling reconstructs the full one-row-per-player-per-
snapshot panel — and it keeps a month inside a couple of megabytes instead of
twenty-six, which matters when the consumer is fetching over `curl`. The daily
baseline means you never have to read the previous month to resolve a player.
`python build_snapshots.py --mode full` writes the dense panel instead.

`now_cost` and the `cost_change_*` columns are in tenths of a million, as the API
reports them. (`players.csv` `price` is in millions.)

### `fixture_stats.csv`

```
fixture_id, gw, player_id, team_side, identifier, value
```

Flattened from `fixtures[].stats` — one row per fixture per player per stat.
`team_side` is `h` or `a`.

This overlaps `player_history.csv` but is not redundant. `event/{id}/live/`
aggregates a player across a whole gameweek, so in a double gameweek it cannot
say which of the two fixtures a goal came in. This can.

Unlike the append-only files it is rebuilt in full on every run, because it is
re-fetchable at any time and FPL amends it: bonus points are provisional until
`data_checked`, and stat corrections land days later. A rewrite picks those up;
an append would freeze the first, wrong version.

### `player_history.csv`

One row per player per completed gameweek, appended as the season runs. This is
what makes form analysis possible from roughly GW4 — a season-to-date total
cannot distinguish a player who scored six in August from one who scored six in
December.

Built from `/api/event/{id}/live/`, one call per gameweek rather than ~600
per-player calls. A gameweek is captured once FPL sets `data_checked: true`,
which is its own signal that bonus points are final.

In a double gameweek, `fixture_id`, `opponent_team` and `was_home` carry both
entries joined by `|`, and every other column is the gameweek total across them.
`opponent_team` is the FPL short code.

Fully backfillable — the live endpoint serves historical gameweeks indefinitely:

```bash
python build_player_history.py --backfill-from 1
```

Re-running never duplicates: gameweeks already in the file are skipped.

### `squad.json`

Picks, captain, bank, value, chip usage, gameweek history, full transfer history,
past seasons, standings for each mini-league, and **selling prices**. Read
`notes[]` before acting on any of it — in particular:

- **Free transfers are an estimate.** The API does not publish them.

#### Selling prices

`selling_prices` is derived, not fetched. The endpoint that publishes it needs an
authenticated session; the numbers themselves are a pure function of purchase
price and current price, and both are already in the feed. One entry per pick,
keyed by player id, all values in **tenths of a million** — the API's own units,
so `155` is £15.5m:

```json
"selling_prices_confidence": "derived",
"selling_prices": {
  "351": {"now_cost": 155, "purchase": 150, "selling": 152,
          "source": "transfer", "bought_event": 4, "suspect": false}
}
```

`source` says where the purchase price came from, and the two are not equally
certain:

- `transfer` — `element_in_cost` on the most recent non-Free-Hit transfer that
  brought the player in. Literally what was paid.
- `initial_squad` — no transfer record, so the player has been held since GW1 and
  the purchase price is `now_cost - cost_change_start`. Pre-season prices are
  static, so that is the GW1 deadline price. Carries the assumption that the
  player was never sold and re-bought.

Selling price is then the full fall, or the purchase price plus **half of any
rise, rounded down** — 7.0 → 7.3 sells for 7.1. Free Hit transfers are excluded
(the squad reverts, but the transfers stay in the history looking permanent);
wildcard transfers count, because they are real.

Alongside them, also in tenths: `squad_selling_value` (sum of `selling`),
`squad_market_value` (sum of `now_cost`, for comparison) and `available_budget`
(`squad_selling_value` + bank). Note that `bank` and `squad_value` at the top of
the file remain in **millions**, as they always have been.

##### When the numbers are doubtful

The transfer feed is cross-checked against `history.current[].event_transfers`,
which counts the same transfers without reference to the transfer list. If the
feed has **fewer** records than that count, purchase records are missing, and a
player really bought in GW7 is indistinguishable from one held since GW1.

The prices are still written — withholding them sends you back to `now_cost`,
which is not neutral but systematically overstates proceeds on every risen
player — but the doubt travels with them:

- `selling_prices_confidence` goes from `"derived"` to `"suspect"`;
- every price resting on the *absence* of a record gets `"suspect": true`, while
  prices resting on a record that is present stay `false` and are as good as on
  any other run;
- `notes[]` gains a line saying what contradicted and by how much;
- `meta.json warnings[]` carries it too.

`suspect` is always present, never absent-means-fine, so a consumer reading one
price in isolation can tell "not suspect" from "written before this field
existed".

`selling_prices` is `null` — with the reason in `notes[]`, and
`selling_prices_confidence` null with it — in two cases only: pre-season, when
there is no squad to price; and when the transfer feed **cannot be read at all**,
where there is no purchase-price source to work from and the whole squad would
silently fall back to GW1 prices. That is a different situation from two readable
sources disagreeing.

## Reliability

This is designed to run unattended for nine months, where the realistic failure
is not a crash but a job that keeps succeeding while producing garbage, or one
that quietly stops running.

**Every write is verified.** After writing, `build_fpl.py` reads each file back
off disk and checks two independent things: that the content carries this run's
timestamp, and that the file's modification time is from this run. Either alone
has a blind spot — the content check cannot see a write that never happened when
the previous run fell inside the same clock second. A separate workflow step then
confirms `meta.json` reached the *commit* with that same timestamp and all its
keys intact, which catches a stray `.gitignore` rule or an unstaged path. A
frozen `meta.json` would defeat the entire staleness design from the inside, so
it fails the run rather than being published.

**Nothing is written partially.** Every file is built into a temp path, validated,
then renamed into place. A malformed API response exits non-zero and leaves the
whole previous set untouched — a stale but well-formed file is far more useful
than a fresh corrupt one, because staleness is detectable and corruption is not.

**Nothing historical is rewritten.** `snapshots` and `player_history` only ever
gain rows. Existing rows are carried through byte for byte; only the comment
header is refreshed, so its `fetched=` stays honest.

**Schema drift is loud, but not noisy.** The expected field list for each
endpoint is an explicit constant in the code. If FPL renames or drops a field, the
column is still written — empty — *and* the discrepancy lands in `meta.json`
`warnings[]`. A silently null column is far worse than a loud one.

That check looks at whether a field is *present*, which is only half the problem.
A field FPL keeps sending but stops populating passes it silently and lands in
the CSV as a column of blanks or zeros indistinguishable from a real result —
which is exactly what happened to `teams.csv`, and why it went out for weeks
looking complete and reporting no warnings at all. So there is a second,
value-level guard: for a curated set of fields where "identical across every
record" is genuinely diagnostic, an all-empty or all-zero column is reported too,
with the two cases named separately because they mean different things. It is
pointed at a short list on purpose — plenty of fields are legitimately zero in
August, and a guard that cries wolf every hour is one nobody reads by September.

**A known-dead column is acknowledged, not warned about.** Same idea as the
`IGNORED_*` lists, applied to values instead of keys. FPL is never going to
populate the team strength breakdown or the league-table counters, and reporting
them hourly turns a permanent upstream fact into weekly news — a reader told to
"read `warnings[]` in full" ends up filing the same bug every gameweek. Those
fields live in `KNOWN_EMPTY_TEAM_FIELDS` and are published in `meta.json`
`known_empty[]`, with a note saying what to use instead. The acknowledgement is
not a silencer: if one of them starts carrying values, that *is* reported, because
it means the list is stale and a column just became usable.

Fields FPL has
that this mirror deliberately does not carry (derived ranks, photo URLs, internal
flags) sit in a matching `IGNORED_*` constant, so the guard stays quiet about the
forty already reviewed and still fires the moment something genuinely new
appears. A guard that reports the same forty fields every hour is a guard nobody
reads.

**Staleness surfaces in one place.** Each builder records its last run in
`data/build_status.json`. `build_fpl.py` runs last and folds the lot into
`meta.json`: `stale` is true if any component has gone longer than twice its
expected interval without running, with the culprit named in `warnings[]`. A
partial failure therefore shows up in exactly the same place a total one does.
The odds job runs on its own schedule rather than inside the hourly one, so it
rewrites that freshness block itself when it finishes — otherwise a freshly
pulled `odds.csv` would sit next to a `meta.json` still quoting the previous
pull, and a reader doing the documented freshness check would get the wrong
answer.

**Odds age is checked against the deadline, not just the clock.** The generic
stale flag fires at twice a component's interval, which is the wrong instrument
for odds: they move on team news, and a line built before a Friday press
conference is not one to transfer on. So `build_fpl` also warns whenever
`odds.csv` is more than 12 hours old with a deadline inside 72 hours, naming both
numbers. The odds job runs every six hours, which puts the generic stale flag at
12 hours too.

**Failures notify.** Repo-owner email on failed runs is on by default, but easy
to miss. Both mirrors also open (or comment on) an issue labelled
`mirror-failure`, which is visible in the repo and notifies watchers. Worth
confirming once that this reaches an address you actually read.

**Rate limits are respected.** Exponential backoff on 429 and 5xx, no retry on a
4xx that will never succeed, a hard per-process request ceiling, and a ten-minute
on-disk cache so the four builders in one job fetch `bootstrap-static` once
between them.

**A snapshot that writes nothing fails.** A baseline run that produces no rows,
or a bootstrap response with implausibly few players, exits non-zero rather than
appending a thin file to the one thing that cannot be reconstructed.

**The runtime is pinned.** Python 3.12.7 exactly; no third-party dependencies at
all, enforced on every push by the tests workflow.

**Every scheduled job has a `workflow_dispatch` trigger.** GitHub disables
cron-triggered Actions after 60 days without repository activity. `build_status.json`
changes on every run, so the mirror commits at least once an hour even on a
completely quiet day, which should keep that from ever firing. If a job does go
quiet anyway, check the Actions tab — a disabled workflow shows a banner with a re-enable button, and any
job can be kicked by hand from there.

## Running locally

```bash
python build_snapshots.py        # price/ownership/news history  (run first)
python build_player_history.py   # per-gameweek panel
python build_squad.py            # my squad                      (FPL_TEAM_ID)
python build_fpl.py              # players/teams/fixtures/fdr/fixture_stats/meta
ODDS_API_KEY=... python build_odds.py
```

Order matters only in that `build_fpl.py` should run last, since it aggregates
the others' status into `meta.json`.

## Tests

```bash
python tests/test_offline.py
```

Runs the spec's acceptance checks against synthetic API payloads — no network, no
secrets. Faking the transport rather than hitting the live API is what makes the
two most important cases testable at all: a malformed response, and a field FPL
has renamed away.

## What this deliberately does not solve

**Predicted line-ups.** The FPL API has no view on who will start next weekend.
`starts_per_90` and `chance_of_playing_next_round` are the closest available
proxies and they are not close. This stays a manual input from Fantasy Football
Scout or similar, and it is the largest single source of error in anything built
on top of this mirror. The expanded data here has not closed that gap.

**Player-level betting odds.** Out of scope by decision. Anytime-goalscorer
prices are largely derivable from team xG (already in `odds.csv`) times a
player's share of chances (from `expected_goals` once the season starts).

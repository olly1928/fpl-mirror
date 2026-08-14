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

## Files

| File | Contents | Refresh |
|---|---|---|
| `data/meta.json` | Season state, gameweek list, scoring rules, chips, squad limits, freshness and warnings | hourly |
| `data/players.csv` | Every player, season-to-date. Prices, ownership, underlying numbers, set pieces, availability | hourly |
| `data/teams.csv` | FPL's own team strength ratings and league table | hourly |
| `data/fixtures.csv` | Full season fixture list, with results once played | hourly |
| `data/fdr.csv` | Per-team fixture difficulty over the next six gameweeks | hourly |
| `data/squad.json` | My squad, transfers, chip usage, past seasons, mini-league standings | hourly |
| `data/snapshots/YYYY-MM.csv` | Price / ownership / transfer / news history | hourly |
| `data/player_history.csv` | Per-player, per-gameweek stats | once per gameweek |
| `data/odds.csv` | Bookmaker consensus, de-vigged, with clean-sheet probabilities | twice daily |
| `data/build_status.json` | When each builder last ran and what it complained about | every run |

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

`starts` is the one to reach for first. `minutes` alone cannot separate a player
who started twenty games from one who came off the bench in thirty-eight, and
minutes assumptions move squad selection roughly twice as hard as the entire
bookmaker layer does.

Every cumulative column is **season-to-date**. For per-gameweek splits use
`player_history.csv`.

> `form`, `ep_this`, `ep_next`, `value_form` and `value_season` are FPL's own
> projections. They are mirrored for reference and comparison. Do not feed them
> to a model as inputs.

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
past seasons, and standings for each mini-league. Read `notes[]` before acting on
any of it — in particular:

- **Selling prices are not available.** They require an authenticated session
  this mirror deliberately does not have. Every `now_cost` is the current market
  price, which overstates sale proceeds for any player who has risen.
- **Free transfers are an estimate.** The API does not publish them.

## Reliability

This is designed to run unattended for nine months, where the realistic failure
is not a crash but a job that keeps succeeding while producing garbage, or one
that quietly stops running.

**Nothing is written partially.** Every file is built into a temp path, validated,
then renamed into place. A malformed API response exits non-zero and leaves the
whole previous set untouched — a stale but well-formed file is far more useful
than a fresh corrupt one, because staleness is detectable and corruption is not.

**Nothing historical is rewritten.** `snapshots` and `player_history` only ever
gain rows. Existing rows are carried through byte for byte; only the comment
header is refreshed, so its `fetched=` stays honest.

**Schema drift is loud.** The expected field list for each endpoint is an
explicit constant in the code. If FPL renames or drops a field, the column is
still written — empty — *and* the discrepancy lands in `meta.json` `warnings[]`.
A silently null column is far worse than a loud one. New fields FPL adds are
reported there too.

**Staleness surfaces in one place.** Each builder records its last run in
`data/build_status.json`. `build_fpl.py` runs last and folds the lot into
`meta.json`: `stale` is true if any component has gone longer than twice its
expected interval without running, with the culprit named in `warnings[]`. A
partial failure therefore shows up in exactly the same place a total one does.

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
python build_fpl.py              # players/teams/fixtures/fdr/meta (run last)
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

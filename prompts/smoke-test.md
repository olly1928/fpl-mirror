<!-- smoke-test.md · v1 · 2026-08-15 -->

# FPL MIRROR — ACCESS SMOKE TEST

This is a health check, not an analysis. Verify the feed is reachable and coherent, report a
pass/fail table, and stop. Do not build a squad, project points, search the web, or give FPL
advice, even if something looks interesting.

## How to fetch

Use **bash and curl**, not web_fetch — the sandbox reaches `raw.githubusercontent.com`
directly and web_fetch has URL-allowlist problems with it.

```
B=https://raw.githubusercontent.com/olly1928/fpl-mirror/main/data
```

**Cache warning — read before reporting any failure.** `raw.githubusercontent.com` caches per
path for a few minutes, independently per file. You can fetch six files from one build and
get a mix of current and several-minutes-old copies. Query strings do **not** reliably defeat
this. If something looks stale, wait a minute and re-fetch, or pull that file via
`https://api.github.com/repos/olly1928/fpl-mirror/contents/data/<file>`, which is not behind
the same cache. Do not report a stale feed on a single mismatched read.

## Files to check

| Path | Expect |
|---|---|
| `meta.json` | present |
| `build_status.json` | present |
| `players.csv` | present, ~587 rows |
| `teams.csv` | present, 20 rows |
| `fdr.csv` | present, 20 rows |
| `fixtures.csv` | present, 380 rows |
| `odds.csv` | present, 10 rows |
| `squad.json` | present |
| `snapshots/2026-08.csv` | present, non-empty |
| `player_history.csv` | **may be absent or empty — this is correct pre-season**, not a failure |
| `fixture_stats.csv` | **may be absent — correct until games are played** |

## Checks to run

Write one Python script that does all of this and prints a table. Don't narrate each curl.

**1. Reachability** — HTTP status and byte size for every path above.

**2. Cache coherence** — the `fetched=` stamp in the `#` header of `players.csv`,
`teams.csv`, `fdr.csv` and `fixtures.csv` must all equal `meta.json`'s `fetched_at`. Any
disagreement means a cached read; re-fetch before reporting it.

**3. Integrity gate** — from `meta.json`:
* `season` is `2026/27`
* `teams_in_game` has 20 clubs, no duplicates
* `next_deadline` is a real upcoming date
* `fetched_at` is recent
* `stale` is `false`
* report `warnings[]` in full — these name API fields that arrived missing and were written
  as empty columns, which is otherwise indistinguishable from a real zero

**4. Component freshness** — from `build_status.json`, each component's `last_run_at` versus
its `expected_interval_minutes`. Flag anything older than roughly twice its interval.

**5. Column contract** — `players.csv` must begin with exactly these fourteen, in order:

```
id,name,team,pos,price,own,pts,ppg,mins,g,a,cs,bonus,st
```

Then report the total column count. **52** means the expansion landed but PR #3 hasn't;
**57** means PR #3 merged and `clearances_blocks_interceptions`, `tackles`, `recoveries`,
`expected_goal_involvements_per_90` and `goals_conceded` are live. Say which state it's in.

**6. Data is actually populated, not just present** — print these rows so I can eyeball them:

* Haaland (MCI) — `goals_scored`, `expected_goals`, `starts`, `defensive_contribution`
* B.Fernandes (MUN) — `assists`, `expected_assists`, `starts`
* Raya (ARS) — `cs`, `saves`, `starts`

Expected-goals and defensive-contribution fields carry **last season's totals**, not zeros.
If they read zero, the columns exist but aren't populated — that's a failure. `form` and the
transfer counts *should* read zero pre-season; that's correct.

**7. Snapshots sanity** — row count in `snapshots/2026-08.csv`, number of distinct
`snapshot_at` values, and whether the earliest timestamp of each UTC day carries a full
baseline for every player. It's delta-encoded: a daily baseline, then only players whose
values moved.

**8. Odds freshness** — `fetched_at` in `odds.csv`, how many fixtures it covers, and the
`n_books_totals` distribution. Flag any row where `n_books_totals` is 1 or 2.

## Output

A pass/fail table, then a short list of anything that needs attention. If everything passes,
say so in one line. British English.
* Confirm this playbook's version stamp (top line) in your output, and that you reached
  the closing comment at the bottom. If you didn't, you have a truncated copy — say so and
  re-fetch before doing anything else.

Do not proceed to any analysis afterwards.

<!-- end of smoke-test.md v1 — confirm this line was reached -->

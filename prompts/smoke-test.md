<!-- smoke-test.md · v3 · 2026-10-22 -->

# FPL MIRROR — ACCESS SMOKE TEST

This is a **feed health check only**. Check reachability/coherence, report a
pass/fail table and stop. Do not forecast points, build a squad, recommend
transfers/captains/chips, run the advisor model, or search football news.

First response line: "Loaded smoke-test.md v3 — feed health only."
Confirm this playbook's version stamp (top line) and the closing marker were
reached. Re-fetch a truncated copy. Use **bash + curl, never web_fetch**.
The same three project routing URLs remain valid.

## Fetch and capture evidence

Use curl to record HTTP status and byte size for each path below. Read
`meta.json` first. Prefer a pinned repository SHA, resolved via curl to
`https://api.github.com/repos/olly1928/fpl-mirror/commits/main`, then fetch
`https://raw.githubusercontent.com/olly1928/fpl-mirror/<SHA>/data/<path>`.
If that API is unavailable, use raw `main` and verify coherence before claiming
a fault.

| Path | Expected |
|---|---|
| `meta.json` | Present and parseable |
| `build_status.json` | Present and parseable |
| `players.csv` | Count equals `meta.player_count`; do not hardcode 587 |
| `teams.csv` | 20 distinct clubs |
| `fdr.csv` | 20 clubs |
| `fixtures.csv` | 380 fixtures in this 20-club/38-GW season |
| `odds.csv` | Non-empty, fixture count varies; do not hardcode 10 |
| `squad.json` | Correct team 790889 and expected locked-GW state |
| `snapshots/YYYY-MM.csv` | Current UTC month; daily baseline then deltas |
| `player_history.csv` | May be absent/empty only before completed GWs |
| `fixture_stats.csv` | May be absent/empty before any matches have been played |

Use the month of the core fetch around a UTC month boundary if the new month's
first snapshot has not yet been written; explain that boundary rather than
silently choosing an old arbitrary month. Fetching a monthly snapshot can be
large; do not print its entire contents.

Raw GitHub CDN caching is per path. Query strings do not reliably defeat it.
A core timestamp mismatch calls for a re-fetch of the core set or contents API
reads via curl. Do not declare a frozen mirror from one mismatched read.

## Checks

Run the arithmetic and parsing in one Python script; don't narrate each curl.

1. **Reachability:** HTTP status, bytes, parse success and required/optional status.
   Ignore CSV comment lines when reading headers/data. JSON must be a JSON
   document, not an HTML error page saved as one.

2. **Core coherence:** `players.csv`, `teams.csv`, `fdr.csv`,
   `fixtures.csv` and `fixture_stats.csv` are rebuilt by `build_fpl`.
   Their first comment's `fetched=` must equal `meta.fetched_at`.
   Do **not** apply that equality test to player history, snapshots or odds.

3. **Metadata:** correct season derived from event deadlines; 20 distinct clubs;
   correct player count; an upcoming next deadline while the season is active;
   recent core timestamp; `stale=false`; all `warnings[]` reviewed.
   After GW38, report "season complete" instead of failing because no next deadline
   exists. Report the count in `known_empty[]` only: these are acknowledged
   upstream columns, not new faults.

4. **Component freshness:** use each builder's own `last_run_at` and
   `expected_interval_minutes` in `build_status.json`. More than twice its
   interval is stale. Do not confuse an unchanged historical file with a builder
   that stopped running.

5. **Column contract:** the first fourteen players columns remain exactly:

   `id,name,team,pos,price,own,pts,ppg,mins,g,a,cs,bonus,st`

   Report the actual total column count; appended columns are expected.
   Check the current fields needed by the advisor: xG, xA, starts, defensive
   contributions, saves, cards and price projections. Do not infer an old PR's
   state from a hardcoded count of 52/57.

6. **Values:** print a compact sample using actual CSV names: Haaland's
   `g,expected_goals,starts`; a playing midfielder's
   `a,expected_assists,defensive_contribution`; a playing goalkeeper's
   `cs,saves,starts`. Before GW1 these may be carried previous-season totals;
   in-season they are cumulative current-season values. A legitimate zero
   (for example a goalkeeper's xG or a forward's defensive contribution) is not
   proof of a failed column. Use schema warnings and cross-record checks.

7. **History coverage:** every `meta.events[]` entry with `data_checked=true`
   must exist in `player_history.csv`. An older file-header timestamp is normal
   when no new GW has completed. Check unique (GW,player) rows and coherent numeric
   values. Where complete, reconcile cumulative points/minutes/goals/assists/xG/xA
   against `players.csv`; report real discrepancies without rewriting history.
   DGW rows aggregate multiple fixtures: don't interpret them as one match.

8. **Teams/fixtures:** club references and fixture IDs should join cleanly.
   Use the populated `derived_*` table and `strength_overall_home/away`.
   The known-empty FPL table counters and attack/defence breakdown stay expected.

9. **Snapshots:** report row count, distinct timestamps and the first snapshot of
   each UTC day. Validate daily anchors and forward-fill delta rows. The player
   population may grow during a day/month, so do not demand that an old anchor
   contain every player who exists today. Distinguish newly added players from a
   truncated baseline.

10. **Odds:** inspect row-level timestamps, exact teams/kickoff dates, upcoming
    fixture coverage, bookmaker-count distributions, normalised 1X2 probabilities,
    nonnegative fitted goal rates and clean-sheet probabilities in [0,1].
    Flag totals coverage of 0/1/2 books as a limitation. Odds have an independent
    six-hour clock and no requirement to equal `meta.fetched_at`. Odds more than
    12 hours old near a deadline need attention even if the file parses.

11. **Squad:** team ID, `picks_event`, chip/transfer history, bank units and
    selling-price confidence. Last-deadline picks are expected; upcoming-GW
    transfers mean a decision consumer must reconcile current edits. They do not
    by themselves mean the public feed failed.

## Output and stop

Return a compact pass/fail table and the short list of material faults or
limitations, with the snapshot time. Separate expected upstream empty fields and
normal historical timestamps from actual faults. A successful fetch alone is not
proof of correct contents.

If healthy, say so. If blocked, say exactly which required input/check failed.
Confirm the closing marker was reached. Do not continue to football advice.

<!-- end of smoke-test.md v3 — confirm this line was reached -->

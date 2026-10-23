# Expanded historical data — 23 October 2026

The complete FPL outcome panel now has **303,126 player/fixture records across
2014/15–2025/26**, up from 253,509 across ten seasons. It contains **3,020 distinct
official player codes**, including **2,486 absent from the captured 2026/27
roster**. Those absent players account for 230,128 records. Absence means absent
from this FPL roster: it does not distinguish retirement, transfer or relegation.

The previous ten-season panel already included 2,090 absent players and 182,224
of their fixture records. Earlier tests were not restricted to today's players.
The live API's 2,110 past-season aggregates *were* restricted to today's roster;
that limitation should not be confused with the match archive's coverage.

## Files ready for research

All CSV files are UTF-8 and gzip compressed. Python's `gzip`/`csv`, pandas and
ordinary CSV tools can read them. Counts describe different grains and must not
be summed as independent training observations.

| File under `data/` | Coverage | Records | Use and limitation |
|---|---|---:|---|
| `fpl-complete-seasons.csv.gz` | 2014/15–2025/26 | 303,126 | Main FPL outcome panel, unique `(season, player_id, fixture_id)` |
| `fpl-2014-15.csv.gz` | 2014/15, 380 fixtures / 38 GWs | 24,876 | Additional full season, 711 players, explicit official codes |
| `fpl-2015-16.csv.gz` | 2015/16, 380 fixtures / 38 GWs | 24,741 | Additional full season, 723 players, explicit official codes |
| `fpl-2013-14.csv.gz` | 2013/14, 291 fixtures / GW1–30 | 17,805 | Partial season; explicit official codes; not a complete year |
| `annual-history.csv.gz` | Season labels 2006/07–2025/26 | 9,994 | Unique player/season aggregates for 2,683 codes; older populations remain incomplete |
| `annual-history-versions.csv.gz` | All recovered versions | See coverage | Preserves overlapping source versions and their hashes |
| `partial-database-player-matches.csv.gz` | Partial 2010/11–2015/16 | 103,152 | Includes unknown targets, source overlaps and identity conflicts; filter explicitly |
| `understat-player-matches.csv.gz` | Observed career matches in 2014–2024 start years | 119,464 | 1,213 players, xG/xA and attacking stats; incomplete, multiple-league coverage |
| `understat-player-seasons.csv.gz` | Six leagues, 2014/15 onward | 37,237 | 34,815 completed-year rows plus 2,422 **partial** 2025/26 rows captured in September 2025 |
| `team-results-and-odds.csv.gz` | 1993/94–2025/26 | 12,704 | 33 seasons of EPL results, available match stats and bookmaker odds; not player FPL labels |
| `r-converted/*.csv.gz` | Five legacy R datasets | See files | Plain CSV exports; no R installation needed to use them |

The 49,617 extra full-season FPL rows enlarge the original panel by 19.6%.
The new Understat match data provides additional attacking measurements in old
years where FPL did not publish xG, and some history outside England. It uses a
different xG/assist definition. It must remain a separate provider layer.

The old SQLite archives supply 9,790 complete, identity-consistent points/minutes
records for 2010/11, 10,897 for 2011/12 and 11,366 for 2012/13. Their player
populations are partial. Later SQLite rows overlap the JSON/full panels; unknown
or inconsistent values are retained and flagged. `complete_points_and_minutes`
is true only when both targets are observed, minutes are valid, and neither
core targets nor local identity conflict. These rows are not automatically
admitted to a benchmark.

## What was checked

- The new 2014/15 and 2015/16 panels cover all 380 fixture results and 38 GWs.
  Each row resolves to a Football-Data fixture by date, opponent and venue;
  scores agree. Player points sum to each source's captured season total.
- All 24,876 2014/15 player/GW/minutes/points observations also reconcile with
  `durtal/fantasysocceR`'s separately saved R dataset. Both share upstream FPL
  lineage; this is not independent truth or proof of historical availability.
- The combined 12-year panel has no duplicate player/fixture keys and preserves
  missing xG/starts/DC as missing. It does not establish a complete historical
  population of everyone registered at every deadline.
- 5,978 archived player-history paths were collected, representing 5,064 unique
  file contents. Prior season names with `/` or `_` are reconciled to one key.
  Repeated source versions are not additional independent player seasons.
- Understat careers are deduplicated by `(understat_player_id, match_id)`.
  Explicit 2021/22 and 2022/23 ID dictionaries resolve 98,312 rows to a stable
  FPL code. Other rows remain unmapped; no name-only join is forced. Numerical
  differences within 0.00001 xG/xA are treated as CSV rounding; minutes, goals
  and assists require exact agreement. No core disagreements remain under
  those declared rules. Provider revision/vintage limitations still apply.
- Raw file identities, byte sizes, SHA-256 hashes, package hashes, derived row
  counts, missing targets and identity conflicts are verified by
  `python history/verify_extended.py`. See [validation.json](validation.json),
  [coverage.json](coverage.json), and the executed [coverage notebook](coverage.ipynb).

## Where the data came from

The [source inventories](inventories/) pin GitHub revisions and individual Git
blob identities. [Raw packages](raw/) preserve captured response bytes and
metadata, including original paths, URLs and checksums. Source ZIPs are used to
avoid thousands of small downloads; each selected member must agree with its
independently inventoried Git blob SHA. Raw package names are storage groups,
not counts of unique observations.

- [Vaastav Anand's FPL Historical Dataset](https://github.com/vaastav/Fantasy-Premier-League),
  commit `d7b05b51d137bfefef4b692386d18c610975b9b9`: the original ten seasons,
  archived per-player `history.csv` files, Understat careers and ID dictionaries.
  Credit: Anand, Vaastav, *FPL Historical Dataset*.
- [llimllib/fantasypl_stats](https://github.com/llimllib/fantasypl_stats): saved
  API responses including the closed 2014/15 panel. Eleven monthly/final snapshots
  were selected from 2,579 listed snapshots; thousands of near-identical copies
  were not counted as new data.
- [mvbfontes/premierleaguedatasets](https://github.com/mvbfontes/premierleaguedatasets):
  723 closed-season 2015/16 player responses. The corresponding
  [barryedmund/gaffer](https://github.com/barryedmund/gaffer) archive is retained
  as overlapping source evidence.
- [keithxm23/fplPlayer](https://github.com/keithxm23/fplPlayer),
  [fplassistant](https://github.com/keithxm23/fplassistant),
  [fplassistantv2](https://github.com/keithxm23/fplassistantv2), and
  [nori/hbv401g-2015v-f1a](https://github.com/nori/hbv401g-2015v-f1a): old JSON
  histories and roster snapshots. The inspected `data.json` commit histories
  contain one, 19 and 44 revisions respectively; the selected head snapshots
  are not an exhaustive publication-history collection.
- [durtal/fantasysocceR](https://github.com/durtal/fantasysocceR): five R-data
  objects, preserved as original bytes and converted to CSV with pyreadr 0.5.7
  and pandas 2.2.3. Unrelated R random-seed objects are ignored.
- [sjp4/differentialfpl](https://github.com/sjp4/differentialfpl): old SQLite
  databases and supporting SQL. They are read as immutable databases with
  query-only mode; downloaded SQL is not executed.
- [vibedatascience/understat_players_aggregated](https://github.com/vibedatascience/understat_players_aggregated):
  league/player/year aggregates, including the explicitly partial 2025/26 file.
- [Football-Data.co.uk](https://www.football-data.co.uk/englandm.php): original
  EPL CSVs and [field definitions](https://www.football-data.co.uk/notes.txt).
  Smaller legacy archives from `jokecamp`, `clwatkins` and `sandalsoft` are
  retained as raw source evidence as listed in the inventories.

Discovery also benefited from the source register in
[OrbitalLabBOG/mova-pro-futbol-data-analytics](https://github.com/OrbitalLabBOG/mova-pro-futbol-data-analytics)
at `6f8d99455c272aedc5ef1c1f65561e444b677dd0`. Its claims were leads; our numbers
come from the primary bytes actually collected and checked here. Upstream
readmes/licences are retained when present; code licences do not establish a
new licence over all underlying football data.

## API findings and boundaries

The bounded probes are retained in `raw/api-probes.tar.gz`. A request to
`element-summary/11334/` using Petr Cech's historical player code returned 404.
Appending `?season=2014-15` to player 1's summary returned the same current
2026/27 response as the ordinary endpoint. The legacy `/web/api/elements/`
route returned an HTML app shell, not historical player JSON. These observations
do not prove that no undocumented endpoint exists. They did not yield an old
player database, so public saved responses supplied the expansion.

Historical season-local FPL IDs must not be used as today's IDs. Stable official
player codes are retained where explicit. SQLite-only identities remain local
and conflicted identities are flagged. Missing rows, unavailable stats and NULL
targets never become invented zero outcomes. Whole-season totals are not
expanded into fabricated gameweek rows.

All these files are retrospective outcomes. Old FPL dates are interpreted as
Europe/London wall-clock times and converted with historical DST rules. Final
prices, status, team metadata and archived post-match xP are not pre-deadline
features. Football-Data distinguishes pre-closing and closing odds, but exact
publication relative to each FPL deadline is unproved here; closing odds may
postdate the deadline. Understat uses date-only matches and differing provider
definitions. Source-aware joins and strict time cutoffs remain necessary.

## Reproduce

Using the committed CSVs requires Python's standard library only:

```bash
python history/verify_extended.py --root history/extended
```

A fresh one-time acquisition/conversion can be run in an isolated research
environment from the repository root:

```bash
python -m venv /tmp/fpl-history-conversion
/tmp/fpl-history-conversion/bin/pip install pyreadr==0.5.7 pandas==2.2.3 numpy==2.2.6
PATH="/tmp/fpl-history-conversion/bin:$PATH" bash history/collect_extended.sh
```

The [one-off workflow](../../.github/workflows/historical-extension.yml) does
this once when the collection request changes, or when manually invoked. It
has **no cron** and commits only the historical extension outputs. Existing
raw packages, production feeds, live v1 forecasts, frozen priors and Claude's
three playbook URLs are preserved.

This acquisition does not retrain or promote a model. The larger, source-aware
panel is ready for the broader chronological model comparison; the previously
examined seasons and GW1–7 remain retrospective research data.

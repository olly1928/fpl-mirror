#!/usr/bin/env python3
"""
build_fpl.py — the bootstrap-derived half of the mirror.

Writes data/players.csv, data/teams.csv, data/fdr.csv, data/fixtures.csv,
data/fixture_stats.csv and data/meta.json from one call to bootstrap-static and
one to fixtures.

Runs in GitHub Actions, which has open internet access. Output lands in a public
repo, which Claude's sandbox CAN reach.

Two rules shape the whole file:

  * The existing contract does not move. players.csv, fdr.csv and fixtures.csv
    keep their paths, their leading '#' comment block and their original column
    names in their original order. Everything new is appended to the right-hand
    end, because downstream scripts read by column name and parse the header for
    their integrity gate.

  * Nothing is written until everything is built. Every file is assembled in
    memory and validated first, then the batch is written atomically. A
    malformed API response therefore leaves the whole previous set intact and
    exits non-zero — a stale-but-valid mirror is far more useful than a fresh
    corrupt one.

This script runs LAST in the workflow, because it also folds every other
builder's warnings and freshness into meta.json.
"""

import json
import os
import sys
import time

from fpl_common import (
    DATA, atomic_write_json, check_fields, clean, die, now_iso,
    api_required, read_status, record_status, staleness, write_all,
)

BOOTSTRAP_CACHE_TTL = 600  # seconds; several builders want it in the same job

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# ---------------------------------------------------------------- schema

# The fields players.csv is built from. Compared against the live response on
# every run; anything missing is written as an empty column AND recorded in
# meta.json warnings[], never left to look like a genuine zero.
EXPECTED_ELEMENT_FIELDS = [
    # identity and the original fourteen columns
    "id", "web_name", "first_name", "second_name", "team", "element_type",
    "now_cost", "selected_by_percent", "total_points", "points_per_game",
    "minutes", "goals_scored", "assists", "clean_sheets", "bonus", "status",
    "news", "news_added",
    # underlying performance
    "expected_goals", "expected_goals_per_90",
    "expected_assists", "expected_assists_per_90",
    "expected_goal_involvements",
    "expected_goals_conceded", "expected_goals_conceded_per_90",
    # minutes certainty
    "starts", "starts_per_90",
    "chance_of_playing_this_round", "chance_of_playing_next_round",
    # defensive contribution, and the raw components it is built from
    "defensive_contribution", "defensive_contribution_per_90",
    "clean_sheets_per_90", "goals_conceded_per_90", "saves_per_90",
    "clearances_blocks_interceptions", "tackles", "recoveries",
    "goals_conceded", "expected_goal_involvements_per_90",
    # set pieces
    "penalties_order", "penalties_text",
    "direct_freekicks_order", "direct_freekicks_text",
    "corners_and_indirect_freekicks_order", "corners_and_indirect_freekicks_text",
    # discipline and misc
    "yellow_cards", "red_cards", "own_goals", "penalties_missed",
    "penalties_saved", "saves", "bps",
    "influence", "creativity", "threat", "ict_index",
    # FPL's own projections — mirrored, never trusted as model input
    "form", "ep_this", "ep_next", "value_form", "value_season",
    # snapshot fields, consumed by build_snapshots.py
    "cost_change_event", "cost_change_start",
    "transfers_in_event", "transfers_out_event",
]

EXPECTED_TEAM_FIELDS = [
    "id", "code", "name", "short_name", "strength",
    "strength_overall_home", "strength_overall_away",
    "strength_attack_home", "strength_attack_away",
    "strength_defence_home", "strength_defence_away",
    "played", "win", "draw", "loss", "points", "position", "form",
]

EXPECTED_EVENT_FIELDS = [
    "id", "deadline_time", "finished", "data_checked", "is_current", "is_next",
    "average_entry_score", "highest_score", "most_captained", "most_selected",
    "chip_plays",
]

EXPECTED_FIXTURE_FIELDS = [
    "id", "event", "team_h", "team_a", "team_h_difficulty", "team_a_difficulty",
    "kickoff_time", "finished", "provisional_start_time",
    "team_h_score", "team_a_score", "minutes", "stats",
]

# Each entry of a fixture's stats array.
EXPECTED_FIXTURE_STAT_FIELDS = ["identifier", "a", "h"]

# Fields that exist in the API, have been looked at, and are deliberately not
# mirrored. Keeping them here rather than leaving them out entirely is the point:
# the drift guard subtracts both lists, so it stays silent about the forty fields
# already reviewed and still shouts the moment FPL ships a genuinely new one. A
# guard that reports forty known fields every hour is a guard nobody reads.
#
# Mostly derived ranks (recomputable from the values already mirrored), asset
# URLs, and internal flags.
IGNORED_ELEMENT_FIELDS = [
    "birth_date", "can_select", "can_transact", "code", "cost_change_event_fall",
    "cost_change_start_fall", "creativity_rank", "creativity_rank_type",
    "dreamteam_count", "event_points", "form_rank", "form_rank_type",
    "has_temporary_code", "ict_index_rank", "ict_index_rank_type", "in_dreamteam",
    "influence_rank", "influence_rank_type", "known_name", "now_cost_rank",
    "now_cost_rank_type", "opta_code", "photo", "points_per_game_rank",
    "points_per_game_rank_type", "price_change_percent", "region", "removed",
    "scout_news_link", "scout_risks", "selected_rank", "selected_rank_type",
    "special", "squad_number", "team_code", "team_join_date", "threat_rank",
    "threat_rank_type", "transfers_in", "transfers_out",
]
IGNORED_TEAM_FIELDS = ["link_url", "pulse_id", "team_division", "unavailable"]
IGNORED_EVENT_FIELDS = [
    "can_enter", "can_manage", "cup_leagues_created", "deadline_time_epoch",
    "deadline_time_game_offset", "h2h_ko_matches_created", "highest_scoring_entry",
    "is_previous", "most_transferred_in", "most_vice_captained", "name", "overrides",
    "ranked_count", "release_time", "released", "top_element", "top_element_info",
    "transfers_made",
]
IGNORED_FIXTURE_FIELDS = ["code", "finished_provisional", "pulse_id", "started"]

# Without these there is no usable players.csv at all, so their absence is fatal
# rather than a warning.
CRITICAL_ELEMENT_FIELDS = ["id", "web_name", "team", "element_type", "now_cost", "status"]

# ---------------------------------------------------------------- columns

# The original contract. Never reorder, never rename, never remove.
LEGACY_PLAYER_COLS = ["id", "name", "team", "pos", "price", "own", "pts", "ppg",
                      "mins", "g", "a", "cs", "bonus", "st"]

# Appended to the right-hand end only. Column names match the API field names so
# there is nothing to look up.
NEW_PLAYER_COLS = [
    "expected_goals", "expected_goals_per_90",
    "expected_assists", "expected_assists_per_90",
    "expected_goal_involvements",
    "expected_goals_conceded", "expected_goals_conceded_per_90",

    "starts", "starts_per_90",
    "chance_of_playing_this_round", "chance_of_playing_next_round",

    "defensive_contribution", "defensive_contribution_per_90",
    "clean_sheets_per_90", "goals_conceded_per_90", "saves_per_90",

    "penalties_order", "penalties_text",
    "direct_freekicks_order", "direct_freekicks_text",
    "corners_and_indirect_freekicks_order", "corners_and_indirect_freekicks_text",

    "yellow_cards", "red_cards", "own_goals", "penalties_missed",
    "penalties_saved", "saves", "bps",
    "influence", "creativity", "threat", "ict_index",

    "form", "ep_this", "ep_next", "value_form", "value_season",

    # Added after the first live run, appended here at the right-hand end for
    # the same reason as everything above it: columns only ever grow rightwards.
    #
    # The first three are the raw components defensive_contribution is built
    # from. Defenders and midfielders cross the DEFCON threshold on different
    # combinations of them, and the aggregate cannot be decomposed after the
    # fact, so modelling the probability of clearing it needs the parts.
    # recoveries counts towards the threshold for midfielders and forwards only.
    "clearances_blocks_interceptions", "tackles", "recoveries",
    "expected_goal_involvements_per_90",   # completes the per-90 set
    "goals_conceded",                      # raw count; only the per-90 was here
]

PLAYER_COLS = LEGACY_PLAYER_COLS + NEW_PLAYER_COLS

TEAM_COLS = EXPECTED_TEAM_FIELDS

FIXTURE_COLS = ["gw", "home", "away", "h_diff", "a_diff", "kickoff",
                "fixture_id", "finished", "provisional_start_time",
                "team_h_score", "team_a_score", "minutes_played"]

FIXTURE_STAT_COLS = ["fixture_id", "gw", "player_id", "team_side", "identifier", "value"]

UNTRUSTED_COLS = ["form", "ep_this", "ep_next", "value_form", "value_season"]


def cell(record, field):
    """One CSV cell from an API field, safe for a naively-joined row."""
    return clean(record.get(field))


def verify_outputs(fetched_at, started):
    """
    Read every file back off disk and prove this run actually wrote it.

    Writing a file and assuming it landed is the one failure this pipeline
    cannot afford, because meta.json is the only thing the downstream consumer
    reads for its integrity gate. If meta.json silently froze while every other
    file kept moving, the feed would report a valid season and deadline forever
    while quietly ageing — the exact failure the staleness design exists to
    prevent, defeating it from the inside.

    So: re-read, compare, and exit non-zero on any mismatch. Cheap, and it turns
    an invisible freeze into a red run. Applies to every file whose absence a
    consumer could not detect, not just meta.json.

    Two independent checks per file, because either alone has a blind spot. The
    content timestamp catches a write that landed with the wrong data; the file
    mtime catches a write that never happened at all, which a content check
    cannot see whenever the previous run fell inside the same clock second.
    """
    problems = []

    def written_this_run(path):
        try:
            # Writes go via a temp file and a rename, so mtime is the moment this
            # process wrote it. Anything older is a file we did not touch.
            return os.stat(path).st_mtime >= started
        except OSError:
            return False

    for name in ("meta.json", "players.csv", "teams.csv", "fdr.csv",
                 "fixtures.csv", "fixture_stats.csv"):
        if not written_this_run(DATA / name):
            problems.append(
                f"{name} was not written by this run — it still has an older "
                "modification time. The write silently did not land."
            )

    try:
        on_disk = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"meta.json could not be read back after writing: {exc}")
    else:
        if on_disk.get("fetched_at") != fetched_at:
            problems.append(
                f"meta.json on disk says fetched_at={on_disk.get('fetched_at')!r} but this "
                f"run is {fetched_at!r}. The write did not land."
            )
        for key in ("events", "element_types", "chips", "game_settings",
                    "warnings", "components", "stale"):
            if key not in on_disk:
                problems.append(f"meta.json on disk is missing {key!r}")

    for name in ("players.csv", "teams.csv", "fdr.csv", "fixtures.csv",
                 "fixture_stats.csv"):
        try:
            first = (DATA / name).read_text(encoding="utf-8").split("\n", 1)[0]
        except OSError as exc:
            problems.append(f"{name} could not be read back after writing: {exc}")
            continue
        if f"fetched={fetched_at}" not in first:
            problems.append(
                f"{name} header does not carry fetched={fetched_at} — got {first[:120]!r}"
            )

    if problems:
        die("post-write verification failed. The mirror may be serving a frozen file:\n  "
            + "\n  ".join(problems))


def main():
    # Anchor for the post-write mtime check below.
    started = time.time()
    warnings = []

    boot = api_required("/bootstrap-static/", "bootstrap-static",
                        cache_ttl=BOOTSTRAP_CACHE_TTL)
    fixtures = api_required("/fixtures/", "fixtures")

    # ---- validate before building anything --------------------------
    elements = boot.get("elements") or []
    boot_teams = boot.get("teams") or []
    events = boot.get("events") or []
    if not elements:
        die("bootstrap-static returned no elements — refusing to overwrite players.csv")
    if not boot_teams:
        die("bootstrap-static returned no teams — refusing to overwrite the mirror")
    if not events:
        die("bootstrap-static returned no events — refusing to overwrite the mirror")
    if not isinstance(fixtures, list) or not fixtures:
        die("the fixtures endpoint returned nothing usable — refusing to overwrite fixtures.csv")

    missing_critical = [f for f in CRITICAL_ELEMENT_FIELDS
                        if not all(f in e for e in elements)]
    if missing_critical:
        die("bootstrap-static elements are missing field(s) the mirror cannot work "
            f"without: {', '.join(missing_critical)}. Leaving every existing file untouched.")

    warnings += check_fields(elements, EXPECTED_ELEMENT_FIELDS, "bootstrap-static.elements",
                             ignore=IGNORED_ELEMENT_FIELDS)
    warnings += check_fields(boot_teams, EXPECTED_TEAM_FIELDS, "bootstrap-static.teams",
                             ignore=IGNORED_TEAM_FIELDS)
    warnings += check_fields(events, EXPECTED_EVENT_FIELDS, "bootstrap-static.events",
                             ignore=IGNORED_EVENT_FIELDS)
    warnings += check_fields(fixtures, EXPECTED_FIXTURE_FIELDS, "fixtures",
                             ignore=IGNORED_FIXTURE_FIELDS)
    # Fixture stats only exist once games have been played, so there is nothing
    # to check pre-season and an empty list is not drift.
    played_stats = [s for f in fixtures for s in (f.get("stats") or [])]
    if played_stats:
        warnings += check_fields(played_stats, EXPECTED_FIXTURE_STAT_FIELDS,
                                 "fixtures.stats")

    # ---- shared header ----------------------------------------------
    current = next((e for e in events if e.get("is_current")), None)
    nxt = (next((e for e in events if e.get("is_next")), None)
           or next((e for e in events if not e.get("finished")), None))
    season_year = int(events[0]["deadline_time"][:4])
    season = f"{season_year}/{str(season_year + 1)[2:]}"
    teams = {t["id"]: t["short_name"] for t in boot_teams}

    fetched_at = now_iso()
    meta = {
        "fetched_at": fetched_at,
        "season": season,
        "preseason": current is None,
        "current_event": current["id"] if current else None,
        "next_event": nxt["id"] if nxt else None,
        "next_deadline": nxt["deadline_time"] if nxt else None,
        "player_count": len(elements),
        "teams_in_game": sorted(teams.values()),
    }

    header = (
        f"# season={meta['season']} preseason={meta['preseason']} "
        f"next_gw={meta['next_event']} deadline={meta['next_deadline']} "
        f"players={meta['player_count']} fetched={meta['fetched_at']}"
    )

    pending = {}

    # ---- players.csv -------------------------------------------------
    rows = []
    for e in elements:
        row = {
            "id": e["id"], "name": clean(e["web_name"]), "team": teams[e["team"]],
            "pos": POS[e["element_type"]], "price": e["now_cost"] / 10,
            "own": e.get("selected_by_percent"), "pts": e.get("total_points"),
            "ppg": e.get("points_per_game"), "mins": e.get("minutes"),
            "g": e.get("goals_scored"), "a": e.get("assists"),
            "cs": e.get("clean_sheets"), "bonus": e.get("bonus"), "st": e["status"],
            # not columns — these feed the ALERTS block below
            "news": clean(e.get("news")), "chance": e.get("chance_of_playing_next_round"),
        }
        for col in NEW_PLAYER_COLS:
            row[col] = cell(e, col)
        rows.append(row)
    rows.sort(key=lambda r: -(r["pts"] or 0))

    # These three comment lines are the published header block. Their structure
    # is part of the contract — the downstream integrity gate parses them.
    lines = [
        header,
        "# pts/ppg/mins/g/a/cs/bonus are LAST SEASON totals carried into the new game.",
        "# st: a=available i=injured d=doubtful s=suspended u=unavailable",
        ",".join(PLAYER_COLS),
    ]
    lines += [",".join(clean(r[c]) for c in PLAYER_COLS) for r in rows]

    alerts = [r for r in rows if r["st"] != "a"]
    if alerts:
        lines += ["", "# ALERTS — id,name,team,status,chance,news"]
        lines += [f"# {r['id']},{r['name']},{r['team']},{r['st']},"
                  f"{r['chance'] if r['chance'] is not None else ''},{r['news']}"
                  for r in alerts]
    pending[DATA / "players.csv"] = "\n".join(lines)

    # ---- teams.csv — FPL's own strength ratings ----------------------
    # Worth having as an independent prior alongside the bookmaker-derived
    # numbers in odds.csv: they disagree often enough to be informative.
    tlines = [
        header,
        "# FPL's own team strength ratings (1-5 scale for 'strength', ~1000-1400 for the rest).",
        "# played/win/draw/loss/points/position/form are 0 or blank until the season starts.",
        ",".join(TEAM_COLS),
    ]
    for t in sorted(boot_teams, key=lambda x: x.get("short_name") or ""):
        tlines.append(",".join(cell(t, c) for c in TEAM_COLS))
    pending[DATA / "teams.csv"] = "\n".join(tlines)

    # ---- fdr.csv — per-team difficulty over the next 6 GWs ------------
    start = meta["next_event"] or 1
    n = 6
    tbl = {s: [] for s in teams.values()}
    for f in fixtures:
        gw = f.get("event")
        if gw is None or gw < start or gw >= start + n:
            continue
        tbl[teams[f["team_h"]]].append((gw, teams[f["team_a"]], True, f["team_h_difficulty"]))
        tbl[teams[f["team_a"]]].append((gw, teams[f["team_h"]], False, f["team_a_difficulty"]))

    summary = []
    for team, fx in tbl.items():
        fx.sort()
        avg = round(sum(d for *_, d in fx) / len(fx), 2) if fx else None
        seq = " ".join(f"{o if h else o.lower()}({d})" for _, o, h, d in fx)
        summary.append((avg if avg is not None else 9, team, len(fx), avg, seq))
    summary.sort()

    fdr = [header, f"# difficulty GW{start}-{start + n - 1}, easiest first",
           "# opponents: UPPER=home lower=away, (n)=difficulty",
           "team,games,avg_difficulty,opponents"]
    fdr += [f"{t},{g},{a},{s}" for _, t, g, a, s in summary]
    pending[DATA / "fdr.csv"] = "\n".join(fdr)

    # ---- fixtures.csv — full season, now with results -----------------
    # Scores and finished flags are what let the mirror grade its own model
    # against outcomes rather than only ever projecting forward.
    fx = [header,
          "# scores/minutes_played are blank until a fixture has been played.",
          "# provisional_start_time=True means the kickoff time is not yet confirmed.",
          ",".join(FIXTURE_COLS)]
    for f in sorted((x for x in fixtures if x.get("event")),
                    key=lambda x: (x["event"], x.get("kickoff_time") or "", x["id"])):
        fx.append(",".join([
            clean(f["event"]), teams[f["team_h"]], teams[f["team_a"]],
            clean(f.get("team_h_difficulty")), clean(f.get("team_a_difficulty")),
            (f.get("kickoff_time") or "")[:10],
            clean(f.get("id")), clean(f.get("finished")),
            clean(f.get("provisional_start_time")),
            clean(f.get("team_h_score")), clean(f.get("team_a_score")),
            clean(f.get("minutes")),
        ]))
    pending[DATA / "fixtures.csv"] = "\n".join(fx)

    # ---- fixture_stats.csv — per-fixture, per-player stat lines --------
    # This overlaps player_history.csv but is not redundant. event/{id}/live/
    # aggregates a player across a whole gameweek, so in a double gameweek it
    # cannot say which of the two fixtures a goal came in. This can.
    #
    # Rewritten in full each run rather than appended, because unlike snapshots
    # it is re-fetchable at any time and FPL amends it — bonus points are
    # provisional until data_checked, and stat corrections land days later. A
    # rewrite picks those up; an append would freeze the first, wrong version.
    fs = [header,
          "# One row per fixture per player per stat, flattened from fixtures[].stats.",
          "# team_side: h=home side, a=away side. Empty until fixtures are played.",
          "# Unlike the other append-only files this one is rebuilt each run, so FPL's "
          "later bonus-point and stat corrections are picked up.",
          ",".join(FIXTURE_STAT_COLS)]
    stat_rows = 0
    for f in sorted((x for x in fixtures if x.get("event")), key=lambda x: x["id"]):
        for block in f.get("stats") or []:
            identifier = clean(block.get("identifier"))
            for side in ("h", "a"):
                for entry in block.get(side) or []:
                    fs.append(",".join([
                        clean(f.get("id")), clean(f.get("event")),
                        clean(entry.get("element")), side, identifier,
                        clean(entry.get("value")),
                    ]))
                    stat_rows += 1
    pending[DATA / "fixture_stats.csv"] = "\n".join(fs)

    # ---- meta.json ----------------------------------------------------
    meta["events"] = [
        {k: e.get(k) for k in EXPECTED_EVENT_FIELDS} for e in events
    ]
    meta["element_types"] = boot.get("element_types") or []
    meta["chips"] = boot.get("chips") or []
    meta["game_settings"] = boot.get("game_settings") or {}
    if not meta["element_types"]:
        warnings.append("bootstrap-static: element_types missing — squad limits unavailable")
    if not meta["game_settings"]:
        warnings.append("bootstrap-static: game_settings missing — scoring rules unavailable")

    # Fold in every other builder's last run, so one place answers "is any part
    # of this mirror out of date?" rather than the consumer having to check six.
    record_status("build_fpl", expected_interval_minutes=60, warnings=warnings,
                  files=["players.csv", "teams.csv", "fdr.csv", "fixtures.csv",
                         "fixture_stats.csv", "meta.json"])
    status = read_status()
    components, any_stale = staleness(status)
    for name, entry in sorted(status.items()):
        for w in entry.get("warnings") or []:
            if name != "build_fpl":
                warnings.append(f"[{name}] {w}")
    for name, comp in components.items():
        if comp["stale"]:
            warnings.append(
                f"[{name}] STALE — last ran {comp['last_run_at']}, expected every "
                f"{comp['expected_interval_minutes']} min"
            )

    meta["stale"] = any_stale
    meta["warnings"] = warnings
    meta["components"] = components
    meta["notes"] = [
        "Columns form, ep_this, ep_next, value_form and value_season are FPL's own "
        "projections. They are mirrored for reference and comparison only and should "
        "never be fed to a model as an input.",
        "players.csv columns up to and including 'st' are the original contract and "
        "will not move. New columns are only ever appended to the right-hand end.",
        "pts/ppg/mins/g/a/cs/bonus and every cumulative stat in players.csv are "
        "season-to-date totals. For per-gameweek splits use player_history.csv.",
        "warnings[] is populated by the schema drift guard. A named field in there is "
        "one whose column is present but empty because the API stopped sending it.",
        "stale is true when any component has gone longer than twice its expected "
        "refresh interval without running. Check components{} for which one.",
        "CACHE CHECK: every CSV in this mirror carries 'fetched=<timestamp>' in its "
        "first comment line, and it is always identical to this file's fetched_at "
        "within a single build. If they disagree, one of the two was served from a "
        "cache rather than being genuinely out of date — raw.githubusercontent.com "
        "caches per-path, so meta.json can lag while players.csv is current. Refetch "
        "with a cache-busting query string before concluding the mirror is stale.",
    ]

    # ---- everything built and validated; now write ---------------------
    write_all(pending)
    atomic_write_json(DATA / "meta.json", meta)
    verify_outputs(fetched_at, started)

    print(f"OK — {season}, GW{meta['next_event']}, {len(rows)} players, "
          f"{len(boot_teams)} teams, {len(fixtures)} fixtures, "
          f"{stat_rows} fixture stat rows")
    if warnings:
        print(f"  {len(warnings)} warning(s) recorded in meta.json:")
        for w in warnings:
            print(f"    ! {w}")
    if any_stale:
        print("  meta.json stale=true — a component has not run recently", file=sys.stderr)
    for p in sorted(DATA.rglob("*")):
        if p.is_file():
            print(f"  {p}  {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()

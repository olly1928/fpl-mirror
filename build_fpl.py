#!/usr/bin/env python3
"""
build_fpl.py — the bootstrap-derived half of the mirror.

Writes data/players.csv, data/teams.csv, data/fdr.csv, data/fixtures.csv and
data/meta.json from one call to bootstrap-static and one to fixtures.

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

import sys

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
    # defensive contribution
    "defensive_contribution", "defensive_contribution_per_90",
    "clean_sheets_per_90", "goals_conceded_per_90", "saves_per_90",
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
    "team_h_score", "team_a_score", "minutes",
]

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
]

PLAYER_COLS = LEGACY_PLAYER_COLS + NEW_PLAYER_COLS

TEAM_COLS = EXPECTED_TEAM_FIELDS

FIXTURE_COLS = ["gw", "home", "away", "h_diff", "a_diff", "kickoff",
                "fixture_id", "finished", "provisional_start_time",
                "team_h_score", "team_a_score", "minutes_played"]

UNTRUSTED_COLS = ["form", "ep_this", "ep_next", "value_form", "value_season"]


def cell(record, field):
    """One CSV cell from an API field, safe for a naively-joined row."""
    return clean(record.get(field))


def main():
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

    warnings += check_fields(elements, EXPECTED_ELEMENT_FIELDS, "bootstrap-static.elements")
    warnings += check_fields(boot_teams, EXPECTED_TEAM_FIELDS, "bootstrap-static.teams")
    warnings += check_fields(events, EXPECTED_EVENT_FIELDS, "bootstrap-static.events")
    warnings += check_fields(fixtures, EXPECTED_FIXTURE_FIELDS, "fixtures")

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
                  files=["players.csv", "teams.csv", "fdr.csv", "fixtures.csv", "meta.json"])
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
    ]

    # ---- everything built and validated; now write ---------------------
    write_all(pending)
    atomic_write_json(DATA / "meta.json", meta)

    print(f"OK — {season}, GW{meta['next_event']}, {len(rows)} players, "
          f"{len(boot_teams)} teams, {len(fixtures)} fixtures")
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

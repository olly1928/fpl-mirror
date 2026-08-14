#!/usr/bin/env python3
"""
test_offline.py — the acceptance checks from the expansion spec, run without a
network.

Every builder in this repo talks to the API through fpl_common.http_json, so
replacing that one function with a table of synthetic payloads is enough to
drive the whole pipeline offline. That matters for more than convenience: two of
the checks below (a malformed response, a renamed field) cannot be produced
against the real API on demand.

Run with:  python tests/test_offline.py
"""

import copy
import csv
import io
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fpl_common  # noqa: E402

# ------------------------------------------------------------------ fake API

RESPONSES = {}


def fake_http_json(url, *, headers=None, cache_ttl=0):
    for suffix, payload in RESPONSES.items():
        if url.endswith(suffix):
            if isinstance(payload, Exception):
                raise payload
            return copy.deepcopy(payload)
    raise AssertionError(f"test made an unexpected request: {url}")


fpl_common.http_json = fake_http_json

import build_fpl  # noqa: E402
import build_player_history  # noqa: E402
import build_snapshots  # noqa: E402
import build_squad  # noqa: E402

TEAMS = ["ARS", "AVL", "BHA", "BOU", "BRE", "CHE", "COV", "CRY", "EVE", "FUL",
         "HUL", "IPS", "LEE", "LIV", "MCI", "MUN", "NEW", "NFO", "SUN", "TOT"]
N_PLAYERS = 200


def make_element(i):
    """One bootstrap element carrying every field the mirror expects."""
    e = {
        "id": i, "web_name": f"Player{i}", "first_name": "A", "second_name": f"B{i}",
        "team": (i % 20) + 1, "element_type": (i % 4) + 1,
        "now_cost": 40 + (i % 100), "selected_by_percent": f"{i % 50}.5",
        "total_points": 300 - i, "points_per_game": "4.2", "minutes": 1000 + i,
        "goals_scored": i % 20, "assists": i % 10, "clean_sheets": i % 15,
        "bonus": i % 30, "status": "a" if i % 25 else "i",
        "news": "Knee injury - Expected back 23 Aug, doubtful" if i % 25 == 0 else "",
        "news_added": "2026-08-10T09:00:00Z" if i % 25 == 0 else None,
        "cost_change_event": 0, "cost_change_start": i % 5,
        "transfers_in_event": i * 3, "transfers_out_event": i,
        "chance_of_playing_this_round": None if i % 25 else 25,
        "chance_of_playing_next_round": None if i % 25 else 50,
    }
    for f in ["expected_goals", "expected_goals_per_90", "expected_assists",
              "expected_assists_per_90", "expected_goal_involvements",
              "expected_goals_conceded", "expected_goals_conceded_per_90",
              "starts_per_90", "defensive_contribution_per_90", "clean_sheets_per_90",
              "goals_conceded_per_90", "saves_per_90", "influence", "creativity",
              "threat", "ict_index", "form", "ep_this", "ep_next", "value_form",
              "value_season"]:
        e[f] = f"{(i % 30) / 10:.2f}"
    for f in ["starts", "defensive_contribution", "yellow_cards", "red_cards",
              "own_goals", "penalties_missed", "penalties_saved", "saves", "bps"]:
        e[f] = i % 12
    # A set-piece note with a comma in it, to prove the naive-join writer stays safe.
    e["penalties_order"] = 1 if i % 17 == 0 else None
    e["penalties_text"] = "Takes penalties, and corners" if i % 17 == 0 else ""
    e["direct_freekicks_order"] = None
    e["direct_freekicks_text"] = ""
    e["corners_and_indirect_freekicks_order"] = None
    e["corners_and_indirect_freekicks_text"] = ""
    return e


def make_bootstrap(completed_gws=0):
    events = []
    for gw in range(1, 39):
        done = gw <= completed_gws
        events.append({
            "id": gw, "deadline_time": f"2026-08-{min(gw, 28):02d}T17:30:00Z",
            "finished": done, "data_checked": done,
            "is_current": gw == completed_gws,
            "is_next": gw == completed_gws + 1,
            "average_entry_score": 55 if done else 0,
            "highest_score": 120 if done else None,
            "most_captained": 411 if done else None,
            "most_selected": 411 if done else None,
            "chip_plays": [],
        })
    teams = [{
        "id": i + 1, "code": 100 + i, "name": f"Club {short}", "short_name": short,
        "strength": 3, "strength_overall_home": 1200, "strength_overall_away": 1180,
        "strength_attack_home": 1210, "strength_attack_away": 1190,
        "strength_defence_home": 1205, "strength_defence_away": 1170,
        "played": 0, "win": 0, "draw": 0, "loss": 0, "points": 0,
        "position": i + 1, "form": None,
    } for i, short in enumerate(TEAMS)]

    return {
        "elements": [make_element(i) for i in range(1, N_PLAYERS + 1)],
        "teams": teams,
        "events": events,
        "element_types": [{"id": i, "singular_name_short": s, "squad_select": n}
                          for i, (s, n) in enumerate(
                              [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)], 1)],
        "chips": [{"id": 1, "name": "wildcard", "chip_type": "transfer"}],
        "game_settings": {"squad_squadplay": 11, "squad_total_spend": 1000},
    }


def make_fixtures(played_gws=0):
    out, fid = [], 1
    for gw in range(1, 39):
        for pair in range(10):
            h = ((gw + pair * 2) % 20) + 1
            a = ((gw + pair * 2 + 1) % 20) + 1
            done = gw <= played_gws
            out.append({
                "id": fid, "event": gw, "team_h": h, "team_a": a,
                "team_h_difficulty": (pair % 5) + 1, "team_a_difficulty": (pair % 4) + 1,
                "kickoff_time": f"2026-08-{min(gw, 28):02d}T14:00:00Z",
                "finished": done, "provisional_start_time": False,
                "team_h_score": 2 if done else None,
                "team_a_score": 1 if done else None,
                "minutes": 90 if done else 0,
            })
            fid += 1
    return out


def make_live(gw, fixtures):
    """A live payload for one gameweek, with each player attached to a real fixture."""
    boot = RESPONSES["/bootstrap-static/"]
    by_team = {}
    for f in fixtures:
        if f["event"] == gw:
            by_team[f["team_h"]] = f["id"]
            by_team[f["team_a"]] = f["id"]

    elements = []
    for e in boot["elements"]:
        stats = {c: (e["id"] % 7) for c in build_player_history.STAT_COLS}
        stats["minutes"] = 90 if e["id"] % 3 else 0
        stats["total_points"] = gw + (e["id"] % 5)
        elements.append({
            "id": e["id"], "stats": stats,
            "explain": [{"fixture": by_team.get(e["team"]), "stats": []}],
        })
    return {"elements": elements}


def load_api(completed_gws=0):
    RESPONSES.clear()
    RESPONSES["/bootstrap-static/"] = make_bootstrap(completed_gws)
    fixtures = make_fixtures(completed_gws)
    RESPONSES["/fixtures/"] = fixtures
    for gw in range(1, completed_gws + 1):
        RESPONSES[f"/event/{gw}/live/"] = make_live(gw, fixtures)


# ------------------------------------------------------------------ helpers

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f" — {detail}" if detail and not condition else ""))


def run(module, argv=None):
    """Call a builder's main() as if from the command line. Returns the exit code."""
    old = sys.argv
    sys.argv = [module.__name__] + (argv or [])
    try:
        module.main()
        return 0
    except SystemExit as exc:
        return exc.code or 0
    finally:
        sys.argv = old


def read_csv_like_a_consumer(path):
    """Exactly what a downstream script does: drop '#' lines, then DictReader."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    body = [ln for ln in text.split("\n") if ln and not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(body))))


def scratch():
    d = tempfile.mkdtemp(prefix="fpl-mirror-test-")
    os.chdir(d)
    pathlib.Path("data").mkdir()
    return pathlib.Path(d)


# ------------------------------------------------------------------ checks

def test_existing_contract():
    print("\n[1] players.csv keeps its published contract")
    scratch()
    load_api()
    run(build_fpl)

    lines = pathlib.Path("data/players.csv").read_text(encoding="utf-8").split("\n")
    check("header block is 3 comment lines then the column row",
          lines[0].startswith("# season=") and lines[1].startswith("# pts/ppg")
          and lines[2].startswith("# st: ") and not lines[3].startswith("#"),
          repr(lines[:4]))
    check("header line 1 still carries season/preseason/next_gw/deadline/players/fetched",
          all(k in lines[0] for k in
              ["season=", "preseason=", "next_gw=", "deadline=", "players=", "fetched="]))

    cols = lines[3].split(",")
    legacy = ["id", "name", "team", "pos", "price", "own", "pts", "ppg",
              "mins", "g", "a", "cs", "bonus", "st"]
    check("the original 14 columns are unchanged and still leftmost",
          cols[:14] == legacy, f"got {cols[:14]}")

    rows = read_csv_like_a_consumer("data/players.csv")
    check("a consumer reading by column name still gets every legacy field",
          len(rows) == N_PLAYERS and all(r["id"] and r["team"] and r["pos"] for r in rows))
    check("every row has exactly as many cells as there are columns",
          all(len(ln.split(",")) == len(cols)
              for ln in lines[4:] if ln and not ln.startswith("#")))
    check("a set-piece note containing a comma does not shift the columns",
          any(r["penalties_text"] == "Takes penalties  and corners" for r in rows))
    check("the ALERTS comment block survives",
          "# ALERTS — id,name,team,status,chance,news" in lines)


def test_new_columns_and_files():
    print("\n[2] the new columns and files are all present")
    scratch()
    load_api()
    run(build_fpl)

    rows = read_csv_like_a_consumer("data/players.csv")
    for col in ["expected_goals", "expected_assists", "expected_goal_involvements",
                "expected_goals_conceded", "expected_goals_per_90",
                "expected_assists_per_90", "expected_goals_conceded_per_90",
                "starts", "starts_per_90", "chance_of_playing_this_round",
                "chance_of_playing_next_round", "defensive_contribution",
                "defensive_contribution_per_90", "clean_sheets_per_90",
                "goals_conceded_per_90", "saves_per_90", "penalties_order",
                "penalties_text", "direct_freekicks_order", "direct_freekicks_text",
                "corners_and_indirect_freekicks_order",
                "corners_and_indirect_freekicks_text", "yellow_cards", "red_cards",
                "own_goals", "penalties_missed", "penalties_saved", "saves", "bps",
                "influence", "creativity", "threat", "ict_index", "form",
                "ep_this", "ep_next", "value_form", "value_season"]:
        if col not in rows[0]:
            check(f"players.csv has {col}", False)
            return
    check("players.csv carries all 38 new columns", True)

    teams = read_csv_like_a_consumer("data/teams.csv")
    check("teams.csv has 20 clubs with strength ratings",
          len(teams) == 20 and teams[0]["strength_attack_home"] == "1210")

    fixtures = read_csv_like_a_consumer("data/fixtures.csv")
    check("fixtures.csv keeps its original columns and gains results",
          list(fixtures[0])[:6] == ["gw", "home", "away", "h_diff", "a_diff", "kickoff"]
          and all(k in fixtures[0] for k in
                  ["fixture_id", "finished", "provisional_start_time",
                   "team_h_score", "team_a_score", "minutes_played"]))

    meta = json.loads(pathlib.Path("data/meta.json").read_text())
    check("meta.json keeps every original key",
          all(k in meta for k in ["fetched_at", "season", "preseason", "current_event",
                                  "next_event", "next_deadline", "player_count",
                                  "teams_in_game"]))
    check("meta.json gains events/game_settings/chips/element_types",
          len(meta["events"]) == 38 and meta["game_settings"] and meta["chips"]
          and len(meta["element_types"]) == 4)
    check("meta.json events carry average_entry_score",
          "average_entry_score" in meta["events"][0])
    check("meta.json exposes stale and warnings", "stale" in meta and "warnings" in meta)


def test_malformed_response_is_non_destructive():
    print("\n[3] a malformed API response leaves every previous file intact")
    scratch()
    load_api()
    run(build_fpl)

    before = {p.name: p.read_bytes() for p in pathlib.Path("data").iterdir() if p.is_file()}

    # Case A: the response shape collapses entirely.
    RESPONSES["/bootstrap-static/"] = {"elements": [], "teams": [], "events": []}
    code = run(build_fpl)
    after = {p.name: p.read_bytes() for p in pathlib.Path("data").iterdir() if p.is_file()}
    check("empty bootstrap exits non-zero", code != 0, f"exit={code}")
    check("empty bootstrap changes nothing on disk", before == after)

    # Case B: a field the mirror cannot work without is renamed away.
    boot = make_bootstrap()
    for e in boot["elements"]:
        e.pop("now_cost")
    RESPONSES["/bootstrap-static/"] = boot
    code = run(build_fpl)
    after = {p.name: p.read_bytes() for p in pathlib.Path("data").iterdir() if p.is_file()}
    check("a missing critical field exits non-zero", code != 0, f"exit={code}")
    check("a missing critical field changes nothing on disk", before == after)


def test_schema_drift_warns():
    print("\n[4] a renamed or missing field warns rather than nulling silently")
    scratch()
    load_api()
    run(build_fpl)

    boot = make_bootstrap()
    for e in boot["elements"]:
        e.pop("defensive_contribution")
        e["some_new_field_fpl_added"] = 1
    RESPONSES["/bootstrap-static/"] = boot
    code = run(build_fpl)

    meta = json.loads(pathlib.Path("data/meta.json").read_text())
    warnings = " ".join(meta["warnings"])
    check("the build still succeeds", code == 0, f"exit={code}")
    check("the missing field is named in meta.json warnings[]",
          "defensive_contribution" in warnings, warnings)
    check("a newly appeared API field is reported too",
          "some_new_field_fpl_added" in warnings, warnings)

    rows = read_csv_like_a_consumer("data/players.csv")
    check("the column is still written, just empty",
          "defensive_contribution" in rows[0] and rows[0]["defensive_contribution"] == "")


def test_snapshots():
    print("\n[5] snapshots capture what cannot be reconstructed")
    d = scratch()
    load_api()

    check("first run writes a baseline", run(build_snapshots) == 0)
    month_file = next(pathlib.Path("data/snapshots").glob("*.csv"))
    first = read_csv_like_a_consumer(month_file)
    check("baseline covers every player", len(first) == N_PLAYERS)

    # Nothing changed: a delta run should write nothing and still exit 0.
    run(build_snapshots)
    check("an unchanged delta run appends no rows",
          len(read_csv_like_a_consumer(month_file)) == N_PLAYERS)

    # Now move a price, exactly as FPL's overnight run does.
    boot = RESPONSES["/bootstrap-static/"]
    boot["elements"][0]["now_cost"] += 1
    boot["elements"][1]["news"] = "Hamstring injury, out for three weeks"
    # Builders import now_iso by name, so patch it where it is used.
    build_snapshots.now_iso = lambda: "2026-08-15T03:00:00+00:00"
    run(build_snapshots)

    rows = read_csv_like_a_consumer(month_file)
    stamps = {r["snapshot_at"] for r in rows}
    check("two distinct snapshot timestamps are present", len(stamps) >= 2, str(stamps))

    by_player = [r for r in rows if r["player_id"] == "1"]
    check("the same player appears twice with different now_cost",
          len(by_player) == 2 and by_player[0]["now_cost"] != by_player[1]["now_cost"],
          str(by_player))
    check("only the changed players were appended", len(rows) == N_PLAYERS + 2)
    check("news with a comma in it round-trips intact",
          any(r["news"] == "Hamstring injury, out for three weeks" for r in rows))
    check("the monthly file is named by month",
          month_file.name.startswith("2026-08"), month_file.name)

    # A thin API response must never be appended to the one unrebuildable file.
    RESPONSES["/bootstrap-static/"] = {"elements": make_bootstrap()["elements"][:5]}
    before = month_file.read_bytes()
    check("a thin bootstrap exits non-zero", run(build_snapshots) != 0)
    check("a thin bootstrap appends nothing", month_file.read_bytes() == before)
    return d


def test_player_history():
    print("\n[6] player_history is append-only, idempotent and backfillable")
    scratch()
    load_api(completed_gws=3)

    check("first run captures every completed gameweek", run(build_player_history) == 0)
    rows = read_csv_like_a_consumer("data/player_history.csv")
    check("one row per player per completed gameweek",
          len(rows) == 3 * N_PLAYERS, f"got {len(rows)}")
    check("gameweeks 1-3 are present", {r["gw"] for r in rows} == {"1", "2", "3"})
    check("opponent and venue resolved",
          all(r["opponent_team"] and r["was_home"] in ("true", "false") for r in rows))
    check("fixture_id populated", all(r["fixture_id"] for r in rows))

    # Re-running the same gameweeks must be a no-op.
    before = pathlib.Path("data/player_history.csv").read_bytes()
    run(build_player_history)
    rows2 = read_csv_like_a_consumer("data/player_history.csv")
    check("re-running produces no duplicate rows", len(rows2) == len(rows))
    check("existing rows are byte-identical apart from the refreshed header",
          [ln for ln in before.decode().split("\n") if not ln.startswith("#")]
          == [ln for ln in pathlib.Path("data/player_history.csv")
              .read_text().split("\n") if not ln.startswith("#")])

    # A new gameweek completes: only that one is appended.
    load_api(completed_gws=4)
    run(build_player_history)
    rows3 = read_csv_like_a_consumer("data/player_history.csv")
    check("a newly completed gameweek is appended", len(rows3) == 4 * N_PLAYERS)
    check("still no duplicates",
          len({(r["gw"], r["player_id"]) for r in rows3}) == len(rows3))

    # --backfill-from 1 against an empty file rebuilds the whole panel.
    pathlib.Path("data/player_history.csv").unlink()
    check("--backfill-from 1 exits 0",
          run(build_player_history, ["--backfill-from", "1"]) == 0)
    rebuilt = read_csv_like_a_consumer("data/player_history.csv")
    check("--backfill-from 1 reconstructs every gameweek",
          len(rebuilt) == 4 * N_PLAYERS)
    check("--backfill-from 1 produces no duplicates",
          len({(r["gw"], r["player_id"]) for r in rebuilt}) == len(rebuilt))
    check("--backfill-from 1 matches the incrementally built panel",
          {(r["gw"], r["player_id"], r["total_points"]) for r in rebuilt}
          == {(r["gw"], r["player_id"], r["total_points"]) for r in rows3})


def test_stale_flag():
    print("\n[7] a component that stops running surfaces as stale in meta.json")
    scratch()
    load_api()
    run(build_fpl)
    meta = json.loads(pathlib.Path("data/meta.json").read_text())
    check("a fresh mirror is not stale", meta["stale"] is False)

    status = json.loads(pathlib.Path("data/build_status.json").read_text())
    status["build_snapshots"] = {
        "last_run_at": "2026-08-01T00:00:00+00:00",
        "expected_interval_minutes": 60,
        "warnings": ["a warning raised by another process"],
    }
    pathlib.Path("data/build_status.json").write_text(json.dumps(status))
    run(build_fpl)

    meta = json.loads(pathlib.Path("data/meta.json").read_text())
    check("a component that has not run recently sets stale=true", meta["stale"] is True)
    check("the stale component is named in warnings[]",
          any("build_snapshots" in w and "STALE" in w for w in meta["warnings"]))
    check("another process's warnings reach meta.json",
          any("a warning raised by another process" in w for w in meta["warnings"]))


def test_squad():
    print("\n[9] squad.json gains transfers, past seasons and mini-leagues")
    scratch()
    load_api(completed_gws=2)
    tid = build_squad.TEAM_ID
    RESPONSES[f"/entry/{tid}/"] = {
        "name": "Test XI", "player_first_name": "O", "player_last_name": "M",
        "summary_overall_points": 120, "summary_overall_rank": 500000,
        "last_deadline_bank": 5, "last_deadline_value": 1003,
        "leagues": {"classic": [
            {"id": 314, "name": "Overall", "league_type": "s", "entry_rank": 500000},
            {"id": 99, "name": "Work league", "league_type": "x", "entry_rank": 3,
             "entry_last_rank": 5},
        ]},
    }
    RESPONSES[f"/entry/{tid}/history/"] = {
        "current": [{"event": 1, "points": 60, "event_transfers": 0},
                    {"event": 2, "points": 60, "event_transfers": 1}],
        "chips": [{"name": "wildcard", "event": 2}],
        "past": [{"season_name": "2025/26", "total_points": 2100, "rank": 250000}],
    }
    RESPONSES[f"/entry/{tid}/transfers/"] = [
        {"element_in": 5, "element_in_cost": 75, "element_out": 9,
         "element_out_cost": 70, "event": 2, "time": "2026-08-28T10:00:00Z"},
    ]
    RESPONSES["/leagues-classic/99/standings/"] = {
        "standings": {"results": [
            {"rank": 1, "last_rank": 2, "entry": 111, "entry_name": "Rival",
             "player_name": "R", "event_total": 70, "total": 130},
            {"rank": 3, "last_rank": 5, "entry": int(tid), "entry_name": "Test XI",
             "player_name": "O M", "event_total": 60, "total": 120},
        ]},
    }
    RESPONSES[f"/entry/{tid}/event/2/picks/"] = {
        "active_chip": None,
        "picks": [{"element": i, "position": i, "multiplier": 2 if i == 1 else 1,
                   "is_captain": i == 1, "is_vice_captain": i == 2} for i in range(1, 16)],
    }

    check("build_squad exits 0", run(build_squad) == 0)
    squad = json.loads(pathlib.Path("data/squad.json").read_text())

    check("the selling-price caveat is preserved verbatim",
          any("SELLING PRICES: not available." in n and
              "authenticated FPL session" in n for n in squad["notes"]))
    check("notes[] still carries the free-transfer estimate warning",
          any(n.startswith("FREE TRANSFERS: ESTIMATE ONLY") for n in squad["notes"]))
    check("picks are still resolved to names and teams",
          len(squad["picks"]) == 15 and squad["picks"][0]["name"] == "Player1"
          and squad["captain"] == "Player1")
    check("transfer history is present and both sides resolved",
          len(squad["transfers"]) == 1
          and squad["transfers"][0]["in"]["name"] == "Player5"
          and squad["transfers"][0]["out"]["cost_at_transfer"] == 7.0)
    check("past seasons are present",
          squad["past_seasons"] == [{"season": "2025/26", "total_points": 2100,
                                     "overall_rank": 250000}])
    check("only the mini-league is fetched, not the global one",
          len(squad["mini_leagues"]) == 1 and squad["mini_leagues"][0]["id"] == 99)
    check("my own row in the standings is flagged",
          any(r["is_me"] for r in squad["mini_leagues"][0]["standings"]))
    check("chips_used survives", squad["chips_used"] == [{"name": "wildcard", "event": 2}])


def test_no_secrets():
    """
    A literal scan for the shapes a leaked credential takes in this repo.

    Assembled from fragments so this file does not trip its own scan — it is the
    one place in the tree where those strings legitimately appear.
    """
    print("\n[8] nothing secret is committed")
    os.chdir(ROOT)
    # Each pattern requires a plausible *value*, not just the name of a variable.
    # "ODDS_API_KEY=..." in the README is documentation; a 32-character hex string
    # after the equals sign is a leak.
    patterns = [
        (r"ODDS_API" + r"_KEY\s*[=:]\s*[A-Za-z0-9]{8,}", "an API key inline"),
        (r"api" + r"Key=[A-Za-z0-9]{8,}", "a live key in a URL"),
        (r"Auth" + r"orization\s*[=:]\s*[\"']?\w", "an authorization header"),
        (r"pl_pro" + r"file", "an authenticated FPL session cookie"),
        (r"csrf" + r"token\s*[=:]\s*\S", "a session CSRF token"),
    ]
    bad = []
    for p in ROOT.rglob("*"):
        if ".git/" in str(p) or p.resolve() == pathlib.Path(__file__).resolve():
            continue
        if p.is_file() and p.suffix in (".py", ".yml", ".yaml", ".md", ".csv", ".json"):
            text = p.read_text(encoding="utf-8", errors="replace")
            bad += [f"{p.relative_to(ROOT)}: {what}"
                    for rx, what in patterns if re.search(rx, text)]
    check("no credentials or session tokens in the tree", not bad, str(bad))


def main():
    here = os.getcwd()
    try:
        test_existing_contract()
        test_new_columns_and_files()
        test_malformed_response_is_non_destructive()
        test_schema_drift_warns()
        test_snapshots()
        test_player_history()
        test_stale_flag()
        test_squad()
        test_no_secrets()
    finally:
        os.chdir(here)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  FAILED: {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()

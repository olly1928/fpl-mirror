#!/usr/bin/env python3
"""
build_player_history.py — the per-gameweek panel, appended one gameweek at a time.

Output: data/player_history.csv, one row per player per completed gameweek.

This is the file that makes real form analysis possible from about GW4 onward.
players.csv only ever carries a season-to-date cumulative total, which cannot
distinguish a player who scored six in August from one who scored six in
December.

Source: /api/event/{id}/live/, deliberately, rather than
/api/element-summary/{player_id}/. One call returns every player's stats for a
gameweek; the per-player alternative would be roughly 600 calls for the same
data.

Trigger: a gameweek is captured once bootstrap-static reports data_checked=true
for it, which is FPL's own signal that bonus points and stats are final.

Idempotent by construction: gameweeks already present in the file are skipped,
so re-running is a no-op and no data row is ever rewritten. Nothing here is
time-critical — the live endpoint serves historical gameweeks indefinitely, so
`--backfill-from 1` reconstructs the whole panel from scratch.
"""

import argparse
import csv
import io
import pathlib
import sys

from fpl_common import (
    DATA, api_required, atomic_write, check_fields, die, now_iso, record_status,
)

OUT = DATA / "player_history.csv"

COLS = [
    "gw", "player_id", "minutes", "starts", "total_points", "goals_scored",
    "assists", "clean_sheets", "goals_conceded", "own_goals", "penalties_saved",
    "penalties_missed", "yellow_cards", "red_cards", "saves", "bonus", "bps",
    "influence", "creativity", "threat", "ict_index",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "defensive_contribution",
    "fixture_id", "opponent_team", "was_home",
]

# The stats block on each element of the live payload. Everything from
# 'minutes' through 'defensive_contribution' is read straight out of it.
STAT_COLS = COLS[2:-3]

EXPECTED_LIVE_STAT_FIELDS = list(STAT_COLS)

MIN_PLAUSIBLE_PLAYERS = 100


def header_block(gameweeks, rows):
    """
    The comment block, rebuilt on every append.

    Only the header moves — every data row below it is carried through byte for
    byte. Refreshing it is what keeps the file's own fetched_at honest without
    touching history.
    """
    span = ",".join(str(g) for g in gameweeks) if gameweeks else "none"
    return [
        f"# fpl-mirror player_history — gameweeks={span} rows={rows} fetched={now_iso()}",
        "# One row per player per completed gameweek, appended as the season runs.",
        "# Source: /api/event/{id}/live/. A gameweek lands once FPL sets data_checked=true.",
        "# opponent_team is the FPL short code. In a double gameweek fixture_id, "
        "opponent_team and was_home carry both entries joined by '|', and every other "
        "column is the gameweek total across them.",
        ",".join(COLS),
    ]


def read_existing(path):
    """
    Return (data rows as raw text lines, set of gameweeks already captured).

    Comment lines and the column header are dropped — they get rebuilt on write.
    Data lines are returned verbatim so an append never reformats history.
    """
    if not path.exists():
        return [], set()

    lines = [ln for ln in path.read_text(encoding="utf-8").split("\n")
             if ln and not ln.startswith("#")]
    if lines and lines[0].startswith("gw,"):
        lines = lines[1:]

    seen = set()
    for row in csv.reader(io.StringIO("\n".join(lines))):
        if row:
            seen.add(row[0])
    return lines, seen


def fixture_index(fixtures):
    """fixture id -> (home team id, away team id)."""
    return {f["id"]: (f["team_h"], f["team_a"]) for f in fixtures if f.get("id")}


def opponents(explain, player_team, fx_index, teams):
    """
    Resolve a player's fixtures in a gameweek into ids, opponents and venues.

    A double gameweek gives more than one entry, so all three come back as
    '|'-joined strings. Bootstrap only knows a player's *current* club, so if
    neither side of a fixture matches — a mid-season transfer, most likely —
    the opponent and venue are left blank rather than guessed at.

    Returns (fixture_ids, opponent_codes, was_home, unresolved_count).
    """
    ids, opp, home, unresolved = [], [], [], 0
    for entry in explain or []:
        fid = entry.get("fixture")
        if fid is None:
            continue
        ids.append(str(fid))
        pair = fx_index.get(fid)
        if pair and player_team == pair[0]:
            opp.append(teams.get(pair[1], ""))
            home.append("true")
        elif pair and player_team == pair[1]:
            opp.append(teams.get(pair[0], ""))
            home.append("false")
        else:
            opp.append("")
            home.append("")
            unresolved += 1
    return "|".join(ids), "|".join(opp), "|".join(home), unresolved


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--backfill-from", type=int, default=1, metavar="GW",
                    help="lowest gameweek to consider (default 1). The normal run is "
                         "already self-healing — any completed gameweek missing from "
                         "the file is fetched — so this only narrows the walk.")
    ap.add_argument("--out", default=str(OUT),
                    help="write somewhere other than data/player_history.csv, for "
                         "rebuilding into a new file rather than mutating this one")
    args = ap.parse_args()

    out = pathlib.Path(args.out)

    boot = api_required("/bootstrap-static/", "bootstrap-static", cache_ttl=600)
    events = boot.get("events") or []
    elements = boot.get("elements") or []
    if not events or not elements:
        die("bootstrap-static returned no events/elements — cannot decide what to capture")

    teams = {t["id"]: t["short_name"] for t in (boot.get("teams") or [])}
    player_team = {e["id"]: e["team"] for e in elements}

    completed = sorted(e["id"] for e in events
                       if e.get("data_checked") and e.get("id") is not None)
    body, seen = read_existing(out)
    todo = [gw for gw in completed if gw >= args.backfill_from and str(gw) not in seen]
    captured = sorted({int(g) for g in seen if g.isdigit()})

    if not todo:
        record_status("build_player_history", expected_interval_minutes=60,
                      warnings=[], captured_gameweeks=captured, last_rows_written=0)
        print(f"OK — nothing to do. {len(completed)} completed gameweek(s), "
              f"{len(captured)} already captured.")
        return

    fixtures = api_required("/fixtures/", "fixtures", cache_ttl=600)
    fx_index = fixture_index(fixtures)

    warnings, new_rows, unresolved = [], [], 0
    for gw in todo:
        live = api_required(f"/event/{gw}/live/", f"live data for GW{gw}")
        live_elements = live.get("elements") or []
        if len(live_elements) < MIN_PLAUSIBLE_PLAYERS:
            die(f"GW{gw} live returned {len(live_elements)} elements, expected at least "
                f"{MIN_PLAUSIBLE_PLAYERS}. Refusing to append a partial gameweek.")

        warnings += check_fields([e.get("stats") or {} for e in live_elements],
                                 EXPECTED_LIVE_STAT_FIELDS, f"event/{gw}/live.stats")

        gw_rows = []
        for e in live_elements:
            pid = e.get("id")
            st = e.get("stats") or {}
            fids, opp, home, n = opponents(
                e.get("explain"), player_team.get(pid), fx_index, teams)
            unresolved += n
            gw_rows.append(
                [gw, pid]
                + ["" if st.get(c) is None else st.get(c) for c in STAT_COLS]
                + [fids, opp, home]
            )
        gw_rows.sort(key=lambda r: r[1])
        new_rows += gw_rows
        print(f"  GW{gw}: {len(gw_rows)} rows")

    if unresolved:
        warnings.append(
            f"{unresolved} fixture(s) could not be resolved to an opponent — most likely "
            "players who changed club mid-season, since bootstrap only reports their "
            "current one. opponent_team/was_home are blank for those."
        )

    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(new_rows)
    body += buf.getvalue().rstrip("\n").split("\n")

    captured = sorted(set(captured) | set(todo))
    atomic_write(out, "\n".join(header_block(captured, len(body)) + body) + "\n")

    record_status("build_player_history", expected_interval_minutes=60,
                  warnings=sorted(set(warnings)), captured_gameweeks=captured,
                  last_rows_written=len(new_rows))

    print(f"OK — appended GW{todo} ({len(new_rows)} rows) to {out}")
    for w in sorted(set(warnings)):
        print(f"  ! {w}", file=sys.stderr)


if __name__ == "__main__":
    main()

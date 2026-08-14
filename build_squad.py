#!/usr/bin/env python3
"""
build_squad.py — mirrors my FPL squad state into data/squad.json

Public FPL endpoints only. No API key, no login, no secrets.
Runs alongside build_fpl.py in the same workflow; touches nothing it writes.

Pre-season (before the GW1 deadline) the picks endpoint does not exist yet.
That is expected — the script writes a file saying so rather than failing.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

TEAM_ID = os.environ.get("FPL_TEAM_ID", "790889")
BASE = "https://fantasy.premierleague.com/api"
OUT = "data/squad.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fpl-mirror/1.0)"}

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def try_get(path):
    try:
        return get(path)
    except Exception as exc:
        print(f"  ! {path} -> {exc}")
        return None


def estimate_free_transfers(history):
    """
    Best-effort only. FPL does not publish free-transfer counts.

    Rule modelled: 1 FT earned per gameweek, bankable up to 5.
    Gameweeks played under a wildcard or free hit do not consume FTs.
    Hits (event_transfers_cost > 0) mean transfers exceeded the FTs held.

    Anything this returns must be confirmed against the FPL site before acting.
    """
    if not history:
        return None, "no history yet"

    chip_events = {
        c.get("event"): c.get("name")
        for c in history.get("chips", [])
        if c.get("name") in ("wildcard", "freehit")
    }

    ft = 1
    for ev in history.get("current", []):
        gw = ev.get("event")
        if gw == 1:
            ft = 1
            continue
        ft = min(5, ft + 1)
        if gw in chip_events:
            continue
        ft = max(0, ft - ev.get("event_transfers", 0))
    return ft, "estimated from transfer history — confirm on the FPL site"


def main():
    os.makedirs("data", exist_ok=True)
    print(f"Mirroring squad for entry {TEAM_ID}")

    boot = try_get("/bootstrap-static/")
    if not boot:
        print("FATAL: bootstrap-static unreachable")
        sys.exit(1)

    elements = {e["id"]: e for e in boot["elements"]}
    teams = {t["id"]: t["short_name"] for t in boot["teams"]}

    current_event = None
    next_event = None
    for ev in boot.get("events", []):
        if ev.get("is_current"):
            current_event = ev["id"]
        if ev.get("is_next"):
            next_event = ev["id"]

    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "team_id": int(TEAM_ID),
        "current_event": current_event,
        "next_event": next_event,
        "notes": [],
    }

    entry = try_get(f"/entry/{TEAM_ID}/")
    if entry:
        out["entry_name"] = entry.get("name")
        out["manager"] = f"{entry.get('player_first_name','')} {entry.get('player_last_name','')}".strip()
        out["overall_points"] = entry.get("summary_overall_points")
        out["overall_rank"] = entry.get("summary_overall_rank")
        if entry.get("last_deadline_bank") is not None:
            out["bank"] = entry["last_deadline_bank"] / 10.0
        if entry.get("last_deadline_value") is not None:
            out["squad_value"] = entry["last_deadline_value"] / 10.0

    history = try_get(f"/entry/{TEAM_ID}/history/")
    if history:
        out["chips_used"] = [
            {"name": c.get("name"), "event": c.get("event")}
            for c in history.get("chips", [])
        ]
        out["gameweek_history"] = [
            {
                "event": e.get("event"),
                "points": e.get("points"),
                "overall_rank": e.get("overall_rank"),
                "transfers": e.get("event_transfers"),
                "hit": e.get("event_transfers_cost"),
                "bench_points": e.get("points_on_bench"),
            }
            for e in history.get("current", [])
        ]
        ft, ft_note = estimate_free_transfers(history)
        out["free_transfers_estimate"] = ft
        out["free_transfers_confidence"] = ft_note

    # Picks only exist once a gameweek has started.
    picks_event = current_event
    picks = try_get(f"/entry/{TEAM_ID}/event/{picks_event}/picks/") if picks_event else None

    if picks:
        out["picks_event"] = picks_event
        out["active_chip"] = picks.get("active_chip")
        rows = []
        for p in picks.get("picks", []):
            el = elements.get(p["element"], {})
            rows.append(
                {
                    "slot": p.get("position"),
                    "element": p["element"],
                    "name": el.get("web_name"),
                    "team": teams.get(el.get("team")),
                    "pos": POS.get(el.get("element_type")),
                    "now_cost": (el.get("now_cost") or 0) / 10.0,
                    "multiplier": p.get("multiplier"),
                    "is_captain": p.get("is_captain"),
                    "is_vice_captain": p.get("is_vice_captain"),
                    "status": el.get("status"),
                    "chance_next_round": el.get("chance_of_playing_next_round"),
                    "news": (el.get("news") or "").strip(),
                }
            )
        out["picks"] = rows
    else:
        out["picks"] = None
        out["notes"].append(
            "No picks available — pre-season, or the gameweek has not started. "
            "Squad must be pasted by hand until GW1 kicks off."
        )

    out["selling_prices"] = None
    out["notes"].append(
        "Selling prices are NOT in the public API — they need an authenticated "
        "session. Values above are current market prices. Any transfer maths "
        "using them is approximate; ask me to paste selling prices for anything tight."
    )

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    n = len(out["picks"]) if out.get("picks") else 0
    print(f"Wrote {OUT} — {n} picks, bank {out.get('bank')}, "
          f"value {out.get('squad_value')}, FT est {out.get('free_transfers_estimate')}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
build_squad.py — mirrors my FPL squad state into data/squad.json

Public FPL endpoints only. No API key, no login, no secrets.
Runs alongside build_fpl.py in the same workflow; touches nothing it writes.

Entry ID comes from FPL_TEAM_ID, defaulting to 790889.

Pre-season (before the GW1 deadline) the picks endpoint does not exist yet and
returns 404. That is expected, not a failure: the script writes "picks": null
with a note saying why and exits 0. Everything else is required — if
bootstrap-static, /entry/ or /history/ cannot be read the script exits 1 rather
than writing a half-empty file.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

TEAM_ID = os.environ.get("FPL_TEAM_ID", "790889").strip()
BASE = "https://fantasy.premierleague.com/api"
OUT = "data/squad.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fpl-mirror/1.0)"}

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# FPL's own single-letter availability codes, spelled out for a cold reader.
STATUS_WORDS = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "not in squad / ineligible",
}

MAX_BANKED_FREE_TRANSFERS = 5


def die(msg):
    """Fail loudly. Never leave a partial file behind."""
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def get(path):
    """GET a JSON endpoint. Raises on any failure — callers decide what is fatal."""
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_required(path, what):
    """Fetch something the file is useless without."""
    try:
        return get(path)
    except urllib.error.HTTPError as exc:
        die(f"{what} — {BASE}{path} returned HTTP {exc.code} {exc.reason}")
    except Exception as exc:
        die(f"{what} — could not read {BASE}{path}: {exc}")


def get_picks(event):
    """
    Fetch the picks for a gameweek.

    Returns (payload, note). A 404 means the gameweek has not started yet, which
    is normal pre-season and is reported rather than raised. Any other failure is
    fatal — a network blip must not masquerade as "no team picked".
    """
    path = f"/entry/{TEAM_ID}/event/{event}/picks/"
    try:
        return get(path), None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, (
                f"The picks endpoint for GW{event} returned 404. The FPL API does not "
                "publish picks until a gameweek has actually started, so this is the "
                "expected pre-season / pre-kickoff response, not an error."
            )
        die(f"picks for GW{event} — {BASE}{path} returned HTTP {exc.code} {exc.reason}")
    except Exception as exc:
        die(f"picks for GW{event} — could not read {BASE}{path}: {exc}")


def estimate_free_transfers(history):
    """
    Best-effort estimate. FPL does not publish free-transfer counts anywhere.

    Rules modelled:
      * one free transfer is earned per gameweek,
      * they bank up to a maximum of 5,
      * transfers made in a gameweek where a wildcard or free hit was active do
        not consume them,
      * a manager's first gameweek is squad selection, not transfers, so nothing
        is spent there.

    Order matters: you spend the transfers you hold, then earn next week's. Doing
    it the other way round loses the earned transfer whenever you are sitting on
    the cap.

    The return value is the estimate for the *next* gameweek, and must be
    confirmed against the FPL site before acting on it.
    """
    weeks = history.get("current") or []
    if not weeks:
        return 1, (
            "ESTIMATE — no gameweeks played yet. GW1 is unlimited squad selection, "
            "so 1 free transfer going into GW2. Confirm on the FPL site."
        )

    chip_weeks = {
        c.get("event")
        for c in (history.get("chips") or [])
        if c.get("name") in ("wildcard", "freehit")
    }

    ft = 1  # earned after the manager's first gameweek
    for week in weeks[1:]:  # weeks[0] is squad selection, no transfers charged
        if week.get("event") not in chip_weeks:
            ft = max(0, ft - (week.get("event_transfers") or 0))
        ft = min(MAX_BANKED_FREE_TRANSFERS, ft + 1)

    return ft, (
        "ESTIMATE ONLY — the FPL API does not expose free transfers. Derived from "
        "transfer history assuming 1 earned per gameweek, banked to a maximum of "
        f"{MAX_BANKED_FREE_TRANSFERS}, with wildcard/free-hit weeks not consuming any. "
        "Confirm on the FPL site before making transfer decisions."
    )


def describe_pick(pick, element, teams):
    """Resolve one pick's element ID into something readable with no other context."""
    status = element.get("status")
    return {
        "slot": pick.get("position"),
        "role": "starting XI" if (pick.get("position") or 99) <= 11 else "bench",
        "element": pick.get("element"),
        "name": element.get("web_name"),
        "full_name": f"{element.get('first_name', '')} {element.get('second_name', '')}".strip(),
        "team": teams.get(element.get("team")),
        "pos": POS.get(element.get("element_type")),
        "now_cost": (element.get("now_cost") or 0) / 10.0,
        "multiplier": pick.get("multiplier"),
        "is_captain": pick.get("is_captain"),
        "is_vice_captain": pick.get("is_vice_captain"),
        "status": status,
        "status_meaning": STATUS_WORDS.get(status, status),
        "chance_of_playing_next_round": element.get("chance_of_playing_next_round"),
        "news": (element.get("news") or "").strip(),
    }


def main():
    os.makedirs("data", exist_ok=True)
    print(f"Mirroring squad for entry {TEAM_ID}")

    boot = get_required("/bootstrap-static/", "bootstrap-static")
    if not boot.get("elements") or not boot.get("teams"):
        die("bootstrap-static came back without elements/teams — refusing to write a stub file")

    elements = {e["id"]: e for e in boot["elements"]}
    teams = {t["id"]: t["short_name"] for t in boot["teams"]}

    current_event = next((e["id"] for e in boot.get("events", []) if e.get("is_current")), None)
    next_ev = next((e for e in boot.get("events", []) if e.get("is_next")), None)

    entry = get_required(f"/entry/{TEAM_ID}/", "entry summary")
    history = get_required(f"/entry/{TEAM_ID}/history/", "entry history")

    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "team_id": int(TEAM_ID),
        "entry_name": entry.get("name"),
        "manager": f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip(),
        "current_event": current_event,
        "next_event": next_ev["id"] if next_ev else None,
        "next_deadline": next_ev.get("deadline_time") if next_ev else None,
        "preseason": current_event is None,
        "overall_points": entry.get("summary_overall_points"),
        "overall_rank": entry.get("summary_overall_rank"),
        # Both are in tenths of a million in the API.
        "bank": (entry["last_deadline_bank"] / 10.0)
        if entry.get("last_deadline_bank") is not None else None,
        "squad_value": (entry["last_deadline_value"] / 10.0)
        if entry.get("last_deadline_value") is not None else None,
        "notes": [],
    }

    out["chips_used"] = [
        {"name": c.get("name"), "event": c.get("event")}
        for c in (history.get("chips") or [])
    ]
    out["gameweek_history"] = [
        {
            "event": w.get("event"),
            "points": w.get("points"),
            "overall_rank": w.get("overall_rank"),
            "rank": w.get("rank"),
            "transfers": w.get("event_transfers"),
            "transfer_cost": w.get("event_transfers_cost"),
            "points_on_bench": w.get("points_on_bench"),
        }
        for w in (history.get("current") or [])
    ]

    ft, ft_note = estimate_free_transfers(history)
    out["free_transfers_estimate"] = ft
    out["free_transfers_estimate_note"] = ft_note
    out["notes"].append(f"FREE TRANSFERS: {ft_note}")

    # Picks only exist once a gameweek has started, so there is nothing to ask for
    # while current_event is still None.
    if current_event is None:
        out["picks_event"] = None
        out["active_chip"] = None
        out["picks"] = None
        note = (
            "No picks available. It is pre-season: no gameweek is current yet, so the "
            f"FPL API has no picks to publish for entry {TEAM_ID}. The squad will appear "
            f"here automatically once GW{out['next_event']} starts"
            + (f" (deadline {out['next_deadline']})." if out["next_deadline"] else ".")
        )
        out["notes"].append(note)
        print(f"  {note}")
    else:
        picks, note = get_picks(current_event)
        out["picks_event"] = current_event
        if picks is None:
            out["active_chip"] = None
            out["picks"] = None
            out["notes"].append(note)
            print(f"  {note}")
        else:
            out["active_chip"] = picks.get("active_chip")
            rows = [
                describe_pick(p, elements.get(p["element"], {}), teams)
                for p in picks.get("picks", [])
            ]
            if not rows:
                die(f"picks for GW{current_event} came back empty — refusing to write a squad with no players")
            out["picks"] = rows
            captain = next((r["name"] for r in rows if r["is_captain"]), None)
            vice = next((r["name"] for r in rows if r["is_vice_captain"]), None)
            out["captain"] = captain
            out["vice_captain"] = vice

    out["selling_prices"] = None
    out["notes"].append(
        "SELLING PRICES: not available. They require an authenticated FPL session, "
        "which this mirror deliberately does not have. Every 'now_cost' above is the "
        "CURRENT MARKET PRICE, not what I would receive for selling. A player who has "
        "risen since I bought them sells for purchase price plus half the rise, rounded "
        "down, so market price overstates my sale proceeds. Treat any transfer or budget "
        "maths built on these numbers as approximate, and ask me to paste the real "
        "selling prices before committing to anything tight."
    )
    out["notes"].append(
        "All prices and squad_value are in millions. bank and squad_value are as at the "
        "last deadline, so they do not move with in-week price changes."
    )

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    n = len(out["picks"]) if out.get("picks") else 0
    print(
        f"Wrote {OUT} — {n} picks, bank {out.get('bank')}, value {out.get('squad_value')}, "
        f"FT est {out.get('free_transfers_estimate')} (estimate)"
    )


if __name__ == "__main__":
    main()

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

Beyond the squad itself this also mirrors the full transfer history, previous
seasons, chip usage and standings for every mini-league the entry is in, so
rank-vs-rivals is answerable from the feed. Those four are enrichment: if one of
them fails the file is still written, with the reason recorded in notes[].
"""

import json
import os
import sys
import urllib.error
from datetime import datetime, timezone

from fpl_common import api, record_status

TEAM_ID = os.environ.get("FPL_TEAM_ID", "790889").strip()
BASE = "https://fantasy.premierleague.com/api"
OUT = "data/squad.json"

# Classic leagues come in two flavours: 'x' is a mini-league someone created,
# 's' is a system league (Overall, your country, your favourite club). System
# leagues have millions of entries and no rival worth tracking, so only the
# mini-leagues are worth a standings call.
MINI_LEAGUE_TYPE = "x"
MAX_LEAGUES = 10
MAX_STANDINGS_ROWS = 50

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


def get(path, cache_ttl=0):
    """
    GET a JSON endpoint. Raises on any failure — callers decide what is fatal.

    Retries and the per-run request ceiling live in fpl_common, so every builder
    in this repo backs off the same way. A 404 still comes straight back as an
    HTTPError, which is what get_picks relies on.
    """
    return api(path, cache_ttl=cache_ttl)


def get_optional(path, what, default=None):
    """
    Fetch something worth having but not worth failing the whole file over.

    Transfer history, past seasons and league standings are all enrichment. If
    one of them is down, a squad.json without it still answers every question the
    consumer actually needs to make a transfer, so the failure is noted in
    notes[] rather than raised.
    """
    try:
        return get(path), None
    except urllib.error.HTTPError as exc:
        return default, f"{what} unavailable — {BASE}{path} returned HTTP {exc.code} {exc.reason}"
    except Exception as exc:
        return default, f"{what} unavailable — could not read {BASE}{path}: {exc}"


def get_required(path, what, cache_ttl=0):
    """Fetch something the file is useless without."""
    try:
        return get(path, cache_ttl=cache_ttl)
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


def describe_transfer(t, elements, teams):
    """One transfer, with both players resolved so the row reads on its own."""
    def side(element_id, cost):
        e = elements.get(element_id, {})
        return {
            "element": element_id,
            "name": e.get("web_name"),
            "team": teams.get(e.get("team")),
            "pos": POS.get(e.get("element_type")),
            # Costs in the transfers feed are in tenths of a million, and they are
            # the prices at the moment of the transfer, not today's.
            "cost_at_transfer": (cost / 10.0) if cost is not None else None,
        }

    return {
        "event": t.get("event"),
        "time": t.get("time"),
        "in": side(t.get("element_in"), t.get("element_in_cost")),
        "out": side(t.get("element_out"), t.get("element_out_cost")),
    }


def mini_leagues(entry, notes):
    """
    Standings for the mini-leagues this entry is in, so rank-vs-rivals is
    queryable rather than something to go and look up on the website.

    Capped at MAX_LEAGUES leagues and MAX_STANDINGS_ROWS rows each: the point is
    the rivals near the top, and an unbounded fetch here is how a quiet hourly
    job turns into a rate-limit problem.
    """
    classic = ((entry.get("leagues") or {}).get("classic") or [])
    mine = [lg for lg in classic if lg.get("league_type") == MINI_LEAGUE_TYPE]
    if len(mine) > MAX_LEAGUES:
        notes.append(
            f"MINI-LEAGUES: {len(mine)} found, only the first {MAX_LEAGUES} were fetched."
        )
        mine = mine[:MAX_LEAGUES]

    out = []
    for lg in mine:
        payload, err = get_optional(
            f"/leagues-classic/{lg['id']}/standings/", f"standings for league {lg['id']}"
        )
        if err:
            notes.append(err)
            out.append({"id": lg.get("id"), "name": lg.get("name"),
                        "my_rank": lg.get("entry_rank"), "standings": None,
                        "standings_error": err})
            continue

        results = ((payload or {}).get("standings") or {}).get("results") or []
        truncated = len(results) > MAX_STANDINGS_ROWS
        out.append({
            "id": lg.get("id"),
            "name": lg.get("name"),
            "my_rank": lg.get("entry_rank"),
            "my_last_rank": lg.get("entry_last_rank"),
            "entries": len(results),
            "standings_truncated_to": MAX_STANDINGS_ROWS if truncated else None,
            "standings": [
                {
                    "rank": r.get("rank"),
                    "last_rank": r.get("last_rank"),
                    "entry": r.get("entry"),
                    "entry_name": r.get("entry_name"),
                    "manager": r.get("player_name"),
                    "event_total": r.get("event_total"),
                    "total": r.get("total"),
                    "is_me": str(r.get("entry")) == TEAM_ID,
                }
                for r in results[:MAX_STANDINGS_ROWS]
            ],
        })
    return out


def main():
    os.makedirs("data", exist_ok=True)
    print(f"Mirroring squad for entry {TEAM_ID}")

    boot = get_required("/bootstrap-static/", "bootstrap-static", cache_ttl=600)
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

    out["past_seasons"] = [
        {
            "season": p.get("season_name"),
            "total_points": p.get("total_points"),
            "overall_rank": p.get("rank"),
        }
        for p in (history.get("past") or [])
    ]

    # Full transfer history. The costs in it are the prices at the time of the
    # transfer, which is the only place a purchase price is recoverable without
    # an authenticated session — see the selling-prices note below.
    transfers, transfers_err = get_optional(
        f"/entry/{TEAM_ID}/transfers/", "transfer history", default=[]
    )
    if transfers_err:
        out["notes"].append(transfers_err)
    out["transfers"] = [describe_transfer(t, elements, teams) for t in (transfers or [])]

    out["mini_leagues"] = mini_leagues(entry, out["notes"])

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

    # Temp file then rename: an interrupted write must never replace a good
    # squad.json with a truncated one.
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, OUT)

    record_status(
        "build_squad",
        expected_interval_minutes=60,
        warnings=[n for n in out["notes"] if "unavailable" in n],
        picks=len(out["picks"]) if out.get("picks") else 0,
        transfers=len(out.get("transfers") or []),
        mini_leagues=len(out.get("mini_leagues") or []),
    )

    n = len(out["picks"]) if out.get("picks") else 0
    print(
        f"Wrote {OUT} — {n} picks, bank {out.get('bank')}, value {out.get('squad_value')}, "
        f"FT est {out.get('free_transfers_estimate')} (estimate), "
        f"{len(out.get('transfers') or [])} transfers, "
        f"{len(out.get('mini_leagues') or [])} mini-league(s)"
    )


if __name__ == "__main__":
    main()

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

Selling prices are DERIVED here rather than fetched. The endpoint that publishes
them needs an authenticated session, but the numbers themselves are a pure
function of purchase price and current price, and both are already in the feed —
see selling_price() and derive_selling_prices() below.
"""

import json
import os
import sys
import urllib.error
from datetime import datetime, timezone

from fpl_common import api, check_fields, record_status

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

# ---------------------------------------------------------------- schema
#
# The selling-price derivation reads three feeds, so all three go through the
# same drift guard the CSV builders use. A renamed element_in_cost would
# otherwise turn every purchase price into a silent fallback to the initial-squad
# branch — plausible-looking numbers that are wrong for anyone who has
# transferred, which is the worst failure this file can produce.

EXPECTED_TRANSFER_FIELDS = [
    "element_in", "element_in_cost", "element_out", "element_out_cost",
    "entry", "event", "time",
]
IGNORED_TRANSFER_FIELDS = []

# chips[] from entry history. Free Hit weeks have to be identifiable from here.
EXPECTED_CHIP_FIELDS = ["name", "event", "time"]
IGNORED_CHIP_FIELDS = ["chip_type", "id", "played_by_entry", "status"]

# The bootstrap element fields the initial-squad fallback depends on. Reported
# missing-only: build_fpl owns the new-field report for elements, and listing
# fifty bootstrap fields from here every hour would bury this.
EXPECTED_PRICE_ELEMENT_FIELDS = ["id", "now_cost", "cost_change_start"]

# The chip that makes a transfer temporary. Wildcard transfers are permanent and
# must be counted; a Free Hit squad is handed back at the end of the gameweek.
FREE_HIT = "freehit"


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


def selling_price(purchase, now_cost):
    """
    FPL's sell rule, in integer tenths of a million.

    A fall is absorbed in full — you take the whole loss. A rise is halved and
    rounded DOWN, so a player bought at 7.0 and now worth 7.3 sells for 7.1, not
    7.15 and not 7.2.

    Integers throughout deliberately. In floats, 0.1 is not 0.1, and rounding a
    half-tenth at the end of the sum lands on the wrong side often enough to
    matter — roughly every second odd-numbered rise.
    """
    if now_cost <= purchase:
        return now_cost
    return purchase + (now_cost - purchase) // 2


def free_hit_events(chips):
    """
    The gameweeks whose transfers must be ignored.

    A Free Hit squad exists for one gameweek and is handed straight back, but
    every one of its transfers stays in the transfer history looking exactly like
    a permanent purchase. Reading them as purchases prices fifteen players off a
    squad that was reverted a week later.

    Wildcard weeks are deliberately not in here: those transfers are permanent
    and their prices are the real purchase prices.
    """
    return {
        c.get("event")
        for c in (chips or [])
        if c.get("name") == FREE_HIT and c.get("event") is not None
    }


def latest_purchases(transfers, chips):
    """
    element_id -> (cost_in_tenths, event) for the most recent real purchase.

    Sorted by (event, time) ascending and overwritten as it goes, so a player
    bought, sold and bought again is priced off the second purchase. The feed
    arrives newest-first, so taking the first match would silently pick the
    oldest price for exactly the players whose price has moved most.
    """
    skip = free_hit_events(chips)
    out = {}
    for t in sorted((transfers or []),
                    key=lambda t: (t.get("event") or 0, t.get("time") or "")):
        if t.get("event") in skip:
            continue
        element_id, cost = t.get("element_in"), t.get("element_in_cost")
        if element_id is None or cost is None:
            continue
        out[element_id] = (cost, t.get("event"))
    return out


def derive_selling_prices(picks, elements, transfers, chips):
    """
    One selling price per pick, in integer tenths, with its provenance.

    Purchase price comes from one of two places, in this order:

      1. element_in_cost on the most recent non-Free-Hit transfer that brought
         the player in. That is literally what was paid.
      2. Failing that the player has been held since the initial squad, so the
         purchase price is the GW1 price: now_cost - cost_change_start.
         Pre-season prices are static, so the season-start price and the GW1
         deadline price are the same number.

    source records which branch was taken, because they are not equally certain:
    a transfer record is what happened, while the initial-squad figure carries
    the assumption that the player was never sold and re-bought outside the
    window the transfer feed covers.

    Returns (prices, warnings).
    """
    purchases = latest_purchases(transfers, chips)
    prices, no_cost_change, unpriced = {}, [], []

    for pick in picks:
        element_id = pick.get("element")
        element = elements.get(element_id) or {}
        now_cost = element.get("now_cost")
        if now_cost is None:
            # No current price means no selling price. Leaving the pick out is
            # the only honest answer, but a squad that silently prices fourteen
            # of fifteen players is a wrong total, so it has to be said.
            unpriced.append(str(element_id))
            continue

        if element_id in purchases:
            purchase, bought_event = purchases[element_id]
            source = "transfer"
        else:
            change = element.get("cost_change_start")
            if change is None:
                # Without it the GW1 price is unknowable. Assuming no movement is
                # the smallest wrong answer, but it must not pass unremarked.
                no_cost_change.append(str(element_id))
                change = 0
            purchase = now_cost - change
            bought_event = None
            source = "initial_squad"

        prices[str(element_id)] = {
            "now_cost": now_cost,
            "purchase": purchase,
            "selling": selling_price(purchase, now_cost),
            "source": source,
            "bought_event": bought_event,
        }

    warnings = []
    if no_cost_change:
        warnings.append(
            "bootstrap-static.elements: cost_change_start absent for player(s) "
            f"{', '.join(no_cost_change)}, so their initial-squad purchase price "
            "was derived assuming the price has not moved since GW1."
        )
    if unpriced:
        warnings.append(
            f"{len(unpriced)} of {len(picks)} picks have no now_cost in "
            f"bootstrap-static (player(s) {', '.join(unpriced)}), so they have no "
            "selling price and are missing from squad_selling_value and "
            "squad_market_value — both totals are short by that many players."
        )
    return prices, warnings


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
        # Filled in once picks and selling prices are known. Declared here so
        # they sit alongside bank in the written file rather than at the end.
        # Unlike bank and squad_value these three are in TENTHS of a million,
        # matching selling_prices below and the API's own units.
        "squad_selling_value": None,
        "squad_market_value": None,
        "available_budget": None,
        "notes": [],
    }

    # Warnings that belong in meta.json warnings[] rather than only in notes[]:
    # these are feed problems, not caveats about the data.
    warnings = []

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
    # transfer, which is where every purchase price below comes from.
    transfers, transfers_err = get_optional(
        f"/entry/{TEAM_ID}/transfers/", "transfer history", default=[]
    )
    if transfers_err:
        out["notes"].append(transfers_err)
    out["transfers"] = [describe_transfer(t, elements, teams) for t in (transfers or [])]

    # Drift guard over the three feeds the selling-price maths reads. An empty
    # transfer list or chip list is a legitimate state rather than drift — a
    # manager who has made no transfers has nothing to check — so those are only
    # checked when there is something to check. The elements check is
    # missing-fields-only: build_fpl owns the new-field report for that endpoint.
    if transfers:
        warnings += check_fields(transfers, EXPECTED_TRANSFER_FIELDS,
                                 "entry.transfers", ignore=IGNORED_TRANSFER_FIELDS)
    if history.get("chips"):
        warnings += check_fields(history["chips"], EXPECTED_CHIP_FIELDS,
                                 "entry.history.chips", ignore=IGNORED_CHIP_FIELDS)
    warnings += check_fields(boot["elements"], EXPECTED_PRICE_ELEMENT_FIELDS,
                             "bootstrap-static.elements", report_new=False)

    # An empty transfer feed outside pre-season is a feed problem, not an empty
    # squad, and it is a quiet one: every player would fall through to the
    # initial-squad branch and come out with a plausible wrong price. The entry's
    # own gameweek history counts transfers independently, so the two feeds can
    # be cross-checked — they only disagree when one of them is broken. A GW1
    # manager who genuinely has not transferred yet shows zero in both, and
    # warning about that every hour is how a warnings[] array stops being read.
    transfers_made = sum((w.get("event_transfers") or 0)
                         for w in (history.get("current") or []))
    transfer_feed_broken = bool(
        current_event is not None and not transfers and not transfers_err and transfers_made
    )
    if transfer_feed_broken:
        warnings.append(
            f"entry.transfers came back empty, but the entry's gameweek history records "
            f"{transfers_made} transfer(s) made. The transfer feed is not reporting what "
            "this entry actually did, so purchase prices cannot be trusted this run."
        )

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

    # ---- selling prices ------------------------------------------------
    #
    # The endpoint that publishes these needs an authenticated session. The
    # numbers do not: selling price is a pure function of purchase price and
    # current price, and both are in the feeds already fetched above.
    if not out["picks"]:
        out["selling_prices"] = None
        out["notes"].append(
            "SELLING PRICES: none, because there is no squad to price yet. "
            + ("It is pre-season — the picks endpoint publishes nothing until a "
               "gameweek has started, and an empty squad must not be turned into "
               "fifteen invented entries."
               if current_event is None else
               "The picks endpoint returned nothing for this gameweek, so there is "
               "no list of players to derive prices for.")
        )
    elif transfers_err or transfer_feed_broken:
        # Falling back to the initial-squad branch for everyone would price the
        # whole squad off GW1 and look entirely plausible while being wrong for
        # every player who has been transferred in. No number beats a wrong one.
        out["selling_prices"] = None
        note = (
            "SELLING PRICES: not derived. The transfer history "
            + ("could not be read this run"
               if transfers_err else
               f"came back empty while the gameweek history records {transfers_made} "
               "transfer(s), so it is not reporting what this entry did")
            + ", and without it every player would be treated as an initial-squad pick "
            "— plausible-looking numbers that are wrong for anyone transferred in. Use "
            "'now_cost' as an upper bound until the feed recovers."
        )
        out["notes"].append(note)
        warnings.append(note)
    else:
        prices, price_warnings = derive_selling_prices(
            out["picks"], elements, transfers, history.get("chips")
        )
        warnings += price_warnings
        out["selling_prices"] = prices

        out["squad_selling_value"] = sum(p["selling"] for p in prices.values())
        out["squad_market_value"] = sum(p["now_cost"] for p in prices.values())
        bank_tenths = entry.get("last_deadline_bank")
        if bank_tenths is None:
            out["notes"].append(
                "available_budget is null: the entry summary did not carry "
                "last_deadline_bank, so the bank could not be added to the squad's "
                "selling value."
            )
        else:
            out["available_budget"] = out["squad_selling_value"] + bank_tenths

        derived = sum(1 for p in prices.values() if p["source"] == "initial_squad")
        out["notes"].append(
            "SELLING PRICES: DERIVED, not fetched — and they are the numbers to do "
            "transfer maths with, not 'now_cost'. Purchase price comes from one of two "
            "sources, recorded per player in selling_prices[id].source: "
            "'transfer' means element_in_cost on the most recent non-Free-Hit transfer "
            "that brought the player in, which is literally what was paid; "
            "'initial_squad' means the player has been held since GW1 and the purchase "
            "price is bootstrap-static's now_cost minus cost_change_start, which is the "
            "GW1 price because pre-season prices are static. Selling price is then the "
            "full fall, or the purchase price plus half of any rise rounded down. "
            f"{len(prices) - derived} from a transfer record, {derived} derived from "
            "the initial squad — the latter assume the player was never sold and "
            "re-bought. Free Hit transfers are excluded; wildcard transfers count."
        )

    out["notes"].append(
        "UNITS: bank and squad_value are in millions. selling_prices, "
        "squad_selling_value, squad_market_value and available_budget are in TENTHS of "
        "a million (the API's own units), so 155 is 15.5m — bank in those units is "
        "bank * 10, and available_budget = squad_selling_value + bank * 10. "
        "bank and squad_value are as at the last deadline, so they do not move with "
        "in-week price changes."
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
        # notes[] carries caveats a consumer of squad.json should read; warnings[]
        # in meta.json is for things that have actually gone wrong with the feed.
        warnings=[n for n in out["notes"] if "unavailable" in n] + warnings,
        picks=len(out["picks"]) if out.get("picks") else 0,
        transfers=len(out.get("transfers") or []),
        mini_leagues=len(out.get("mini_leagues") or []),
        selling_prices=len(out["selling_prices"] or {}),
    )

    n = len(out["picks"]) if out.get("picks") else 0
    print(
        f"Wrote {OUT} — {n} picks, bank {out.get('bank')}, value {out.get('squad_value')}, "
        f"FT est {out.get('free_transfers_estimate')} (estimate), "
        f"{len(out.get('transfers') or [])} transfers, "
        f"{len(out.get('mini_leagues') or [])} mini-league(s)"
    )
    if out["selling_prices"]:
        from_transfer = sum(1 for p in out["selling_prices"].values()
                            if p["source"] == "transfer")
        budget = out["available_budget"]
        print(
            f"  selling value {out['squad_selling_value'] / 10.0}m vs market "
            f"{out['squad_market_value'] / 10.0}m, available budget "
            f"{budget / 10.0 if budget is not None else '?'}m "
            f"({from_transfer} priced from a transfer record, "
            f"{len(out['selling_prices']) - from_transfer} from the initial squad)"
        )
    for w in warnings:
        print(f"  ! {w}", file=sys.stderr)


if __name__ == "__main__":
    main()

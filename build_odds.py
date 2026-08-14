#!/usr/bin/env python3
"""
build_odds.py — pulls bookmaker odds and non-PL fixture congestion from
API-Football (v3.football.api-sports.io) and writes two small CSVs.

Outputs
  data/odds.csv        one row per upcoming PL fixture: de-vigged 1X2,
                       over/under 2.5, fitted expected goals, clean-sheet
                       probability for each side
  data/congestion.csv  non-PL matches for PL clubs in the next 28 days,
                       with rest days before the following league game

Needs the API_SPORTS_KEY environment variable. NEVER hardcode the key here.

Budget: roughly 12-15 requests per run. Free tier is 100/day, 10/minute,
so twice a day sits comfortably inside it.
"""

import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KEY = os.environ.get("API_SPORTS_KEY", "").strip()
BASE = "https://v3.football.api-sports.io"
SEASON = 2026                     # API-Football uses the starting year
PL = 39                           # Premier League

# If any of these come back with zero fixtures, the ID may be wrong for this
# season — the script prints a warning so it can be corrected.
OTHER_COMPS = {
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    45: "FA Cup",
    48: "League Cup",
}

PREFERRED_BOOKMAKERS = ["Bet365", "Pinnacle", "William Hill", "1xBet"]

# API-Football team name -> FPL short code.
TEAM_CODES = {
    "arsenal": "ARS",
    "aston villa": "AVL",
    "brighton": "BHA", "brighton hove albion": "BHA", "brighton and hove albion": "BHA",
    "bournemouth": "BOU", "afc bournemouth": "BOU",
    "brentford": "BRE",
    "chelsea": "CHE",
    "coventry": "COV", "coventry city": "COV",
    "crystal palace": "CRY",
    "everton": "EVE",
    "fulham": "FUL",
    "hull": "HUL", "hull city": "HUL",
    "ipswich": "IPS", "ipswich town": "IPS",
    "leeds": "LEE", "leeds united": "LEE",
    "liverpool": "LIV",
    "manchester city": "MCI", "man city": "MCI",
    "manchester united": "MUN", "man united": "MUN", "manchester utd": "MUN",
    "newcastle": "NEW", "newcastle united": "NEW",
    "nottingham forest": "NFO", "nottingham": "NFO",
    "sunderland": "SUN",
    "tottenham": "TOT", "tottenham hotspur": "TOT", "spurs": "TOT",
}

CALLS = 0
UNMATCHED = set()


def api(path, params=None):
    """One authenticated GET. Sleeps to respect the 10/min free-tier limit."""
    global CALLS
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        remaining = r.headers.get("x-ratelimit-requests-remaining")
        body = json.load(r)
    CALLS += 1
    errs = body.get("errors")
    if errs and errs not in ([], {}):
        print(f"  ! API errors on {path}: {errs}")
    print(f"  [{CALLS}] {path} -> {body.get('results', 0)} results "
          f"(quota left: {remaining})")
    time.sleep(7)  # stay under 10 requests/minute
    return body.get("response", [])


def code_for(name):
    key = (name or "").lower().replace(".", "").replace("-", " ").strip()
    if key in TEAM_CODES:
        return TEAM_CODES[key]
    UNMATCHED.add(name)
    return None


# ---------------------------------------------------------------- odds maths

def devig(odds_map):
    """Proportional de-vig: strip the bookmaker margin off implied probs."""
    inv = {k: 1.0 / v for k, v in odds_map.items() if v and v > 1.0}
    total = sum(inv.values())
    if not total:
        return None
    return {k: v / total for k, v in inv.items()}


def pmf_table(lam, maxg=10):
    return [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(maxg)]


def outcome_probs(ph, pa):
    home = draw = away = over = 0.0
    for i, hi in enumerate(ph):
        for j, aj in enumerate(pa):
            p = hi * aj
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
            if i + j >= 3:
                over += p
    return home, draw, away, over


def fit_expected_goals(p_home, p_draw, p_away, p_over):
    """
    Grid-search the independent-Poisson pair (lambda_home, lambda_away) that
    best reproduces the market's implied probabilities.

    Independent Poisson slightly understates draws in real football, so treat
    these as good estimates rather than exact numbers.
    """
    grid = [round(0.20 + 0.05 * i, 2) for i in range(77)]  # 0.20 -> 4.00
    tables = {g: pmf_table(g) for g in grid}
    best, best_err = None, float("inf")
    for lh in grid:
        ph = tables[lh]
        for la in grid:
            h, d, a, o = outcome_probs(ph, tables[la])
            err = (h - p_home) ** 2 + (d - p_draw) ** 2 + (a - p_away) ** 2
            if p_over is not None:
                err += (o - p_over) ** 2
            if err < best_err:
                best_err, best = err, (lh, la)
    return best


# ---------------------------------------------------------------- extraction

def extract_market(bookmaker):
    """Pull 1X2 and Over/Under 2.5 prices out of one bookmaker block."""
    res = {"h": None, "d": None, "a": None, "over": None}
    for bet in bookmaker.get("bets", []):
        name = (bet.get("name") or "").lower()
        vals = {(v.get("value") or "").lower(): v.get("odd") for v in bet.get("values", [])}
        if "match winner" in name or name == "1x2":
            res["h"] = safe_float(vals.get("home"))
            res["d"] = safe_float(vals.get("draw"))
            res["a"] = safe_float(vals.get("away"))
        elif "goals over/under" in name or name == "over/under":
            res["over"] = safe_float(vals.get("over 2.5"))
    return res


def safe_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    if not KEY:
        print("FATAL: API_SPORTS_KEY is not set. Add it as a repository secret.")
        sys.exit(1)

    os.makedirs("data", exist_ok=True)
    now = datetime.now(timezone.utc)

    print("=" * 60)
    print("STEP 1 — account check")
    status = api("/status")
    if isinstance(status, dict):
        sub = status.get("subscription", {})
        req = status.get("requests", {})
        print(f"  plan: {sub.get('plan')}  active: {sub.get('active')}")
        print(f"  requests today: {req.get('current')} / {req.get('limit_day')}")

    print("STEP 2 — season coverage check")
    leagues = api("/leagues", {"id": PL, "season": SEASON})
    if not leagues:
        print(f"  FATAL: no Premier League data for season {SEASON}.")
        print("  Free plans are restricted to certain seasons — this is the")
        print("  check that decides whether the free tier is usable. Upgrade or stop here.")
        sys.exit(1)
    for s in leagues[0].get("seasons", []):
        if s.get("year") == SEASON:
            cov = s.get("coverage", {})
            print(f"  odds coverage: {cov.get('odds')}  |  fixtures: {bool(cov.get('fixtures'))}")
            if not cov.get("odds"):
                print("  WARNING: odds flag is false — odds.csv will be empty.")

    print("STEP 3 — upcoming Premier League fixtures")
    pl_fixtures = api("/fixtures", {"league": PL, "season": SEASON, "next": 30})
    fixture_meta = {}
    pl_by_club = {}
    for f in pl_fixtures:
        fid = f["fixture"]["id"]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        ko = f["fixture"]["date"]
        fixture_meta[fid] = {"home": home, "away": away, "kickoff": ko}
        for club in (home, away):
            pl_by_club.setdefault(code_for(club), []).append(ko)

    print("STEP 4 — bookmaker selection")
    books = api("/odds/bookmakers")
    book_id, book_name = None, None
    for want in PREFERRED_BOOKMAKERS:
        for b in books:
            if (b.get("name") or "").lower() == want.lower():
                book_id, book_name = b["id"], b["name"]
                break
        if book_id:
            break
    if not book_id and books:
        book_id, book_name = books[0]["id"], books[0]["name"]
    print(f"  using bookmaker: {book_name} (id {book_id})")

    print("STEP 5 — odds")
    odds_rows = []
    page, pages = 1, 1
    while page <= pages and page <= 4:
        req = urllib.request.Request(
            BASE + "/odds?" + urllib.parse.urlencode(
                {"league": PL, "season": SEASON, "bookmaker": book_id, "page": page}),
            headers={"x-apisports-key": KEY},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
        pages = (body.get("paging") or {}).get("total", 1)
        print(f"  [odds] page {page}/{pages} -> {body.get('results', 0)} fixtures")
        for item in body.get("response", []):
            fid = item["fixture"]["id"]
            meta = fixture_meta.get(fid)
            if not meta:
                continue
            for bk in item.get("bookmakers", []):
                m = extract_market(bk)
                if not (m["h"] and m["d"] and m["a"]):
                    continue
                probs = devig({"h": m["h"], "d": m["d"], "a": m["a"]})
                if not probs:
                    continue
                p_over = None
                if m["over"]:
                    p_over = min(0.95, max(0.05, 1.0 / m["over"] * 0.95))
                lh, la = fit_expected_goals(probs["h"], probs["d"], probs["a"], p_over)
                odds_rows.append({
                    "kickoff_utc": meta["kickoff"],
                    "home": meta["home"],
                    "away": meta["away"],
                    "home_code": code_for(meta["home"]),
                    "away_code": code_for(meta["away"]),
                    "p_home": round(probs["h"], 4),
                    "p_draw": round(probs["d"], 4),
                    "p_away": round(probs["a"], 4),
                    "p_over25": round(p_over, 4) if p_over else "",
                    "xg_home": lh,
                    "xg_away": la,
                    "cs_prob_home": round(math.exp(-la), 4),
                    "cs_prob_away": round(math.exp(-lh), 4),
                    "bookmaker": book_name,
                    "fetched_at": now.isoformat(),
                })
                break
        page += 1
        time.sleep(7)

    print("STEP 6 — non-PL fixtures (congestion)")
    horizon = (now + timedelta(days=28)).strftime("%Y-%m-%d")
    congestion_rows = []
    for lid, lname in OTHER_COMPS.items():
        fx = api("/fixtures", {
            "league": lid, "season": SEASON,
            "from": now.strftime("%Y-%m-%d"), "to": horizon,
        })
        if not fx:
            print(f"  note: no fixtures for {lname} (id {lid}) — check the ID if this persists")
        for f in fx:
            for side in ("home", "away"):
                club = f["teams"][side]["name"]
                code = code_for(club)
                if not code:
                    continue
                ko = f["fixture"]["date"]
                nxt = sorted([d for d in pl_by_club.get(code, []) if d > ko])
                rest = ""
                if nxt:
                    a = datetime.fromisoformat(ko.replace("Z", "+00:00"))
                    b = datetime.fromisoformat(nxt[0].replace("Z", "+00:00"))
                    rest = round((b - a).total_seconds() / 86400, 1)
                congestion_rows.append({
                    "club_code": code,
                    "club": club,
                    "competition": lname,
                    "kickoff_utc": ko,
                    "opponent": f["teams"]["away" if side == "home" else "home"]["name"],
                    "venue": "H" if side == "home" else "A",
                    "next_pl_kickoff": nxt[0] if nxt else "",
                    "days_rest_before_next_pl": rest,
                })

    write_csv("data/odds.csv", odds_rows)
    write_csv("data/congestion.csv", sorted(congestion_rows, key=lambda r: r["kickoff_utc"]))

    print("=" * 60)
    print(f"DONE — {len(odds_rows)} odds rows, {len(congestion_rows)} congestion rows, "
          f"{CALLS} API calls used")
    if UNMATCHED:
        print("UNMATCHED TEAM NAMES (send these to Claude to fix the mapping):")
        for n in sorted(UNMATCHED):
            print(f"    {n}")


def write_csv(path, rows):
    if not rows:
        print(f"  (no rows for {path} — writing header only)")
    with open(path, "w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        else:
            f.write("no_data\n")
    print(f"  wrote {path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()

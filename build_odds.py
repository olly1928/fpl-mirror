#!/usr/bin/env python3
"""
build_odds.py — bookmaker consensus for upcoming Premier League fixtures.

Source: The Odds API v4 (api.the-odds-api.com). Key comes from the ODDS_API_KEY
environment variable and is never written to disk.

Output: data/odds.csv, one row per upcoming listed fixture, carrying de-vigged
1X2 probabilities, P(over 2.5), a fitted independent-Poisson goal expectation for
each side, and the clean-sheet probability that falls out of it. The clean-sheet
columns are the point of the file: they are a market-priced replacement for FPL's
own fixture difficulty rating when picking defenders and goalkeepers.

Request budget
    One call to /v4/sports/ (free, does not consume credits) to confirm the key
    works and the competition is listed.
    One call to /v4/sports/soccer_epl/odds/ with regions=uk and markets=h2h,totals.
    Credit cost is markets x regions, so that is 2 credits per run. Every upcoming
    fixture and every UK bookmaker comes back inside that single response, so
    there is never a reason to loop per fixture.

    At the four-runs-a-day schedule this is 8 credits a day, about 248 a month,
    which sits inside the 500-a-month free tier with room for manual re-runs.

Both calls print x-requests-remaining and x-requests-used so the real allowance is
visible in the Actions log.

Empty results are normal between rounds, during international breaks and in
pre-season: bookmakers simply have nothing listed. The API does not charge a
credit for a request that returns no events. The script writes a header-only CSV
and exits 0, so a stale file is never left behind pretending to be current.
"""

import csv
import json
import math
import os
import re
import statistics
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fpl_common import record_status, refresh_meta_components

ODDS_BASE = "https://api.the-odds-api.com/v4"
FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
SPORT = "soccer_epl"
REGIONS = "uk"
MARKETS = "h2h,totals"
TOTALS_LINE = 2.5  # books list several lines; only 2.5 is comparable across them

# Must match the workflow cron in .github/workflows/odds-mirror.yml. meta.json
# calls a component stale at twice this, so 360 puts the odds stale flag at the
# same 12 hours the consuming playbook treats as too old near a deadline.
ODDS_INTERVAL_MINUTES = 360

OUT = "data/odds.csv"
COLUMNS = [
    "kickoff_utc", "home", "away", "home_code", "away_code",
    "p_home", "p_draw", "p_away", "p_over25",
    "xg_home", "xg_away", "cs_prob_home", "cs_prob_away",
    "n_books", "n_books_totals", "fetched_at",
]

# Poisson grid: 0.20 to 4.00 in 0.05 steps.
GRID = [round(0.20 + 0.05 * i, 2) for i in range(77)]
MAX_GOALS = 15  # Poisson(4.0) has ~1e-5 mass above this

# Normalised Odds-API club name -> FPL short code, for names that normalisation
# alone cannot reach. Anything in here that is not in the current bootstrap is
# dropped at startup, so retired clubs are harmless. Print-and-top-up: any club
# that fails to match is listed loudly at the end of the run.
ALIASES = {
    "manchester city": "MCI",
    "manchester united": "MUN",
    "man united": "MUN",
    "man utd": "MUN",
    "tottenham hotspur": "TOT",
    "tottenham": "TOT",
    "spurs": "TOT",
    "nottingham forest": "NFO",
    "notts forest": "NFO",
    "wolverhampton wanderers": "WOL",
    "wolves": "WOL",
    "brighton and hove albion": "BHA",
    "brighton hove albion": "BHA",
    "west ham united": "WHU",
    "west bromwich albion": "WBA",
    "sheffield united": "SHU",
    "leicester city": "LEI",
    "luton town": "LUT",
    "newcastle united": "NEW",
    "leeds united": "LEE",
    "ipswich town": "IPS",
    "hull city": "HUL",
    "coventry city": "COV",
    "afc bournemouth": "BOU",
    "norwich city": "NOR",
}

# Generic club-name furniture, dropped when reducing a name to its core.
FILLER = {"fc", "afc", "cf", "united", "utd", "city", "town", "hotspur",
          "albion", "wanderers", "and", "hove", "association", "the"}

UNMATCHED = set()


def die(msg):
    """Fail loudly. Never leave a half-written file behind."""
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ http

def get_json(url, headers=None):
    """GET returning (payload, response headers)."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r), dict(r.headers)


def report_quota(label, headers):
    """The headers that decide whether the free tier is viable at all."""
    remaining = headers.get("x-requests-remaining")
    used = headers.get("x-requests-used")
    last = headers.get("x-requests-last")
    print(f"  [{label}] x-requests-remaining={remaining}  x-requests-used={used}"
          + (f"  x-requests-last={last}" if last else ""))
    return remaining, used


def odds_get(path, params, label):
    """One call to The Odds API, with the quota headers surfaced either way."""
    url = f"{ODDS_BASE}{path}?" + urllib.parse.urlencode(params)
    try:
        payload, headers = get_json(url)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        report_quota(f"{label} (HTTP {exc.code})", dict(exc.headers or {}))
        hint = {
            401: "ODDS_API_KEY is missing, malformed or rejected.",
            422: "The request parameters were rejected — check sport/regions/markets.",
            429: "Monthly credit allowance is exhausted.",
        }.get(exc.code, "")
        die(f"{label} — {ODDS_BASE}{path} returned HTTP {exc.code} {exc.reason}. "
            f"{hint} {body}".strip())
    except Exception as exc:
        die(f"{label} — could not reach {ODDS_BASE}{path}: {exc}")
    report_quota(label, headers)
    return payload, headers


# ------------------------------------------------------------------ team names

def normalise(name):
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def core(name):
    """Strip generic club-name furniture, keeping at least one token."""
    tokens = [t for t in normalise(name).split() if t not in FILLER]
    return " ".join(tokens) if tokens else normalise(name)


def build_team_index(fpl_teams):
    """
    Map club names to FPL short codes using the bootstrap 'teams' array at
    runtime, rather than a hardcoded dictionary that rots every August.

    Three layers, most specific first: exact normalised name or short_name, then
    the small alias table, then a 'core' match that is only trusted when it
    resolves to exactly one club (this is what keeps Manchester City and
    Manchester United — which share the core 'manchester' — from colliding).
    """
    shorts = {t["short_name"] for t in fpl_teams}
    exact = {}
    core_counts = {}
    for t in fpl_teams:
        short = t["short_name"]
        exact[normalise(t["name"])] = short
        exact[normalise(short)] = short
        core_counts.setdefault(core(t["name"]), set()).add(short)

    aliases = {k: v for k, v in ALIASES.items() if v in shorts}
    unique_cores = {k: next(iter(v)) for k, v in core_counts.items() if len(v) == 1}

    def lookup(name):
        n = normalise(name)
        if n in exact:
            return exact[n]
        if n in aliases:
            return aliases[n]
        c = core(name)
        if c in unique_cores:
            return unique_cores[c]
        UNMATCHED.add(str(name))
        return ""

    return lookup


# ------------------------------------------------------------------ odds maths

def devig(prices):
    """
    Proportional de-vig: invert each decimal price to an implied probability,
    then divide each by their sum so the book totals 1.

    This is an approximation. It assumes the bookmaker's margin is spread evenly
    across outcomes, which it is not — real books load more margin onto longshots,
    so this mildly overstates outsiders and understates favourites. Shin or
    power methods correct for that; proportional is fine at the accuracy this
    file is used at.
    """
    inv = {k: 1.0 / v for k, v in prices.items() if v and v > 1.0}
    total = sum(inv.values())
    if len(inv) != len(prices) or total <= 0:
        return None
    return {k: v / total for k, v in inv.items()}


def poisson_pmf(lam, n=MAX_GOALS):
    out, p = [], math.exp(-lam)
    for k in range(n + 1):
        out.append(p)
        p = p * lam / (k + 1)
    return out


def below_cdf(pmf):
    """below[i] = P(X < i)."""
    out, run = [0.0], 0.0
    for p in pmf:
        run += p
        out.append(run)
    return out


def fit_expected_goals(p_home, p_draw, p_away, p_over):
    """
    Grid-search the independent-Poisson pair (lambda_home, lambda_away) whose
    implied outcome probabilities best reproduce the market's.

    Scored on squared error against the de-vigged P(home)/P(draw)/P(away) and,
    when available, P(over 2.5). The PMF and CDF for every lambda are computed
    once up front, so the inner loop is a handful of multiply-adds and the whole
    5929-pair search runs in well under a second in pure Python.

    Independent Poisson understates draws slightly in real football (goals are
    mildly negatively correlated), so treat these as good estimates, not truth.
    """
    pmfs = {g: poisson_pmf(g) for g in GRID}
    cdfs = {g: below_cdf(pmfs[g]) for g in GRID}

    best, best_err = None, float("inf")
    for lh in GRID:
        ph, ch = pmfs[lh], cdfs[lh]
        for la in GRID:
            pa, ca = pmfs[la], cdfs[la]

            draw = 0.0
            for k in range(MAX_GOALS + 1):
                draw += ph[k] * pa[k]
            home = 0.0
            for i in range(MAX_GOALS + 1):
                home += ph[i] * ca[i]      # away strictly below home
            away = 0.0
            for j in range(MAX_GOALS + 1):
                away += pa[j] * ch[j]      # home strictly below away

            total = home + draw + away
            if total <= 0:
                continue
            home, draw, away = home / total, draw / total, away / total

            err = (home - p_home) ** 2 + (draw - p_draw) ** 2 + (away - p_away) ** 2
            if p_over is not None:
                under = (ph[0] * pa[0]
                         + ph[0] * pa[1] + ph[1] * pa[0]
                         + ph[0] * pa[2] + ph[1] * pa[1] + ph[2] * pa[0])
                err += ((1.0 - under) - p_over) ** 2

            if err < best_err:
                best_err, best = err, (lh, la)

    return best


# ------------------------------------------------------------------ extraction

def median_prices(event):
    """
    Collapse every UK bookmaker on this event into one consensus price set.

    A median across books is more robust than trusting a single book, and since
    one call already returns all of them it costs nothing extra. Only the 2.5
    totals line is used — books list several lines and they are not comparable.

    Returns (h2h median prices, totals median prices, number of books in the
    h2h median, number of books in the totals median). The two counts are
    reported separately because a fixture can have deep 1X2 pricing and a single
    book's over/under line — and if the xG fit is resting on one book, that
    should be visible in the CSV rather than buried.
    """
    home, away = event.get("home_team"), event.get("away_team")
    h_prices, d_prices, a_prices, over, under = [], [], [], [], []

    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            key = market.get("key")
            outcomes = market.get("outcomes") or []
            if key == "h2h":
                by_name = {o.get("name"): o.get("price") for o in outcomes}
                h, d, a = by_name.get(home), by_name.get("Draw"), by_name.get(away)
                if h and d and a:
                    h_prices.append(float(h))
                    d_prices.append(float(d))
                    a_prices.append(float(a))
            elif key == "totals":
                line = {o.get("name"): o.get("price") for o in outcomes
                        if o.get("point") == TOTALS_LINE}
                if line.get("Over") and line.get("Under"):
                    over.append(float(line["Over"]))
                    under.append(float(line["Under"]))

    h2h = None
    if h_prices:
        h2h = {
            "home": statistics.median(h_prices),
            "draw": statistics.median(d_prices),
            "away": statistics.median(a_prices),
        }
    totals = None
    if over:
        totals = {"over": statistics.median(over), "under": statistics.median(under)}

    return h2h, totals, len(h_prices), len(over)


def build_row(event, lookup, fetched_at, tally):
    """Build one CSV row, recording why an event was dropped if it was."""
    h2h, totals, n_books, n_books_totals = median_prices(event)
    if totals:
        tally["with_totals_25"] += 1

    if not h2h:
        print(f"  ! no usable UK h2h prices for "
              f"{event.get('home_team')} v {event.get('away_team')} — skipped")
        return None
    tally["with_h2h"] += 1

    probs = devig(h2h)
    if not probs:
        print(f"  ! could not de-vig {event.get('home_team')} v {event.get('away_team')} — skipped")
        return None

    # The over/under pair carries its own margin, so de-vig it as a two-way book
    # rather than reading 1/over straight off.
    p_over = None
    if totals:
        two_way = devig(totals)
        if two_way:
            p_over = two_way["over"]

    lh, la = fit_expected_goals(probs["home"], probs["draw"], probs["away"], p_over)

    return {
        "kickoff_utc": event.get("commence_time"),
        "home": event.get("home_team"),
        "away": event.get("away_team"),
        "home_code": lookup(event.get("home_team")),
        "away_code": lookup(event.get("away_team")),
        "p_home": round(probs["home"], 4),
        "p_draw": round(probs["draw"], 4),
        "p_away": round(probs["away"], 4),
        "p_over25": round(p_over, 4) if p_over is not None else "",
        "xg_home": lh,
        "xg_away": la,
        # A clean sheet for the home side means the away side fails to score.
        "cs_prob_home": round(math.exp(-la), 4),
        "cs_prob_away": round(math.exp(-lh), 4),
        "n_books": n_books,
        "n_books_totals": n_books_totals,
        "fetched_at": fetched_at,
    }


def write_csv(rows):
    os.makedirs("data", exist_ok=True)
    # Temp file then rename, so a crash mid-write cannot leave a truncated
    # odds.csv where a complete stale one used to be.
    tmp = OUT + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, OUT)
    print(f"  wrote {OUT} ({len(rows)} rows)")

    # Every six hours. staleness() fires at twice the stated interval, so this
    # puts the stale flag at twelve hours — which is the threshold that actually
    # matters, because odds move on team news and a line built before a Friday
    # press conference is not one to transfer on. The previous value of 720 gave
    # a 24-hour grace period, so an eighteen-hour-old odds.csv sailed through
    # reporting stale=false and the only way to catch it was to read the
    # timestamp inside the file by hand.
    record_status("build_odds", expected_interval_minutes=ODDS_INTERVAL_MINUTES,
                  warnings=[f"unmatched club name: {n}" for n in sorted(UNMATCHED)],
                  rows=len(rows))

    # This job runs on its own schedule, so between two FPL runs meta.json is the
    # only thing a consumer reads for freshness and it would still be quoting the
    # previous odds pull. Rewrites the components block and nothing else.
    if refresh_meta_components():
        print("  refreshed meta.json components/stale")


# ------------------------------------------------------------------ main

def main():
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        die("ODDS_API_KEY is not set. Add it as a repository secret "
            "(gh secret set ODDS_API_KEY) and inject it in the workflow env block. "
            "Never commit the key.")

    fetched_at = datetime.now(timezone.utc).isoformat()

    print("STEP 1 — key check and quota (free endpoint, costs no credits)")
    sports, _ = odds_get("/sports/", {"apiKey": key}, "sports")
    listed = {s.get("key") for s in sports if isinstance(s, dict)}
    if SPORT not in listed:
        die(f"{SPORT} is not in the list of sports this key can see "
            f"({len(listed)} sports returned). The competition may be out of season.")
    print(f"  {SPORT} is listed ({len(listed)} sports available)")

    print("STEP 2 — FPL bootstrap, for the runtime team-name mapping")
    try:
        boot, _ = get_json(FPL_BOOTSTRAP, {"User-Agent": "Mozilla/5.0 (fpl-mirror)"})
    except Exception as exc:
        die(f"could not read the FPL bootstrap for team codes: {exc}")
    if not boot.get("teams"):
        die("FPL bootstrap returned no teams — cannot map club names to short codes")
    lookup = build_team_index(boot["teams"])
    print(f"  {len(boot['teams'])} clubs indexed")

    print(f"STEP 3 — odds ({MARKETS} x {REGIONS} = 2 credits)")
    events, _ = odds_get(
        f"/sports/{SPORT}/odds/",
        {"apiKey": key, "regions": REGIONS, "markets": MARKETS,
         "oddsFormat": "decimal", "dateFormat": "iso"},
        "odds",
    )
    if not events:
        print("  events returned by the API : 0")
        print("  No fixtures listed. Normal between rounds, in international breaks "
              "and pre-season — bookmakers simply have nothing up. The API does not "
              "charge a credit for this. Writing a header-only CSV so nothing stale "
              "is left behind.")
        write_csv([])
        return

    print("STEP 4 — consensus, de-vig and Poisson fit")
    tally = {"with_h2h": 0, "with_totals_25": 0}
    rows = [r for r in (build_row(e, lookup, fetched_at, tally) for e in events) if r]

    # An empty odds.csv is only ever legitimate when the API returned no events
    # at all. If events came back and nothing survived parsing, the response
    # shape has moved and a header-only file would be indistinguishable from a
    # quiet weekend — so say so, show the shape, and fail.
    print("\n  events returned by the API : %d" % len(events))
    print("  with a usable h2h market   : %d" % tally["with_h2h"])
    print("  with a totals line at 2.5  : %d" % tally["with_totals_25"])
    print("  rows written               : %d" % len(rows))

    if not rows:
        print("\nPARSE FAILURE — the API returned events but none produced a row.",
              file=sys.stderr)
        print("Raw JSON of the first event, so the actual shape is visible:",
              file=sys.stderr)
        print(json.dumps(events[0], indent=2)[:8000], file=sys.stderr)
        die(f"{len(events)} events returned, 0 rows written. Refusing to overwrite "
            "odds.csv with an empty file that would look like a quiet weekend.")

    rows.sort(key=lambda r: r["kickoff_utc"] or "")
    write_csv(rows)

    if UNMATCHED:
        print("\nUNMATCHED CLUB NAMES — add these to the ALIASES table in build_odds.py:")
        for n in sorted(UNMATCHED):
            print(f"    {n!r}  (normalised: {normalise(n)!r}, core: {core(n)!r})")

    print(f"\nDONE — {len(rows)} fixtures, {len(UNMATCHED)} unmatched club names")


if __name__ == "__main__":
    main()

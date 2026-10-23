#!/usr/bin/env python3
"""Versioned, offline FPL baseline. Network access and FPL account writes: none.

See model/README.md for assumptions, inputs and limitations. Standard library only.
"""
import argparse
import csv
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
POSITIONS = ("GKP", "DEF", "MID", "FWD")
QUOTA = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
GOAL_POINTS = {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}
CS_POINTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
RATE_FIELDS = ("expected_goals", "expected_assists", "defensive_contribution",
               "bonus", "saves", "yellow_cards", "red_cards", "own_goals",
               "penalties_missed", "penalties_saved")


def number(row, key, default=0.0):
    value = row.get(key)
    return default if value in (None, "") else float(value)


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def write_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")


def poisson_tail(mean, threshold):
    """P(Poisson(mean) >= threshold); this is not mean / threshold."""
    if mean <= 0:
        return 0.0
    p = math.exp(-mean)
    below = p
    for k in range(1, threshold):
        p *= mean / k
        below += p
    return max(0.0, min(1.0, 1.0 - below))


def expected_blocks(mean, size):
    """E[floor(N/size)] for Poisson counts (saves/conceded deductions)."""
    return sum(poisson_tail(mean, k) for k in range(size, 121, size))


def validate_data(data, config, clock):
    meta = read_json(data / "meta.json")
    if meta["season"] != config["season"]:
        raise ValueError("season differs from frozen priors; update model configuration")
    if not meta.get("next_event") or utc(meta["next_deadline"]) <= clock:
        raise ValueError("no future gameweek deadline in this snapshot")
    clubs = meta.get("teams_in_game", [])
    if len(clubs) != 20 or len(set(clubs)) != 20:
        raise ValueError("expected 20 distinct clubs")
    if meta.get("stale") or meta.get("warnings"):
        raise ValueError("resolve meta.json stale/warnings before forecasting: " +
                         json.dumps(meta.get("warnings", [])))
    status = read_json(data / "build_status.json")
    core_age = (clock - utc(meta["fetched_at"])).total_seconds() / 60
    core_interval = status.get("build_fpl", {}).get("expected_interval_minutes", 60)
    if core_age < -5 or core_age > 2 * core_interval:
        raise ValueError("meta.json core timestamp is stale or dated in the future")
    for name in ("build_fpl", "build_squad", "build_player_history", "build_odds"):
        component = status.get(name)
        if not component:
            raise ValueError("missing build status: " + name)
        age = (clock - utc(component["last_run_at"])).total_seconds() / 60
        if age < -5 or age > 2 * component["expected_interval_minutes"]:
            raise ValueError("component is stale or dated in the future: " + name)
    # Only files rebuilt by build_fpl share meta's timestamp. Historical panels
    # and odds intentionally have their own update clocks.
    for name in ("players.csv", "teams.csv", "fixtures.csv"):
        first = (data / name).read_text(encoding="utf-8").splitlines()[0]
        if "fetched=" + meta["fetched_at"] not in first:
            raise ValueError("core-build timestamp mismatch: " + name)
    history = read_csv(data / "player_history.csv") if (data / "player_history.csv").exists() else []
    expected = {int(e["id"]) for e in meta["events"] if e.get("data_checked")}
    captured = {int(r["gw"]) for r in history}
    keys = [(int(r["gw"]), int(r["player_id"])) for r in history]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate (gameweek, player) rows in player history")
    if not expected.issubset(captured):
        raise ValueError("completed gameweeks missing from player_history.csv")
    if len(read_csv(data / "players.csv")) != meta["player_count"]:
        raise ValueError("players.csv count differs from meta.json")
    return meta, history


def position_priors(priors):
    output = {}
    for pos in POSITIONS:
        rows = [r for r in priors.values() if r["pos"] == pos]
        minutes = sum(number(r, "mins") for r in rows)
        output[pos] = {k: sum(number(r, k) for r in rows) * 90 / max(minutes, 1)
                       for k in RATE_FIELDS}
        # FPL assists include rebounds, penalties won etc. xA does not. Use a
        # frozen position-wide supplement, not a player's recent assist luck.
        output[pos]["extra_assists"] = max(0, sum(number(r, "a") - number(r, "expected_assists")
                                                    for r in rows) * 90 / max(minutes, 1))
    return output


def player_rates(player, rows, prior, config, group):
    rates = {}
    last = max((int(r["gw"]) for r in rows), default=0)
    weights = [0.5 ** ((last - int(r["gw"])) / config["history_half_life_gameweeks"])
               for r in rows]
    current_minutes = sum(w * number(r, "minutes") for w, r in zip(weights, rows))
    prior_minutes = min(number(prior, "mins"), config["rate_prior_minutes"])
    position_minutes = config["position_prior_minutes"]
    for key in RATE_FIELDS:
        # Bonus rules changed this season. Use a broad position prior for bonus,
        # rather than pretending last season's individual BPS is unchanged.
        prior_rate = (number(prior, key) * 90 / number(prior, "mins")
                      if prior_minutes and key != "bonus" else group[key])
        observed = sum(w * number(r, key) for w, r in zip(weights, rows))
        rates[key] = (observed * 90 + prior_minutes * prior_rate +
                      position_minutes * group[key]) / max(1, current_minutes + prior_minutes + position_minutes)
    rates["extra_assists"] = group["extra_assists"]
    return rates


def minute_states(player, rows, prior, config, override):
    """Disjoint states: starts >=60, short start, cameo, no appearance.

    Start counts are divided by fixture opportunities, never by minutes/90.
    DGW rows cannot supply conditional minute distributions, so only single-
    fixture observations inform conditional minutes; overrides handle news.
    """
    recent = sorted(rows, key=lambda r: int(r["gw"]))[-config["minutes_window"]:]
    opportunities = sum(max(1, len(r.get("fixture_id", "").split("|"))) for r in recent)
    starts = sum(number(r, "starts") for r in recent)
    single = [r for r in recent if "|" not in r.get("fixture_id", "")]
    start_rows = [r for r in single if number(r, "starts") > 0]
    long_rows = [r for r in start_rows if number(r, "minutes") >= 60]
    short_rows = [r for r in start_rows if 0 < number(r, "minutes") < 60]
    nonstarts = [r for r in single if not number(r, "starts")]
    cameos = [r for r in nonstarts if number(r, "minutes") > 0]
    prior_start = min(1, number(prior, "starts") / 38) if prior else 0.15
    strength = config["minutes_prior_matches"]
    p_start = (starts + strength * prior_start) / max(1, opportunities + strength)
    p_long = (len(long_rows) + 2 * 0.9) / (len(start_rows) + 2)
    bench_rate = (len(cameos) + 1 * 0.2) / (len(nonstarts) + 1)
    availability = number(player, "chance_of_playing_next_round", 100) / 100
    if player.get("st") in ("i", "s", "u"):
        availability = 0
    p_cameo = (1 - p_start) * bench_rate * availability
    p_start *= availability
    values = {
        "p_start": p_start, "p_cameo": p_cameo, "p60_if_start": p_long,
        "minutes_60": sum(number(r, "minutes") for r in long_rows) / len(long_rows) if long_rows else 85,
        "minutes_short_start": sum(number(r, "minutes") for r in short_rows) / len(short_rows) if short_rows else 40,
        "minutes_cameo": sum(number(r, "minutes") for r in cameos) / len(cameos) if cameos else 15,
    }
    for key in values:
        if key in override:
            values[key] = float(override[key])
    for key in ("p_start", "p_cameo", "p60_if_start"):
        if not 0 <= values[key] <= 1:
            raise ValueError("minute probability outside [0,1]: " + key)
    if values["p_start"] + values["p_cameo"] > 1 + 1e-9:
        raise ValueError("start and cameo probabilities exceed 1")
    for key, low, high in (("minutes_60", 60, 90), ("minutes_short_start", 1, 59),
                           ("minutes_cameo", 1, 90)):
        if not low <= values[key] <= high:
            raise ValueError("invalid conditional minutes: " + key)
    p = values["p_start"]; q = values["p60_if_start"]
    return [(p * q, values["minutes_60"]), (p * (1 - q), values["minutes_short_start"]),
            (values["p_cameo"], values["minutes_cameo"])], values


def team_environment(fixtures, start, config):
    past = [f for f in fixtures if f.get("finished", "").lower() == "true" and int(f["gw"] or 0) < start]
    scored = defaultdict(float); conceded = defaultdict(float); games = Counter()
    for f in past:
        h, a = f["home"], f["away"]
        hg, ag = number(f, "team_h_score"), number(f, "team_a_score")
        scored[h] += hg; conceded[h] += ag; scored[a] += ag; conceded[a] += hg
        games[h] += 1; games[a] += 1
    neutral = config["neutral_team_goals"]; strength = config["team_prior_matches"]
    def team_rate(team, values):
        return (values[team] + strength * neutral) / (games[team] + strength)
    def environment(team, opponent, home):
        factor = config["home_goal_factor"] if home else config["away_goal_factor"]
        own = team_rate(team, scored) / neutral
        opp = team_rate(opponent, conceded) / neutral
        return neutral * factor * own ** config["fallback_attack_home_exponent"] * opp ** config["fallback_defence_exponent"]
    return environment, {t: team_rate(t, scored) for t in games}


def fixture_points(pos, rates, states, own_goals, opp_goals, attack_scale, dc_long):
    parts = dict.fromkeys(("appearance", "goals", "assists", "clean_sheet", "defensive_contribution",
                           "bonus", "saves", "conceded", "discipline", "penalties"), 0.0)
    for probability, minutes in states:
        exposure = minutes / 90
        parts["appearance"] += probability * (2 if minutes >= 60 else 1)
        parts["goals"] += probability * GOAL_POINTS[pos] * rates["expected_goals"] * exposure * attack_scale
        parts["assists"] += probability * 3 * (rates["expected_assists"] + rates["extra_assists"]) * exposure * attack_scale
        if minutes >= 60:
            parts["clean_sheet"] += probability * CS_POINTS[pos] * math.exp(-opp_goals * exposure)
        if pos != "GKP":
            threshold = 10 if pos == "DEF" else 12
            dc_probability = dc_long if minutes >= 60 else poisson_tail(rates["defensive_contribution"] * exposure, threshold)
            parts["defensive_contribution"] += probability * 2 * dc_probability
        parts["bonus"] += probability * min(3, rates["bonus"] * exposure)
        if pos == "GKP":
            parts["saves"] += probability * expected_blocks(rates["saves"] * exposure, 3)
        if pos in ("GKP", "DEF"):
            parts["conceded"] -= probability * expected_blocks(opp_goals * exposure, 2)
        parts["discipline"] -= probability * exposure * (rates["yellow_cards"] + 3 * rates["red_cards"] + 2 * rates["own_goals"])
        parts["penalties"] += probability * exposure * (5 * rates["penalties_saved"] - 2 * rates["penalties_missed"])
    return parts


def forecast(data, model, assumptions, horizon, clock, replay, source_ref):
    config = read_json(model / "config.json")
    if config["version"] != VERSION:
        raise ValueError("script/config version mismatch; fetch both from the same commit")
    if not 1 <= horizon <= 8:
        raise ValueError("horizon must be between 1 and 8 gameweeks")
    meta, history = validate_data(data, config, clock)
    start = meta["next_event"]
    priors = {int(p["id"]): p for p in read_csv(model / config["prior_file"])}
    groups = position_priors(priors)
    players = {int(p["id"]): p for p in read_csv(data / "players.csv")}
    for pid, rules in assumptions.get("players", {}).items():
        if not str(pid).isdigit() or int(pid) not in players:
            raise ValueError("override references an unknown player: " + str(pid))
        if any(key != "all" and (not str(key).isdigit() or not 1 <= int(key) <= 38) for key in rules):
            raise ValueError("override gameweek must be 'all' or an integer 1-38")
    fixtures = read_csv(data / "fixtures.csv")
    odds = read_csv(data / "odds.csv")
    history = [r for r in history if int(r["gw"]) < start]
    grouped = defaultdict(list)
    for r in history:
        grouped[int(r["player_id"])].append(r)
    environment, team_average = team_environment(fixtures, start, config)
    warnings = ["Uncalibrated baseline; reported xP differences are not proven edges.",
                "Historical club labels do not identify summer role changes. Verify minutes and roles from news.",
                "Bonus is a shrunk empirical rate, not a match-level BPS simulation; FPL assists use a position-wide supplement.",
                "Minutes and autosub scenarios assume independent appearances; team rotation/injury correlations are not modelled."]
    deadlines = {str(e["id"]): e["deadline_time"] for e in meta["events"]}
    weeks = [g for g in range(start, min(39, start + horizon))]
    output = {}
    for pid, p in players.items():
        rows = grouped[pid]; prior = priors.get(pid, {}); group = groups[p["pos"]]
        rates = player_rates(p, rows, prior, config, group)
        player_output = {"id": pid, "name": p["name"], "team": p["team"], "pos": p["pos"],
                         "price_tenths": round(float(p["price"]) * 10), "weeks": {},
                         "thin_history": not prior or number(prior, "mins") < 450,
                         "status": p.get("st"), "fitness_requires_review": p.get("st") not in (None, "a")}
        overrides = assumptions.get("players", {}).get(str(pid), {})
        for gw in weeks:
            override = {**overrides.get("all", {}), **overrides.get(str(gw), {})}
            allowed = {"p_start", "p_cameo", "p60_if_start", "minutes_60", "minutes_short_start", "minutes_cameo",
                       "goal_rate_multiplier", "assist_rate_multiplier", "dc_rate_multiplier", "source", "reason"}
            if set(override) - allowed:
                raise ValueError("unknown override fields for player " + str(pid))
            if override and (not override.get("source") or not override.get("reason")):
                raise ValueError("every news override needs source and reason")
            states, minute_values = minute_states(p, rows, prior, config, override)
            adjusted = rates.copy()
            for field, key in (("expected_goals", "goal_rate_multiplier"), ("expected_assists", "assist_rate_multiplier"),
                               ("defensive_contribution", "dc_rate_multiplier")):
                if key in override:
                    multiplier = float(override[key])
                    if not 0 <= multiplier <= 3:
                        raise ValueError("role multiplier must lie in [0,3]")
                    adjusted[field] *= multiplier
            parts = defaultdict(float); fixture_inputs = []; p_none = 1.0; p_under60 = 1.0; expected_minutes = 0.0
            for f in [f for f in fixtures if int(f["gw"] or 0) == gw and p["team"] in (f["home"], f["away"])]:
                home = p["team"] == f["home"]; opponent = f["away"] if home else f["home"]
                o = next((o for o in odds if o["home_code"] == f["home"] and o["away_code"] == f["away"]
                          and o["kickoff_utc"][:10] == f["kickoff"]), None)
                if o:
                    own = number(o, "xg_home" if home else "xg_away")
                    opp = number(o, "xg_away" if home else "xg_home")
                    age = (clock - utc(o["fetched_at"])).total_seconds() / 3600
                    if age < -0.1 or age > 12:
                        o = None
                if not o:
                    own = environment(p["team"], opponent, home)
                    opp = environment(opponent, p["team"], not home)
                source = "market_poisson" if o else "shrunk_team_results"
                baseline = team_average.get(p["team"], config["neutral_team_goals"])
                scale = max(0.4, min(2.5, own / baseline))
                eligible_dc = [r for r in rows if "|" not in r.get("fixture_id", "") and number(r, "minutes") >= 60]
                threshold = 10 if p["pos"] == "DEF" else 12
                dc_prior = poisson_tail(adjusted["defensive_contribution"] * minute_values["minutes_60"] / 90, threshold)
                # A verified role change invalidates old threshold-hit frequencies.
                if "dc_rate_multiplier" in override:
                    eligible_dc = []
                hits = sum(number(r, "defensive_contribution") >= threshold for r in eligible_dc)
                dc_long = (hits + config["dc_prior_matches"] * dc_prior) / (len(eligible_dc) + config["dc_prior_matches"])
                component = fixture_points(p["pos"], adjusted, states, own, opp, scale, dc_long)
                for k, v in component.items(): parts[k] += v
                appear = sum(prob for prob, _ in states)
                p60 = sum(prob for prob, minutes in states if minutes >= 60)
                p_none *= 1 - appear; p_under60 *= 1 - p60
                expected_minutes += sum(prob * minutes for prob, minutes in states)
                fixture_inputs.append({"fixture_id": f["fixture_id"], "opponent": opponent, "home": home,
                                       "source": source, "team_goals": round(own, 4), "opponent_goals": round(opp, 4),
                                       "n_books_totals": int(o["n_books_totals"] or 0) if o else None,
                                       "odds_fetched_at": o["fetched_at"] if o else None})
            xp = sum(parts.values())
            player_output["weeks"][str(gw)] = {"xp": round(xp, 5), "p_appear": round(1 - p_none, 6),
                "p60": round(1 - p_under60, 6), "expected_minutes": round(expected_minutes, 3),
                "components": {k: round(v, 5) for k, v in parts.items()},
                "minutes_assumptions": minute_values, "fixtures": fixture_inputs, "override": override}
        output[str(pid)] = player_output
    files = [data / n for n in ("meta.json", "build_status.json", "players.csv", "teams.csv", "fixtures.csv", "odds.csv", "squad.json")]
    if (data / "player_history.csv").exists(): files.append(data / "player_history.csv")
    files += [model / "config.json", model / config["prior_file"]]
    digests = {str(p.relative_to(data)) if p.is_relative_to(data) else "model/" + p.name:
               hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    digests["fpl_advisor.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity = {"model_version": VERSION, "inputs": digests, "players": assumptions.get("players", {}),
                "gameweeks": weeks, "as_of": clock.isoformat()}
    forecast_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {"schema_version": 1, "model_version": VERSION, "forecast_id": forecast_id,
            "season": meta["season"], "team_id": config["team_id"],
            "created_at": now().isoformat(), "as_of": clock.isoformat(), "replay": replay,
            "source_ref": source_ref, "start_gw": start, "gameweeks": weeks, "deadlines": deadlines,
            "input_sha256": digests, "config": config, "assumptions": assumptions, "warnings": warnings, "players": output}


def legal_squad(ids, players):
    if len(ids) != 15 or len(set(ids)) != 15:
        raise ValueError("a squad must contain 15 distinct players")
    if Counter(players[str(i)]["pos"] for i in ids) != Counter(QUOTA):
        raise ValueError("squad must have 2 goalkeepers, 5 defenders, 5 midfielders, 3 forwards")
    if max(Counter(players[str(i)]["team"] for i in ids).values()) > 3:
        raise ValueError("more than three players from one club")


@lru_cache(maxsize=50000)
def bench_subset(original, missing, bench_positions, playing_mask):
    """Legal autosub subset, respecting bench priority and nominal formation.

    Missing players keep a zero-point slot when no legal substitute exists.
    A bench cameo is eligible; starting the real match is not required.
    """
    possible = []
    for mask in range(8):
        if mask & ~playing_mask: continue
        count = mask.bit_count()
        if count > sum(missing): continue
        added = Counter(bench_positions[i] for i in range(3) if mask & (1 << i))
        for rd in range(missing[0] + 1):
            for rm in range(missing[1] + 1):
                rf = count - rd - rm
                if not 0 <= rf <= missing[2]: continue
                final = [original[0] - rd + added["DEF"], original[1] - rm + added["MID"],
                         original[2] - rf + added["FWD"]]
                if 3 <= final[0] <= 5 and 2 <= final[1] <= 5 and 1 <= final[2] <= 3:
                    priority = sum(1 << (2 - i) for i in range(3) if mask & (1 << i))
                    possible.append((count, priority, mask))
    return max(possible, default=(0, 0, 0))[2]


def autosub_expectation(xi, bench, projection, players):
    # xi/bench here are outfield only. Collapse starting non-appearances by position.
    distribution = {(0, 0, 0): 1.0}
    original = tuple(sum(players[str(i)]["pos"] == p for i in xi) for p in POSITIONS[1:])
    for pid in xi:
        position = POSITIONS[1:].index(players[str(pid)]["pos"])
        p = projection[pid]["p_appear"]; updated = defaultdict(float)
        for missing, probability in distribution.items():
            updated[missing] += probability * p
            added = list(missing); added[position] += 1
            updated[tuple(added)] += probability * (1 - p)
        distribution = {k: v for k, v in updated.items() if v > 1e-12}
    positions = tuple(players[str(i)]["pos"] for i in bench)
    extra = 0.0
    for mask in range(8):
        probability = math.prod(projection[pid]["p_appear"] if mask & (1 << j) else 1 - projection[pid]["p_appear"]
                                for j, pid in enumerate(bench))
        if probability < 1e-12: continue
        for missing, chance in distribution.items():
            submask = bench_subset(original, missing, positions, mask)
            extra += probability * chance * sum(projection[pid]["xp"] / max(1e-12, projection[pid]["p_appear"])
                      for j, pid in enumerate(bench) if submask & (1 << j))
    return extra


def best_lineup(ids, forecast_data, gw, include_bench=True):
    players = forecast_data["players"]
    projection = {i: players[str(i)]["weeks"][str(gw)] for i in ids}
    keepers = [i for i in ids if players[str(i)]["pos"] == "GKP"]
    outfield = [i for i in ids if i not in keepers]
    options = []
    for selected in itertools.combinations(outfield, 10):
        counts = Counter(players[str(i)]["pos"] for i in selected)
        if counts["DEF"] < 3 or counts["MID"] < 2 or counts["FWD"] < 1: continue
        for keeper in keepers:
            xi = [keeper, *selected]
            ranked = sorted(xi, key=lambda i: (-projection[i]["xp"], i))
            choices = [(i, ranked[0] if i != ranked[0] else ranked[1]) for i in xi]
            captain, vice = max(choices,
                key=lambda pair: projection[pair[0]]["xp"] + (1 - projection[pair[0]]["p_appear"]) * projection[pair[1]]["xp"])
            captain_extra = projection[captain]["xp"] + (1 - projection[captain]["p_appear"]) * projection[vice]["xp"]
            other_keeper = next(i for i in keepers if i != keeper)
            gk_sub = (1 - projection[keeper]["p_appear"]) * projection[other_keeper]["xp"]
            score = sum(projection[i]["xp"] for i in xi) + captain_extra + gk_sub
            options.append((score, xi, captain, vice, other_keeper))
    # Exact enumeration of legal XIs on starter/captain points. Optimise autosubs
    # for the leading eight XIs; this is explicitly a bounded heuristic overall.
    best = None
    for base, xi, captain, vice, keeper in sorted(options, key=lambda r: (-r[0], r[1]))[:8 if include_bench else 1]:
        remaining = [i for i in outfield if i not in xi]
        if include_bench:
            candidates = [(autosub_expectation(xi[1:], order, projection, players), order)
                          for order in itertools.permutations(remaining)]
            extra, order = max(candidates, key=lambda pair: (pair[0], tuple(-i for i in pair[1])))
        else:
            extra = 0; order = tuple(sorted(remaining, key=lambda i: -projection[i]["xp"]))
        result = {"gw": gw, "xi": xi, "bench": [keeper, *order], "captain": captain, "vice_captain": vice,
                  "xp": round(base + extra, 5), "autosub_xp": round(extra, 5),
                  "formation": "-".join(str(sum(players[str(i)]["pos"] == p for i in xi)) for p in POSITIONS[1:])}
        if best is None or result["xp"] > best["xp"]: best = result
    if best is None: raise ValueError("no legal starting XI")
    return best


def compare_plans(forecasts, squad, assumptions, include_bench=True):
    start = forecasts["start_gw"]; players = forecasts["players"]
    if forecasts["assumptions"].get("players", {}) != assumptions.get("players", {}):
        raise ValueError("news assumptions changed: regenerate the forecast before comparing plans")
    if squad.get("team_id") != forecasts["team_id"]:
        raise ValueError("squad belongs to a different FPL entry")
    if any(t["event"] >= start for t in squad.get("transfers", [])):
        reconciled = squad.get("reconciliation", {})
        if (reconciled.get("event") != start or not reconciled.get("source") or not reconciled.get("notes")
                or not assumptions.get("free_transfers_confirmed") or assumptions.get("free_transfers") is None):
            raise ValueError("in-week transfers already exist: reconcile locked picks/bank and confirm free transfers before comparing plans")
    ids = [p["element"] for p in squad.get("picks", [])]
    legal_squad(ids, players)
    if not squad.get("selling_prices") or squad.get("selling_prices_confidence") != "derived":
        raise ValueError("confirmed selling prices are required for transfer comparisons")
    free = assumptions.get("free_transfers")
    estimated = free is None
    free = int(squad["free_transfers_estimate"] if estimated else free)
    if not 0 <= free <= 5: raise ValueError("free transfers must be between 0 and 5")
    plans = assumptions.get("plans") or [{"name": "roll", "moves": []}]
    if not any(p["name"] == "roll" and not p.get("moves") for p in plans):
        plans = [{"name": "roll", "moves": []}, *plans]
    if len({p["name"] for p in plans}) != len(plans): raise ValueError("plan names must be unique")
    results = []
    for plan in plans:
        try:
            owned = ids.copy(); ft = free; bank = round(float(squad["bank"]) * 10)
            purchase = {int(i): int(v["purchase"]) for i, v in squad["selling_prices"].items()}
            weekly = []; total_hit = 0; total_xp = 0
            moves = plan.get("moves", [])
            if any(int(m.get("gw", start)) not in forecasts["gameweeks"] for m in moves):
                raise ValueError("a scheduled move lies outside the comparison horizon")
            for gw in forecasts["gameweeks"]:
                current = [m for m in moves if int(m.get("gw", start)) == gw]
                outgoing = [int(m["out"]) for m in current]; incoming = [int(m["in"]) for m in current]
                if len(set(outgoing)) != len(outgoing) or len(set(incoming)) != len(incoming):
                    raise ValueError("duplicate player in a week's transfer plan")
                if any(i not in owned for i in outgoing) or any(i in owned for i in incoming):
                    raise ValueError("transfer ownership mismatch")
                for pid in outgoing:
                    price = players[str(pid)]["price_tenths"]; paid = purchase[pid]
                    bank += price if price <= paid else paid + (price - paid) // 2
                    owned.remove(pid)
                for pid in incoming:
                    if str(pid) not in players: raise ValueError("unknown incoming player")
                    bank -= players[str(pid)]["price_tenths"]
                    purchase[pid] = players[str(pid)]["price_tenths"]; owned.append(pid)
                if bank < 0: raise ValueError("transfer plan exceeds selling-value budget")
                legal_squad(owned, players)
                hit = 4 * max(0, len(current) - ft)
                ft = max(0, ft - len(current))
                team = best_lineup(owned, forecasts, gw, include_bench)
                team.update({"hit": hit, "bank_tenths": bank, "free_transfers_left": ft, "moves": current})
                weekly.append(team); total_hit += hit; total_xp += team["xp"] - hit
                ft = min(5, ft + 1)
            results.append({"name": plan["name"], "valid": True, "net_xp": round(total_xp, 4),
                            "hits": total_hit, "weekly": weekly, "free_transfers_next": ft})
        except (ValueError, KeyError) as exc:
            results.append({"name": plan["name"], "valid": False, "reason": str(exc)})
    baseline = next(p["net_xp"] for p in results if p["name"] == "roll" and p["valid"])
    for result in results:
        if result["valid"]: result["gain_vs_roll"] = round(result["net_xp"] - baseline, 4)
    return {"model_version": VERSION, "forecast_id": forecasts["forecast_id"],
            "start_gw": start, "horizon": len(forecasts["gameweeks"]),
            "free_transfers": free, "free_transfers_estimated": estimated or not assumptions.get("free_transfers_confirmed", False),
            "decision_assumptions": assumptions,
            "squad_input": {key: squad.get(key) for key in ("team_id", "picks", "bank", "selling_prices",
                              "selling_prices_confidence", "free_transfers_estimate", "reconciliation")},
            "assumptions": ["Future prices fixed at today's market/selling prices; recheck before execution.",
                            "Future transfers occur only where explicitly scheduled. Remaining transfer flexibility is reported, not assigned invented points.",
                            "No chips included. Re-run with an explicitly stated shorter horizon when a wildcard is planned.",
                            "Lineup search evaluates autosubs for the eight best starter/captain XIs, not every XI."],
            "plans": results}


def build_squad(forecasts, budget, width, force=(), exclude=()):
    """Bounded beam search, labelled as a heuristic, never a global optimum."""
    players = forecasts["players"]
    force = tuple(sorted(set(force))); exclude = set(exclude)
    if set(force) & exclude: raise ValueError("a player cannot be both forced and excluded")
    counts = Counter(players[str(i)]["pos"] for i in force)
    if any(counts[p] > QUOTA[p] for p in POSITIONS): raise ValueError("forced players exceed a position quota")
    if max(Counter(players[str(i)]["team"] for i in force).values(), default=0) > 3:
        raise ValueError("forced players exceed the club limit")
    @lru_cache(maxsize=None)
    def total(pid): return sum(w["xp"] for w in players[str(pid)]["weeks"].values())
    pools = {}
    n = forecasts["config"]["pool_per_position"]
    for pos in POSITIONS:
        ids = [int(i) for i, p in players.items() if p["pos"] == pos and int(i) not in exclude and int(i) not in force]
        quality = sorted(ids, key=lambda i: (-total(i), i))[:n]
        value = sorted(ids, key=lambda i: (-total(i) / max(1, players[str(i)]["price_tenths"]), i))[:n]
        cheap = sorted(ids, key=lambda i: (players[str(i)]["price_tenths"], -total(i), i))[:QUOTA[pos] + 3]
        pools[pos] = sorted(set(quality + value + cheap))
    schedule = [pos for pos in POSITIONS for _ in range(QUOTA[pos] - counts[pos])]
    initial_cost = sum(players[str(i)]["price_tenths"] for i in force)
    if initial_cost > budget: raise ValueError("forced players exceed the budget")
    beam = [(0.0, initial_cost, force)]
    for index, pos in enumerate(schedule):
        expanded = []
        for score, spent, selected in beam:
            last = max((i for i in selected if players[str(i)]["pos"] == pos and i not in force), default=-1)
            clubs = Counter(players[str(i)]["team"] for i in selected)
            for pid in pools[pos]:
                p = players[str(pid)]
                if pid <= last or clubs[p["team"]] >= 3: continue
                cost = spent + p["price_tenths"]
                future_min = 0
                for future_pos, count in Counter(schedule[index + 1:]).items():
                    available = sorted(players[str(i)]["price_tenths"] for i in pools[future_pos]
                                       if i not in selected and i != pid and (future_pos != pos or i > pid))
                    if len(available) < count: future_min = budget + 1; break
                    future_min += sum(available[:count])
                if cost + future_min > budget: continue
                ids = (*selected, pid)
                # Approximate bench contribution in partial squads; final candidates
                # are rescored with legal weekly XIs and captaincy below.
                partial = 0.0
                for pp in POSITIONS:
                    values = sorted((total(i) for i in ids if players[str(i)]["pos"] == pp), reverse=True)
                    starters = {"GKP": 1, "DEF": 3, "MID": 4, "FWD": 3}[pp]
                    partial += sum(values[:starters]) + 0.1 * sum(values[starters:])
                partial += max(total(i) for i in ids)
                expanded.append((partial, cost, ids))
        beam = sorted(expanded, key=lambda x: (-x[0], x[1], x[2]))[:width]
        if not beam: raise ValueError("no feasible build found; expand the candidate pool or beam")
    finalists = []
    for _, spent, ids in beam[:30]:
        legal_squad(ids, players)
        teams = [best_lineup(list(ids), forecasts, g, include_bench=False) for g in forecasts["gameweeks"]]
        finalists.append({"ids": list(ids), "cost_tenths": spent, "starter_captain_xp": round(sum(t["xp"] for t in teams), 4), "weekly": teams})
    finalists.sort(key=lambda x: (-x["starter_captain_xp"], x["cost_tenths"]))
    for result in finalists[:3]:
        result["weekly"] = [best_lineup(result["ids"], forecasts, g) for g in forecasts["gameweeks"]]
        result["xp_with_autosubs"] = round(sum(t["xp"] for t in result["weekly"]), 4)
    return {"model_version": VERSION, "forecast_id": forecasts["forecast_id"],
            "start_gw": forecasts["start_gw"], "budget_tenths": budget, "beam_width": width,
            "forced": list(force), "excluded": sorted(exclude),
            "search": "bounded beam heuristic; not a proven global optimum; repeat at wider beam and compare",
            "candidate_pool": pools, "squads": sorted(finalists[:3], key=lambda x: -x["xp_with_autosubs"])}


def record_decision(forecasts, comparison, name):
    clock = now(); deadline = utc(forecasts["deadlines"][str(forecasts["start_gw"])])
    if forecasts.get("replay") or clock >= deadline:
        raise ValueError("cannot label a replay or post-deadline forecast a prospective decision")
    if (comparison["start_gw"] != forecasts["start_gw"] or comparison["model_version"] != forecasts["model_version"]
            or comparison.get("forecast_id") != forecasts.get("forecast_id")):
        raise ValueError("comparison and forecast do not match")
    chosen = next(p for p in comparison["plans"] if p["name"] == name)
    if not chosen["valid"]: raise ValueError("cannot record an invalid plan")
    return {"schema_version": 1, "created_at": clock.isoformat(), "status": "recommended_not_confirmed_executed",
            "chosen_plan": name, "forecast": forecasts, "comparison": comparison,
            "notes": ["This records advice, not an FPL account action. Keep the input packet with this file.",
                      "An attachment is durable only when retained or committed. The script does not upload it."]}


def comparison_from_build(result):
    return {"start_gw": result["start_gw"], "model_version": result["model_version"],
            "forecast_id": result["forecast_id"], "search": result["search"], "build_result": result,
            "plans": [{"name": "build-" + str(index + 1), "valid": True,
                       "weekly": [{**w, "hit": 0} for w in team["weekly"]]}
                      for index, team in enumerate(result["squads"])]}


def review_decision(record, history):
    forecast_data = record["forecast"]; gw = forecast_data["start_gw"]
    if utc(record["created_at"]) >= utc(forecast_data["deadlines"][str(gw)]):
        raise ValueError("record was not created before its deadline")
    actual = {int(r["player_id"]): r for r in history if int(r["gw"]) == gw}
    if not actual: raise ValueError("gameweek not completed/captured yet")
    selected = next(p for p in record["comparison"]["plans"] if p["name"] == record["chosen_plan"])["weekly"][0]
    xi, bench = selected["xi"], selected["bench"]
    scope = set(xi + bench)
    for p in record["comparison"]["plans"]:
        if p.get("valid"):
            scope.update(p["weekly"][0]["xi"] + p["weekly"][0]["bench"])
    if any(i not in actual for i in scope): raise ValueError("outcomes missing for a forecast candidate")
    players = forecast_data["players"]
    rows = []
    for pid in sorted(scope):
        predicted = players[str(pid)]["weeks"][str(gw)]; observed = actual[pid]
        appeared = number(observed, "minutes") > 0
        # DGW aggregate minutes cannot identify the per-fixture >=60 event.
        is_double = "|" in observed.get("fixture_id", "")
        rows.append({"id": pid, "name": players[str(pid)]["name"], "xp": predicted["xp"],
                     "actual_points": number(observed, "total_points"),
                     "error_actual_minus_expected": round(number(observed, "total_points") - predicted["xp"], 4),
                     "appearance_brier": (predicted["p_appear"] - int(appeared)) ** 2,
                     "p60_brier": None if is_double else (predicted["p60"] - int(number(observed, "minutes") >= 60)) ** 2})
    active = [i for i in xi if number(actual[i], "minutes") > 0]
    if not number(actual[xi[0]], "minutes") and number(actual[bench[0]], "minutes"):
        active.append(bench[0])
    original = tuple(sum(players[str(i)]["pos"] == p for i in xi[1:]) for p in POSITIONS[1:])
    missing = tuple(sum(players[str(i)]["pos"] == p and not number(actual[i], "minutes") for i in xi[1:]) for p in POSITIONS[1:])
    mask = sum(1 << j for j, pid in enumerate(bench[1:]) if number(actual[pid], "minutes") > 0)
    chosen = bench_subset(original, missing, tuple(players[str(i)]["pos"] for i in bench[1:]), mask)
    active += [pid for j, pid in enumerate(bench[1:]) if chosen & (1 << j)]
    captain = selected["captain"] if number(actual[selected["captain"]], "minutes") else selected["vice_captain"]
    score = sum(number(actual[i], "total_points") for i in active) - selected["hit"]
    if number(actual[captain], "minutes"): score += number(actual[captain], "total_points")
    return {"gw": gw, "status": record["status"], "recommended_team_points": score,
            "recommended_team_xp": selected["xp"] - selected["hit"], "players": rows,
            "candidate_mae": sum(abs(r["error_actual_minus_expected"]) for r in rows) / len(rows),
            "appearance_brier": sum(r["appearance_brier"] for r in rows) / len(rows),
            "interpretation": "Descriptive forecast errors, not a quantified measure of luck. Review multiple prospective gameweeks; never tune to one result."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("forecast")
    p.add_argument("--data-dir", type=Path, default=Path("data")); p.add_argument("--model-dir", type=Path, default=ROOT / "model")
    p.add_argument("--assumptions", type=Path); p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--as-of", help="historical replay only; records cannot be prospective")
    p.add_argument("--source-ref", default="unrecorded; retain input packet"); p.add_argument("--output", required=True)
    p = sub.add_parser("compare")
    p.add_argument("--forecast", required=True); p.add_argument("--squad", default="data/squad.json")
    p.add_argument("--assumptions", required=True); p.add_argument("--output", required=True)
    p = sub.add_parser("build")
    p.add_argument("--forecast", required=True); p.add_argument("--budget-tenths", required=True, type=int)
    p.add_argument("--beam-width", type=int, default=400); p.add_argument("--output", required=True)
    p.add_argument("--force", default="", help="comma-separated player IDs")
    p.add_argument("--exclude", default="", help="comma-separated player IDs")
    p = sub.add_parser("record")
    p.add_argument("--forecast", required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--comparison"); source.add_argument("--build-result")
    p.add_argument("--plan", required=True); p.add_argument("--output", required=True)
    p = sub.add_parser("review")
    p.add_argument("--record", required=True); p.add_argument("--history", default="data/player_history.csv")
    p.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "forecast":
            assumptions = read_json(args.assumptions) if args.assumptions else {}
            result = forecast(args.data_dir, args.model_dir, assumptions, args.horizon,
                              utc(args.as_of) if args.as_of else now(), bool(args.as_of), args.source_ref)
        elif args.command == "compare":
            result = compare_plans(read_json(args.forecast), read_json(args.squad), read_json(args.assumptions))
        elif args.command == "build":
            if args.budget_tenths <= 0 or args.beam_width < 10: raise ValueError("invalid build budget or beam width")
            forced = [int(i) for i in args.force.split(",") if i]
            excluded = [int(i) for i in args.exclude.split(",") if i]
            result = build_squad(read_json(args.forecast), args.budget_tenths, args.beam_width, forced, excluded)
        elif args.command == "record":
            comparison = read_json(args.comparison) if args.comparison else comparison_from_build(read_json(args.build_result))
            result = record_decision(read_json(args.forecast), comparison, args.plan)
        else:
            result = review_decision(read_json(args.record), read_csv(args.history))
        write_json(args.output, result, exclusive=args.command == "record")
        print(json.dumps({"command": args.command, "model_version": VERSION, "output": args.output}))
    except (ValueError, KeyError, StopIteration, FileExistsError) as exc:
        parser.exit(2, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Offline behavioural checks for scoring, legal choices and honest records."""
import copy
import json
import math
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fpl_advisor as advisor


def tiny_forecast():
    positions = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3 + ["DEF", "MID", "FWD"]
    players = {}
    for pid, pos in enumerate(positions, 1):
        players[str(pid)] = {"id": pid, "name": "Player" + str(pid), "team": "T" + str(pid % 7),
                             "pos": pos, "price_tenths": 50,
                             "weeks": {str(g): {"xp": 2.0 + pid / 100, "p_appear": 1.0,
                                               "p60": 1.0, "expected_minutes": 90}
                                       for g in (8, 9)}}
    return {"model_version": advisor.VERSION, "forecast_id": "fixture-forecast", "season": "2026/27",
            "team_id": 790889, "start_gw": 8, "gameweeks": [8, 9], "replay": False,
            "deadlines": {"8": "2026-10-23T17:30:00Z"}, "assumptions": {},
            "config": {"pool_per_position": 20}, "players": players}


def tiny_squad():
    return {"team_id": 790889, "picks": [{"element": i} for i in range(1, 16)],
            "bank": 1.0, "selling_prices_confidence": "derived", "free_transfers_estimate": 1,
            "selling_prices": {str(i): {"purchase": 50, "selling": 50} for i in range(1, 16)},
            "transfers": []}


class ScoringTests(unittest.TestCase):
    def test_poisson_threshold_not_linear_average(self):
        self.assertAlmostEqual(advisor.poisson_tail(1, 1), 1 - math.exp(-1))
        self.assertLess(advisor.poisson_tail(6, 12), 0.1)
        self.assertEqual(advisor.poisson_tail(0, 10), 0)

    def test_expected_save_and_conceded_blocks(self):
        mean = 2.4
        brute = sum(math.exp(-mean) * mean ** k / math.factorial(k) * (k // 3) for k in range(50))
        self.assertAlmostEqual(advisor.expected_blocks(mean, 3), brute)
        self.assertNotAlmostEqual(advisor.expected_blocks(mean, 3), mean / 3)

    def test_goal_values_and_sixty_minute_gate(self):
        rates = dict.fromkeys(advisor.RATE_FIELDS, 0.0); rates["extra_assists"] = 0
        rates["expected_goals"] = 1
        for pos, points in advisor.GOAL_POINTS.items():
            projected = advisor.fixture_points(pos, rates, [(1, 90)], 1, 0, 1, 0)
            self.assertEqual(projected["goals"], points)
        short = advisor.fixture_points("DEF", rates, [(1, 59)], 1, 0, 1, 0.5)
        long = advisor.fixture_points("DEF", rates, [(1, 60)], 1, 0, 1, 0.5)
        self.assertEqual(short["appearance"], 1)
        self.assertEqual(short["clean_sheet"], 0)
        self.assertEqual(long["appearance"], 2)
        self.assertEqual(long["clean_sheet"], 4)
        self.assertEqual(long["defensive_contribution"], 1)

    def test_start_rate_never_uses_starts_per_90(self):
        config = json.loads((ROOT / "model/config.json").read_text())
        player = {"st": "a", "starts_per_90": "3.0"}
        rows = [{"gw": g, "starts": 1, "minutes": 30, "fixture_id": str(g)} for g in range(1, 7)]
        states, values = advisor.minute_states(player, rows, {"starts": 38}, config, {})
        self.assertEqual(values["p_start"], 1)
        self.assertLessEqual(sum(p for p, _ in states), 1)
        player["starts_per_90"] = "0.01"
        self.assertEqual(advisor.minute_states(player, rows, {"starts": 38}, config, {})[0], states)

    def test_dgw_is_two_start_opportunities(self):
        config = json.loads((ROOT / "model/config.json").read_text())
        rows = [{"gw": 1, "starts": 2, "minutes": 180, "fixture_id": "1|2"}]
        _, values = advisor.minute_states({"st": "a"}, rows, {"starts": 38}, config, {})
        self.assertEqual(values["p_start"], 1)
        self.assertEqual(values["minutes_60"], 85)

    def test_cameo_can_reach_sixty_and_remains_eligible(self):
        config = json.loads((ROOT / "model/config.json").read_text())
        rows = [{"gw": 1, "starts": 0, "minutes": 75, "fixture_id": "1"}]
        _, values = advisor.minute_states({"st": "a"}, rows, {}, config, {})
        self.assertEqual(values["minutes_cameo"], 75)

    def test_invalid_probability_overrides_fail(self):
        config = json.loads((ROOT / "model/config.json").read_text())
        with self.assertRaisesRegex(ValueError, "exceed"):
            advisor.minute_states({"st": "a"}, [], {}, config, {"p_start": .9, "p_cameo": .5})


class LegalChoiceTests(unittest.TestCase):
    def test_defender_required_over_higher_priority_midfielder(self):
        mask = advisor.bench_subset((3, 4, 3), (1, 0, 0), ("MID", "DEF", "DEF"), 7)
        self.assertEqual(mask, 2)

    def test_flexible_formation_allows_first_midfielder(self):
        mask = advisor.bench_subset((4, 3, 3), (1, 0, 0), ("MID", "DEF", "FWD"), 7)
        self.assertEqual(mask, 1)

    def test_two_absences_preserve_bench_priority_and_formation(self):
        mask = advisor.bench_subset((3, 4, 3), (1, 1, 0), ("MID", "DEF", "FWD"), 7)
        self.assertEqual(mask, 3)

    def test_cameo_probability_not_start_is_autosub_gate(self):
        data = tiny_forecast(); players = data["players"]
        xi = [3, 4, 5, 8, 9, 10, 11, 13, 14, 15]
        bench = [12, 6, 7]
        projection = {i: {"p_appear": 1.0, "xp": 2.0} for i in xi + bench}
        projection[3] = {"p_appear": 0, "xp": 0}
        projection[6] = {"p_appear": 1, "p_start": 0, "xp": 1}
        self.assertEqual(advisor.autosub_expectation(xi, bench, projection, players), 1)

    def test_lineup_is_legal_and_captain_is_owned(self):
        data = tiny_forecast(); ids = list(range(1, 16))
        result = advisor.best_lineup(ids, data, 8)
        self.assertEqual(len(result["xi"]), 11)
        self.assertEqual(set(result["xi"] + result["bench"]), set(ids))
        self.assertIn(result["captain"], result["xi"])
        self.assertNotEqual(result["captain"], result["vice_captain"])

    def test_sale_profit_rounding_can_make_market_affordable_move_invalid(self):
        data = tiny_forecast(); squad = tiny_squad()
        data["players"]["3"]["price_tenths"] = 51
        squad["selling_prices"]["3"] = {"purchase": 40, "selling": 45}
        data["players"]["16"]["price_tenths"] = 60
        assumptions = {"plans": [{"name": "too-tight", "moves": [{"out": 3, "in": 16}]}]}
        result = advisor.compare_plans(data, squad, assumptions, include_bench=False)
        bad = next(p for p in result["plans"] if p["name"] == "too-tight")
        self.assertFalse(bad["valid"])
        self.assertIn("budget", bad["reason"])

    def test_hits_and_staged_transfer_banking(self):
        data = tiny_forecast(); squad = tiny_squad()
        assumptions = {"free_transfers": 1, "free_transfers_confirmed": True, "plans": [
            {"name": "hit", "moves": [{"out": 3, "in": 16}, {"out": 8, "in": 17}]},
            {"name": "staged", "moves": [{"out": 3, "in": 16}, {"gw": 9, "out": 8, "in": 17}]}]}
        result = advisor.compare_plans(data, squad, assumptions, include_bench=False)
        plans = {p["name"]: p for p in result["plans"]}
        self.assertEqual(plans["hit"]["hits"], 4)
        self.assertEqual(plans["staged"]["hits"], 0)
        self.assertEqual(plans["roll"]["weekly"][1]["free_transfers_left"], 2)
        self.assertFalse(result["free_transfers_estimated"])

    def test_club_cap_and_position_quota_enforced(self):
        data = tiny_forecast(); squad = tiny_squad()
        for i in [1, 2, 4]: data["players"][str(i)]["team"] = "OVER"
        data["players"]["16"]["team"] = "OVER"
        assumptions = {"plans": [{"name": "four-club", "moves": [{"out": 3, "in": 16}]},
                                  {"name": "wrong-position", "moves": [{"out": 3, "in": 17}]}]}
        result = advisor.compare_plans(data, squad, assumptions, include_bench=False)
        self.assertTrue(result["plans"][0]["valid"])
        self.assertFalse(result["plans"][1]["valid"])
        self.assertFalse(result["plans"][2]["valid"])

    def test_in_week_edits_and_changed_news_refused(self):
        data = tiny_forecast(); squad = tiny_squad()
        squad["transfers"] = [{"event": 8}]
        with self.assertRaisesRegex(ValueError, "in-week"):
            advisor.compare_plans(data, squad, {})
        squad["transfers"] = []
        with self.assertRaisesRegex(ValueError, "assumptions changed"):
            advisor.compare_plans(data, squad, {"players": {"3": {}}})

    def test_sourced_in_week_reconciliation_is_supported_and_retained(self):
        data = tiny_forecast(); squad = tiny_squad()
        squad["transfers"] = [{"event": 8}]
        squad["reconciliation"] = {"event": 8, "source": "user-confirmed FPL state",
                                   "notes": "Owned IDs, purchase prices and bank reconciled after the existing move"}
        assumptions = {"free_transfers": 0, "free_transfers_confirmed": True}
        result = advisor.compare_plans(data, squad, assumptions, include_bench=False)
        self.assertTrue(result["plans"][0]["valid"])
        self.assertEqual(result["squad_input"]["reconciliation"], squad["reconciliation"])

    def test_build_budget_force_exclude_and_legality(self):
        data = tiny_forecast()
        result = advisor.build_squad(data, 750, 30, force=[16], exclude=[3])
        self.assertTrue(result["squads"])
        for team in result["squads"]:
            advisor.legal_squad(team["ids"], data["players"])
            self.assertLessEqual(team["cost_tenths"], 750)
            self.assertIn(16, team["ids"])
            self.assertNotIn(3, team["ids"])


class IntegrityAndRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temp.name)
        self.config = json.loads((ROOT / "model/config.json").read_text())
        self.stamp = "2026-10-22T18:00:00+00:00"
        self.meta = {"season": "2026/27", "next_event": 8, "next_deadline": "2026-10-23T17:30:00Z",
                     "fetched_at": self.stamp, "teams_in_game": ["T" + str(i) for i in range(20)],
                     "player_count": 1, "stale": False, "warnings": [],
                     "events": [{"id": 7, "data_checked": True, "deadline_time": "2026-10-17T10:00:00Z"},
                                {"id": 8, "deadline_time": "2026-10-23T17:30:00Z"}]}
        advisor.write_json(self.root / "meta.json", self.meta)
        self.status = {n: {"last_run_at": self.stamp, "expected_interval_minutes": 60}
                       for n in ["build_fpl", "build_squad", "build_odds", "build_player_history"]}
        advisor.write_json(self.root / "build_status.json", self.status)
        self.core("players.csv", "id,name,pos,team,price,st,mins,starts,expected_goals,expected_assists\n1,Test,MID,T0,5.0,a,90,1,0.5,0.1\n")
        self.core("teams.csv", "team\nT0\n")
        self.core("fixtures.csv", "gw,home,away,kickoff,fixture_id,finished,team_h_score,team_a_score\n7,T0,T1,2026-10-17,1,True,2,0\n8,T0,T1,2026-10-23,2,False,,\n")
        (self.root / "player_history.csv").write_text("# fetched=2026-10-20T17:41:13+00:00\ngw,player_id,minutes,starts,expected_goals,expected_assists,fixture_id\n7,1,90,1,0.5,0.1,1\n")
        (self.root / "odds.csv").write_text("kickoff_utc,home_code,away_code,xg_home,xg_away,n_books_totals,fetched_at\n2026-10-23T19:00:00Z,T0,T1,1.5,1,4,2026-10-22T18:00:00Z\n")
        advisor.write_json(self.root / "squad.json", {"team_id": 790889})
        self.clock = advisor.utc("2026-10-22T18:05:00Z")

    def tearDown(self): self.temp.cleanup()

    def core(self, name, body):
        (self.root / name).write_text("# fetched=" + self.stamp + "\n" + body)

    def test_old_history_header_is_not_stale_if_coverage_complete(self):
        meta, history = advisor.validate_data(self.root, self.config, self.clock)
        self.assertEqual(meta["next_event"], 8)
        self.assertEqual(len(history), 1)

    def test_missing_completed_gameweek_fails(self):
        (self.root / "player_history.csv").write_text("gw,player_id\n6,1\n")
        with self.assertRaisesRegex(ValueError, "completed gameweeks missing"):
            advisor.validate_data(self.root, self.config, self.clock)

    def test_duplicate_history_cannot_silently_double_count_rates(self):
        with (self.root / "player_history.csv").open("a") as f:
            f.write("7,1,90,1,0.5,0.1,1\n")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            advisor.validate_data(self.root, self.config, self.clock)

    def test_stale_component_and_core_cache_mismatch_fail(self):
        self.status["build_squad"]["last_run_at"] = "2026-10-20T18:00:00Z"
        advisor.write_json(self.root / "build_status.json", self.status)
        with self.assertRaisesRegex(ValueError, "component is stale"):
            advisor.validate_data(self.root, self.config, self.clock)
        self.status["build_squad"]["last_run_at"] = self.stamp
        advisor.write_json(self.root / "build_status.json", self.status)
        (self.root / "teams.csv").write_text("# old\nteam\nT0\n")
        with self.assertRaisesRegex(ValueError, "timestamp mismatch"):
            advisor.validate_data(self.root, self.config, self.clock)

    def test_new_season_requires_new_priors(self):
        self.meta["season"] = "2027/28"; advisor.write_json(self.root / "meta.json", self.meta)
        with self.assertRaisesRegex(ValueError, "season differs"):
            advisor.validate_data(self.root, self.config, self.clock)

    def test_frozen_meta_fails_even_when_component_status_is_fresh(self):
        self.meta["fetched_at"] = "2026-10-20T18:00:00Z"
        advisor.write_json(self.root / "meta.json", self.meta)
        with self.assertRaisesRegex(ValueError, "core timestamp is stale"):
            advisor.validate_data(self.root, self.config, self.clock)

    def test_fixed_forecast_ignores_fpl_projection_and_ownership(self):
        model = self.root / "model"; model.mkdir()
        config = {**self.config, "prior_file": "priors.csv"}
        advisor.write_json(model / "config.json", config)
        (model / "priors.csv").write_text("id,pos,mins,starts,expected_goals,expected_assists,a\n1,MID,1800,20,4,3,4\n")
        first = advisor.forecast(self.root, model, {}, 1, self.clock, True, "fixture")
        self.core("players.csv", "id,name,pos,team,price,st,mins,starts,expected_goals,expected_assists,form,ep_next,own\n1,Test,MID,T0,5.0,a,90,1,0.5,0.1,999,999,100\n")
        second = advisor.forecast(self.root, model, {}, 1, self.clock, True, "fixture")
        self.assertEqual(first["players"], second["players"])
        self.assertNotEqual(first["forecast_id"], second["forecast_id"])

    def test_record_cannot_overwrite_or_cross_deadline_or_relabel_replay(self):
        data = tiny_forecast()
        comparison = advisor.compare_plans(data, tiny_squad(), {}, include_bench=False)
        with patch.object(advisor, "now", return_value=self.clock):
            record = advisor.record_decision(data, comparison, "roll")
            self.assertEqual(record["status"], "recommended_not_confirmed_executed")
            path = self.root / "record.json"; advisor.write_json(path, record, exclusive=True)
            with self.assertRaises(FileExistsError): advisor.write_json(path, record, exclusive=True)
            data["replay"] = True
            with self.assertRaisesRegex(ValueError, "replay or post-deadline"):
                advisor.record_decision(data, comparison, "roll")
        data["replay"] = False
        with patch.object(advisor, "now", return_value=advisor.utc("2026-10-24T00:00:00Z")):
            with self.assertRaises(ValueError): advisor.record_decision(data, comparison, "roll")

    def test_record_binds_comparison_to_forecast(self):
        data = tiny_forecast()
        comparison = advisor.compare_plans(data, tiny_squad(), {}, include_bench=False)
        comparison["forecast_id"] = "another-forecast"
        with patch.object(advisor, "now", return_value=self.clock):
            with self.assertRaisesRegex(ValueError, "do not match"):
                advisor.record_decision(data, comparison, "roll")

    def test_review_scores_actual_autosub_and_vice_without_claiming_execution(self):
        data = tiny_forecast()
        comparison = advisor.compare_plans(data, tiny_squad(), {}, include_bench=False)
        with patch.object(advisor, "now", return_value=self.clock):
            record = advisor.record_decision(data, comparison, "roll")
        captain = comparison["plans"][0]["weekly"][0]["captain"]
        history = [{"gw": 8, "player_id": i, "minutes": 0 if i == captain else 1,
                    "total_points": 0 if i == captain else 2, "fixture_id": "8"} for i in range(1, 16)]
        review = advisor.review_decision(record, history)
        self.assertEqual(review["recommended_team_points"], 24)
        self.assertEqual(review["status"], "recommended_not_confirmed_executed")
        self.assertGreater(review["appearance_brier"], 0)


class PlaybookContractTests(unittest.TestCase):
    def test_project_routes_stay_exactly_where_existing_instructions_point(self):
        project = (ROOT / "prompts/project.md").read_text()
        for name, version in [("weekly", 4), ("preseason", 4), ("smoke-test", 3)]:
            url = "https://raw.githubusercontent.com/olly1928/fpl-mirror/main/prompts/" + name + ".md"
            self.assertIn(url, project)
            text = (ROOT / "prompts" / (name + ".md")).read_text()
            self.assertIn("v" + str(version), text.splitlines()[0])
            self.assertIn("first response line", text.lower())
            self.assertIn("version stamp (top line)", text)
            self.assertEqual(text.rstrip().splitlines()[-1], "<!-- end of " + name + ".md v" + str(version) + " — confirm this line was reached -->")

    def test_health_route_does_not_run_the_model(self):
        text = (ROOT / "prompts/smoke-test.md").read_text()
        self.assertIn("Do not forecast", text)
        self.assertNotIn("python fpl_advisor.py forecast", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

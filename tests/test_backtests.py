"""Temporal boundaries and source hygiene for the predictive backtest."""
import csv
import contextlib
import io
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'backtests'/f'{name}.py')
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


class BacktestTests(unittest.TestCase):
    def test_research_poisson_speedup_matches_live_formula(self):
        history = module('historical_predictive')
        spec = importlib.util.spec_from_file_location('original_formula', ROOT/'fpl_advisor.py')
        original = importlib.util.module_from_spec(spec); spec.loader.exec_module(original)
        research = history.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
        for mean in (0, .1, 2.4, 8, 20):
            for size in (2, 3):
                self.assertAlmostEqual(original.expected_blocks(mean, size), research.expected_blocks(mean, size), places=10)

    def test_postponed_duplicate_keeps_actual_fixture_and_missing_xg(self):
        normaliser = module('history_data')
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp); folder = root/'archive/2019-20'
            (folder/'gws').mkdir(parents=True)
            players = [{'id': '1', 'code': '123', 'team': '1', 'element_type': '3'}]
            with (folder/'players_raw.csv').open('w') as f:
                writer = csv.DictWriter(f, fieldnames=players[0]); writer.writeheader(); writer.writerows(players)
            common = {'element': '1', 'fixture': '55', 'name': 'Test', 'was_home': 'True',
                      'opponent_team': '2', 'value': '50'}
            rows = [{**common, 'GW': '30', 'kickoff_time': '2020-03-14T12:00:00Z', 'minutes': '0', 'total_points': '0'},
                    {**common, 'GW': '39', 'kickoff_time': '2020-06-17T12:00:00Z', 'minutes': '90', 'total_points': '7'}]
            with (folder/'gws/merged_gw.csv').open('w') as f:
                writer = csv.DictWriter(f, fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)
            with contextlib.redirect_stdout(io.StringIO()):
                result, quality = normaliser.normalise(root/'archive', root/'normalised')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['gw'], 39)
        self.assertEqual(result[0]['total_points'], 7)
        self.assertEqual(result[0]['expected_goals'], '')
        self.assertFalse(result[0]['has_xg'])
        self.assertEqual(quality[0]['postponed_placeholders_removed'], 1)

    def test_manifest_corruption_is_rejected(self):
        acquire = module('acquire')
        record = {'bytes': 3, 'sha256': 'incorrect', 'url': 'https://example.com/source'}
        with self.assertRaisesRegex(ValueError, 'pinned manifest'):
            acquire.verify(b'abc', record)

    def test_previous_season_codes_not_recycled_ids(self):
        history = module('historical_predictive')
        model = history.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
        base = json.loads((ROOT/'backtests/baseline-v1/model/config.json').read_text())
        rows = [{**dict.fromkeys(history.STATS, '0'), 'gw': str(gw), 'fixture_id': str(gw),
                 'kickoff_time': f'2023-08-{gw:02d}T12:00:00Z', 'player_id': '1',
                 'code': '123', 'pos': 'MID', 'name': 'Test', 'team_id': '1',
                 'opponent_team': '2', 'was_home': 'True', 'minutes': '90', 'has_xg': '1'}
                for gw in (1, 2)]
        with tempfile.TemporaryDirectory() as temp:
            archive = pathlib.Path(temp); (archive/'2022-23').mkdir()
            prior_rows = [{'id': '1', 'code': '999', 'minutes': '300', 'assists': '0', 'element_type': '3'},
                          {'id': '77', 'code': '123', 'minutes': '1700', 'assists': '0', 'element_type': '3'}]
            with (archive/'2022-23/players_raw.csv').open('w') as f:
                w = csv.DictWriter(f, fieldnames=prior_rows[0]); w.writeheader(); w.writerows(prior_rows)
            cases, _ = history.cases_for_season('2023-24', {'2023-24': rows}, archive, model, base)
        self.assertEqual(cases[0]['prior']['mins'], '1700')

    def test_dgw_aggregation_retains_fixture_opportunities(self):
        history = module('historical_predictive')
        items = [{**dict.fromkeys(history.STATS, ''), 'gw': '2', 'fixture_id': str(i),
                  'minutes': '90', 'starts': '1'} for i in (1, 2)]
        result = history.aggregate(items)
        self.assertEqual(result[0]['fixture_id'], '1|2')
        self.assertEqual(result[0]['starts'], 2)
        self.assertEqual(result[0]['minutes'], 180)

    def test_future_values_cannot_change_rolling_forecast(self):
        history = module('historical_predictive')
        case = {'fixture_rows': [{'kickoff_time': '2020-01-01', 'total_points': '5', 'minutes': '90'}],
                'old': [], 'actual_points': 100}
        before = history.rolling_prediction(case, 3)
        case['actual_points'] = -100
        case['xP'] = 999
        self.assertEqual(before, history.rolling_prediction(case, 3))

    def test_cutoff_excludes_same_gw_and_late_previous_fixtures(self):
        history = module('historical_predictive')
        model = history.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
        base = json.loads((ROOT/'backtests/baseline-v1/model/config.json').read_text())
        def row(gw, fixture, date, points):
            return {**dict.fromkeys(history.STATS, '0'), 'gw': str(gw), 'fixture_id': str(fixture),
                    'kickoff_time': date, 'player_id': '1', 'code': '123', 'pos': 'MID',
                    'name': 'Test', 'team_id': '1', 'opponent_team': '2', 'was_home': 'True',
                    'minutes': '90', 'starts': '1', 'total_points': str(points), 'has_xg': '1'}
        rows = [row(1, 1, '2023-08-01T12:00:00Z', 3),
                row(2, 2, '2023-08-10T12:00:00Z', 999),
                row(3, 3, '2023-08-10T13:00:00Z', 999)]
        with tempfile.TemporaryDirectory() as temp:
            cases, _ = history.cases_for_season('2023-24', {'2023-24': rows},
                                                pathlib.Path(temp), model, base)
        target = next(c for c in cases if c['gw'] == 3)
        self.assertEqual(len(target['rows']), 1)
        self.assertEqual(target['rows'][0]['total_points'], 3)

    def test_finite_selection_grid_has_no_current_season_results(self):
        history = module('historical_predictive')
        base = json.loads((ROOT/'backtests/baseline-v1/model/config.json').read_text())
        self.assertEqual(len(history.variants(base)), 17)
        self.assertNotIn('2026-27', history.DEVELOPMENT + (history.VALIDATION,) + history.HOLDOUTS)


if __name__ == '__main__':
    unittest.main()

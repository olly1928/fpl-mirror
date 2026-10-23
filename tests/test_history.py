"""Chronology, probability coherence and raw-byte retention for history research."""
import importlib.util
import json
import pathlib
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'history'/f'{name}.py')
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


class HistoricalResearchTests(unittest.TestCase):
    def test_minute_thresholds_are_disjoint(self):
        minutes = load('minutes')
        self.assertEqual([minutes.state(x) for x in (0, 1, 59, 60, 90)], [0, 1, 1, 2, 2])

    def test_learned_probabilities_sum_to_one(self):
        minutes = load('minutes'); model = minutes.Model()
        keys = minutes.features('MID', 2700, [{'minutes':90}], 6)
        for value in (0, 15, 59, 60, 90):
            model.add(keys, value)
        probabilities, means = model.predict(keys)
        self.assertAlmostEqual(sum(probabilities), 1)
        self.assertTrue(all(0 <= p <= 1 for p in probabilities))
        self.assertTrue(1 <= means[1] < 60 <= means[2] <= 90)

    def test_features_do_not_read_target_or_postmatch_xp(self):
        minutes = load('minutes')
        rows = [{'minutes':90, 'total_points':2, 'xP':0}]
        first = minutes.features('DEF', 2000, rows, 6)
        rows[0]['total_points'] = 1000; rows[0]['xP'] = 999
        self.assertEqual(first, minutes.features('DEF', 2000, rows, 6))

    def test_target_year_labels_cannot_change_fitted_model(self):
        research = load('research')
        def row(year, minutes):
            return {'minutes':minutes, 'eligible':1,
                    'keys':{w:research.MINUTES.features('MID', 2000, [{'minutes':90}], w) for w in (3, 6, 10)}}
        datasets = {f'{year}-{str(year+1)[-2:]}':[row(year, m) for m in (0, 30, 90)] for year in range(2016, 2020)}
        first, audit = research.select_and_fit(datasets, '2019-20')
        datasets['2019-20'] = [row(2019, 0)]*100
        second, _ = research.select_and_fit(datasets, '2019-20')
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(audit['inner_validation'], '2018-19')
        self.assertTrue(all(s < '2018-19' for s in audit['inner_training_seasons']))
        self.assertTrue(all(s < '2019-20' for s in audit['training_seasons']))

    def test_empty_points_fit_is_identity_and_absence_stays_zero(self):
        research = load('research'); coefficients = research.fit_points([])['coefficients']
        self.assertEqual(coefficients, [1, 0, 0, 0, 0])
        self.assertEqual(research.calibrated(0, 0, 'MID', [2, 3, 4, 5, 6]), 0)

    def test_research_availability_flags_are_respected(self):
        research = load('research'); learned = research.MINUTES.Model()
        core = research.OLD.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
        wrapped = research.wrap_current(core, learned)
        states, _ = wrapped.minute_states({'pos':'MID', 'st':'i'}, [], {}, {}, {})
        self.assertEqual(sum(p for p,m in states), 0)

    def test_raw_package_is_deterministic_and_lossless(self):
        collector = load('collect_history')
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp); source = root/'source'; source.mkdir()
            path = source/'response.json'; raw = b'{"history_past":[]}\r\n'; path.write_bytes(raw)
            first = root/'first.tar.gz'; second = root/'second.tar.gz'
            collector.pack(first, source, [path]); collector.pack(second, source, [path])
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                self.assertEqual(archive.extractfile('response.json').read(), raw)

    def test_corrupt_cached_source_is_rejected(self):
        collector = load('collect_history')
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp)/'source.json'; path.write_bytes(b'changed')
            path.with_suffix('.json.meta.json').write_text(json.dumps({'url':'https://example.com/data', 'sha256':'bad'}))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                collector.capture('https://example.com/data', path)

    def test_mismatched_prediction_count_is_rejected(self):
        minutes = load('minutes')
        with self.assertRaisesRegex(ValueError, 'count'):
            minutes.metrics([{'minutes':90}], [])

    def test_prior_proposal_selects_explicit_season_and_preserves_source(self):
        proposal = load('propose_prior_repairs')
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp); (root/'official/players').mkdir(parents=True)
            row = {'id':'1', 'name':'Test', **dict.fromkeys(proposal.FIELDS, '0')}
            import csv
            with (root/'frozen.csv').open('w') as f:
                writer = csv.DictWriter(f, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            original = (root/'frozen.csv').read_bytes()
            (root/'official/players/1.json').write_text(json.dumps({'history':[{'minutes':999}],
                'history_past':[{'season_name':'2024/25', 'element_code':99, 'minutes':999},
                                {'season_name':'2025/26', 'element_code':99, 'minutes':90}]}))
            report = proposal.propose(root/'official', root/'frozen.csv', '2025/26', root/'proposal')
            self.assertEqual(report['changes'][0]['fields']['mins']['proposed'], 90)
            self.assertEqual((root/'frozen.csv').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

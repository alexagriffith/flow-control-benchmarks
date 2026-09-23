"""Source-derived scene arithmetic and historical-copy regressions; no benchmarks."""
import csv
import json
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'learn/flow-control-interactive.html'


def js_value(expression):
    html = PAGE.read_text()
    pure = html.split('/* scene metrics start */')[1].split('/* scene metrics end */')[0]
    canon = re.search(r'const CANON=(\{[\s\S]*?\n\});', html)[0]
    knee = re.search(r'const KNEE_DATA=(\[[\s\S]*?\n\]);', html)[0]
    result = subprocess.run(['node', '-e', pure + canon + knee + '\nconsole.log(JSON.stringify(' + expression + '))'], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


class LearnerSceneTests(unittest.TestCase):
    def test_original_arithmetic_contradiction(self):
        result = js_value("sceneMetrics({endpoints:[{waiting:2.8,kv:.85},{waiting:2.6,kv:.9}]})")
        self.assertAlmostEqual(result['pool'], 1.09375)
        self.assertFalse(result['canDispatch'])

    def test_each_detector_at_boundary(self):
        for detector in ('requests', 'tokens', 'hybrid', 'utilization'):
            for score in (.799, .8, .801):
                with self.subTest(detector=detector, score=score):
                    result = js_value(f"sceneMetrics({{detector:'{detector}',ceiling:.8,endpoints:[{{requests:{score*100},tokens:{score*1000},waiting:{score*4},kv:{score*.8}}}]}})")
                    self.assertAlmostEqual(result['pool'], score)
                    self.assertEqual(result['canDispatch'], score < .8)

    def test_two_checks_and_fail_open(self):
        for a,b in ((109,61),(110,60),(120,120)):
            result = js_value(f"sceneMetrics({{detector:'requests',headroom:.1,endpoints:[{{requests:{a}}},{{requests:{b}}}]}})")
            self.assertAlmostEqual(result['pool'], (a+b)/200)
            self.assertEqual(result['eligible'], [a < 110, b < 110])
            self.assertEqual(result['failOpen'], a>=110 and b>=110)
            if result['failOpen']:
                self.assertEqual(result['candidates'], [True,True])
                self.assertFalse(result['canDispatch'])

    def test_filter_truncates_relaxed_integer_limits(self):
        result = js_value("sceneMetrics({detector:'requests',requestLimit:128,headroom:.1,endpoints:[{requests:140},{requests:100}]})")
        self.assertEqual(result['eligible'], [False,True])
        result = js_value("sceneMetrics({detector:'tokens',tokenLimit:68182,headroom:.1,endpoints:[{tokens:75000},{tokens:60000}]})")
        self.assertEqual(result['eligible'], [False,True])

    def test_hybrid_endpoint_max_precedes_average(self):
        result = js_value("sceneMetrics({detector:'hybrid',endpoints:[{requests:90,tokens:100},{requests:10,tokens:900}]})")
        self.assertAlmostEqual(result['pool'], .9)

    def test_pd_max_not_fleet_average(self):
        result = js_value("sceneMetrics({detector:'requests',topology:'pd',endpoints:[{requests:65,role:'prefill'},{requests:90,role:'decode'}]})")
        self.assertEqual(result['stages'], [.65,.9])
        self.assertAlmostEqual(result['pool'], .9)
        combined = js_value("sceneMetrics({detector:'requests',topology:'pd',endpoints:[{requests:60,role:'prefill'},{requests:100,role:'decode'},{requests:80,role:'combined'}]})")
        self.assertEqual(combined['stages'], [.7,.9])

    def test_canonical_copies_and_source_files(self):
        copied = js_value('CANON')
        canonical = json.loads((ROOT / copied['source']).read_text())
        for name, priority in [('premium','100'),('standard','0')]:
            truth = canonical['tiers']['128']['on']['tiers'][priority]
            self.assertEqual(copied['tiers128'][name], {'p95':truth['p95'],'range':truth['p95_range']})
        for mode in ('off','on'):
            self.assertEqual(copied['batch128'][mode]['n429'],canonical['batch']['128'][mode]['n429'])
        # Canonical same-band record names differ only in display spelling.
        truth = canonical['fairness']['128']['on']['tenants']
        for label,value in copied['fairness128']['tenants'].items():
            self.assertEqual(value, truth['premium-'+label.replace(' ','-')]['p95'])
        for section in ('tiers128','batch128','fairness128'):
            paths = copied[section]['source']
            for path in paths if isinstance(paths,list) else [paths]:
                self.assertTrue((ROOT / path).is_file(), path)

    def test_knee_values_match_actual_named_pass(self):
        rows=list(csv.DictReader((ROOT/'benchmark-data/rhaii-3.4-flow-control/operating-point-sweep/pass1/summary.csv').read_text().splitlines()))
        copied=js_value('KNEE_DATA')
        for point,row in zip(copied,rows):
            self.assertEqual(point['c'],int(row['scenario'].rsplit('_',1)[1]))
            self.assertEqual(point['r'],round(float(row['throughput_rps']),1))
            self.assertEqual(point['ttft'],round(float(row['ttft_p95_s'])*1000))
        self.assertEqual(len(copied),len(rows))


if __name__ == '__main__':
    unittest.main()

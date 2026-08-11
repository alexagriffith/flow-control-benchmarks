import unittest

import metrics_preflight
import prometheus_validate as prom


class PrometheusValidationTests(unittest.TestCase):
    def test_combined_report_requires_both_sources(self) -> None:
        self.assertTrue(metrics_preflight.combined_report(
            {"valid": True}, {"valid": True}
        )["valid"])
        self.assertFalse(metrics_preflight.combined_report(
            {"valid": True}, {"valid": False}
        )["valid"])

    def test_build_report_requires_up_targets_and_all_metrics(self) -> None:
        names = [aliases[0] for aliases in prom.metrics_capture.METRIC_ALIASES.values()]
        query = {
            "data": {
                "result": [{"metric": {"__name__": name}, "value": [1, "1"]} for name in names]
            }
        }
        targets = {
            "data": {
                "activeTargets": [
                    {
                        "labels": {"namespace": "benchmark", "service": service},
                        "health": "up",
                        "lastError": "",
                        "scrapeUrl": "http://example/metrics",
                    }
                    for service in ("epp", "vllm")
                ]
            }
        }

        report = prom.build_report(
            targets, query, "benchmark", ("epp", "vllm"), require_flow_control=True
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["missing_metrics"], [])

    def test_build_report_rejects_unhealthy_target(self) -> None:
        targets = {
            "data": {
                "activeTargets": [
                    {"labels": {"namespace": "n", "service": "epp"}, "health": "down"},
                    {"labels": {"namespace": "n", "service": "vllm"}, "health": "up"},
                ]
            }
        }

        report = prom.build_report(
            targets, {"data": {"result": []}}, "n", ("epp", "vllm"), True
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["targets"]["unhealthy_services"], ["epp"])

    def test_range_report_requires_active_queue_samples(self) -> None:
        names = [aliases[0] for aliases in prom.metrics_capture.METRIC_ALIASES.values()]
        queue_duration = prom.metrics_capture.METRIC_ALIASES["epp_flow_queue_duration"][0]
        names.remove(queue_duration)
        names.append(queue_duration + "_count")
        payload = {
            "data": {
                "result": [
                    {"metric": {"__name__": name}, "values": [[1, "1"], [2, "1"]]}
                    for name in names
                ]
            }
        }

        report = prom.build_range_report(payload, True, True)

        self.assertTrue(report["valid"])


if __name__ == "__main__":
    unittest.main()

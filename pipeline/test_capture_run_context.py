import tempfile
import unittest
from pathlib import Path
from unittest import mock

import capture_run_context as context


class CaptureRunContextTests(unittest.TestCase):
    def test_load_envoy_logs_prefers_streamed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "envoy.log"
            path.write_text("complete streamed log")
            with mock.patch.object(context, "kubectl") as kubectl:
                self.assertEqual(
                    context.load_envoy_logs("test", "epp", str(path)),
                    "complete streamed log",
                )
                kubectl.assert_not_called()

    def test_load_envoy_logs_uses_run_start_for_long_runs(self) -> None:
        with mock.patch.object(context, "kubectl", return_value="complete") as kubectl:
            self.assertEqual(
                context.load_envoy_logs(
                    "test", "epp", since_time="2026-08-10T07:34:00Z"
                ),
                "complete",
            )
        kubectl.assert_called_once_with(
            "logs", "-n", "test", "deployment/epp", "-c", "envoy",
            "--since-time=2026-08-10T07:34:00Z",
        )

    def test_parse_envoy_routes_limits_run_window(self) -> None:
        text = (
            "2026-08-05T22:00:00.000Z status=200 flags=- method=POST "
            "path=/v1/completions upstream=10.0.0.1:8000 cluster=x "
            "dest=10.0.0.1:8000 details=via_upstream\\n"
            "2026-08-05T22:02:00.000Z status=200 flags=- method=POST "
            "path=/v1/completions upstream=10.0.0.2:8000 cluster=x "
            "dest=10.0.0.2:8000 details=via_upstream\\n"
        )
        start = 1785967200.0
        report = context.parse_envoy_routes(text, start, start + 60)

        self.assertTrue(report["valid"])
        self.assertEqual(report["completion_requests"], 1)
        self.assertEqual(report["destinations"], ["10.0.0.1:8000"])

    def test_parse_envoy_routes_matches_client_status_counts(self) -> None:
        text = (
            "2026-08-05T22:00:00.000Z status=200 flags=- method=POST "
            "path=/v1/completions upstream=10.0.0.1:8000 cluster=x "
            "dest=10.0.0.1:8000 details=via_upstream\n"
            "2026-08-05T22:00:01.000Z status=429 flags=- method=POST "
            "path=/v1/completions upstream=10.0.0.1:8000 cluster=x "
            "dest=10.0.0.1:8000 details=via_upstream\n"
        )
        start = 1785967200.0

        matching = context.parse_envoy_routes(
            text, start, start + 60, {"200": 1, "429": 1}
        )
        missing = context.parse_envoy_routes(
            text, start, start + 60, {"200": 2, "429": 1}
        )

        self.assertTrue(matching["valid"])
        self.assertTrue(matching["count_matches"])
        self.assertTrue(matching["statuses_match"])
        self.assertEqual(matching["upstream_counts"], {"10.0.0.1:8000": 2})
        self.assertEqual(matching["destination_counts"], {"10.0.0.1:8000": 2})
        self.assertFalse(missing["valid"])
        self.assertFalse(missing["count_matches"])

    def test_parse_envoy_routes_matches_request_and_policy_headers(self) -> None:
        text = (
            "2026-08-05T22:00:00.000Z request_id=req-1 status=200 flags=- "
            "objective=premium fairness=tenant-a dropped_reason=- method=POST "
            "path=/v1/completions upstream=10.0.0.1:8000 cluster=x "
            "dest=10.0.0.1:8000 details=via_upstream\n"
        )
        start = 1785967200.0

        report = context.parse_envoy_routes(
            text, start, start + 60, {"200": 1}, {"req-1"},
            {"tenant-a"}, {"premium"},
        )

        self.assertTrue(report["valid"])
        self.assertTrue(report["request_ids_match"])
        self.assertTrue(report["all_client_requests_observed_at_gateway"])
        self.assertFalse(report["direct_vllm_bypass_detected"])
        self.assertTrue(report["fairness_ids_match"])
        self.assertTrue(report["objectives_match"])
        self.assertEqual(report["response_flags"], {"-": 1})

    def test_parse_envoy_routes_rejects_missing_request_id(self) -> None:
        text = (
            "2026-08-05T22:00:00.000Z status=200 flags=- method=POST "
            "path=/v1/completions upstream=10.0.0.1:8000 cluster=x "
            "dest=10.0.0.1:8000 details=via_upstream\n"
        )
        start = 1785967200.0

        report = context.parse_envoy_routes(
            text, start, start + 60, {"200": 1}, {"req-1"}, set(), set(),
        )

        self.assertFalse(report["valid"])
        self.assertFalse(report["request_ids_match"])


if __name__ == "__main__":
    unittest.main()

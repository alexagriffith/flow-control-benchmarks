#!/usr/bin/env python3
"""Offline tests for shared metrics capture."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import metrics_capture as metrics


VLLM = """
# HELP vllm:num_requests_running Running requests.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="m"} 4
# HELP vllm:num_requests_waiting Waiting requests.
vllm:num_requests_waiting{model_name="m"} 1
# HELP vllm:gpu_cache_usage_perc KV use.
vllm:gpu_cache_usage_perc{model_name="m"} 0.5
# HELP vllm:num_preemptions_total Preemptions.
vllm:num_preemptions_total{model_name="m"} 0
# HELP vllm:prompt_tokens_total Prompt tokens.
vllm:prompt_tokens_total{model_name="m"} 100
# HELP vllm:generation_tokens_total Generated tokens.
vllm:generation_tokens_total{model_name="m"} 20
# HELP vllm:prefix_cache_queries Prefix queries.
vllm:prefix_cache_queries{model_name="m"} 0
# HELP vllm:prefix_cache_hits Prefix hits.
vllm:prefix_cache_hits{model_name="m"} 0
"""

EPP_CANONICAL = """
# HELP llm_d_epp_info Build info.
llm_d_epp_info{commit="abc"} 1
# HELP llm_d_epp_average_kv_cache_utilization KV.
llm_d_epp_average_kv_cache_utilization{name="pool"} 0.5
# HELP llm_d_epp_average_queue_size Queue.
llm_d_epp_average_queue_size{name="pool"} 1
# HELP llm_d_epp_average_running_requests Running.
llm_d_epp_average_running_requests{name="pool"} 4
# HELP llm_d_epp_ready_endpoints Ready.
llm_d_epp_ready_endpoints{name="pool"} 1
# HELP process_resident_memory_bytes Resident memory.
process_resident_memory_bytes 70000000
# HELP process_start_time_seconds Process start.
process_start_time_seconds 12345
# HELP llm_d_epp_flow_control_request_queue_duration_seconds Queue time.
# TYPE llm_d_epp_flow_control_request_queue_duration_seconds histogram
# HELP llm_d_epp_flow_control_queue_size Queue size.
llm_d_epp_flow_control_queue_size{fairness_id="a",priority="100"} 2
# HELP llm_d_epp_flow_control_queue_bytes Queue bytes.
llm_d_epp_flow_control_queue_bytes{fairness_id="a",priority="100"} 200
# HELP llm_d_epp_flow_control_pool_saturation Saturation.
llm_d_epp_flow_control_pool_saturation{inference_pool="pool"} 1
"""

EPP_PREFIX = EPP_CANONICAL + """
# HELP llm_d_epp_prefix_indexer_size Index size.
llm_d_epp_prefix_indexer_size 2
# HELP llm_d_epp_prefix_indexer_hit_ratio Prefix hit ratio.
llm_d_epp_prefix_indexer_hit_ratio 0.75
# HELP llm_d_epp_prefix_indexer_hit_bytes Prefix hit bytes.
llm_d_epp_prefix_indexer_hit_bytes 4096
"""

ENVOY = """
# HELP envoy_cluster_circuit_breakers_default_rq_open Request breaker open.
envoy_cluster_circuit_breakers_default_rq_open{envoy_cluster_name="epp"} 0
# HELP envoy_cluster_circuit_breakers_default_rq_pending_open Pending breaker open.
envoy_cluster_circuit_breakers_default_rq_pending_open{envoy_cluster_name="epp"} 0
# HELP envoy_cluster_circuit_breakers_default_remaining_rq Remaining requests.
envoy_cluster_circuit_breakers_default_remaining_rq{envoy_cluster_name="epp"} 10000
# HELP envoy_cluster_circuit_breakers_default_remaining_pending Remaining pending requests.
envoy_cluster_circuit_breakers_default_remaining_pending{envoy_cluster_name="epp"} 10000
# HELP envoy_cluster_upstream_rq_active Active requests.
envoy_cluster_upstream_rq_active{envoy_cluster_name="epp"} 0
# HELP envoy_cluster_upstream_rq_pending_overflow Pending request overflows.
envoy_cluster_upstream_rq_pending_overflow{envoy_cluster_name="epp"} 0
# HELP envoy_cluster_upstream_cx_overflow Connection overflows.
envoy_cluster_upstream_cx_overflow{envoy_cluster_name="epp"} 0
"""


class MetricsCaptureTests(unittest.TestCase):
    def test_record_scrapes_two_vllm_targets_concurrently_without_gaps(self) -> None:
        plugin_state = json.dumps({
            "timestamp": "now",
            "plugins": {
                "inflight-load": {
                    "state": {
                        "endpoints": [
                            {"endpoint": "pod-a", "requests": 1, "tokens": 128},
                            {"endpoint": "pod-b", "requests": 1, "tokens": 128},
                        ]
                    }
                }
            },
        })

        def fake_scrape(url: str, *_args: object, **_kwargs: object) -> str:
            time.sleep(0.02)
            if "plugins/state" in url:
                return plugin_state
            if "envoy" in url:
                return ENVOY
            if "vllm" in url:
                return VLLM
            return EPP_PREFIX

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            metrics, "scrape_url", side_effect=fake_scrape
        ):
            args = argparse.Namespace(
                out_dir=temp_dir,
                epp_token=None,
                epp_token_file=None,
                epp_plugin_state_url="http://epp/plugins/state",
                epp_url="http://epp/metrics",
                vllm_url=[
                    "pod-a=http://vllm-a/metrics",
                    "pod-b=http://vllm-b/metrics",
                ],
                envoy_url="http://envoy/metrics",
                timeout=1.0,
                insecure_https=False,
                require_flow_control=True,
                require_prefix_cache=True,
                require_envoy=True,
                envoy_cluster_name="epp",
                expected_envoy_remaining_requests=10000,
                epp_memory_limit_bytes=None,
                max_epp_memory_fraction=0.85,
                allow_missing=False,
                preflight_only=False,
                duration=0.055,
                stop_file=None,
                interval=0.05,
                run_id="run",
                scenario="scenario",
            )

            self.assertEqual(metrics.record(args), 0)
            health = json.loads(
                (Path(temp_dir) / "metric_capture_health.json").read_text()
            )

        self.assertTrue(health["valid"])
        self.assertEqual(health["skipped_intervals"], 0)
        self.assertEqual(health["samples"], 2)
        self.assertEqual(
            health["vllm_target_samples"], {"pod-a": 2, "pod-b": 2}
        )
        self.assertLess(health["max_sample_duration_s"], 0.05)

    def test_record_rejects_empty_vllm_body_during_sampling(self) -> None:
        plugin_state = json.dumps({
            "plugins": {
                "inflight-load": {
                    "state": {"endpoints": []}
                }
            },
        })
        calls: dict[str, int] = {}

        def fake_scrape(url: str, *_args: object, **_kwargs: object) -> str:
            calls[url] = calls.get(url, 0) + 1
            if "plugins/state" in url:
                return plugin_state
            if "envoy" in url:
                return ENVOY
            if "vllm-b" in url and calls[url] > 1:
                return ""
            if "vllm" in url:
                return VLLM
            return EPP_PREFIX

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            metrics, "scrape_url", side_effect=fake_scrape
        ):
            args = argparse.Namespace(
                out_dir=temp_dir,
                epp_token=None,
                epp_token_file=None,
                epp_plugin_state_url="http://epp/plugins/state",
                epp_url="http://epp/metrics",
                vllm_url=[
                    "pod-a=http://vllm-a/metrics",
                    "pod-b=http://vllm-b/metrics",
                ],
                envoy_url="http://envoy/metrics",
                timeout=1.0,
                insecure_https=False,
                require_flow_control=True,
                require_prefix_cache=True,
                require_envoy=True,
                envoy_cluster_name="epp",
                expected_envoy_remaining_requests=10000,
                epp_memory_limit_bytes=None,
                max_epp_memory_fraction=0.85,
                allow_missing=False,
                preflight_only=False,
                duration=0.001,
                stop_file=None,
                interval=0.05,
                run_id="run",
                scenario="scenario",
            )

            self.assertEqual(metrics.record(args), 3)
            health = json.loads(
                (Path(temp_dir) / "metric_capture_health.json").read_text()
            )

        self.assertFalse(health["valid"])
        self.assertEqual(health["vllm_target_samples"]["pod-b"], 0)
        self.assertEqual(
            health["missing_samples_by_vllm_target"]["pod-b"], health["samples"]
        )
        self.assertTrue(any(
            error.get("error") == "MissingRequiredMetrics"
            and error.get("target") == "pod-b"
            for error in health["errors"]
        ))

    def test_named_vllm_urls_accept_multiple_pods(self) -> None:
        self.assertEqual(metrics.named_urls([
            "pod-a=http://10.0.0.1:8000/metrics",
            "pod-b=http://10.0.0.2:8000/metrics",
        ]), [
            ("pod-a", "http://10.0.0.1:8000/metrics"),
            ("pod-b", "http://10.0.0.2:8000/metrics"),
        ])

    def test_target_label_prevents_equal_pod_series_from_colliding(self) -> None:
        combined = metrics.combine_target_metrics({"pod-a": VLLM, "pod-b": VLLM})
        parsed = metrics.parse_prometheus(combined)
        running = [key for key in parsed if key[0] == "vllm:num_requests_running"]
        self.assertEqual(len(running), 2)
        self.assertEqual(
            {dict(labels)["metrics_target"] for _name, labels in running},
            {"pod-a", "pod-b"},
        )

    def test_prefix_cache_preflight_requires_indexer_metrics(self) -> None:
        self.assertFalse(
            metrics.build_preflight_report(EPP_CANONICAL, VLLM, True, True)["valid"]
        )
        self.assertTrue(
            metrics.build_preflight_report(EPP_PREFIX, VLLM, True, True)["valid"]
        )

    def test_each_vllm_target_must_have_all_required_metrics(self) -> None:
        self.assertTrue(metrics.build_vllm_target_report(VLLM)["valid"])
        self.assertFalse(metrics.build_vllm_target_report(
            VLLM.replace('vllm:num_requests_waiting{model_name="m"} 1\n', "")
        )["valid"])

    def test_each_epp_sample_must_have_required_live_series(self) -> None:
        self.assertTrue(metrics.build_epp_target_report(EPP_CANONICAL)["valid"])
        self.assertFalse(metrics.build_epp_target_report("")["valid"])
        self.assertFalse(metrics.build_epp_target_report(
            EPP_CANONICAL.replace(
                'llm_d_epp_ready_endpoints{name="pool"} 1\n', ""
            )
        )["valid"])

    def test_epp_runtime_metrics_are_captured(self) -> None:
        rows = list(metrics.long_rows(
            EPP_CANONICAL + "go_goroutines 42\n", "epp", 1.0, "r", "s", 123.0
        ))
        runtime = {
            row["metric"]: row["metric_generation"] for row in rows
            if row["metric"].startswith("process_") or row["metric"] == "go_goroutines"
        }
        self.assertEqual(runtime["process_resident_memory_bytes"], "runtime")
        self.assertEqual(runtime["process_start_time_seconds"], "runtime")
        self.assertEqual(runtime["go_goroutines"], "runtime")

    def test_epp_memory_ceiling_rejects_low_headroom(self) -> None:
        self.assertTrue(metrics.memory_limit_report(800, 1000, 0.85)["valid"])
        report = metrics.memory_limit_report(900, 1000, 0.85)
        self.assertFalse(report["valid"])
        self.assertEqual(report["peak_fraction_of_limit"], 0.9)

    def test_parse_inflight_plugin_state_sums_endpoints(self) -> None:
        state = metrics.parse_inflight_plugin_state(json.dumps({
            "timestamp": "now",
            "plugins": {
                "inflight-load": {
                    "state": {
                        "endpoints": [
                            {"endpoint": "pod-a", "requests": 3, "tokens": 2048},
                            {"endpoint": "pod-b", "requests": 2, "tokens": 512},
                        ]
                    }
                }
            },
        }))

        self.assertEqual(state["requests"], 5)
        self.assertEqual(state["tokens"], 2560)
        self.assertEqual(len(state["endpoints"]), 2)

    def test_inflight_state_rows_record_exact_request_and_token_counts(self) -> None:
        rows = metrics.inflight_state_rows({
            "plugin": "inflight-load", "requests": 5, "tokens": 2560,
        }, 1.5, "run", "scenario", 123.25)
        self.assertEqual(
            {row["metric"]: row["value"] for row in rows},
            {"epp:inflight_requests": 5, "epp:inflight_tokens": 2560},
        )
        self.assertTrue(all(row["metric_generation"] == "exact" for row in rows))

    def test_active_flow_report_requires_queue_metrics_after_traffic(self) -> None:
        self.assertFalse(metrics.build_active_flow_report(
            "llm_d_epp_flow_control_pool_saturation 1\n"
        )["valid"])
        self.assertTrue(metrics.build_active_flow_report(EPP_CANONICAL)["valid"])

    def test_preflight_accepts_canonical_metrics(self) -> None:
        report = metrics.build_preflight_report(EPP_CANONICAL, VLLM, True)
        self.assertTrue(report["valid"])
        self.assertEqual(report["missing_concepts"], [])

    def test_envoy_preflight_requires_capacity_and_overflow_metrics(self) -> None:
        report = metrics.build_envoy_preflight_report(ENVOY, "epp", 10000)
        self.assertTrue(report["valid"])
        self.assertEqual(report["remaining_requests"], 10000)
        self.assertFalse(
            metrics.build_envoy_preflight_report(ENVOY, "epp", 1024)["valid"]
        )

    def test_long_rows_capture_envoy_cluster_metrics(self) -> None:
        rows = list(metrics.long_rows(ENVOY, "envoy", 1.0, "r", "s", 123.0))
        self.assertIn(
            "envoy_cluster_upstream_rq_pending_overflow",
            {row["metric"] for row in rows},
        )
        self.assertTrue(all(row["source"] == "envoy" for row in rows))

    def test_stop_file_argument_is_available_for_scenario_drain(self) -> None:
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", [
            "metrics_capture.py", "--run-id", "r", "--scenario", "s",
            "--out-dir", "/tmp/out", "--stop-file", "/tmp/stop",
        ]):
            self.assertEqual(metrics.parse_args().stop_file, "/tmp/stop")

    def test_preflight_accepts_legacy_flow_control_metrics(self) -> None:
        legacy = EPP_CANONICAL.replace(
            "llm_d_epp_flow_control_", "inference_extension_flow_control_"
        )
        report = metrics.build_preflight_report(legacy, VLLM, True)
        self.assertTrue(report["valid"])
        self.assertTrue(
            report["resolved_metrics"]["epp_flow_queue_size"].startswith("inference_extension_")
        )

    def test_preflight_reports_missing_kv_metric(self) -> None:
        report = metrics.build_preflight_report(
            EPP_CANONICAL,
            VLLM.replace("# HELP vllm:gpu_cache_usage_perc KV use.\n", "").replace(
                'vllm:gpu_cache_usage_perc{model_name="m"} 0.5\n', ""
            ),
            True,
        )
        self.assertFalse(report["valid"])
        self.assertIn("vllm_kv_cache", report["missing_concepts"])

    def test_metric_discovery_uses_help_without_samples(self) -> None:
        names = metrics.discover_metric_names(
            "# HELP llm_d_epp_flow_control_queue_size Queue.\n"
            "# TYPE llm_d_epp_flow_control_queue_size gauge\n"
        )
        self.assertIn("llm_d_epp_flow_control_queue_size", names)

    def test_long_rows_capture_all_relevant_series(self) -> None:
        rows = list(metrics.long_rows(EPP_CANONICAL, "epp", 1.5, "r", "s", 123.25))
        names = {row["metric"] for row in rows}
        self.assertIn("llm_d_epp_flow_control_queue_size", names)
        self.assertTrue(all(row["source"] == "epp" for row in rows))
        self.assertTrue(all(row["sample_epoch_s"] == 123.25 for row in rows))

    def test_long_rows_add_model_pod_target(self) -> None:
        rows = list(metrics.long_rows(
            VLLM, "vllm", 1.5, "r", "s", 123.25,
            {"metrics_target": "pod-a"},
        ))
        self.assertTrue(all(
            json.loads(row["labels_json"])["metrics_target"] == "pod-a"
            for row in rows
        ))

    def test_parser_handles_optional_prometheus_timestamp(self) -> None:
        parsed = metrics.parse_prometheus('vllm:num_requests_running{model_name="m"} 4 12345\n')
        self.assertEqual(next(iter(parsed.values())), 4)


if __name__ == "__main__":
    unittest.main()

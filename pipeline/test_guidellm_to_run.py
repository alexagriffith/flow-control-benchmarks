#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import guidellm_to_run as converter
from guidellm_scenario_to_run import envoy_proxy_evidence
import guidellm_scenario_to_run as scenario_converter


class GuideLlmConverterTests(unittest.TestCase):
    def test_flow_control_engagement_accepts_subsecond_policy_queue(self) -> None:
        evidence = scenario_converter.flow_control_engagement_evidence(
            0.0,
            1.05,
            [{
                "fairness_id": "premium",
                "queue_count_delta": 100,
                "queue_sum_s_delta": 0.25,
            }],
        )

        self.assertTrue(evidence["valid"])
        self.assertEqual(evidence["peak_queue_depth"], 0.0)
        self.assertEqual(evidence["queued_request_count_delta"], 100)

    def test_flow_control_engagement_rejects_unsaturated_queue_metrics(self) -> None:
        evidence = scenario_converter.flow_control_engagement_evidence(
            2.0,
            0.99,
            [{"queue_count_delta": 100, "queue_sum_s_delta": 0.25}],
        )

        self.assertFalse(evidence["valid"])

    def test_request_shape_evidence_requires_success_near_target(self) -> None:
        specs = [
            {"fairness_id": "chat", "input_tokens": 1024},
            {"fairness_id": "long", "input_tokens": 20000},
        ]
        rows = [
            {"tenant": "chat", "status": "200", "prompt_tokens": 1040},
            {"tenant": "long", "status": "400", "prompt_tokens": 20000},
        ]

        evidence = scenario_converter.request_shape_evidence(rows, specs)

        self.assertFalse(evidence["valid"])
        self.assertTrue(evidence["tenants"][0]["valid"])
        self.assertFalse(evidence["tenants"][1]["valid"])

    def test_scenario_timing_preserves_a_delayed_tenant_start(self) -> None:
        global_epoch, spread_ms, offsets = scenario_converter.scenario_timing(
            {"batch": 100.0, "realtime": 160.05},
            [
                {"fairness_id": "batch", "first_arrival_s": 0.0},
                {"fairness_id": "realtime", "first_arrival_s": 60.05},
            ],
        )

        self.assertEqual(global_epoch, 100.0)
        self.assertAlmostEqual(spread_ms, 0.0)
        self.assertEqual(offsets["batch"], 0.0)
        self.assertAlmostEqual(offsets["realtime"], 60.05)

    def test_tenant_offsets_restore_exact_trace_timestamps(self) -> None:
        rows = [{
            "planned_arrival_s": 0.0,
            "actual_send_s": 0.149355173,
            "start_s": 0.149355173,
        }, {
            "planned_arrival_s": 0.284134263,
            "actual_send_s": 0.285151672,
            "start_s": 0.285151672,
        }]

        scenario_converter.apply_tenant_offsets(
            rows, planned_offset=60.05, actual_offset=60.049999952316284
        )

        self.assertEqual(
            [row["planned_arrival_s"] for row in rows],
            [60.05, 60.334134263],
        )
        self.assertAlmostEqual(rows[0]["actual_send_s"], 60.199355125316284)

    def test_runtime_metric_summary_reports_pressure_and_preemptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "metric_samples_long.csv"
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "elapsed_s", "metric_generation", "metric", "labels_json", "value",
                ])
                writer.writeheader()
                writer.writerows([
                    {"elapsed_s": 0, "metric_generation": "canonical", "metric": "vllm:num_preemptions_total", "labels_json": "{}", "value": 2},
                    {"elapsed_s": 1, "metric_generation": "canonical", "metric": "llm_d_epp_flow_control_queue_size", "labels_json": '{"fairness_id":"a"}', "value": 7},
                    {"elapsed_s": 1, "metric_generation": "canonical", "metric": "llm_d_epp_flow_control_pool_saturation", "labels_json": "{}", "value": 1.1},
                    {"elapsed_s": 1, "metric_generation": "canonical", "metric": "vllm:num_requests_running", "labels_json": "{}", "value": 128},
                    {"elapsed_s": 1, "metric_generation": "canonical", "metric": "vllm:num_requests_waiting", "labels_json": "{}", "value": 4},
                    {"elapsed_s": 1, "metric_generation": "canonical", "metric": "vllm:kv_cache_usage_perc", "labels_json": "{}", "value": 0.8},
                    {"elapsed_s": 1, "metric_generation": "canonical", "metric": "vllm:num_preemptions_total", "labels_json": "{}", "value": 5},
                    {"elapsed_s": 1, "metric_generation": "exact", "metric": "epp:inflight_requests", "labels_json": "{}", "value": 115},
                    {"elapsed_s": 1, "metric_generation": "exact", "metric": "epp:inflight_tokens", "labels_json": "{}", "value": 148480},
                    {"elapsed_s": 1, "metric_generation": "proxy", "metric": "envoy_cluster_upstream_rq_active", "labels_json": '{"envoy_cluster_name":"epp"}', "value": 140},
                    {"elapsed_s": 1, "metric_generation": "runtime", "metric": "process_resident_memory_bytes", "labels_json": "{}", "value": 524288000},
                    {"elapsed_s": 1, "metric_generation": "runtime", "metric": "process_start_time_seconds", "labels_json": "{}", "value": 12345},
                    {"elapsed_s": 1, "metric_generation": "canonical", "metric": "llm_d_epp_prefix_indexer_size", "labels_json": "{}", "value": 31250},
                ])

            summary = scenario_converter.runtime_metric_summary(path)

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["max_epp_queue"], 7)
        self.assertEqual(summary["max_epp_queue_by_tenant"], {"a": 7})
        self.assertEqual(summary["max_pool_saturation"], 1.1)
        self.assertEqual(summary["max_vllm_running"], 128)
        self.assertEqual(summary["max_vllm_waiting"], 4)
        self.assertEqual(summary["max_vllm_kv_cache_usage"], 0.8)
        self.assertEqual(summary["max_envoy_epp_active_requests"], 140)
        self.assertEqual(summary["max_epp_inflight_requests"], 115)
        self.assertEqual(summary["max_epp_inflight_tokens"], 148480)
        self.assertEqual(summary["vllm_preemptions_delta"], 3)
        self.assertEqual(summary["max_epp_resident_memory_bytes"], 524288000)
        self.assertEqual(summary["max_epp_prefix_index_entries"], 31250)
        self.assertFalse(summary["epp_process_restart_detected"])

    def test_envoy_proxy_evidence_requires_zero_overflow(self) -> None:
        def snapshot(overflow: int) -> str:
            return (
                'envoy_cluster_circuit_breakers_default_remaining_rq{envoy_cluster_name="epp"} 10000\n'
                'envoy_cluster_circuit_breakers_default_rq_open{envoy_cluster_name="epp"} 0\n'
                f'envoy_cluster_upstream_rq_pending_overflow{{envoy_cluster_name="epp"}} {overflow}\n'
                'envoy_cluster_upstream_cx_overflow{envoy_cluster_name="epp"} 0\n'
            )

        self.assertTrue(envoy_proxy_evidence(snapshot(0), snapshot(0))["valid"])
        failed = envoy_proxy_evidence(snapshot(0), snapshot(1))
        self.assertFalse(failed["valid"])
        self.assertEqual(failed["request_overflow_delta"], 1)

    def test_stream_integrity_requires_exact_server_token_usage(self) -> None:
        rows = [
            {"tenant": "a", "status": "200", "completion_tokens": 128},
            {"tenant": "a", "status": "200", "completion_tokens": 127},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            evidence = scenario_converter.stream_integrity_evidence(
                rows,
                [{"fairness_id": "a", "output_tokens": 128}],
                Path(temporary),
            )
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["tenants"][0]["exact_completion_tokens"], 1)

    def test_converts_request_and_splits_metric_window(self) -> None:
        raw = {
            "benchmarks": [{
                "start_time": 100.0,
                "end_time": 110.0,
                "duration": 10.0,
                "config": {"strategy": {"max_concurrency": 4}},
                "requests": {"successful": [{
                    "request_id": "r1",
                    "request_start_time": 101.0,
                    "request_latency": 2.0,
                    "time_to_first_token_ms": 125.0,
                    "time_per_output_token_ms": 8.0,
                    "prompt_tokens": 512,
                    "output_tokens": 64,
                    "info": {"timings": {
                        "targeted_start": 100.99,
                        "request_start": 101.0,
                        "token_iterations": 60,
                    }, "settings": {"relative_timestamp": 0.0}},
                }], "errored": [], "incomplete": []},
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "raw.json"
            raw_path.write_text(json.dumps(raw))
            metrics_path = root / "metrics.csv"
            with metrics_path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "run_id", "scenario", "elapsed_s", "sample_epoch_s", "source",
                    "metric_generation", "metric", "labels_json", "value",
                ])
                writer.writeheader()
                writer.writerow({
                    "run_id": "sweep", "scenario": "capacity", "elapsed_s": 2,
                    "sample_epoch_s": 102, "source": "vllm", "metric_generation": "canonical",
                    "metric": "vllm:num_requests_running", "labels_json": "{}", "value": 4,
                })
            run_dir = converter.convert(
                raw_path, root / "runs", "capacity", "standard-a", 0,
                "standard", metrics_path,
            )[0]

            with (run_dir / "client_samples.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            with (run_dir / "metric_samples_long.csv").open() as handle:
                metrics = list(csv.DictReader(handle))
            summary = json.loads((run_dir / "summary.json").read_text())
            self.assertEqual(rows[0]["ttft_s"], "0.125")
            self.assertEqual(rows[0]["completion_tokens"], "64")
            self.assertEqual(rows[0]["planned_arrival_s"], "0.0")
            self.assertAlmostEqual(float(rows[0]["actual_send_s"]), 0.01)
            self.assertEqual(metrics[0]["elapsed_s"], "2.0")
            self.assertEqual(summary["client_summary"][0]["ttft_p95_s"], 0.125)
            conversion = json.loads((run_dir / "conversion.json").read_text())
            self.assertTrue(conversion["valid"])
            self.assertAlmostEqual(
                conversion["schedule_fidelity"]["send_lag_p99_ms"], 10.0
            )

    def test_rejects_missing_planned_timing(self) -> None:
        benchmark = {
            "start_time": 100.0,
            "requests": {"successful": [{
                "request_id": "r1",
                "request_start_time": 101.0,
                "request_latency": 1.0,
                "info": {"timings": {}},
            }], "errored": [], "incomplete": []},
        }
        rows = converter.request_rows(benchmark, "run", "scenario", "tenant", 0, "objective")
        evidence = converter.schedule_fidelity(rows)
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["requests_with_planned_time"], 0)

    def test_schedule_gate_uses_linear_p99_for_small_tenants(self) -> None:
        rows = [
            {"planned_arrival_s": index, "actual_send_s": index + 0.001}
            for index in range(77)
        ] + [{"planned_arrival_s": 77, "actual_send_s": 77.110}]

        evidence = converter.schedule_fidelity(rows)

        self.assertTrue(evidence["valid"])
        self.assertEqual(evidence["schedule_gate_version"], 2)
        self.assertEqual(evidence["send_lag_percentile_method"], "type_7_linear")
        self.assertLess(evidence["send_lag_p99_ms"], 100)
        self.assertAlmostEqual(evidence["send_lag_max_ms"], 110)

    def test_schedule_gate_rejects_two_late_requests_in_small_tenant(self) -> None:
        rows = [
            {"planned_arrival_s": index, "actual_send_s": index + 0.001}
            for index in range(76)
        ] + [
            {"planned_arrival_s": 76, "actual_send_s": 76.110},
            {"planned_arrival_s": 77, "actual_send_s": 77.110},
        ]

        evidence = converter.schedule_fidelity(rows)

        self.assertFalse(evidence["valid"])
        self.assertAlmostEqual(evidence["send_lag_p99_ms"], 110)

    def test_client_schema_matches_benchmark_request_sample(self) -> None:
        from dataclasses import fields
        from benchmark import RequestSample

        self.assertEqual(converter.CLIENT_FIELDS, [field.name for field in fields(RequestSample)])

    def test_refuses_metrics_without_absolute_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "metrics.csv"
            path.write_text("elapsed_s,metric,value\n1,x,1\n")
            with self.assertRaisesRegex(ValueError, "sample_epoch_s"):
                converter.split_metrics(path, Path(temporary) / "out.csv", 0, 10, "r", "s")

    def test_client_errors_do_not_fabricate_http_status(self) -> None:
        request = {
            "request_id": "r1", "request_start_time": 101.0,
            "request_latency": 1.0, "info": {"timings": {}, "error": "parse"},
        }
        benchmark = {
            "start_time": 100.0,
            "requests": {"successful": [], "errored": [request], "incomplete": []},
        }
        row = converter.request_rows(
            benchmark, "run", "scenario", "tenant", 0, "objective",
        )[0]
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["error_class"], "guidellm_error")

    def test_client_http_error_preserves_explicit_status(self) -> None:
        request = {
            "request_id": "r1", "request_start_time": 101.0,
            "request_latency": 1.0,
            "info": {
                "timings": {},
                "error": "HTTPStatusError(\"Client error '429 Too Many Requests' "
                "for url 'http://example/v1/completions'\")",
            },
        }
        benchmark = {
            "start_time": 100.0,
            "requests": {"successful": [], "errored": [request], "incomplete": []},
        }
        row = converter.request_rows(
            benchmark, "run", "scenario", "tenant", 0, "objective",
        )[0]
        self.assertEqual(row["status"], "429")
        self.assertEqual(row["error_class"], "guidellm_error")


if __name__ == "__main__":
    unittest.main()

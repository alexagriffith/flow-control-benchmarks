#!/usr/bin/env python3
"""Unit checks for benchmark helper logic that do not require a cluster."""

from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import patch

import benchmark as bench


class BenchmarkV4HelperTests(unittest.TestCase):
    def test_validate_prompt_pool_rejects_wrong_token_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "token count mismatch"):
            bench.validate_prompt_pool(
                [{"id": 0, "prompt": "x", "tokens": 511}], 512, 1
            )

    def test_rate_for_phase_uses_rate_rps_and_ramp(self) -> None:
        phases = [{"start_s": 0, "duration_s": 10, "rate_rps": 20, "ramp_s": 10}]

        self.assertEqual(bench.rate_for_phase(phases, -1), 0)
        self.assertAlmostEqual(bench.rate_for_phase(phases, 5), 10)
        self.assertAlmostEqual(bench.rate_for_phase(phases, 10), 0)

    def test_closed_loop_flat_phase_supports_ramp(self) -> None:
        phases = [{"start_s": 0, "duration_s": 20, "concurrency": 100, "ramp_s": 10}]

        self.assertEqual(bench.target_for_phase(phases, 0), 0)
        self.assertEqual(bench.target_for_phase(phases, 5), 50)
        self.assertEqual(bench.target_for_phase(phases, 10), 100)

    def test_throughput_excludes_completions_after_active_window(self) -> None:
        inside = _ok_sample()
        inside.start_s = 1
        inside.latency_s = 1
        drained = _ok_sample()
        drained.request_id = "req-2"
        drained.start_s = 9
        drained.latency_s = 2

        row = bench.summarize_samples("run", "scenario", [inside, drained], 10, 0)[0]

        self.assertEqual(row["active_window_http_200"], 1)
        self.assertEqual(row["drain_window_http_200"], 1)
        self.assertEqual(row["throughput_rps"], 0.1)

    def test_rate_for_phase_rejects_concurrency_shape(self) -> None:
        phases = [{"start_s": 0, "duration_s": 10, "concurrency": 7}]

        with self.assertRaisesRegex(ValueError, "explicit rate_rps"):
            bench.rate_for_phase(phases, 1)

    def test_poisson_validation_lists_missing_tenant_phases(self) -> None:
        tenants = [bench.Tenant("tenant-a", 100, [{"start_s": 0, "duration_s": 10, "concurrency": 2}])]
        with self.assertRaisesRegex(ValueError, r"tenant-a\[0\]"):
            bench.validate_arrival_configuration(tenants, "poisson")

    def test_explicit_poisson_overrides_are_applied_by_scenario_and_tenant(self) -> None:
        definitions = [(
            "priority",
            [bench.Tenant("premium-a", 100, [
                {"start_s": 0, "duration_s": 30, "concurrency": 2},
                {"start_s": 30, "duration_s": 30, "concurrency": 4},
            ])],
            60,
        )]
        bench.apply_poisson_phases(definitions, {
            "priority": {"premium-a": [{"rate_rps": 1.5}, {"rate_rps": 3.0}]},
        })

        bench.validate_arrival_configuration(definitions[0][1], "poisson")
        self.assertEqual(definitions[0][1][0].phases[1]["rate_rps"], 3.0)

    def test_noisy_poisson_rate_uses_rate_fields(self) -> None:
        phases = [{
            "start_s": 0,
            "duration_s": 60,
            "rate_pattern": "noisy_sinusoidal",
            "rate_center": 10,
            "rate_amplitude": 0,
            "rate_noise": 0,
            "rate_spike_probability": 0,
        }]
        self.assertEqual(bench.rate_for_phase(phases, 1, bench.random.Random(1)), 10)

    def test_poisson_load_does_not_require_a_concurrency_field(self) -> None:
        phases = [{"start_s": 0, "duration_s": 10, "rate_rps": 7.5}]

        concurrency, rate = bench.load_for_phase(phases, 1, "poisson")

        self.assertEqual(concurrency, 0)
        self.assertEqual(rate, 7.5)

    def test_poisson_arrival_schedule_is_repeatable(self) -> None:
        phases = [{
            "start_s": 0,
            "duration_s": 30,
            "rate_pattern": "noisy_sinusoidal",
            "rate_center": 12,
            "rate_amplitude": 3,
            "period_s": 11,
            "rate_noise": 0.5,
            "rate_spike_probability": 0.03,
        }]

        first = bench.poisson_arrival_schedule(phases, 30, 42)
        second = bench.poisson_arrival_schedule(phases, 30, 42)

        self.assertEqual(first, second)
        self.assertNotEqual(first, bench.poisson_arrival_schedule(phases, 30, 43))
        self.assertTrue(all(a < b for a, b in zip(first, first[1:])))

    def test_poisson_arrival_schedule_integrates_across_zero_rate_ramp(self) -> None:
        phases = [
            {"start_s": 0, "duration_s": 60, "rate_rps": 0.4},
            {"start_s": 60, "duration_s": 90, "rate_rps": 1.8, "ramp_s": 10},
            {"start_s": 150, "duration_s": 30, "rate_rps": 0.4},
        ]

        arrivals = bench.poisson_arrival_schedule(phases, 180, 7045440225874013280)

        self.assertGreater(len(arrivals), 100)
        self.assertTrue(any(60 < arrival < 150 for arrival in arrivals))
        self.assertTrue(any(arrival >= 150 for arrival in arrivals))

    def test_poisson_arrival_schedule_does_not_force_an_arrival_at_zero(self) -> None:
        phases = [{"start_s": 0, "duration_s": 10, "rate_rps": 2}]

        arrivals = bench.poisson_arrival_schedule(phases, 10, 42)

        self.assertGreater(len(arrivals), 0)
        self.assertGreater(arrivals[0], 0)

    def test_offered_schedule_evidence_requires_every_planned_request(self) -> None:
        tenant = bench.Tenant(
            "tenant-a",
            100,
            [{"start_s": 0, "duration_s": 1, "rate_rps": 2}],
        )
        seed = bench.tenant_traffic_seed(42, tenant.fairness_id)
        planned = bench.poisson_arrival_schedule(tenant.phases, 1, seed)
        samples = []
        for index in range(len(planned)):
            sample = _ok_sample()
            sample.request_id = f"req-{index}"
            sample.tenant = tenant.fairness_id
            sample.planned_arrival_s = planned[index]
            sample.actual_send_s = planned[index] + 0.01
            samples.append(sample)

        valid = bench.offered_schedule_evidence([tenant], 1, 42, samples, "poisson")
        missing = bench.offered_schedule_evidence([tenant], 1, 42, samples[:-1], "poisson")

        self.assertTrue(valid["valid"])
        self.assertFalse(missing["valid"])
        self.assertEqual(valid["tenants"][0]["planned_requests"], len(planned))
        self.assertEqual(len(valid["tenants"][0]["schedule_sha256"]), 64)
        self.assertTrue(valid["tenants"][0]["schedule_fidelity_valid"])

    def test_offered_schedule_evidence_rejects_late_open_loop_sends(self) -> None:
        tenant = bench.Tenant(
            "tenant-a",
            100,
            [{"start_s": 0, "duration_s": 1, "rate_rps": 2}],
        )
        seed = bench.tenant_traffic_seed(42, tenant.fairness_id)
        planned = bench.poisson_arrival_schedule(tenant.phases, 1, seed)
        samples = []
        for index, planned_arrival_s in enumerate(planned):
            sample = _ok_sample()
            sample.request_id = f"req-{index}"
            sample.tenant = tenant.fairness_id
            sample.planned_arrival_s = planned_arrival_s
            sample.actual_send_s = planned_arrival_s + 0.75
            samples.append(sample)

        evidence = bench.offered_schedule_evidence(
            [tenant], 1, 42, samples, "poisson", 100, 500
        )

        self.assertFalse(evidence["valid"])
        self.assertFalse(evidence["tenants"][0]["schedule_fidelity_valid"])
        self.assertAlmostEqual(evidence["tenants"][0]["send_lag_max_ms"], 750)

    def test_closed_loop_keeps_concurrency_phases(self) -> None:
        tenants = [bench.Tenant("tenant-a", 100, [{"start_s": 0, "duration_s": 10, "concurrency": 2}])]
        bench.validate_arrival_configuration(tenants, "closed_loop")

    def test_tenant_shapes_apply_different_realtime_and_batch_tokens(self) -> None:
        definitions = [(
            "batch",
            [
                bench.Tenant("realtime", 100, [{"start_s": 0, "duration_s": 10, "concurrency": 1}]),
                bench.Tenant("batch", -10, [{"start_s": 0, "duration_s": 10, "concurrency": 1}]),
            ],
            10,
        )]
        bench.apply_tenant_shapes(definitions, {
            "realtime": {"input_tokens": 4096, "output_tokens": 128},
            "batch": {"input_tokens": 1024, "output_tokens": 1024},
        })

        self.assertEqual(definitions[0][1][0].input_tokens, 4096)
        self.assertEqual(definitions[0][1][0].output_tokens, 128)
        self.assertEqual(definitions[0][1][1].input_tokens, 1024)
        self.assertEqual(definitions[0][1][1].output_tokens, 1024)

    def test_required_prompt_targets_include_scenario_token_sizes(self) -> None:
        definitions = [(
            "mixed",
            [
                bench.Tenant("realtime", 100, [], input_tokens=512),
                bench.Tenant("long-context", 0, [], input_tokens=8192),
            ],
            60,
        )]

        self.assertEqual(bench.required_prompt_targets(512, definitions), [512, 8192])

    def test_warmup_shapes_cover_each_scenario_shape(self) -> None:
        definitions = [(
            "mixed",
            [
                bench.Tenant("realtime", 100, [], input_tokens=4096, output_tokens=128),
                bench.Tenant("agentic", 0, [], input_tokens=2048, output_tokens=512),
                bench.Tenant("batch", -10, [], input_tokens=4096, output_tokens=128),
            ],
            60,
        )]

        self.assertEqual(
            bench.required_warmup_shapes(512, 128, definitions),
            [(2048, 512), (4096, 128)],
        )

    def test_prompt_pool_builds_deterministic_entries_concurrently(self) -> None:
        active = 0
        max_active = 0

        async def fake_build(_session, target_tokens: int, seed: int):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return f"prompt-{seed}", target_tokens

        with patch.object(bench, "build_prompt", side_effect=fake_build):
            prompts = asyncio.run(bench.build_prompt_pool(8192, 4))

        self.assertGreater(max_active, 1)
        self.assertEqual([item["id"] for item in prompts], [0, 1, 2, 3])
        self.assertEqual(prompts[0]["prompt"], "prompt-10000")

    def test_long_prompt_builder_reaches_exact_target_with_bounded_tokenization(self) -> None:
        calls = 0

        async def fake_count(_session, prompt: str) -> int:
            nonlocal calls
            calls += 1
            return len(prompt.split())

        with patch.object(bench, "tokenize_count", side_effect=fake_count):
            _prompt, count = asyncio.run(bench.build_prompt(None, 8192, 10000))

        self.assertEqual(count, 8192)
        self.assertLess(calls, 30)

    def test_tenant_shapes_file_rejects_nonpositive_tokens(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump({"batch": {"output_tokens": 0}}, handle)
            handle.flush()
            with self.assertRaisesRegex(ValueError, "must be positive"):
                bench.load_tenant_shapes(handle.name)

    def test_scenario_file_keeps_traffic_shape_and_windows_together(self) -> None:
        document = {
            "schema_version": 1,
            "scenarios": [{
                "name": "batch_protection",
                "duration_s": 180,
                "analysis_windows": [
                    {"name": "batch_only", "start_s": 0, "end_s": 60},
                    {"name": "realtime_pressure", "start_s": 60, "end_s": 150},
                ],
                "tenants": [{
                    "fairness_id": "realtime-a",
                    "priority": 100,
                    "input_tokens": 4096,
                    "output_tokens": 128,
                    "phases": [{"start_s": 60, "duration_s": 90, "rate_rps": 2.5}],
                }, {
                    "fairness_id": "batch-a",
                    "priority": -10,
                    "input_tokens": 1024,
                    "output_tokens": 1024,
                    "phases": [{"start_s": 0, "duration_s": 180, "rate_rps": 5.0}],
                }],
            }],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(document, handle)
            handle.flush()
            definitions, windows = bench.load_scenario_file(handle.name)

        self.assertEqual(definitions[0][0], "batch_protection")
        self.assertEqual(definitions[0][1][0].input_tokens, 4096)
        self.assertEqual(definitions[0][1][1].phases[0]["rate_rps"], 5.0)
        self.assertEqual(windows["batch_protection"][1]["name"], "realtime_pressure")
        bench.validate_arrival_configuration(definitions[0][1], "poisson")

    def test_scenario_file_rejects_phase_past_scenario_end(self) -> None:
        document = {
            "schema_version": 1,
            "scenarios": [{
                "name": "invalid",
                "duration_s": 30,
                "tenants": [{
                    "fairness_id": "tenant-a",
                    "priority": 100,
                    "phases": [{"start_s": 20, "duration_s": 20, "rate_rps": 1}],
                }],
            }],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(document, handle)
            handle.flush()
            with self.assertRaisesRegex(ValueError, "outside the scenario duration"):
                bench.load_scenario_file(handle.name)

    def test_header_evidence_requires_fairness_and_priority_pair(self) -> None:
        tenants = [bench.Tenant("premium-a", 100, [])]
        good = 'llm_d_epp_flow_control_queue_size{fairness_id="premium-a",priority="100"} 1\n'
        wrong = 'llm_d_epp_flow_control_queue_size{fairness_id="premium-a",priority="0"} 1\n'

        self.assertTrue(bench.header_evidence(good, tenants)["valid"])
        self.assertFalse(bench.header_evidence(wrong, tenants)["valid"])

    def test_flow_control_engagement_requires_queue_or_saturation(self) -> None:
        self.assertFalse(bench.flow_control_engagement([{
            "llm_d_epp_flow_control_queue_size|fairness_id=a|priority=100": 0,
            "llm_d_epp_flow_control_pool_saturation": 0.9,
        }])["valid"])

    def test_flow_control_engagement_requires_durable_queue_count(self) -> None:
        rows = [{
            "llm_d_epp_flow_control_queue_size|fairness_id=a|priority=100": 3,
            "llm_d_epp_flow_control_pool_saturation": 1,
        }]
        self.assertFalse(bench.flow_control_engagement(rows)["valid"])
        evidence = [{"fairness_id": "a", "priority": "100", "queue_count_delta": 2}]
        self.assertTrue(bench.flow_control_engagement(rows, evidence)["valid"])
        self.assertEqual(
            bench.flow_control_engagement(rows, evidence)["queued_request_count_delta"], 2
        )

    def test_window_summary_keeps_pressure_separate_from_baseline(self) -> None:
        baseline = _ok_sample()
        baseline.start_s = 10
        baseline.planned_arrival_s = 10
        baseline.ttft_s = 0.1
        pressure = _ok_sample()
        pressure.start_s = 70
        pressure.planned_arrival_s = 70
        pressure.ttft_s = 0.8
        crosses_boundary = _ok_sample()
        crosses_boundary.request_id = "req-boundary"
        crosses_boundary.start_s = 50
        crosses_boundary.planned_arrival_s = 50
        crosses_boundary.latency_s = 15
        rows = bench.summarize_windows(
            "run",
            "scenario",
            [baseline, pressure, crosses_boundary],
            [
                {"name": "baseline", "start_s": 0, "end_s": 60},
                {"name": "pressure", "start_s": 60, "end_s": 120},
            ],
            "poisson",
        )

        self.assertEqual([row["window"] for row in rows], ["baseline", "pressure"])
        self.assertEqual(rows[0]["ttft_p95_s"], 0.1)
        self.assertEqual(rows[1]["ttft_p95_s"], 0.8)
        self.assertAlmostEqual(rows[0]["throughput_rps"], 1 / 60)
        self.assertAlmostEqual(rows[1]["throughput_rps"], 2 / 60)
        self.assertEqual(rows[0]["drain_window_http_200"], 1)
        self.assertEqual(rows[1]["active_window_http_200"], 2)
        self.assertEqual(rows[1]["drain_window_http_200"], 0)

    def test_poisson_window_uses_planned_arrival_not_send_delay(self) -> None:
        sample = _ok_sample()
        sample.planned_arrival_s = 59.99
        sample.start_s = 60.01

        rows = bench.summarize_windows(
            "run",
            "scenario",
            [sample],
            [
                {"name": "baseline", "start_s": 0, "end_s": 60},
                {"name": "pressure", "start_s": 60, "end_s": 120},
            ],
            "poisson",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["window"], "baseline")
        self.assertEqual(rows[0]["total"], 1)

    def test_live_status_exposes_traffic_runtime_and_tenant_latency(self) -> None:
        sample = _ok_sample()
        sample.tenant = "premium-a"
        status = bench.build_live_status(
            "run",
            "scenario",
            "scenarios",
            "running",
            "Counted traffic",
            10,
            [bench.Tenant("premium-a", 100, [])],
            [sample],
            [{"tenant": "premium-a", "target_rps": 2, "outstanding_requests": 3}],
            [{
                "llm_d_epp_flow_control_queue_size|fairness_id=premium-a|priority=100": 7,
                "vllm:num_requests_running": 4,
                "vllm:num_requests_waiting": 2,
                "vllm:kv_cache_usage_perc": 0.2,
            }, {
                "llm_d_epp_flow_control_queue_size|fairness_id=premium-a|priority=100": 4,
                "vllm:num_requests_running": 5,
                "vllm:num_requests_waiting": 1,
                "vllm:kv_cache_usage_perc": 0.25,
            }],
        )

        self.assertEqual(status["offeredRps"], 2)
        self.assertEqual(status["eppQueued"], 4)
        self.assertEqual(status["eppQueuedPeak"], 7)
        self.assertEqual(status["tenants"][0]["queuedPeak"], 7)
        self.assertEqual(status["vllmRunning"], 5)
        self.assertEqual(status["kvCacheUsage"], 0.25)
        self.assertEqual(status["p95TtftMs"], 100)

    def test_live_status_includes_long_requests_that_completed_recently(self) -> None:
        sample = _ok_sample()
        sample.tenant = "premium-a"
        sample.start_s = 10
        sample.latency_s = 80
        sample.ttft_s = 2

        status = bench.build_live_status(
            "run",
            "scenario",
            "stage",
            "running",
            "Counted traffic",
            95,
            [bench.Tenant("premium-a", 100, [])],
            [sample],
            [],
            [],
        )

        self.assertEqual(status["p95TtftMs"], 2000)
        self.assertEqual(status["tenants"][0]["p95TtftMs"], 2000)

    def test_live_status_write_is_atomic_and_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live-status.json"
            bench.write_live_status(path, {"state": "running"})
            self.assertEqual(json.loads(path.read_text()), {"state": "running"})
            self.assertFalse(path.with_suffix(".json.tmp").exists())
        self.assertTrue(bench.flow_control_engagement(
            [{
                "llm_d_epp_flow_control_queue_size|fairness_id=a|priority=100": 1,
                "llm_d_epp_flow_control_pool_saturation": 1.0,
            }],
            [{"queue_count_delta": 1}],
        )["valid"])

    def test_partial_run_checkpoint_preserves_client_and_metric_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            bench.write_partial_run_artifacts(
                run_dir,
                [_ok_sample()],
                [{"elapsed_s": 1.0, "vllm:num_requests_running": 2}],
                [],
                [],
                [{"elapsed_s": 1.0, "tenant": "tenant-a"}],
            )

            client = (run_dir / "client_samples.partial.csv").read_text()
            metrics = (run_dir / "metric_samples.partial.csv").read_text()
            traffic = (run_dir / "traffic_samples.partial.csv").read_text()
            self.assertIn("request_id", client)
            self.assertIn("vllm:num_requests_running", metrics)
            self.assertIn("tenant-a", traffic)
            self.assertFalse(list(run_dir.glob("*.tmp")))

            bench.remove_partial_run_artifacts(run_dir)
            self.assertFalse(list(run_dir.glob("*.partial.csv")))

    def test_stream_usage_completion_tokens(self) -> None:
        event = bench.parse_stream_line(
            b'data: {"choices":[{"text":"x"}],"usage":{"completion_tokens":17}}'
        )

        self.assertIsNotNone(event)
        self.assertEqual(bench.completion_tokens_from_usage(event), 17)
        self.assertEqual(bench.parse_stream_line(b"data: [DONE]"), {"done": True})

    def test_stream_completion_requires_done_and_usage(self) -> None:
        self.assertEqual(
            bench.stream_completion_error(False, 128), "IncompleteStream"
        )
        self.assertEqual(bench.stream_completion_error(True, None), "MissingUsage")
        self.assertIsNone(bench.stream_completion_error(True, 128))

    def test_request_sample_has_dense_artifact_fields(self) -> None:
        field_names = {field.name for field in fields(bench.RequestSample)}

        for expected in {
            "request_id",
            "planned_arrival_s",
            "actual_send_s",
            "prompt_tokens",
            "completion_tokens",
            "tpot_s",
            "timeout",
            "error_class",
            "retry_count",
            "token_count_source",
            "dropped_reason",
            "retry_after",
            "response_detail",
        }:
            self.assertIn(expected, field_names)

    def test_send_one_captures_drop_reason_and_retry_after(self) -> None:
        class Response:
            status = 429
            headers = {
                "x-llm-d-request-dropped-reason": "saturated",
                "Retry-After": "1",
            }

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def read(self):
                return b"inference error: ResourceExhausted - system saturated"

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        samples = []
        tenant = bench.Tenant("tenant-a", 100, [], "premium")
        asyncio.run(
            bench.send_one(
                Session(), "run", "scenario", tenant, "prompt", 1, 1,
                bench.now_s(), samples,
            )
        )

        self.assertEqual(samples[0].status, "429")
        self.assertEqual(samples[0].dropped_reason, "saturated")
        self.assertEqual(samples[0].retry_after, "1")
        self.assertEqual(
            samples[0].response_detail,
            "inference error: ResourceExhausted - system saturated",
        )

    def test_summarize_samples_reports_tpot_percentiles(self) -> None:
        samples = [
            bench.RequestSample(
                run_id="run",
                scenario="scenario",
                request_id=f"req-{index}",
                tenant="premium",
                priority=100,
                objective="premium",
                status="200",
                planned_arrival_s=None,
                actual_send_s=float(index),
                start_s=float(index),
                ttft_s=0.1,
                latency_s=1.0,
                stream_chunks=2,
                prompt_tokens=512,
                completion_tokens=101,
                tpot_s=0.009 + index * 0.001,
                timeout=False,
                error_class=None,
                retry_count=0,
                token_count_source="stream_usage",
                dropped_reason=None,
                retry_after=None,
                response_detail=None,
            )
            for index in range(3)
        ]

        rows = bench.summarize_samples("run", "scenario", samples, duration_s=10)

        self.assertAlmostEqual(rows[0]["tpot_p50_s"], 0.01)
        self.assertAlmostEqual(rows[0]["tpot_p95_s"], 0.011)

    def test_summary_rows_carry_arrival_mode(self) -> None:
        # summary.csv is the artifact most likely to be shared out; each row
        # must state which arrival process produced the percentiles so a
        # closed-loop shape is never mistaken for an SLO proof.
        samples = [_ok_sample()]
        default_rows = bench.summarize_samples("run", "scenario", samples, duration_s=10)
        self.assertEqual(default_rows[0]["arrival_mode"], "closed_loop")

        poisson_rows = bench.summarize_samples(
            "run", "scenario", samples, duration_s=10, arrival_mode="poisson"
        )
        self.assertEqual(poisson_rows[0]["arrival_mode"], "poisson")

    def test_summary_reports_every_http_status(self) -> None:
        samples = [_ok_sample()]
        for index, status in enumerate(("429", "500", "503"), start=2):
            sample = _ok_sample()
            sample.request_id = f"req-{index}"
            sample.status = status
            sample.ttft_s = None
            samples.append(sample)

        row = bench.summarize_samples("run", "scenario", samples, duration_s=10)[0]

        self.assertEqual(row["http_200"], 1)
        self.assertEqual(row["http_429"], 1)
        self.assertEqual(row["http_503"], 1)
        self.assertEqual(row["http_other"], 1)
        self.assertEqual(row["non_200"], 3)
        self.assertEqual(row["status_counts"], {"200": 1, "429": 1, "500": 1, "503": 1})

    def test_compute_tpot_uses_completion_tokens_not_chunks(self) -> None:
        # 101 completion tokens over a 0.9 s decode window => 0.9 / 100 = 0.009 s.
        self.assertAlmostEqual(
            bench.compute_tpot_s(latency_s=1.0, ttft_s=0.1, completion_tokens=101),
            0.009,
        )
        # Signature has no stream-chunk parameter: TPOT cannot be derived from chunks.
        params = set(inspect.signature(bench.compute_tpot_s).parameters)
        self.assertEqual(params, {"latency_s", "ttft_s", "completion_tokens"})
        self.assertNotIn("stream_chunks", params)
        self.assertNotIn("chunks", params)

    def test_compute_tpot_returns_none_without_completion_tokens(self) -> None:
        self.assertIsNone(bench.compute_tpot_s(latency_s=1.0, ttft_s=0.1, completion_tokens=None))
        self.assertIsNone(bench.compute_tpot_s(latency_s=1.0, ttft_s=None, completion_tokens=101))

    def test_compute_tpot_single_token_does_not_divide_by_zero(self) -> None:
        # completion_tokens == 1 would give a zero denominator; max(1, n-1) guards it.
        self.assertAlmostEqual(
            bench.compute_tpot_s(latency_s=0.5, ttft_s=0.1, completion_tokens=1),
            0.4,
        )

    def test_completion_tokens_from_usage_ignores_non_numeric(self) -> None:
        self.assertIsNone(bench.completion_tokens_from_usage(None))
        self.assertIsNone(bench.completion_tokens_from_usage({"usage": {}}))
        self.assertIsNone(bench.completion_tokens_from_usage({"usage": {"completion_tokens": "x"}}))
        self.assertEqual(
            bench.completion_tokens_from_usage({"usage": {"completion_tokens": 5}}), 5
        )

    def test_parse_stream_line_rejects_non_data_and_garbage(self) -> None:
        self.assertIsNone(bench.parse_stream_line(b""))
        self.assertIsNone(bench.parse_stream_line(b": keep-alive"))
        self.assertIsNone(bench.parse_stream_line(b"data: {not json"))
        self.assertEqual(bench.parse_stream_line(b"data: [DONE]"), {"done": True})

    def test_slo_proof_invalid_for_closed_loop(self) -> None:
        samples = [_ok_sample()]
        self.assertFalse(
            bench.compute_slo_proof_valid("closed_loop", None, True, samples)
        )
        self.assertEqual(
            bench.slo_proof_reason("closed_loop", None, True, samples),
            "closed_loop_offered_concurrency_shape_not_a_proof",
        )

    def test_slo_proof_valid_for_clean_poisson_run(self) -> None:
        samples = [_ok_sample()]
        self.assertTrue(bench.compute_slo_proof_valid("poisson", None, True, samples))
        self.assertEqual(bench.slo_proof_reason("poisson", None, True, samples), "valid")

    def test_slo_proof_invalid_when_safety_ceiling_hit(self) -> None:
        samples = [_ok_sample()]
        safety = {"premium": {"state": "hit"}}
        self.assertFalse(bench.compute_slo_proof_valid("poisson", safety, True, samples))
        self.assertEqual(
            bench.slo_proof_reason("poisson", safety, True, samples),
            "outstanding_safety_ceiling_hit",
        )

    def test_slo_proof_invalid_when_request_errors_present(self) -> None:
        bad = _ok_sample()
        bad.error_class = "ClientError"
        self.assertFalse(bench.compute_slo_proof_valid("poisson", None, True, [bad]))
        self.assertEqual(
            bench.slo_proof_reason("poisson", None, True, [bad]),
            "non_200_responses_or_request_errors_present",
        )

    def test_slo_proof_invalid_when_non_200_responses_present(self) -> None:
        bad = _ok_sample()
        bad.status = "429"
        self.assertFalse(bench.compute_slo_proof_valid("poisson", None, True, [bad]))
        self.assertEqual(
            bench.slo_proof_reason("poisson", None, True, [bad]),
            "non_200_responses_or_request_errors_present",
        )

    def test_slo_proof_invalid_without_metrics_or_samples(self) -> None:
        samples = [_ok_sample()]
        self.assertFalse(bench.compute_slo_proof_valid("poisson", None, False, samples))
        self.assertEqual(
            bench.slo_proof_reason("poisson", None, False, samples), "no_metric_samples"
        )
        self.assertFalse(bench.compute_slo_proof_valid("poisson", None, True, []))
        self.assertEqual(
            bench.slo_proof_reason("poisson", None, True, []), "no_client_samples"
        )

    def test_slo_proof_invalid_when_required_proof_checks_fail(self) -> None:
        samples = [_ok_sample()]
        self.assertFalse(bench.compute_slo_proof_valid("poisson", None, True, samples, False))
        self.assertEqual(
            bench.slo_proof_reason("poisson", None, True, samples, False),
            "required_header_cache_or_flow_control_proof_failed",
        )

    def test_request_sample_csv_fieldnames_match_dataclass(self) -> None:
        # The empty-run CSV header must be derivable from the dataclass alone,
        # with no fragile positional construction, and must include every
        # required dense-artifact column.
        header = [f.name for f in fields(bench.RequestSample)]
        self.assertEqual(header, list(asdict(_ok_sample()).keys()))
        for required in (
            "request_id", "planned_arrival_s", "actual_send_s", "prompt_tokens",
            "completion_tokens", "tpot_s", "timeout", "error_class",
            "retry_count", "token_count_source",
            "dropped_reason", "retry_after", "response_detail",
        ):
            self.assertIn(required, header)

    def test_metric_summary_reports_capture_failures_and_missing_exact_state(self) -> None:
        rows = [
            {
                "epp:inflight_requests": 2,
                "epp:inflight_tokens": 1024,
                "scrape_duration_s": 0.1,
                "sample_lag_s": 0.0,
            },
            {
                "vllm_scrape_error": "TimeoutError",
                "epp_plugin_state_error": "JSONDecodeError",
                "scrape_duration_s": 0.2,
                "sample_lag_s": 0.1,
            },
        ]

        health = bench.summarize_metric_samples(rows)["capture_health"]

        self.assertEqual(health["samples"], 2)
        self.assertEqual(health["vllm_scrape_errors"], 1)
        self.assertEqual(health["epp_scrape_errors"], 0)
        self.assertEqual(health["epp_plugin_state_errors"], 1)
        self.assertEqual(health["missing_inflight_request_samples"], 1)
        self.assertEqual(health["missing_inflight_token_samples"], 1)


def _ok_sample() -> "bench.RequestSample":
    return bench.RequestSample(
        run_id="run",
        scenario="scenario",
        request_id="req-1",
        tenant="premium",
        priority=100,
        objective="premium",
        status="200",
        planned_arrival_s=0.0,
        actual_send_s=0.0,
        start_s=0.0,
        ttft_s=0.1,
        latency_s=1.0,
        stream_chunks=2,
        prompt_tokens=512,
        completion_tokens=101,
        tpot_s=0.009,
        timeout=False,
        error_class=None,
        retry_count=0,
        token_count_source="stream_usage",
        dropped_reason=None,
        retry_after=None,
        response_detail=None,
    )


if __name__ == "__main__":
    unittest.main()

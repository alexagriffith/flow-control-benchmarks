#!/usr/bin/env python3
"""Unit checks for benchmark_v4 helper logic that do not require a cluster."""

from __future__ import annotations

import inspect
import unittest
from dataclasses import asdict, fields

import benchmark_v4 as bench


class BenchmarkV4HelperTests(unittest.TestCase):
    def test_rate_for_phase_uses_rate_rps_and_ramp(self) -> None:
        phases = [{"start_s": 0, "duration_s": 10, "rate_rps": 20, "ramp_s": 10}]

        self.assertEqual(bench.rate_for_phase(phases, -1), 0)
        self.assertAlmostEqual(bench.rate_for_phase(phases, 5), 10)
        self.assertAlmostEqual(bench.rate_for_phase(phases, 10), 0)

    def test_rate_for_phase_falls_back_to_concurrency_shape(self) -> None:
        phases = [{"start_s": 0, "duration_s": 10, "concurrency": 7}]

        self.assertEqual(bench.rate_for_phase(phases, 1), 7)

    def test_stream_usage_completion_tokens(self) -> None:
        event = bench.parse_stream_line(
            b'data: {"choices":[{"text":"x"}],"usage":{"completion_tokens":17}}'
        )

        self.assertIsNotNone(event)
        self.assertEqual(bench.completion_tokens_from_usage(event), 17)
        self.assertEqual(bench.parse_stream_line(b"data: [DONE]"), {"done": True})

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
        }:
            self.assertIn(expected, field_names)

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
        ):
            self.assertIn(required, header)


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
    )


if __name__ == "__main__":
    unittest.main()

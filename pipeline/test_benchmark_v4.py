#!/usr/bin/env python3
"""Unit checks for benchmark_v4 helper logic that do not require a cluster."""

from __future__ import annotations

import unittest
from dataclasses import fields

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


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import guidellm_trace


class GuideLlmTraceTests(unittest.TestCase):
    def test_exports_repeatable_per_tenant_trace_and_manifest(self) -> None:
        scenario = {
            "schema_version": 1,
            "scenarios": [{
                "name": "mixed",
                "duration_s": 2,
                "analysis_windows": [{"name": "all", "start_s": 0, "end_s": 2}],
                "tenants": [{
                    "fairness_id": "premium-a",
                    "priority": 100,
                    "objective": "premium",
                    "input_tokens": 64,
                    "output_tokens": 8,
                    "phases": [{"start_s": 0, "duration_s": 2, "rate_rps": 2}],
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scenarios.json"
            source.write_text(json.dumps(scenario))
            first = root / "first"
            second = root / "second"
            first_manifest = json.loads(
                guidellm_trace.export_trace(source, "mixed", first, 42).read_text()
            )
            second_manifest = json.loads(
                guidellm_trace.export_trace(source, "mixed", second, 42).read_text()
            )

            self.assertEqual(first_manifest["tenants"], second_manifest["tenants"])
            self.assertEqual(
                (first / "premium-a.jsonl").read_text(),
                (second / "premium-a.jsonl").read_text(),
            )
            rows = [json.loads(line) for line in (first / "premium-a.jsonl").read_text().splitlines()]
            self.assertGreater(rows[0]["timestamp"], 0.0)
            self.assertTrue(all(row["input_length"] == 64 for row in rows))
            self.assertTrue(all(row["output_length"] == 8 for row in rows))
            self.assertEqual(first_manifest["tenants"][0]["planned_requests"], len(rows))
            self.assertEqual(
                first_manifest["tenants"][0]["first_arrival_s"],
                rows[0]["timestamp"],
            )

    def test_rejects_unknown_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scenarios.json"
            source.write_text(json.dumps({
                "schema_version": 1,
                "scenarios": [{
                    "name": "known",
                    "duration_s": 1,
                    "tenants": [{
                        "fairness_id": "a", "priority": 0, "objective": "standard",
                        "phases": [{"start_s": 0, "duration_s": 1, "rate_rps": 1}],
                    }],
                }],
            }))
            with self.assertRaisesRegex(ValueError, "unknown scenario"):
                guidellm_trace.export_trace(source, "missing", root / "out")


if __name__ == "__main__":
    unittest.main()

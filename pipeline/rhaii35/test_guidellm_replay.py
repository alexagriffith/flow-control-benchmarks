#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import launch_guidellm_replay as replay  # noqa: E402
import runtime_preflight  # noqa: E402


class GuideLlmReplayTests(unittest.TestCase):
    def test_short_names_remain_readable(self) -> None:
        self.assertEqual(replay.replay_name("soft-pt", "tenant-a"), "soft-pt-tenant-a")

    def test_long_names_do_not_collide(self) -> None:
        first = replay.replay_name("x" * 55, "tenant-a")
        second = replay.replay_name("x" * 55, "tenant-b")
        self.assertLessEqual(len(first), 63)
        self.assertNotEqual(first, second)

    def test_manifest_checks_trace_count_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace.jsonl"
            trace.write_text("{}\n")
            manifest = {
                "scenario": "test",
                "duration_s": 1,
                "tenants": [{
                    "fairness_id": "a",
                    "objective": "priority-0",
                    "planned_requests": 2,
                    "trace_file": trace.name,
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "request count mismatch"):
                replay.load_manifest(path)

    def test_runtime_preflight_accepts_tested_contract(self) -> None:
        service = {
            "metadata": {"name": "model"},
            "spec": {
                "model": {"name": "openai/gpt-oss-20b"},
                "replicas": 1,
                "router": {"scheduler": {"config": {"inline": {
                    "featureGates": ["flowControl"],
                    "flowControl": {
                        "defaultRequestTTL": "3s",
                        "saturationDetector": {"pluginRef": "detector"},
                    },
                    "plugins": [{
                        "name": "detector",
                        "type": "concurrency-detector",
                        "parameters": {
                            "concurrencyMode": "requests",
                            "maxConcurrency": 28,
                            "headroom": 0,
                        },
                    }],
                }}}},
                "template": {"containers": [{
                    "name": "main",
                    "env": [{
                        "name": "VLLM_ADDITIONAL_ARGS",
                        "value": "--no-enable-prefix-caching",
                    }],
                }]},
            },
        }
        scheduler = {"spec": {"template": {"spec": {"containers": [{
            "name": "main", "image": "example/endpoint-picker@sha256:abc",
        }]}}}}
        args = argparse.Namespace(
            expected_model="openai/gpt-oss-20b",
            expected_max_concurrency=28,
            expected_headroom=0,
            expected_ttl="3s",
            expected_replicas=1,
        )
        self.assertTrue(runtime_preflight.validate(service, scheduler, args)["valid"])


if __name__ == "__main__":
    unittest.main()

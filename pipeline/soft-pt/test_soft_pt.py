#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import classifier_proxy  # noqa: E402
import generate_batch_input  # noqa: E402


class SoftPtReplayTests(unittest.TestCase):
    def test_policy_records_accepted_cost_and_never_promotes_on_redis_failure(self) -> None:
        policy = classifier_proxy.load_policy(ROOT / "policy.json")
        self.assertEqual(policy["frozen_request"]["estimated_normalized_tokens"], 895)
        self.assertEqual(policy["entitlement"]["rate_normalized_tokens_per_second"], 4475)
        self.assertEqual(policy["entitlement"]["burst_normalized_tokens"], 8950)
        self.assertEqual(policy["redis_failure_policy"], "never-promote")

    def test_header_rewrite_removes_both_caller_objective_headers(self) -> None:
        headers = {
            "Host": "classifier.example",
            "Content-Length": "123",
            "Authorization": "Bearer example",
            "X-LLM-D-Inference-Objective": "forged-priority-100",
            "X-Gateway-Inference-Objective": "forged-priority-100",
            "X-LLM-D-Inference-Fairness-ID": "pt-entitled",
        }
        rewritten = {
            key.lower(): value
            for key, value in classifier_proxy.objective_headers(
                headers, "priority-0"
            ).items()
        }
        self.assertNotIn("host", rewritten)
        self.assertNotIn("content-length", rewritten)
        self.assertEqual(rewritten["x-llm-d-inference-objective"], "priority-0")
        self.assertEqual(rewritten["x-gateway-inference-objective"], "priority-0")
        self.assertEqual(rewritten["x-llm-d-inference-fairness-id"], "pt-entitled")
        self.assertEqual(rewritten["authorization"], "Bearer example")

    def test_batch_input_matches_accepted_hash(self) -> None:
        content = generate_batch_input.payload()
        self.assertEqual(content.count(b"\n"), generate_batch_input.REQUESTS)
        self.assertEqual(
            hashlib.sha256(content).hexdigest(),
            generate_batch_input.EXPECTED_SHA256,
        )
        first = json.loads(content.splitlines()[0])
        self.assertEqual(first["custom_id"], "dispatch-batch-0000")
        self.assertEqual(first["body"]["max_tokens"], 128)

    def test_exact_realtime_traces_are_identical(self) -> None:
        entitled = ROOT / "traces/pt-entitled.jsonl"
        background = ROOT / "traces/pt-background.jsonl"
        self.assertEqual(entitled.read_bytes(), background.read_bytes())
        self.assertEqual(len(entitled.read_text().splitlines()), 1410)
        self.assertEqual(
            hashlib.sha256(entitled.read_bytes()).hexdigest(),
            "06354e5cb32e2242c7b50692a850287317b67c946f24a5adbd93aaebbe3166ec",
        )

    def test_redis_failure_never_promotes_a_request(self) -> None:
        policy = classifier_proxy.load_policy(ROOT / "policy.json")
        classifier = classifier_proxy.Classifier(
            policy, "classifying-quota", "http://unused", "redis", 6379, "", ""
        )
        classifier.reserve = AsyncMock(side_effect=OSError("unavailable"))
        decision, objective, _latency, _caller = asyncio.run(
            classifier.classify("pt-entitled", "request-1", None)
        )
        self.assertEqual(decision, "overflow")
        self.assertEqual(objective, "priority-0")

    def test_redis_scan_is_paginated(self) -> None:
        policy = classifier_proxy.load_policy(ROOT / "policy.json")
        classifier = classifier_proxy.Classifier(
            policy, "classifying-quota", "http://unused", "redis", 6379, "", ""
        )
        classifier.redis_command = AsyncMock(
            side_effect=[["8", ["one"]], ["0", ["two"]]]
        )
        self.assertEqual(
            asyncio.run(classifier.scan_keys("flow-control-soft-pt:v1:*")),
            ["one", "two"],
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

from __future__ import annotations

import unittest

import kubernetes_container_memory as memory


class KubernetesContainerMemoryTests(unittest.TestCase):
    def test_selects_exact_pod_and_container(self) -> None:
        summary = {"pods": [
            {
                "podRef": {"namespace": "other", "name": "epp"},
                "containers": [{"name": "epp", "memory": {
                    "workingSetBytes": 1, "rssBytes": 1, "usageBytes": 1,
                }}],
            },
            {
                "podRef": {"namespace": "benchmark", "name": "epp"},
                "containers": [{"name": "epp", "memory": {
                    "workingSetBytes": 10, "rssBytes": 8, "usageBytes": 12,
                }}],
            },
        ]}

        self.assertEqual(
            memory.container_memory(summary, "benchmark", "epp", "epp"),
            {"workingSetBytes": 10, "rssBytes": 8, "usageBytes": 12},
        )

    def test_rejects_missing_memory_fields(self) -> None:
        summary = {"pods": [{
            "podRef": {"namespace": "benchmark", "name": "epp"},
            "containers": [{"name": "epp", "memory": {"rssBytes": 8}}],
        }]}
        with self.assertRaisesRegex(RuntimeError, "missing container memory fields"):
            memory.container_memory(summary, "benchmark", "epp", "epp")


if __name__ == "__main__":
    unittest.main()

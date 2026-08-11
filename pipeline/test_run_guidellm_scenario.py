#!/usr/bin/env python3

from __future__ import annotations

import unittest
import gzip
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from guidellm_k8s import backend_config, job_document, resource_name
from guidellm_scenario_to_run import prefix_cache_evidence
import run_guidellm_scenario as runner
import sync_live_status


class RunGuideLlmScenarioTests(unittest.TestCase):
    def test_kubernetes_memory_interval_is_configurable(self) -> None:
        argv = [
            "run_guidellm_scenario.py",
            "--manifest", "manifest.json",
            "--run-dir", "run",
            "--prefix", "test",
            "--kubernetes-memory-interval-s", "10",
        ]
        with patch("sys.argv", argv):
            self.assertEqual(runner.parse_args().kubernetes_memory_interval_s, 10.0)

    def test_kubernetes_memory_interval_must_be_positive(self) -> None:
        argv = [
            "run_guidellm_scenario.py",
            "--manifest", "manifest.json",
            "--run-dir", "run",
            "--prefix", "test",
            "--kubernetes-memory-interval-s", "0",
        ]
        with patch("sys.argv", argv), self.assertRaises(SystemExit):
            runner.parse_args()

    def test_cache_evidence_matches_explicit_mode(self) -> None:
        names = {"vllm:prefix_cache_queries", "vllm:prefix_cache_hits"}
        self.assertTrue(prefix_cache_evidence(
            {"queries_delta": 0, "hits_delta": 0}, names, "off"
        )["valid"])
        self.assertTrue(prefix_cache_evidence(
            {"queries_delta": 100, "hits_delta": 75}, names, "on"
        )["valid"])
        self.assertFalse(prefix_cache_evidence(
            {"queries_delta": 100, "hits_delta": 0}, names, "on"
        )["valid"])

    def test_tenant_fixed_epoch_preserves_initial_trace_delay(self) -> None:
        self.assertEqual(
            runner.tenant_fixed_epoch(100.0, {"first_arrival_s": 60.05}),
            160.05,
        )

    def test_run_directory_allows_its_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            (run_dir / "README.md").write_text("# Run\n")
            runner.prepare_run_directory(run_dir)
            self.assertEqual([path.name for path in run_dir.iterdir()], ["README.md"])

    def test_run_directory_rejects_prior_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            (run_dir / "summary.json").write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "run directory is not empty"):
                runner.prepare_run_directory(run_dir)

    def test_stream_copy_writes_atomically_without_buffering(self) -> None:
        class Result:
            returncode = 0
            stderr = b""

        def fake_run(*_args: object, stdout: object, **_kwargs: object) -> Result:
            stdout.write(gzip.compress(b"metric-data"))
            return Result()

        with tempfile.TemporaryDirectory() as temporary, patch(
            "run_guidellm_scenario.subprocess.run", side_effect=fake_run
        ):
            path = Path(temporary) / "metrics.csv"
            runner.stream_copy_from_pod("test", "pod", "/tmp/metrics.csv", path)
            self.assertEqual(path.read_bytes(), b"metric-data")
            self.assertFalse(path.with_name("metrics.csv.partial").exists())

    def test_stream_copy_retries_corrupt_compressed_output(self) -> None:
        class Result:
            returncode = 0
            stderr = b""

        calls = 0

        def fake_run(*_args: object, stdout: object, **_kwargs: object) -> Result:
            nonlocal calls
            calls += 1
            stdout.write(b"corrupt" if calls == 1 else gzip.compress(b"metric-data"))
            return Result()

        with tempfile.TemporaryDirectory() as temporary, patch(
            "run_guidellm_scenario.subprocess.run", side_effect=fake_run
        ), patch("run_guidellm_scenario.time.sleep"):
            path = Path(temporary) / "metrics.csv"
            runner.stream_copy_from_pod("test", "pod", "/tmp/metrics.csv", path)
            payload = path.read_bytes()

        self.assertEqual(calls, 2)
        self.assertEqual(payload, b"metric-data")

    def test_client_preservation_continues_after_one_log_write_error(self) -> None:
        class Result:
            returncode = 0
            stdout = "log"
            stderr = ""

        resources = [
            ("job-a", "config-a", {"fairness_id": "tenant-a"}),
            ("job-b", "config-b", {"fairness_id": "tenant-b"}),
        ]
        write_calls = 0

        def fake_write(_path: Path, _text: str, *_args: object, **_kwargs: object) -> int:
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                raise OSError("disk hiccup")
            return 3

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            runner, "kubectl", return_value=Result()
        ), patch.object(runner, "stream_copy_from_pod"), patch.object(
            Path, "write_text", autospec=True, side_effect=fake_write
        ):
            report = runner.preserve_client_artifacts(
                "test", {"job-a": "pod-a", "job-b": "pod-b"}, resources,
                Path(temporary),
            )

        self.assertEqual(report["preserved"], ["tenant-a.json", "tenant-b.json"])
        self.assertEqual(len(report["errors"]), 1)

    def test_metric_preservation_keeps_available_files_and_reports_missing(self) -> None:
        class Result:
            def __init__(self, returncode: int) -> None:
                self.returncode = returncode

        def fake_kubectl(_namespace: str, command: list[str], **_kwargs: object) -> Result:
            return Result(1 if command[-1].endswith("post_envoy.prom") else 0)

        def fake_copy(
            _namespace: str, _pod: str, remote: str, local: Path, **_kwargs: object
        ) -> None:
            local.write_text(remote)

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            runner, "kubectl", side_effect=fake_kubectl
        ), patch.object(runner, "stream_copy_from_pod", side_effect=fake_copy):
            report = runner.preserve_metric_artifacts(
                "test", "runner", "/tmp/metrics", Path(temporary)
            )

        self.assertIn("pre_epp.prom", report["preserved"])
        self.assertEqual(report["missing"], ["post_envoy.prom"])
        self.assertEqual(report["errors"], [])

    def test_live_status_sync_write_is_atomic_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "live-status.json"
            value = {
                "generatedAt": "2026-08-06T00:00:00Z",
                "state": "running",
                "phase": "traffic",
                "tenants": [],
            }
            sync_live_status.atomic_write(path, value)
            self.assertEqual(json.loads(path.read_text()), value)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_backend_http_version_is_explicit(self) -> None:
        tenant = {
            "fairness_id": "tenant-a",
            "objective": "realtime",
        }
        self.assertTrue(backend_config("http://vllm", "model", tenant)["http2"])
        self.assertFalse(
            backend_config("http://vllm", "model", tenant, http2=False)["http2"]
        )

    def test_backend_can_disable_connection_reuse(self) -> None:
        tenant = {"fairness_id": "tenant-a", "objective": "realtime"}
        headers = backend_config(
            "http://vllm", "model", tenant, connection_close=True
        )["extras"]["headers"]
        self.assertEqual(headers["Connection"], "close")

    def test_guidellm_workers_fit_four_way_cpu_replay(self) -> None:
        document = job_document("test", "run", "config", 1.0, {}, 600, 1)
        container = document["spec"]["template"]["spec"]["containers"][0]
        resources = container["resources"]
        self.assertEqual(resources["requests"]["cpu"], "4")
        self.assertEqual(resources["limits"]["cpu"], "4")
        environment = {value["name"]: value["value"] for value in container["env"]}
        self.assertEqual(environment["GUIDELLM__MAX_WORKER_PROCESSES"], "1")
        self.assertEqual(environment["GUIDELLM__MP_POLL_INTERVAL"], "0.01")

    def test_guidellm_job_can_enable_shared_prefix_mode(self) -> None:
        document = job_document(
            "test", "run", "config", 1.0, {}, 600, 1,
            extra_env={
                "GUIDELLM_SHARED_PREFIX_FRACTION": "0.75",
                "GUIDELLM_SHARED_PREFIX_GROUP": "shared",
            },
        )
        env = {
            item["name"]: item["value"]
            for item in document["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env["GUIDELLM_SHARED_PREFIX_FRACTION"], "0.75")
        self.assertEqual(env["GUIDELLM_SHARED_PREFIX_GROUP"], "shared")

    def test_model_metric_targets_use_each_pod_ip(self) -> None:
        pods = [
            {"metadata": {"name": "model-a"}, "status": {"podIP": "10.0.0.1"}},
            {"metadata": {"name": "model-b"}, "status": {"podIP": "10.0.0.2"}},
        ]
        self.assertEqual(runner.vllm_metrics_targets(pods, 8000), [
            ("model-a", "http://10.0.0.1:8000/metrics"),
            ("model-b", "http://10.0.0.2:8000/metrics"),
        ])

    def test_multi_replica_route_proof_requires_every_model_pod(self) -> None:
        pods = [{"pod_ip": "10.0.0.1"}, {"pod_ip": "10.0.0.2"}]
        partial = runner.validate_model_routes(
            {"destination_counts": {"10.0.0.1:8000": 12}}, pods, True
        )
        complete = runner.validate_model_routes({
            "destination_counts": {
                "10.0.0.1:8000": 7,
                "10.0.0.2:8000": 5,
            },
        }, pods, True)

        self.assertFalse(partial["valid"])
        self.assertEqual(partial["model_pods_without_requests"], ["10.0.0.2"])
        self.assertTrue(complete["valid"])

    def test_route_proof_rejects_unknown_model_destination(self) -> None:
        report = runner.validate_model_routes(
            {"destination_counts": {"10.0.0.9:8000": 1}},
            [{"pod_ip": "10.0.0.1"}],
            False,
        )
        self.assertFalse(report["valid"])
        self.assertEqual(report["unexpected_pod_ips"], ["10.0.0.9"])

    def test_container_identity_matches_by_name(self) -> None:
        pod = {
            "spec": {"containers": [
                {"name": "epp", "image": "router:v0.9.0"},
                {"name": "envoy", "image": "proxy:v1"},
            ]},
            "status": {"containerStatuses": [
                {"name": "envoy", "imageID": "proxy@sha256:2"},
                {"name": "epp", "imageID": "router@sha256:1"},
            ]},
        }
        self.assertEqual(
            runner.container_identity(pod, "epp"),
            ("router:v0.9.0", "router@sha256:1"),
        )

    def test_container_health_records_resources_and_oom_restart(self) -> None:
        pod = {
            "metadata": {"name": "epp-pod", "creationTimestamp": "now"},
            "spec": {"containers": [{
                "name": "epp",
                "args": ["--v", "4"],
                "resources": {"limits": {"memory": "1Gi"}},
            }]},
            "status": {"containerStatuses": [{
                "name": "epp",
                "ready": True,
                "restartCount": 1,
                "lastState": {"terminated": {
                    "reason": "OOMKilled", "exitCode": 137, "finishedAt": "later",
                }},
            }]},
        }

        health = runner.container_health_evidence(pod, "epp")

        self.assertEqual(health["restart_count"], 1)
        self.assertEqual(health["last_termination_reason"], "OOMKilled")
        self.assertEqual(health["resources"]["limits"]["memory"], "1Gi")

    def test_kubernetes_memory_quantity_is_converted_to_bytes(self) -> None:
        self.assertEqual(runner.parse_kubernetes_memory("1Gi"), 1024 ** 3)
        self.assertEqual(runner.parse_kubernetes_memory("500M"), 500_000_000)

    def test_endpoint_picker_restart_rejects_health_transition(self) -> None:
        before = {"pod": "epp", "ready": True, "restart_count": 0}
        after = {
            "pod": "epp", "ready": True, "restart_count": 1,
            "last_termination_reason": "OOMKilled",
        }
        transition = runner.endpoint_picker_health_transition(before, after)
        self.assertFalse(transition["valid"])
        self.assertEqual(transition["last_termination_reason"], "OOMKilled")

    def test_runtime_proof_requires_gate_detector_bands_and_cache_off(self) -> None:
        epp = """
featureGates:
- flowControl
- type: concurrency-detector
  priorityBands:
  - priority: 100
  - priority: 50
  - priority: 0
  - priority: -10
"""
        proof = runner.runtime_proof(
            epp, "--max-num-seqs 128 --no-enable-prefix-caching"
        )
        self.assertTrue(all((
            proof["flow_control_gate"], proof["detector"], proof["priority_bands"],
            proof["prefix_cache_off"], proof["max_num_seqs_128"],
        )))
        self.assertEqual(proof["max_concurrency"], None)
        self.assertEqual(proof["queue_depth_threshold"], None)

    def test_runtime_proof_records_exact_detector_values(self) -> None:
        proof = runner.runtime_proof(
            """flowControl concurrency-detector
concurrencyMode: tokens
maxConcurrency: 128
maxTokenConcurrency: 148480
headroom: 0.0
addEstimatedOutputTokens: false
""",
            "--max-num-seqs 128 --no-enable-prefix-caching",
        )
        self.assertEqual(proof["max_concurrency"], 128)
        self.assertEqual(proof["concurrency_mode"], "tokens")
        self.assertEqual(proof["max_token_concurrency"], 148480)
        self.assertFalse(proof["add_estimated_output_tokens"])
        self.assertEqual(proof["headroom"], 0.0)
        self.assertEqual(proof["token_producer_backend"], "auto-estimate")

    def test_runtime_proof_records_exact_vllm_token_producer(self) -> None:
        proof = runner.runtime_proof(
            """flowControl concurrency-detector
- type: token-producer
  parameters:
    modelName: openai/gpt-oss-20b
    vllm:
      url: http://model-service:8000
""",
            "--max-num-seqs 128 --no-enable-prefix-caching",
        )
        self.assertEqual(proof["token_producer_backend"], "vllm")
        self.assertEqual(proof["token_producer_model"], "openai/gpt-oss-20b")
        self.assertEqual(proof["token_producer_url"], "http://model-service:8000")

    def test_runtime_proof_recognizes_utilization_detector(self) -> None:
        proof = runner.runtime_proof(
            "flowControl utilization-detector queueDepthThreshold: 5 headroom: 0.0",
            "--max-num-seqs 128 --no-enable-prefix-caching",
        )
        self.assertEqual(proof["detector"], "utilization-detector")
        self.assertEqual(proof["queue_depth_threshold"], 5)

    def test_runtime_proof_rejects_cache_on(self) -> None:
        proof = runner.runtime_proof("flowControl concurrency-detector", "--max-num-seqs 128")
        self.assertFalse(proof["prefix_cache_off"])
        self.assertEqual(proof["prefix_cache_mode"], "unspecified")

    def test_runtime_proof_requires_explicit_cache_on(self) -> None:
        proof = runner.runtime_proof(
            "flowControl concurrency-detector",
            "--max-num-seqs 128 --enable-prefix-caching",
        )
        self.assertTrue(proof["prefix_cache_on"])
        self.assertEqual(proof["prefix_cache_mode"], "on")

    def test_runtime_proof_records_bounded_prefix_index_settings(self) -> None:
        proof = runner.runtime_proof(
            """flowControl concurrency-detector
- type: prefix-cache-scorer
- type: approx-prefix-cache-producer
  parameters:
    autoTune: false
    blockSizeTokens: 64
    maxPrefixTokensToMatch: 32768
    lruCapacityPerServer: 31250
""",
            "--max-num-seqs 128 --enable-prefix-caching",
        )
        self.assertFalse(proof["prefix_auto_tune"])
        self.assertEqual(proof["prefix_block_size_tokens"], 64)
        self.assertEqual(proof["prefix_max_tokens_to_match"], 32768)
        self.assertEqual(proof["prefix_lru_capacity_per_server"], 31250)

    def test_process_config_proof_records_loaded_concurrency_settings(self) -> None:
        proof = runner.process_config_proof({
            "featureGates": ["flowControl"],
            "flowControl": {
                "saturationDetector": {"pluginRef": "concurrency-detector"},
                "priorityBands": [
                    {"priority": 100}, {"priority": 50},
                    {"priority": 0}, {"priority": -10},
                ],
            },
            "plugins": [
                {
                    "name": "concurrency-detector",
                    "parameters": {
                        "concurrencyMode": "tokens",
                        "maxConcurrency": 96,
                        "maxTokenConcurrency": 148480,
                        "headroom": 0.05,
                        "inFlightLoadProducerName": "inflight-load",
                    },
                },
                {
                    "name": "inflight-load",
                    "parameters": {"addEstimatedOutputTokens": False},
                },
            ],
        })
        self.assertEqual(proof, {
            "flow_control_gate": True,
            "detector": "concurrency-detector",
            "priority_bands": True,
            "queue_depth_threshold": None,
            "max_concurrency": 96,
            "concurrency_mode": "tokens",
            "max_token_concurrency": 148480,
            "add_estimated_output_tokens": False,
            "headroom": 0.05,
            "picker": None,
            "prefix_cache_scorer": False,
            "prefix_auto_tune": None,
            "prefix_block_size_tokens": None,
            "prefix_max_tokens_to_match": None,
            "prefix_lru_capacity_per_server": None,
            "token_producer_backend": "auto-estimate",
            "token_producer_model": None,
            "token_producer_url": None,
        })

    def test_process_config_proof_records_vllm_token_producer(self) -> None:
        proof = runner.process_config_proof({
            "plugins": [{
                "type": "token-producer",
                "parameters": {
                    "modelName": "openai/gpt-oss-20b",
                    "vllm": {"url": "http://model-service:8000"},
                },
            }],
        })
        self.assertEqual(proof["token_producer_backend"], "vllm")
        self.assertEqual(proof["token_producer_model"], "openai/gpt-oss-20b")
        self.assertEqual(proof["token_producer_url"], "http://model-service:8000")

    def test_process_config_proves_cache_scorer_and_picker(self) -> None:
        proof = runner.process_config_proof({
            "featureGates": ["flowControl"],
            "flowControl": {
                "saturationDetector": {"pluginRef": "concurrency-detector"},
                "priorityBands": [
                    {"priority": 100}, {"priority": 50},
                    {"priority": 0}, {"priority": -10},
                ],
            },
            "plugins": [
                {"type": "prefix-cache-scorer"},
                {"type": "max-score-picker"},
                {
                    "type": "approx-prefix-cache-producer",
                    "parameters": {
                        "autoTune": False,
                        "blockSizeTokens": 64,
                        "maxPrefixTokensToMatch": 32768,
                        "lruCapacityPerServer": 31250,
                    },
                },
                {"name": "concurrency-detector", "type": "concurrency-detector"},
            ],
        })
        self.assertTrue(proof["prefix_cache_scorer"])
        self.assertEqual(proof["picker"], "max-score-picker")
        self.assertFalse(proof["prefix_auto_tune"])
        self.assertEqual(proof["prefix_block_size_tokens"], 64)
        self.assertEqual(proof["prefix_max_tokens_to_match"], 32768)
        self.assertEqual(proof["prefix_lru_capacity_per_server"], 31250)

    def test_process_config_proof_records_loaded_utilization_settings(self) -> None:
        proof = runner.process_config_proof({
            "featureGates": ["flowControl"],
            "flowControl": {
                "saturationDetector": {"pluginRef": "utilization-detector"},
                "priorityBands": [],
            },
            "plugins": [{
                "name": "utilization-detector",
                "parameters": {"queueDepthThreshold": 2, "headroom": 0.0},
            }],
        })
        self.assertEqual(proof["detector"], "utilization-detector")
        self.assertEqual(proof["queue_depth_threshold"], 2)
        self.assertFalse(proof["priority_bands"])

    def test_resource_names_are_kubernetes_safe(self) -> None:
        name = resource_name("FC GuideLLM", "Premium_A")
        self.assertEqual(name, "fc-guidellm-premium-a")
        self.assertLessEqual(len(name), 63)

    def test_metric_artifact_contract_is_complete(self) -> None:
        self.assertEqual(set(runner.METRIC_ARTIFACTS), {
            "pre_epp.prom", "pre_vllm.prom", "pre_envoy.prom", "metric_preflight.json",
            "metric_samples_long.csv", "post_epp.prom", "post_vllm.prom", "post_envoy.prom",
            "metric_capture_health.json",
        })

    def test_route_mismatch_keeps_complete_data_but_rejects_slo_proof(self) -> None:
        merged = runner.merge_route_evidence(
            {"data_quality_valid": True, "slo_proof_valid": False},
            {"count_matches": True, "direct_vllm_bypass_detected": False, "valid": False},
        )
        self.assertTrue(merged["data_quality_valid"])
        self.assertFalse(merged["slo_proof_valid"])
        self.assertEqual(merged["slo_proof_reason"], "client and gateway outcomes differ")

    def test_incomplete_route_count_rejects_data_quality(self) -> None:
        merged = runner.merge_route_evidence(
            {"data_quality_valid": True, "slo_proof_valid": True},
            {"count_matches": False, "direct_vllm_bypass_detected": False, "valid": False},
        )
        self.assertFalse(merged["data_quality_valid"])
        self.assertEqual(merged["data_quality_reason"], "route evidence failed")

    def test_valid_route_preserves_prior_data_quality_failure_reason(self) -> None:
        merged = runner.merge_route_evidence(
            {
                "data_quality_valid": False,
                "data_quality_reason": "offered schedule failed",
                "slo_proof_valid": False,
            },
            {"count_matches": True, "direct_vllm_bypass_detected": False, "valid": True},
        )
        self.assertFalse(merged["data_quality_valid"])
        self.assertEqual(
            merged["data_quality_reason"], "offered schedule failed"
        )

    def test_route_merge_can_recover_after_revalidated_capture(self) -> None:
        preconditions = {
            "data_quality_valid": True,
            "data_quality_reason": "valid",
            "slo_proof_valid": True,
            "slo_proof_reason": "valid",
        }
        failed = runner.merge_route_evidence(preconditions, {
            "count_matches": False,
            "direct_vllm_bypass_detected": False,
            "valid": False,
        })
        recovered = runner.merge_route_evidence(failed, {
            "count_matches": True,
            "direct_vllm_bypass_detected": False,
            "valid": True,
        })
        self.assertTrue(recovered["data_quality_valid"])
        self.assertEqual(recovered["data_quality_reason"], "valid")
        self.assertTrue(recovered["slo_proof_valid"])
        self.assertEqual(recovered["slo_proof_reason"], "valid")


if __name__ == "__main__":
    unittest.main()

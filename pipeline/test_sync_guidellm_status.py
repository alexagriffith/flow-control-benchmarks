#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from sync_guidellm_status import blinded_status, completed_status, metric_snapshot, offered_rates


class SyncGuideLlmStatusTests(unittest.TestCase):
    def test_blinded_status_hides_holdout_outcomes(self) -> None:
        status = blinded_status({
            "state": "running",
            "elapsedS": 42,
            "offeredRps": 18,
            "servedRps": 17,
            "activeRequests": 6,
            "eppQueued": 4,
            "eppQueuedPeak": 9,
            "vllmRunning": 2,
            "vllmWaiting": 1,
            "kvCacheUsage": 0.75,
            "eppMemoryBytes": 123,
            "eppPrefixIndexEntries": 7,
            "eppRestartDetected": True,
            "p95TtftMs": 380,
            "errors": 2,
            "rejections": 3,
            "tenants": [{
                "id": "premium",
                "priority": 100,
                "offeredRps": 10,
                "servedRps": 9,
                "active": 3,
                "queued": 2,
                "queuedPeak": 8,
                "p95TtftMs": 300,
            }],
        })

        self.assertEqual(status["state"], "running")
        self.assertEqual(status["elapsedS"], 42)
        self.assertEqual(status["offeredRps"], 18)
        for key in ("servedRps", "activeRequests", "eppQueued", "eppQueuedPeak",
                    "vllmRunning", "vllmWaiting", "kvCacheUsage", "eppMemoryBytes",
                    "eppPrefixIndexEntries", "eppRestartDetected", "errors", "rejections"):
            self.assertEqual(status[key], 0)
        self.assertIsNone(status["p95TtftMs"])
        self.assertEqual(status["tenants"][0]["offeredRps"], 10)
        self.assertEqual(status["tenants"][0]["servedRps"], 0)
        self.assertIsNone(status["tenants"][0]["p95TtftMs"])

    def test_metric_snapshot_reports_live_queue_and_engine_state(self) -> None:
        header = "run_id,scenario,elapsed_s,sample_epoch_s,source,metric_generation,metric,labels_json,value\n"
        rows = [
            'r,s,1,10,epp,canonical,llm_d_epp_flow_control_queue_size,"{\"\"fairness_id\"\":\"\"batch\"\",\"\"priority\"\":\"\"-10\"\"}",2\n',
            'r,s,1,10,epp,canonical,llm_d_epp_flow_control_request_queue_duration_seconds_count,"{\"\"fairness_id\"\":\"\"batch\"\"}",10\n',
            'r,s,11,20,epp,canonical,llm_d_epp_flow_control_queue_size,"{\"\"fairness_id\"\":\"\"batch\"\",\"\"priority\"\":\"\"-10\"\"}",7\n',
            'r,s,11,20,epp,canonical,llm_d_epp_flow_control_request_queue_duration_seconds_count,"{\"\"fairness_id\"\":\"\"batch\"\"}",50\n',
            'r,s,11,20,vllm,canonical,vllm:num_requests_running,"{}",96\n',
            'r,s,11,20,vllm,canonical,vllm:num_requests_waiting,"{}",3\n',
            'r,s,11,20,vllm,canonical,vllm:kv_cache_usage_perc,"{}",0.25\n',
            'r,s,1,10,epp,runtime,process_start_time_seconds,"{}",12345\n',
            'r,s,11,20,epp,runtime,process_start_time_seconds,"{}",12345\n',
            'r,s,11,20,epp,runtime,process_resident_memory_bytes,"{}",524288000\n',
            'r,s,11,20,epp,canonical,llm_d_epp_prefix_indexer_size,"{}",31250\n',
        ]
        status = metric_snapshot(header + "".join(rows))
        self.assertEqual(status["queue"], {"batch": 7.0})
        self.assertEqual(status["queue_peak"], {"batch": 7.0})
        self.assertEqual(status["queue_total_peak"], 7.0)
        self.assertEqual(status["completion_rps"], {"batch": 4.0})
        self.assertEqual(status["vllm_running"], 96.0)
        self.assertEqual(status["vllm_waiting"], 3.0)
        self.assertEqual(status["kv_cache_usage"], 0.25)
        self.assertEqual(status["epp_memory_bytes"], 524288000)
        self.assertEqual(status["epp_prefix_index_entries"], 31250)
        self.assertFalse(status["epp_restart_detected"])

    def test_offered_rates_use_recent_schedule(self) -> None:
        rates = offered_rates({"a": [0.0, 5.0, 10.0, 15.0]}, 15.0)
        self.assertEqual(rates["a"], 0.3)

    def test_metric_snapshot_ignores_a_repeated_header(self) -> None:
        header = "run_id,scenario,elapsed_s,sample_epoch_s,source,metric_generation,metric,labels_json,value\n"
        status = metric_snapshot(header + header)
        self.assertEqual(status, {})

    def test_metric_snapshot_ignores_an_incomplete_latest_scrape(self) -> None:
        header = "run_id,scenario,elapsed_s,sample_epoch_s,source,metric_generation,metric,labels_json,value\n"
        rows = [
            'r,s,11,20,epp,canonical,llm_d_epp_flow_control_queue_size,"{""fairness_id"":""batch"",""priority"":""-10""}",7\n',
            'r,s,11,20,vllm,canonical,vllm:num_requests_running,"{}",96\n',
            'r,s,11,20,vllm,canonical,vllm:num_requests_waiting,"{}",3\n',
            'r,s,11,20,vllm,canonical,vllm:kv_cache_usage_perc,"{}",0.25\n',
            'r,s,21,30,epp,canonical,llm_d_epp_flow_control_queue_size,"{""fairness_id"":""batch"",""priority"":""-10""}",12\n',
        ]

        status = metric_snapshot(header + "".join(rows))

        self.assertEqual(status["metric_elapsed_s"], 11.0)
        self.assertEqual(status["queue"], {"batch": 7.0})
        self.assertEqual(status["vllm_running"], 96.0)
        self.assertEqual(status["kv_cache_usage"], 0.25)

    def test_completed_status_preserves_recorded_queue_peaks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            result_dir = run_dir / "result"
            result_dir.mkdir()
            (result_dir / "summary.json").write_text(json.dumps({
                "window_summary": [{
                    "window": "surge",
                    "tenant": "realtime",
                    "priority": 100,
                    "total": 10,
                    "duration_s": 5,
                    "throughput_rps": 2,
                    "ttft_p95_s": 0.2,
                }],
                "client_summary": [{"errors": 0}],
                "runtime_metrics": {
                    "max_epp_queue": 9,
                    "max_epp_queue_by_tenant": {"realtime": 4},
                    "max_epp_resident_memory_bytes": 524288000,
                    "max_epp_prefix_index_entries": 31250,
                    "epp_process_restart_detected": False,
                },
            }))
            (result_dir / "preconditions.json").write_text(json.dumps({
                "data_quality_valid": True,
                "http_statuses": {"200": 10},
            }))
            args = argparse.Namespace(
                run_dir=run_dir,
                prefix="run-1",
                stage_id="batch-baseline",
            )

            status = completed_status(args, {
                "scenario": "batch-test",
                "duration_s": 5,
            })

            self.assertEqual(status["eppQueuedPeak"], 9)
            self.assertEqual(status["tenants"][0]["queuedPeak"], 4)
            self.assertEqual(status["eppMemoryBytes"], 524288000)
            self.assertEqual(status["eppPrefixIndexEntries"], 31250)
            self.assertFalse(status["eppRestartDetected"])


if __name__ == "__main__":
    unittest.main()

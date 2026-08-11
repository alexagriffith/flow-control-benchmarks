#!/usr/bin/env python3
"""Publish live GuideLLM and metric-capture state to Flight Recorder."""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import shlex
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sync_live_status import atomic_write


QUEUE_METRIC = "llm_d_epp_flow_control_queue_size"
COMPLETION_METRIC = "llm_d_epp_flow_control_request_queue_duration_seconds_count"
ENGINE_METRICS = {
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:kv_cache_usage_perc",
}
EPP_MEMORY_METRIC = "process_resident_memory_bytes"
EPP_START_METRIC = "process_start_time_seconds"
PREFIX_INDEX_METRIC = "llm_d_epp_prefix_indexer_size"
LIVE_METRICS = {
    QUEUE_METRIC, COMPLETION_METRIC, EPP_MEMORY_METRIC, EPP_START_METRIC,
    PREFIX_INDEX_METRIC, *ENGINE_METRICS,
}


def remote_metric_tail(namespace: str, pod: str, path: str) -> str | None:
    quoted_path = shlex.quote(path)
    metric_pattern = "|".join(sorted(LIVE_METRICS))
    result = subprocess.run(
        [
            "kubectl", "-n", namespace, "exec", pod, "--", "sh", "-c",
            (
                f"head -n 1 {quoted_path}; "
                f"tail -n 20000 {quoted_path} | grep -E {shlex.quote(metric_pattern)}"
            ),
        ],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def metric_snapshot(text: str) -> dict[str, Any]:
    rows = list(csv.DictReader(io.StringIO(text)))
    rows = [
        row for row in rows
        if row.get("elapsed_s") not in (None, "", "elapsed_s")
        and row.get("value") not in (None, "", "value")
    ]
    if not rows:
        return {}
    samples: dict[float, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        samples[float(row["elapsed_s"])].append(row)
    complete_elapsed = sorted(
        sample_elapsed for sample_elapsed, sample_rows in samples.items()
        if ENGINE_METRICS.issubset({
            row["metric"] for row in sample_rows
            if row["metric_generation"] == "canonical"
        })
    )
    if not complete_elapsed:
        return {}
    latest_elapsed = complete_elapsed[-1]
    samples = {
        sample_elapsed: sample_rows
        for sample_elapsed, sample_rows in samples.items()
        if sample_elapsed <= latest_elapsed
    }
    latest = samples[latest_elapsed]

    def labels(row: dict[str, str]) -> dict[str, str]:
        try:
            return json.loads(row["labels_json"])
        except (json.JSONDecodeError, TypeError):
            return {}

    queue: dict[str, float] = defaultdict(float)
    priorities: dict[str, int] = {}
    for row in latest:
        if row["metric_generation"] != "canonical" or row["metric"] != QUEUE_METRIC:
            continue
        item_labels = labels(row)
        tenant = item_labels.get("fairness_id", "unknown")
        queue[tenant] += float(row["value"])
        priorities[tenant] = int(item_labels.get("priority", 0))

    queue_peak: dict[str, float] = defaultdict(float)
    queue_total_peak = 0.0
    completion_counts: dict[float, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for sample_elapsed, sample_rows in samples.items():
        sample_queue: dict[str, float] = defaultdict(float)
        for row in sample_rows:
            if row["metric_generation"] != "canonical":
                continue
            item_labels = labels(row)
            tenant = item_labels.get("fairness_id", "unknown")
            if row["metric"] == QUEUE_METRIC:
                sample_queue[tenant] += float(row["value"])
            elif row["metric"] == COMPLETION_METRIC:
                completion_counts[sample_elapsed][tenant] += float(row["value"])
        for tenant, value in sample_queue.items():
            queue_peak[tenant] = max(queue_peak[tenant], value)
        queue_total_peak = max(queue_total_peak, sum(sample_queue.values()))

    completion_rps: dict[str, float] = defaultdict(float)
    completion_times = sorted(completion_counts)
    if len(completion_times) >= 2:
        first, last = completion_times[0], completion_times[-1]
        duration = last - first
        if duration > 0:
            for tenant in set(completion_counts[first]) | set(completion_counts[last]):
                completion_rps[tenant] = max(
                    0.0,
                    (completion_counts[last][tenant] - completion_counts[first][tenant]) / duration,
                )

    def latest_sum(metric: str) -> float:
        return sum(
            float(row["value"]) for row in latest
            if row["metric_generation"] == "canonical" and row["metric"] == metric
        )

    kv_values = [
        float(row["value"]) for row in latest
        if row["metric_generation"] == "canonical"
        and row["metric"] == "vllm:kv_cache_usage_perc"
    ]
    def latest_max(metric: str) -> float:
        return max(
            (float(row["value"]) for row in latest if row["metric"] == metric),
            default=0.0,
        )

    process_start_times = {
        float(row["value"])
        for sample_rows in samples.values()
        for row in sample_rows
        if row["metric"] == EPP_START_METRIC
    }
    return {
        "metric_elapsed_s": latest_elapsed,
        "queue": dict(queue),
        "queue_peak": dict(queue_peak),
        "queue_total_peak": queue_total_peak,
        "priorities": priorities,
        "completion_rps": dict(completion_rps),
        "vllm_running": latest_sum("vllm:num_requests_running"),
        "vllm_waiting": latest_sum("vllm:num_requests_waiting"),
        "kv_cache_usage": max(kv_values, default=0.0),
        "epp_memory_bytes": latest_max(EPP_MEMORY_METRIC),
        "epp_prefix_index_entries": latest_max(PREFIX_INDEX_METRIC),
        "epp_restart_detected": len(process_start_times) > 1,
    }


def load_schedules(manifest_path: Path) -> tuple[dict[str, Any], dict[str, list[float]]]:
    manifest = json.loads(manifest_path.read_text())
    schedules: dict[str, list[float]] = {}
    for tenant in manifest["tenants"]:
        path = manifest_path.parent / tenant["trace_file"]
        schedules[tenant["fairness_id"]] = [
            float(json.loads(line)["timestamp"]) for line in path.read_text().splitlines()
        ]
    return manifest, schedules


def offered_rates(schedules: dict[str, list[float]], elapsed_s: float) -> dict[str, float]:
    window = 10.0
    start = max(0.0, elapsed_s - window)
    duration = max(1.0, elapsed_s - start)
    return {
        tenant: (
            bisect.bisect_right(times, elapsed_s) - bisect.bisect_left(times, start)
        ) / duration
        for tenant, times in schedules.items()
    }


def completed_status(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads((args.run_dir / "result" / "summary.json").read_text())
    preconditions = json.loads((args.run_dir / "result" / "preconditions.json").read_text())
    runtime = summary.get("runtime_metrics", {})
    queue_peaks = runtime.get("max_epp_queue_by_tenant", {})
    surge = [row for row in summary["window_summary"] if row["window"] == "surge"]
    tenants = [{
        "id": row["tenant"],
        "priority": row["priority"],
        "offeredRps": row["total"] / row["duration_s"],
        "servedRps": row["throughput_rps"],
        "active": 0,
        "queued": 0,
        "queuedPeak": queue_peaks.get(row["tenant"], 0),
            "p95TtftMs": (row.get("ttft_p95_s") or 0) * 1000,
    } for row in surge]
    platinum = next((row for row in surge if row["priority"] == 100), None)
    statuses = preconditions.get("http_statuses", {})
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "runId": args.prefix,
        "scenario": manifest["scenario"],
        "stageId": args.stage_id,
        "state": "complete" if preconditions.get("data_quality_valid") else "failed",
        "elapsedS": float(manifest["duration_s"]),
        "phase": "Artifacts validated" if preconditions.get("data_quality_valid") else "Run rejected",
        "offeredRps": sum(item["offeredRps"] for item in tenants),
        "servedRps": sum(item["servedRps"] for item in tenants),
        "activeRequests": 0,
        "eppQueued": 0,
        "eppQueuedPeak": runtime.get("max_epp_queue", 0),
        "vllmRunning": 0,
        "vllmWaiting": 0,
        "kvCacheUsage": 0,
        "eppMemoryBytes": runtime.get("max_epp_resident_memory_bytes", 0),
        "eppPrefixIndexEntries": runtime.get("max_epp_prefix_index_entries", 0),
        "eppRestartDetected": runtime.get("epp_process_restart_detected", False),
        "p95TtftMs": platinum["ttft_p95_s"] * 1000 if platinum else None,
        "errors": sum(int(item.get("errors", 0)) for item in summary["client_summary"]),
        "rejections": int(statuses.get("429", 0)),
        "tenants": tenants,
    }


def blinded_status(status: dict[str, Any]) -> dict[str, Any]:
    status = dict(status)
    status["blinded"] = True
    status["phase"] = "Confirmatory holdout: results blinded"
    for key in ("servedRps", "activeRequests", "eppQueued", "eppQueuedPeak",
                "vllmRunning", "vllmWaiting", "kvCacheUsage", "eppMemoryBytes",
                "eppPrefixIndexEntries", "eppRestartDetected", "p95TtftMs",
                "errors", "rejections"):
        status[key] = None if key == "p95TtftMs" else 0
    status["tenants"] = [{
        **tenant,
        "servedRps": 0,
        "active": 0,
        "queued": 0,
        "queuedPeak": 0,
        "p95TtftMs": None,
    } for tenant in status.get("tenants", [])]
    return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--remote-metrics", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--runner-pod", default="flow-control-benchmark-runner")
    parser.add_argument("--stage-id", default="headroom")
    parser.add_argument("--start-delay-s", type=float, default=90.0)
    parser.add_argument("--interval-s", type=float, default=5.0)
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--blinded", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest, schedules = load_schedules(args.manifest.resolve())
    peaks: dict[str, float] = defaultdict(float)
    total_peak = 0.0
    started = time.monotonic()
    while True:
        if (args.run_dir / "result" / "preconditions.json").is_file():
            status = completed_status(args, manifest)
            atomic_write(args.output, blinded_status(status) if args.blinded else status)
            return
        snapshot = metric_snapshot(
            remote_metric_tail(args.namespace, args.runner_pod, args.remote_metrics) or ""
        )
        metric_elapsed = float(snapshot.get("metric_elapsed_s", 0.0))
        elapsed = max(0.0, metric_elapsed - args.start_delay_s)
        warming = metric_elapsed < args.start_delay_s
        offered = {} if warming else offered_rates(schedules, elapsed)
        queue = snapshot.get("queue", {})
        for tenant, value in snapshot.get("queue_peak", {}).items():
            peaks[tenant] = max(peaks[tenant], value)
        total_peak = max(total_peak, snapshot.get("queue_total_peak", 0.0))
        served = snapshot.get("completion_rps", {})
        tenants = [{
            "id": item["fairness_id"],
            "priority": int(item["priority"]),
            "offeredRps": offered.get(item["fairness_id"], 0.0),
            "servedRps": served.get(item["fairness_id"], 0.0),
            "active": queue.get(item["fairness_id"], 0.0),
            "queued": queue.get(item["fairness_id"], 0.0),
            "queuedPeak": peaks.get(item["fairness_id"], 0.0),
            "p95TtftMs": None,
        } for item in manifest["tenants"]]
        traffic_active = elapsed < float(manifest["duration_s"])
        status = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "runId": args.prefix,
            "scenario": manifest["scenario"],
            "stageId": args.stage_id,
            "state": "warming" if warming else (
                "running" if traffic_active else "draining"
            ),
            "elapsedS": elapsed,
            "phase": "Warmup and synchronized start" if warming else (
                "Production-shaped traffic" if traffic_active else "Draining and validating artifacts"
            ),
            "offeredRps": sum(offered.values()),
            "servedRps": sum(item["servedRps"] for item in tenants),
            "activeRequests": sum(queue.values()) + snapshot.get("vllm_running", 0.0) + snapshot.get("vllm_waiting", 0.0),
            "eppQueued": sum(queue.values()),
            "eppQueuedPeak": total_peak,
            "vllmRunning": snapshot.get("vllm_running", 0.0),
            "vllmWaiting": snapshot.get("vllm_waiting", 0.0),
            "kvCacheUsage": snapshot.get("kv_cache_usage", 0.0),
            "eppMemoryBytes": snapshot.get("epp_memory_bytes", 0.0),
            "eppPrefixIndexEntries": snapshot.get("epp_prefix_index_entries", 0.0),
            "eppRestartDetected": snapshot.get("epp_restart_detected", False),
            "p95TtftMs": None,
            "errors": 0,
            "rejections": 0,
            "tenants": tenants,
        }
        atomic_write(args.output, blinded_status(status) if args.blinded else status)
        if time.monotonic() - started >= args.timeout_s:
            raise TimeoutError(f"live status timed out for {args.prefix}")
        time.sleep(args.interval_s)


if __name__ == "__main__":
    main()

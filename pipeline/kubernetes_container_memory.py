#!/usr/bin/env python3
"""Sample Kubernetes container memory from the kubelet summary API."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def kubectl_json(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def pod_node(namespace: str, pod: str) -> str:
    payload = kubectl_json(["-n", namespace, "get", "pod", pod, "-o", "json"])
    node = payload.get("spec", {}).get("nodeName")
    if not node:
        raise RuntimeError(f"pod {pod} has no assigned node")
    return str(node)


def container_memory(
    summary: dict[str, Any], namespace: str, pod: str, container: str,
) -> dict[str, int]:
    for pod_stats in summary.get("pods", []):
        reference = pod_stats.get("podRef", {})
        if reference.get("namespace") != namespace or reference.get("name") != pod:
            continue
        for container_stats in pod_stats.get("containers", []):
            if container_stats.get("name") != container:
                continue
            memory = container_stats.get("memory") or {}
            required = ("workingSetBytes", "rssBytes", "usageBytes")
            missing = [name for name in required if memory.get(name) is None]
            if missing:
                raise RuntimeError("missing container memory fields: " + ", ".join(missing))
            return {name: int(memory[name]) for name in required}
    raise RuntimeError(f"container {namespace}/{pod}/{container} not found in node summary")


def sample(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "kubernetes_container_memory.csv"
    health_path = out_dir / "kubernetes_container_memory_health.json"
    node = pod_node(args.namespace, args.pod)
    started = time.monotonic()
    next_sample = started
    rows = 0
    skipped_intervals = 0
    sample_durations: list[float] = []
    errors: list[dict[str, Any]] = []
    peaks = {"workingSetBytes": 0, "rssBytes": 0, "usageBytes": 0}
    fields = [
        "run_id", "scenario", "elapsed_s", "sample_epoch_s", "namespace",
        "pod", "container", "node", "working_set_bytes", "rss_bytes", "usage_bytes",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        while time.monotonic() - started < args.duration:
            if args.stop_file and Path(args.stop_file).exists():
                break
            sleep_s = next_sample - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            elapsed = time.monotonic() - started
            sample_started = time.monotonic()
            try:
                summary = kubectl_json([
                    "get", "--raw", f"/api/v1/nodes/{node}/proxy/stats/summary",
                ])
                memory = container_memory(
                    summary, args.namespace, args.pod, args.container,
                )
                for name, value in memory.items():
                    peaks[name] = max(peaks[name], value)
                writer.writerow({
                    "run_id": args.run_id,
                    "scenario": args.scenario,
                    "elapsed_s": round(elapsed, 6),
                    "sample_epoch_s": round(time.time(), 6),
                    "namespace": args.namespace,
                    "pod": args.pod,
                    "container": args.container,
                    "node": node,
                    "working_set_bytes": memory["workingSetBytes"],
                    "rss_bytes": memory["rssBytes"],
                    "usage_bytes": memory["usageBytes"],
                })
                handle.flush()
                rows += 1
            except Exception as exc:
                errors.append({
                    "elapsed_s": round(elapsed, 6),
                    "error": f"{type(exc).__name__}: {exc}",
                })
            sample_durations.append(time.monotonic() - sample_started)
            next_sample += args.interval
            while next_sample <= time.monotonic():
                next_sample += args.interval
                skipped_intervals += 1

    peak_fraction = (
        peaks["workingSetBytes"] / args.memory_limit_bytes
        if args.memory_limit_bytes else None
    )
    health = {
        "valid": bool(
            rows > 0 and not errors and skipped_intervals == 0
            and peak_fraction is not None
            and peak_fraction < args.max_memory_fraction
        ),
        "samples": rows,
        "interval_s": args.interval,
        "errors": errors,
        "skipped_intervals": skipped_intervals,
        "max_sample_duration_s": max(sample_durations, default=0.0),
        "namespace": args.namespace,
        "pod": args.pod,
        "container": args.container,
        "node": node,
        "peak_working_set_bytes": peaks["workingSetBytes"],
        "peak_rss_bytes": peaks["rssBytes"],
        "peak_usage_bytes": peaks["usageBytes"],
        "container_limit_bytes": args.memory_limit_bytes,
        "peak_working_set_fraction_of_limit": peak_fraction,
        "maximum_allowed_fraction": args.max_memory_fraction,
    }
    health_path.write_text(json.dumps(health, indent=2) + "\n")
    return 0 if health["valid"] else 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--pod", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--memory-limit-bytes", type=int, required=True)
    parser.add_argument("--max-memory-fraction", type=float, default=0.85)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--stop-file")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(sample(parse_args()))

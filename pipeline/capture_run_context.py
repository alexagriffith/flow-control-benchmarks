#!/usr/bin/env python3
"""Capture scoped Kubernetes and Envoy provenance for one benchmark run."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


def kubectl(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, check=False
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "kubectl failed")
    return result.stdout


def load_envoy_logs(
    namespace: str, deployment: str, log_file: str | None = None,
    since_time: str | None = None,
) -> str:
    if log_file:
        return Path(log_file).read_text()
    time_arg = f"--since-time={since_time}" if since_time else "--since=30m"
    return kubectl(
        "logs", "-n", namespace, f"deployment/{deployment}",
        "-c", "envoy", time_arg,
    )


def parse_envoy_routes(
    text: str,
    start_epoch_s: float,
    end_epoch_s: float,
    expected_status_counts: dict[str, int] | None = None,
    expected_request_ids: set[str] | None = None,
    expected_fairness_ids: set[str] | None = None,
    expected_objectives: set[str] | None = None,
) -> dict[str, Any]:
    normalized = text.replace(r"\n", "\n")
    rows = []
    for line in normalized.splitlines():
        parts = line.split()
        if not parts or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", parts[0]
        ):
            continue
        fields = {
            key: value
            for token in parts[1:]
            if "=" in token
            for key, value in [token.split("=", 1)]
        }
        if not all(key in fields for key in ("status", "path", "upstream", "dest")):
            continue
        timestamp = dt.datetime.fromisoformat(
            parts[0].replace("Z", "+00:00")
        ).timestamp()
        if start_epoch_s <= timestamp <= end_epoch_s:
            rows.append({"time": parts[0], **fields})
    completion_rows = [row for row in rows if row["path"] == "/v1/completions"]
    status_counts = dict(Counter(row["status"] for row in completion_rows))
    expected_status_counts = expected_status_counts or {}
    count_matches = not expected_status_counts or len(completion_rows) == sum(
        expected_status_counts.values()
    )
    statuses_match = not expected_status_counts or status_counts == expected_status_counts
    observed_request_ids = {
        row["request_id"] for row in completion_rows
        if row.get("request_id") not in (None, "-")
    }
    request_ids_match = (
        expected_request_ids is None or observed_request_ids == expected_request_ids
    )
    observed_fairness_ids = {
        row["fairness"] for row in completion_rows
        if row.get("fairness") not in (None, "-")
    }
    fairness_ids_match = (
        expected_fairness_ids is None
        or observed_fairness_ids == expected_fairness_ids
    )
    observed_objectives = {
        row["objective"] for row in completion_rows
        if row.get("objective") not in (None, "-")
    }
    objectives_match = (
        expected_objectives is None or observed_objectives == expected_objectives
    )
    all_client_requests_observed_at_gateway = bool(
        count_matches and request_ids_match
    )
    return {
        "valid": all((
            bool(completion_rows), count_matches, statuses_match, request_ids_match,
            fairness_ids_match, objectives_match,
        )),
        "completion_requests": len(completion_rows),
        "status_counts": status_counts,
        "expected_completion_requests": sum(expected_status_counts.values()),
        "expected_status_counts": expected_status_counts,
        "count_matches": count_matches,
        "statuses_match": statuses_match,
        "request_ids_match": request_ids_match,
        "request_ids_present": len(observed_request_ids),
        "expected_request_ids": len(expected_request_ids or ()),
        "all_client_requests_observed_at_gateway": all_client_requests_observed_at_gateway,
        "fairness_ids": sorted(observed_fairness_ids),
        "fairness_ids_match": fairness_ids_match,
        "objectives": sorted(observed_objectives),
        "objectives_match": objectives_match,
        "response_flags": dict(Counter(
            row.get("flags", "-") for row in completion_rows
        )),
        "response_details": dict(Counter(
            row.get("details", "-") for row in completion_rows
        )),
        "dropped_reasons": dict(Counter(
            row["dropped_reason"] for row in completion_rows
            if row.get("dropped_reason") not in (None, "-")
        )),
        "upstreams": sorted({row["upstream"] for row in completion_rows if row["upstream"] != "-"}),
        "destinations": sorted({row["dest"] for row in completion_rows if row["dest"] != "-"}),
        "upstream_counts": dict(Counter(
            row["upstream"] for row in completion_rows if row["upstream"] != "-"
        )),
        "destination_counts": dict(Counter(
            row["dest"] for row in completion_rows if row["dest"] != "-"
        )),
        "direct_vllm_bypass_detected": (
            False if all_client_requests_observed_at_gateway else None
        ),
        "window_start_epoch_s": start_epoch_s,
        "window_end_epoch_s": end_epoch_s,
    }


def client_route_expectations(preconditions_path: Path) -> dict[str, Any]:
    samples_path = preconditions_path.with_name("client_samples.csv")
    if not samples_path.exists():
        return {
            "status_counts": {}, "request_ids": None,
            "fairness_ids": None, "objectives": None,
        }
    with samples_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        "status_counts": dict(Counter(row["status"] for row in rows if row["status"])),
        "request_ids": {row["request_id"] for row in rows if row["request_id"]},
        "fairness_ids": {row["tenant"] for row in rows if row["tenant"]},
        "objectives": {row["objective"] for row in rows if row["objective"]},
    }


def file_checksums(paths: list[str]) -> dict[str, str]:
    return {
        path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for path in paths
    }


def main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.benchmark_dir:
        precondition_paths = sorted(Path(args.benchmark_dir).glob("*/preconditions.json"))
    else:
        precondition_paths = [Path(args.preconditions)]
    windows = []
    for path in precondition_paths:
        preconditions = json.loads(path.read_text())
        windows.append((
            str(preconditions["run_id"]),
            float(preconditions["run_started_epoch_s"]) - 2.0,
            float(preconditions["run_ended_epoch_s"]) + 10.0,
            client_route_expectations(path),
        ))

    resources: dict[str, Any] = {}
    for kind in (
        "pods", "deployments", "services", "inferencepools", "inferenceobjectives",
        "roles", "rolebindings", "servicemonitors", "configmaps",
    ):
        payload = kubectl(
            "get", kind, "-n", args.namespace, "-l", f"experiment={args.experiment}",
            "-o", "json", check=False,
        )
        if payload.strip():
            try:
                resources[kind] = json.loads(payload)
            except json.JSONDecodeError:
                resources[kind] = {"error": "non-JSON kubectl response"}
    snapshot = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "context": kubectl("config", "current-context").strip(),
        "namespace": args.namespace,
        "experiment": args.experiment,
        "resources": resources,
        "config_sha256": file_checksums(args.config),
    }
    (out_dir / "kubernetes_snapshot.json").write_text(json.dumps(snapshot, indent=2))

    earliest_start = min(start for _run_id, start, _end, _expected in windows)
    since_time = dt.datetime.fromtimestamp(
        earliest_start, tz=dt.timezone.utc
    ).isoformat().replace("+00:00", "Z")
    logs = load_envoy_logs(
        args.namespace, args.epp_deployment, args.envoy_log_file, since_time
    )
    route_runs = {
        run_id: parse_envoy_routes(
            logs, start, end, expected["status_counts"],
            expected["request_ids"], expected["fairness_ids"],
            expected["objectives"],
        )
        for run_id, start, end, expected in windows
    }
    routes = (
        next(iter(route_runs.values()))
        if len(route_runs) == 1
        else {
            "valid": bool(route_runs) and all(item["valid"] for item in route_runs.values()),
            "runs": route_runs,
        }
    )
    (out_dir / "route_evidence.json").write_text(json.dumps(routes, indent=2))
    return 0 if routes["valid"] else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--epp-deployment", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--preconditions")
    source.add_argument("--benchmark-dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--envoy-log-file",
        help="Access log streamed during the run; avoids container-log rotation",
    )
    parser.add_argument("--config", action="append", default=[])
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))

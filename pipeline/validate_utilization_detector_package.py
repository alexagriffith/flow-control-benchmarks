#!/usr/bin/env python3
"""Validate the public utilization-detector calibration package."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def is_true(value: str) -> bool:
    return value.strip().lower() == "true"


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_utilization_detector_package(errors: list[str], root: Path) -> None:
    package = root / "benchmark-data" / "upstream-flow-control-v0.9.0" / "utilization-detector-calibration"
    required = {
        "README.md", "analysis.json", "request-results.csv", "run-config.json",
        "run-evidence.csv", "summary.csv", "system-metrics.csv", "traffic-samples.csv",
    }
    found = {path.name for path in package.iterdir() if path.is_file()}
    require(required <= found, "utilization-detector files missing", errors)

    summary = read_csv(package / "summary.csv")
    requests = read_csv(package / "request-results.csv")
    traffic = read_csv(package / "traffic-samples.csv")
    metrics = read_csv(package / "system-metrics.csv")
    evidence = read_csv(package / "run-evidence.csv")
    analysis = json.loads((package / "analysis.json").read_text())
    config = json.loads((package / "run-config.json").read_text())

    require(len(summary) == 23 and len(evidence) == 23, "expected 23 utilization calibration runs", errors)
    require(len(requests) == 78674, "utilization request count changed", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "utilization samples are empty", errors)
    require(all(row["http_status"] == "200" for row in requests), "utilization request failed", errors)
    require(
        Counter(row["sweep"] for row in summary) == {"queue-depth detector": 9, "KV-cache detector": 14},
        "utilization sweep coverage changed",
        errors,
    )
    gates = (
        "proof_checks_passed", "metric_preflight_passed", "prometheus_preflight_passed",
        "prometheus_postflight_passed", "route_count_matched", "headers_passed", "cache_disabled",
    )
    require(all(is_true(row[field]) for row in evidence for field in gates), "a utilization evidence gate failed", errors)
    require(
        sum(is_true(row["flow_control_engaged"]) for row in evidence) == 20
        and all(not is_true(row["direct_model_bypass_detected"]) for row in evidence),
        "utilization engagement or routing boundary changed",
        errors,
    )
    require(
        all(row["prefix_cache_queries"] == "0.0" and row["prefix_cache_hits"] == "0.0" for row in evidence),
        "utilization prefix-cache counters changed",
        errors,
    )
    require(
        analysis["queue_depth_medians"]["5"]["runs"] == 3
        and analysis["queue_depth_medians"]["8"]["runs"] == 3
        and analysis["kv_pressure_medians"]["flow_control_off"]["runs"] == 3
        and analysis["kv_pressure_medians"]["threshold_0.75"]["runs"] == 3
        and analysis["kv_pressure_medians"]["threshold_0.8"]["runs"] == 3,
        "utilization matched repeats changed",
        errors,
    )
    require(
        {
            "endpoint_picker_pool_saturation_ratio", "endpoint_picker_policy_queue_requests",
            "endpoint_picker_average_kv_cache_usage_ratio", "vllm_running_requests",
            "vllm_waiting_requests", "vllm_kv_cache_usage_ratio", "vllm_preemptions_total",
        }
        <= {row["metric"] for row in metrics},
        "utilization metrics are incomplete",
        errors,
    )
    require(
        config["endpoint_picker"]["image"] == "ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0"
        and config["model_service"]["prefix_cache"] == "disabled",
        "utilization configuration changed",
        errors,
    )

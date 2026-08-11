#!/usr/bin/env python3
"""Validate the public request-and-token admission package."""

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


def validate_admission_detector_package(errors: list[str], root: Path) -> None:
    package = root / "benchmark-data" / "upstream-flow-control-v0.9.0" / "request-and-token-admission-calibration"
    required = {
        "README.md", "admission-comparison.csv", "analysis.json", "request-results.csv",
        "run-config.json", "run-evidence.csv", "summary.csv", "system-metrics.csv", "traffic-samples.csv",
    }
    found = {path.name for path in package.iterdir() if path.is_file()}
    require(required <= found, "request-and-token admission files missing", errors)

    summary = read_csv(package / "summary.csv")
    comparison = read_csv(package / "admission-comparison.csv")
    requests = read_csv(package / "request-results.csv")
    traffic = read_csv(package / "traffic-samples.csv")
    metrics = read_csv(package / "system-metrics.csv")
    evidence = read_csv(package / "run-evidence.csv")
    analysis = json.loads((package / "analysis.json").read_text())
    config = json.loads((package / "run-config.json").read_text())

    require(len(summary) == 38 and len(evidence) == 20, "admission run coverage changed", errors)
    require(len(comparison) == 9, "mixed-size comparison coverage changed", errors)
    require(len(requests) == 112165, "admission request count changed", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "admission samples are empty", errors)
    require(
        Counter(row["sweep"] for row in summary) == {"request-count admission": 11, "mixed-size admission": 27},
        "admission sweep coverage changed",
        errors,
    )
    require(
        all(row["http_status"] in {"200", "429"} for row in requests)
        and all(row["timeout"] == "false" and not row["error_class"] for row in requests),
        "admission request outcome changed",
        errors,
    )
    gates = (
        "proof_checks_passed", "metric_preflight_passed", "prometheus_preflight_passed",
        "prometheus_postflight_passed", "route_count_matched", "headers_passed", "cache_disabled",
    )
    require(all(is_true(row[field]) for row in evidence for field in gates), "an admission evidence gate failed", errors)
    require(
        all(is_true(row["flow_control_engaged"]) and not is_true(row["direct_model_bypass_detected"]) for row in evidence),
        "admission flow-control engagement or routing changed",
        errors,
    )
    require(
        all(row["prefix_cache_queries"] == "0.0" and row["prefix_cache_hits"] == "0.0" for row in evidence),
        "admission prefix-cache counters changed",
        errors,
    )
    mixed = analysis["mixed_size_medians"]
    require(
        analysis["request_count_medians"]["128"]["runs"] == 3
        and analysis["request_count_medians"]["160"]["runs"] == 3
        and mixed["request_count_128"]["total_steady_throughput_rps"] == 18.808696
        and mixed["input_token_1.2x"]["total_steady_throughput_rps"] == 21.4
        and mixed["input_plus_output_estimate"]["total_steady_throughput_rps"] == 6.782609,
        "admission decision metrics changed",
        errors,
    )
    require(all(is_true(row["quality_gates_passed"]) for row in comparison), "mixed-size quality gate failed", errors)
    require(
        {
            "endpoint_picker_inflight_requests", "endpoint_picker_inflight_tokens",
            "endpoint_picker_pool_saturation_ratio", "endpoint_picker_policy_queue_requests",
            "vllm_waiting_requests", "vllm_kv_cache_usage_ratio", "vllm_preemptions_total",
        }
        <= {row["metric"] for row in metrics},
        "admission metrics are incomplete",
        errors,
    )
    require(
        config["endpoint_picker"]["image"] == "ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0"
        and config["model_service"]["prefix_cache"] == "disabled",
        "admission configuration changed",
        errors,
    )

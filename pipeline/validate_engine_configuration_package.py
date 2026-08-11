#!/usr/bin/env python3
"""Validate the public engine-configuration evidence package."""

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


def validate_engine_configuration_package(errors: list[str], root: Path) -> None:
    package = root / "benchmark-data" / "upstream-flow-control-v0.9.0" / "engine-configuration"
    required = {
        "README.md", "analysis.json", "request-results.csv", "run-config.json",
        "run-evidence.csv", "summary.csv", "system-metrics.csv", "traffic-samples.csv",
    }
    found = {path.name for path in package.iterdir() if path.is_file()}
    require(required <= found, "engine-configuration files missing", errors)

    summary = read_csv(package / "summary.csv")
    requests = read_csv(package / "request-results.csv")
    traffic = read_csv(package / "traffic-samples.csv")
    metrics = read_csv(package / "system-metrics.csv")
    evidence = read_csv(package / "run-evidence.csv")
    analysis = json.loads((package / "analysis.json").read_text())
    config = json.loads((package / "run-config.json").read_text())

    require(len(summary) == 25 and len(evidence) == 25, "expected 25 engine calibration runs", errors)
    require(len(requests) == 179278, "engine request count changed", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "engine samples are empty", errors)
    require(all(row["http_status"] == "200" for row in requests), "engine request failed", errors)
    require(
        Counter(row["sweep"] for row in summary)
        == {"flow-control-off capacity": 9, "vLLM max sequences": 11, "vLLM batched tokens": 5},
        "engine sweep coverage changed",
        errors,
    )
    gates = (
        "proof_checks_passed", "metric_preflight_passed", "prometheus_preflight_passed",
        "prometheus_postflight_passed", "route_count_matched", "headers_passed", "cache_disabled",
    )
    require(all(is_true(row[field]) for row in evidence for field in gates), "an engine evidence gate failed", errors)
    require(
        all(not is_true(row["direct_model_bypass_detected"]) and not is_true(row["flow_control_engaged"]) for row in evidence),
        "engine calibration unexpectedly used flow control or bypassed routing",
        errors,
    )
    require(
        all(row["prefix_cache_queries"] == "0.0" and row["prefix_cache_hits"] == "0.0" for row in evidence),
        "engine prefix-cache counters changed",
        errors,
    )
    require(
        analysis["selected"] == {"max_num_sequences": 128, "max_num_batched_tokens": 8192}
        and analysis["max_sequence_medians"]["128"]["runs"] == 3
        and analysis["batched_token_results"]["8192"]["runs"] == 3,
        "engine selection changed",
        errors,
    )
    require(
        {
            "vllm_running_requests", "vllm_waiting_requests", "vllm_kv_cache_usage_ratio",
            "vllm_preemptions_total", "vllm_prompt_tokens_total", "vllm_generation_tokens_total",
        }
        <= {row["metric"] for row in metrics},
        "engine metrics are incomplete",
        errors,
    )
    require(
        config["endpoint_picker"]["image"] == "ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0"
        and config["model_service"]["prefix_cache"] == "disabled",
        "engine configuration changed",
        errors,
    )

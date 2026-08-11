#!/usr/bin/env python3
"""Validate the public long-context admission evidence package."""

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


def validate_long_context_package(errors: list[str], root: Path) -> None:
    package = (
        root
        / "benchmark-data"
        / "upstream-flow-control-v0.9.0"
        / "long-context-admission"
    )
    required = {
        "README.md",
        "analysis.json",
        "request-results.csv",
        "run-config.json",
        "run-evidence.csv",
        "summary.csv",
        "system-metrics.csv",
        "traffic-samples.csv",
    }
    found = {path.name for path in package.iterdir() if path.is_file()}
    require(required <= found, "long-context-admission files missing", errors)

    summary = read_csv(package / "summary.csv")
    requests = read_csv(package / "request-results.csv")
    traffic = read_csv(package / "traffic-samples.csv")
    metrics = read_csv(package / "system-metrics.csv")
    evidence = read_csv(package / "run-evidence.csv")
    analysis = json.loads((package / "analysis.json").read_text())
    config = json.loads((package / "run-config.json").read_text())

    require(
        Counter(row["admission_method"] for row in summary)
        == {"request-count admission": 8, "exact-token admission": 8},
        "expected eight paired seeds per long-context admission method",
        errors,
    )
    require(
        len(summary) == 16 and len(evidence) == 16,
        "expected 16 long-context runs",
        errors,
    )
    require(len(requests) == 36906, "long-context request count changed", errors)
    require(
        len(traffic) > 0 and len(metrics) > 0,
        "long-context samples are empty",
        errors,
    )
    require(
        all(row["http_status"] == "200" for row in requests),
        "long-context request failed",
        errors,
    )

    data_gates = (
        "data_quality_passed",
        "offered_schedule_passed",
        "metric_capture_passed",
        "flow_control_metrics_passed",
        "headers_passed",
        "route_evidence_passed",
        "request_shapes_passed",
        "stream_integrity_passed",
    )
    require(
        all(is_true(row[field]) for row in evidence for field in data_gates),
        "a long-context data, route, shape, or metric gate failed",
        errors,
    )
    require(
        all(
            row["prefix_cache_queries"] == "0.0"
            and row["prefix_cache_hits"] == "0.0"
            and row["vllm_preemptions"] == "0.0"
            and not is_true(row["endpoint_picker_restarted"])
            for row in evidence
        ),
        "long-context cache, preemption, or restart evidence changed",
        errors,
    )
    require(
        Counter(
            row["admission_method"]
            for row in evidence
            if is_true(row["flow_control_engaged"])
        )
        == {"exact-token admission": 8, "request-count admission": 1},
        "long-context queue activation boundary changed",
        errors,
    )

    stats = analysis["statistics"]
    require(
        stats["paired_seeds"] == 8
        and stats["request_count_mean_p95_ttft_ms"] == 353.573054
        and stats["exact_token_mean_p95_ttft_ms"] == 337.12855
        and stats["two_sided_p_value"] == 0.36745
        and stats["confidence_interval_95_ms"] == [-23.918753, 56.807762]
        and not stats["claim_advances"],
        "long-context statistical result changed",
        errors,
    )
    require(
        {
            "endpoint_picker_inflight_requests",
            "endpoint_picker_inflight_tokens",
            "endpoint_picker_pool_saturation_ratio",
            "endpoint_picker_policy_queue_requests",
            "vllm_running_requests",
            "vllm_waiting_requests",
            "vllm_kv_cache_usage_ratio",
            "vllm_preemptions_total",
        }
        <= {row["metric"] for row in metrics},
        "long-context system metrics are incomplete",
        errors,
    )

    arms = {
        row["name"]: row for row in config["endpoint_picker"]["admission_arms"]
    }
    require(
        config["topology"]
        == {
            "endpoint_picker_replicas": 1,
            "model_replicas": 2,
            "gpus_per_model_replica": 1,
        }
        and arms["request-count admission"]["max_concurrency_per_model_replica"]
        == 128
        and arms["request-count admission"]["headroom"] == 0.10
        and arms["exact-token admission"][
            "max_input_token_concurrency_per_model_replica"
        ]
        == 20000
        and arms["exact-token admission"]["headroom"] == 0.25
        and config["model_service"]["prefix_cache"] == "disabled",
        "long-context configuration changed",
        errors,
    )

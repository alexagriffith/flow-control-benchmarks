#!/usr/bin/env python3
"""Validate promoted batch-eviction packages before publication."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "benchmark-data" / "batch-eviction"
SINGLE = DATA / "single-model-replica"
TWO = DATA / "two-model-replicas"
TEXT_SUFFIXES = {".csv", ".html", ".json", ".md", ".txt"}
DENYLIST = {
    "customer name": re.compile(r"restricted-customer", re.IGNORECASE),
    "local user path": re.compile(r"/Users/|\\Users\\"),
    "local username": re.compile(r"algriffi", re.IGNORECASE),
    "private run prefix": re.compile(r"fc-prod2093", re.IGNORECASE),
    "private namespace": re.compile(r"llm-test", re.IGNORECASE),
    "internal PR identifier": re.compile(r"pr[ -]?#?2093", re.IGNORECASE),
    "AWS account identifier": re.compile(r"\b\d{12}\.dkr\.ecr\b", re.IGNORECASE),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def is_true(value: str) -> bool:
    return value.strip().lower() == "true"


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_single(errors: list[str]) -> None:
    required = {
        "README.md",
        "batch-completion-index.csv",
        "eviction-retry-correlation.csv",
        "results.html",
        "run-config.json",
        "summary.csv",
    }
    require(required <= {path.name for path in SINGLE.iterdir()}, "single-model files missing", errors)
    summary = read_csv(SINGLE / "summary.csv")
    evictions = read_csv(SINGLE / "eviction-retry-correlation.csv")
    completions = read_csv(SINGLE / "batch-completion-index.csv")
    require(len(summary) == 12, "single-model summary must contain 12 accepted runs", errors)
    require(
        all(is_true(row["data_audit_passed"]) for row in summary),
        "single-model data audit failed",
        errors,
    )
    require(len(evictions) == 38, "single-model package must contain 38 evictions", errors)
    require(
        all(
            is_true(row[field])
            for row in evictions
            for field in (
                "endpoint_picker_issue_observed",
                "envoy_retry_signal_observed",
                "vllm_abort_observed",
                "retry_http_200_observed",
            )
        ),
        "single-model eviction chain is incomplete",
        errors,
    )
    require(
        all(row["final_result_count"] == "1" for row in evictions),
        "single-model eviction produced a duplicate or missing result",
        errors,
    )
    require(len(completions) == 5376, "single-model completion index row count changed", errors)
    require(
        all(row["completion_http_status"] == "200" for row in completions),
        "single-model batch completion failed",
        errors,
    )


def validate_two(errors: list[str]) -> None:
    required = {
        "README.md",
        "analysis.json",
        "batch-completion-index.csv",
        "eviction-retry-correlation.csv",
        "realtime-requests.csv",
        "run-config.json",
        "summary.csv",
        "traffic-samples.csv",
    }
    require(required <= {path.name for path in TWO.iterdir()}, "two-model files missing", errors)
    summary = read_csv(TWO / "summary.csv")
    production = [row for row in summary if row["evidence_role"] == "production evidence"]
    follow_up = [row for row in summary if row["evidence_role"] == "separate follow-up"]
    require(len(production) == 3, "two-model package must contain three production repeats", errors)
    require(len(follow_up) == 1, "two-model package must contain one separate follow-up", errors)
    require(
        all(
            is_true(row["data_audit_passed"])
            and is_true(row["topology_audit_passed"])
            and is_true(row["flow_control_engaged"])
            for row in summary
        ),
        "two-model audit, topology, or flow-control gate failed",
        errors,
    )
    require(
        all(
            row["prefix_cache_queries"] == "0"
            and row["prefix_cache_hits"] == "0"
            and row["vllm_preemptions"] == "0"
            for row in summary
        ),
        "two-model package used prefix cache or recorded a preemption",
        errors,
    )
    require(
        all(
            is_true(row["batch_preload_gate_passed"])
            and is_true(row["batch_preload_simultaneous_model_pressure"])
            and int(row["batch_preload_required_running_requests_per_model"]) == 20
            and float(row["batch_preload_max_running_requests_model_1"]) >= 20
            and float(row["batch_preload_max_running_requests_model_2"]) >= 20
            for row in summary
        ),
        "two-model batch preload did not pressure both model replicas",
        errors,
    )
    require(
        sum(int(row["realtime_offered_requests"]) for row in production) == 7690,
        "two-model production realtime request count changed",
        errors,
    )
    require(
        sum(int(row["evicted_batch_requests"]) for row in production) == 57,
        "two-model production eviction count changed",
        errors,
    )
    require(
        all(
            0.30 <= float(row[field]) <= 0.70
            for row in summary
            for field in (
                "realtime_route_share_model_1",
                "realtime_route_share_model_2",
                "batch_route_share_model_1",
                "batch_route_share_model_2",
                "token_share_model_1",
                "token_share_model_2",
            )
        ),
        "two-model request or token distribution fell outside the 30/70 gate",
        errors,
    )

    requests = read_csv(TWO / "realtime-requests.csv")
    samples = read_csv(TWO / "traffic-samples.csv")
    evictions = read_csv(TWO / "eviction-retry-correlation.csv")
    completions = read_csv(TWO / "batch-completion-index.csv")
    require(
        len(requests) == sum(int(row["realtime_offered_requests"]) for row in summary),
        "two-model realtime request rows do not match the run summaries",
        errors,
    )
    require(len(samples) > 0, "two-model traffic samples are empty", errors)
    require(
        len(evictions) == sum(int(row["evicted_batch_requests"]) for row in summary),
        "two-model eviction rows do not match the run summaries",
        errors,
    )
    require(
        all(
            is_true(row[field])
            for row in evictions
            for field in (
                "endpoint_picker_issue_observed",
                "envoy_retry_signal_observed",
                "vllm_abort_observed",
                "retry_http_200_observed",
            )
        ),
        "two-model eviction chain is incomplete",
        errors,
    )
    require(
        all(row["final_result_count"] == "1" for row in evictions),
        "two-model eviction produced a duplicate or missing result",
        errors,
    )
    require(
        len(completions) == sum(int(row["batch_submitted_jobs"]) for row in summary),
        "two-model completion rows do not match the run summaries",
        errors,
    )
    require(
        all(
            row["completion_http_status"] == "200"
            and row["result_count"] == "1"
            and not is_true(row["duplicate_result"])
            for row in completions
        ),
        "two-model batch completion or duplicate-result check failed",
        errors,
    )

    analysis = json.loads((TWO / "analysis.json").read_text())
    config = json.loads((TWO / "run-config.json").read_text())
    require(analysis["latency"]["decision"] == "inconclusive", "latency claim is overstated", errors)
    require(
        config["topology"] == {
            "endpoint_picker_replicas": 1,
            "vllm_model_replicas": 2,
            "gpus_per_model_replica": 1,
        },
        "two-model topology changed",
        errors,
    )
    require(
        config["model_service"]["prefix_cache"] == "disabled",
        "two-model config does not declare prefix cache disabled",
        errors,
    )


def scan_sensitive_text(errors: list[str]) -> None:
    for path in DATA.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(errors="replace")
        for label, pattern in DENYLIST.items():
            if pattern.search(text):
                errors.append(f"{label} found in {path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    validate_single(errors)
    validate_two(errors)
    scan_sensitive_text(errors)
    if errors:
        print("Batch-eviction promotion validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Batch-eviction promotion validation passed.")
    print("- Single-model package: 12 runs, 38 correlated evictions, 5,376 batch completions")
    print("- Two-model package: 3 production repeats, 1 follow-up, 57 production + 24 follow-up correlated evictions")
    print("- Public-content scan: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

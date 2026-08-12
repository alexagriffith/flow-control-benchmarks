#!/usr/bin/env python3
"""Validate stable-upstream benchmark packages before publication."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from validate_long_context_package import validate_long_context_package
from validate_engine_configuration_package import validate_engine_configuration_package
from validate_utilization_detector_package import validate_utilization_detector_package
from validate_admission_detector_package import validate_admission_detector_package
from validate_production_scenarios_package import validate_production_scenarios_package
from validate_upstream_report import validate_upstream_report


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "benchmark-data" / "upstream-flow-control-v0.9.0"
SCALING = DATA / "multi-replica-scaling"
STABILITY = DATA / "long-stability"
BATCH = DATA / "batch-interference"
MIXED = DATA / "mixed-production-workload"
SHAPES = DATA / "selected-workload-shapes"
PREFIX_CACHE = DATA / "prefix-cache-routing"
TEXT_SUFFIXES = {".csv", ".html", ".json", ".md", ".txt", ".yaml", ".yml"}
DENYLIST = {
    "customer name": re.compile(r"restricted-customer", re.IGNORECASE),
    "forbidden customer identifier": re.compile(
        r"ca" + r"pital[ _-]*o" + r"ne|ca" + r"pitalo" + r"ne",
        re.IGNORECASE,
    ),
    "local user path": re.compile(r"/Users/|\\Users\\"),
    "local username": re.compile(r"algriffi", re.IGNORECASE),
    "private namespace": re.compile(r"llm-test", re.IGNORECASE),
    "private run prefix": re.compile(r"fc-(?:upstream|scale|prod)", re.IGNORECASE),
    "internal PR identifier": re.compile(r"pr[ -]?#?2093", re.IGNORECASE),
    "private IP address": re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "AWS account identifier": re.compile(r"\b\d{12}\.dkr\.ecr\b", re.IGNORECASE),
    "internal Red Hat hostname": re.compile(r"\.corp\.redhat\.com", re.IGNORECASE),
    "internal Quay path": re.compile(r"quay\.io/rh-aiservices", re.IGNORECASE),
    "cluster UUID": re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    ),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def is_true(value: str) -> bool:
    return value.strip().lower() == "true"


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_business_question_answers(errors: list[str]) -> None:
    """Require every capability README to answer its question immediately."""
    readmes = sorted(DATA.rglob("README.md"))
    checked = 0
    for readme in readmes:
        text = readme.read_text(errors="replace")
        if "## Business question" not in text:
            continue
        checked += 1
        section = text.split("## Business question", 1)[1].split("\n## ", 1)[0]
        match = re.search(
            r"\n\n(?P<question>.+?\?)\n\n\*\*Answer\.\*\*\s+(?P<answer>.+?)(?:\n\n|$)",
            section,
            flags=re.DOTALL,
        )
        require(
            match is not None,
            f"business question must be followed immediately by **Answer.** in {readme.relative_to(ROOT)}",
            errors,
        )
        if match is None:
            continue
        answer = " ".join(match.group("answer").split())
        sentences = [part for part in re.split(r"(?<=[.!?])\s+", answer) if part]
        require(
            len(sentences) == 1 and len(answer) <= 300,
            f"business answer must be one concise sentence in {readme.relative_to(ROOT)}",
            errors,
        )
    require(checked == 16, "business-question package inventory changed", errors)


def nearest_rank_percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1)]


def request_percentile(
    rows: list[dict[str, str]],
    run_name: str,
    tenant: str,
    quantile: float,
    start_s: float | None = None,
    end_s: float | None = None,
) -> float | None:
    values = []
    for row in rows:
        if row["run_name"] != run_name or row["tenant"] != tenant:
            continue
        if row["http_status"] != "200" or not row["ttft_ms"]:
            continue
        arrival = float(row["planned_arrival_seconds"])
        if start_s is not None and arrival < start_s:
            continue
        if end_s is not None and arrival >= end_s:
            continue
        values.append(float(row["ttft_ms"]))
    return nearest_rank_percentile(values, quantile)


def require_request_percentile(
    rows: list[dict[str, str]],
    run_name: str,
    tenant: str,
    quantile: float,
    expected: str,
    label: str,
    errors: list[str],
    start_s: float | None = None,
    end_s: float | None = None,
) -> None:
    observed = request_percentile(
        rows, run_name, tenant, quantile, start_s=start_s, end_s=end_s,
    )
    require(
        observed is not None and abs(observed - float(expected)) < 0.001,
        f"request rows do not reproduce {label} for {run_name}",
        errors,
    )


def validate_scaling(errors: list[str]) -> None:
    required = {
        "README.md",
        "analysis.json",
        "request-results.csv",
        "run-config.json",
        "run-evidence.csv",
        "scenario.json",
        "summary.csv",
        "system-metrics.csv",
        "traffic-samples.csv",
    }
    found = {path.name for path in SCALING.iterdir() if path.is_file()}
    require(required <= found, "model-pool-scaling files missing", errors)

    summary = read_csv(SCALING / "summary.csv")
    requests = read_csv(SCALING / "request-results.csv")
    traffic = read_csv(SCALING / "traffic-samples.csv")
    metrics = read_csv(SCALING / "system-metrics.csv")
    evidence = read_csv(SCALING / "run-evidence.csv")
    analysis = json.loads((SCALING / "analysis.json").read_text())
    config = json.loads((SCALING / "run-config.json").read_text())
    scenario_config = json.loads((SCALING / "scenario.json").read_text())

    expected_runs = {1: 3, 2: 3, 4: 3}
    runs_by_topology = Counter(int(row["model_replicas"]) for row in summary)
    require(runs_by_topology == expected_runs, "expected three repeats per topology", errors)
    require(len(summary) == 9 and len(evidence) == 9, "expected nine accepted runs", errors)
    require(len(requests) == 24462, "request row count changed", errors)
    require(len(traffic) > 0, "traffic samples are empty", errors)
    require(len(metrics) > 0, "system metrics are empty", errors)

    request_counts = Counter(row["run_name"] for row in requests)
    for row in summary:
        require(
            request_counts[row["run_name"]] == int(row["offered_requests"]),
            f"request rows do not match summary for {row['run_name']}",
            errors,
        )
        require_request_percentile(
            requests, row["run_name"], "realtime-chat", 0.95,
            row["premium_burst_p95_ttft_ms"], "premium burst p95 TTFT", errors,
            start_s=100, end_s=190,
        )
        require_request_percentile(
            requests, row["run_name"], "realtime-chat", 0.99,
            row["premium_burst_p99_ttft_ms"], "premium burst p99 TTFT", errors,
            start_s=100, end_s=190,
        )

    required_gates = (
        "data_quality_passed",
        "proof_checks_passed",
        "offered_schedule_passed",
        "metric_capture_passed",
        "flow_control_metrics_passed",
        "headers_passed",
        "route_evidence_passed",
        "request_shapes_passed",
        "stream_integrity_passed",
        "flow_control_engaged",
    )
    require(
        all(is_true(row[field]) for row in evidence for field in required_gates),
        "a data, route, metric, schedule, or flow-control gate failed",
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
        "cache, preemption, or Endpoint Picker restart evidence changed",
        errors,
    )

    non_200_by_topology = defaultdict(int)
    for row in summary:
        non_200_by_topology[int(row["model_replicas"])] += int(
            row["non_200_requests"]
        )
    require(
        dict(non_200_by_topology) == {1: 5, 2: 1, 4: 0},
        "the documented HTTP 429 boundary changed",
        errors,
    )

    required_metrics = {
        "endpoint_picker_inflight_requests",
        "endpoint_picker_inflight_tokens",
        "endpoint_picker_pool_saturation_ratio",
        "endpoint_picker_policy_queue_requests",
        "vllm_running_requests",
        "vllm_waiting_requests",
        "vllm_kv_cache_usage_ratio",
        "vllm_preemptions_total",
        "vllm_prefix_cache_queries_total",
        "vllm_prefix_cache_hits_total",
    }
    metric_names = {row["metric"] for row in metrics}
    require(required_metrics <= metric_names, "required system metrics are missing", errors)
    metric_models = defaultdict(set)
    for row in metrics:
        if row["model_replica"]:
            metric_models[row["run_name"]].add(row["model_replica"])
    for row in summary:
        require(
            len(metric_models[row["run_name"]]) == int(row["model_replicas"]),
            f"per-model metrics incomplete for {row['run_name']}",
            errors,
        )

    require(analysis["decision"]["data_publishable"], "analysis is not publishable", errors)
    require(
        not analysis["decision"]["scale_out_pass"],
        "strict scale-out boundary must remain failed",
        errors,
    )
    require(
        config["topology"] == {
            "endpoint_picker_replicas": 1,
            "model_replicas_tested": [1, 2, 4],
            "gpus_per_model_replica": 1,
        },
        "model-pool topology changed",
        errors,
    )
    executable_scenario_names = [
        scenario["name"] for scenario in scenario_config["scenarios"]
    ]
    require(
        config["traffic"] == {
            "scenario_file": "scenario.json",
            "description": (
                "Cache-off long-context interference with offered load scaled "
                "per model replica."
            ),
            "scenario_names": executable_scenario_names,
        },
        "run-config traffic names do not match executable scenario names",
        errors,
    )
    require(
        config["endpoint_picker"]["version"] == "llm-d Endpoint Picker v0.9.0"
        and config["endpoint_picker"]["detector"] == "token-concurrency"
        and config["endpoint_picker"]["max_token_concurrency"] == 20000
        and config["endpoint_picker"]["headroom"] == 0.25,
        "Endpoint Picker configuration changed",
        errors,
    )
    require(
        config["model_service"]["max_num_seqs"] == 128
        and config["model_service"]["prefix_cache"] == "disabled",
        "vLLM configuration changed",
        errors,
    )


def validate_stability(errors: list[str]) -> None:
    required = {
        "README.md",
        "analysis.json",
        "request-results.csv",
        "run-config.json",
        "run-evidence.csv",
        "summary.csv",
        "system-metrics.csv",
        "traffic-samples.csv",
        "window-summary.csv",
    }
    found = {path.name for path in STABILITY.iterdir() if path.is_file()}
    require(required <= found, "long-stability files missing", errors)

    summary = read_csv(STABILITY / "summary.csv")
    windows = read_csv(STABILITY / "window-summary.csv")
    requests = read_csv(STABILITY / "request-results.csv")
    traffic = read_csv(STABILITY / "traffic-samples.csv")
    metrics = read_csv(STABILITY / "system-metrics.csv")
    evidence = read_csv(STABILITY / "run-evidence.csv")
    analysis = json.loads((STABILITY / "analysis.json").read_text())
    config = json.loads((STABILITY / "run-config.json").read_text())

    require(len(summary) == 4, "expected four workload summaries", errors)
    require(len(requests) == 14889, "long-stability request row count changed", errors)
    require(len(evidence) == 1, "expected one long-stability evidence row", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "stability samples are empty", errors)
    require(
        all(row["http_status"] == "200" for row in requests),
        "long-stability request failed",
        errors,
    )
    for row in summary:
        require_request_percentile(
            requests, row["run_name"], row["tenant"], 0.95,
            row["p95_ttft_ms"], f"{row['tenant']} overall p95 TTFT", errors,
        )
        require_request_percentile(
            requests, row["run_name"], row["tenant"], 0.99,
            row["p99_ttft_ms"], f"{row['tenant']} overall p99 TTFT", errors,
        )
    require(
        sum(int(row["offered_requests"]) for row in summary) == 14889
        and all(row["non_200_requests"] == "0" for row in summary),
        "long-stability summaries do not match request evidence",
        errors,
    )

    required_gates = (
        "data_quality_passed",
        "proof_checks_passed",
        "offered_schedule_passed",
        "metric_capture_passed",
        "flow_control_metrics_passed",
        "headers_passed",
        "route_evidence_passed",
        "request_shapes_passed",
        "stream_integrity_passed",
        "flow_control_engaged",
    )
    require(
        all(is_true(evidence[0][field]) for field in required_gates),
        "a long-stability evidence gate failed",
        errors,
    )
    require(
        evidence[0]["prefix_cache_queries"] == "0.0"
        and evidence[0]["prefix_cache_hits"] == "0.0"
        and evidence[0]["vllm_preemptions"] == "0.0"
        and not is_true(evidence[0]["endpoint_picker_restarted"]),
        "long-stability cache, preemption, or restart evidence changed",
        errors,
    )

    premium_windows = {
        row["window"]: round(float(row["p95_ttft_ms"]), 3)
        for row in windows
        if row["tenant"] == "realtime-chat"
    }
    expected_premium = {
        "baseline": 299.295,
        "surge-1": 1760.256,
        "recovery-1": 127.183,
        "surge-2": 1227.273,
        "recovery-2": 279.2,
        "final": 289.845,
    }
    require(
        premium_windows == expected_premium,
        "premium stability-window TTFT values changed",
        errors,
    )
    require(
        analysis["data_publishable"]
        and analysis["stability_pass"]
        and analysis["queue_drained"]
        and analysis["flow_control_engaged_in_both_surges"],
        "long-stability decision changed",
        errors,
    )
    require(
        analysis["requests"] == 14889
        and analysis["http_statuses"] == {"200": 14889}
        and analysis["preemptions"] == 0.0
        and not analysis["epp_restarted"],
        "long-stability result totals changed",
        errors,
    )

    required_metrics = {
        "endpoint_picker_inflight_requests",
        "endpoint_picker_pool_saturation_ratio",
        "endpoint_picker_policy_queue_requests",
        "vllm_running_requests",
        "vllm_waiting_requests",
        "vllm_kv_cache_usage_ratio",
        "vllm_preemptions_total",
    }
    require(
        required_metrics <= {row["metric"] for row in metrics},
        "long-stability system metrics are incomplete",
        errors,
    )
    require(
        config["topology"] == {
            "endpoint_picker_replicas": 1,
            "model_replicas": 1,
            "gpus_per_model_replica": 1,
        }
        and config["endpoint_picker"]["detector"] == "request-concurrency"
        and config["endpoint_picker"]["max_concurrency"] == 128
        and config["endpoint_picker"]["headroom"] == 0.10
        and config["model_service"]["prefix_cache"] == "disabled",
        "long-stability configuration changed",
        errors,
    )


def validate_batch_interference(errors: list[str]) -> None:
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
    found = {path.name for path in BATCH.iterdir() if path.is_file()}
    require(required <= found, "batch-interference files missing", errors)

    summary = read_csv(BATCH / "summary.csv")
    requests = read_csv(BATCH / "request-results.csv")
    traffic = read_csv(BATCH / "traffic-samples.csv")
    metrics = read_csv(BATCH / "system-metrics.csv")
    evidence = read_csv(BATCH / "run-evidence.csv")
    analysis = json.loads((BATCH / "analysis.json").read_text())
    config = json.loads((BATCH / "run-config.json").read_text())

    arms = Counter(row["arm"] for row in summary)
    require(
        arms == {
            "realtime only": 3,
            "realtime with batch already running": 3,
        },
        "expected three repeats in each batch-interference arm",
        errors,
    )
    require(len(requests) == 3600, "batch-interference request count changed", errors)
    require(len(evidence) == 6, "expected six batch-interference evidence rows", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "batch samples are empty", errors)
    require(
        all(row["http_status"] == "200" for row in requests),
        "batch-interference request failed",
        errors,
    )
    require(
        sum(int(row["http_200_requests"]) for row in summary) == 3600
        and all(row["http_429_requests"] == "0" for row in summary),
        "batch-interference summaries do not match requests",
        errors,
    )
    for row in summary:
        require_request_percentile(
            requests, row["run_name"], "realtime-chat", 0.95,
            row["realtime_surge_p95_ttft_ms"], "realtime surge p95 TTFT", errors,
            start_s=80, end_s=160,
        )
        require_request_percentile(
            requests, row["run_name"], "realtime-chat", 0.99,
            row["realtime_surge_p99_ttft_ms"], "realtime surge p99 TTFT", errors,
            start_s=80, end_s=160,
        )
    request_counts = Counter(row["run_name"] for row in requests)
    require(
        all(
            request_counts[row["run_name"]]
            == int(row["http_200_requests"]) + int(row["http_429_requests"])
            for row in summary
        ),
        "batch-interference request rows do not match each run summary",
        errors,
    )

    base_gates = (
        "data_quality_passed",
        "proof_checks_passed",
        "offered_schedule_passed",
        "metric_capture_passed",
        "flow_control_metrics_passed",
        "headers_passed",
        "route_evidence_passed",
        "request_shapes_passed",
        "stream_integrity_passed",
    )
    require(
        all(is_true(row[field]) for row in evidence for field in base_gates),
        "a batch-interference evidence gate failed",
        errors,
    )
    engagement = {row["run_name"]: is_true(row["flow_control_engaged"]) for row in evidence}
    require(
        all(
            not engagement[row["run_name"]]
            if row["arm"] == "realtime only"
            else engagement[row["run_name"]]
            for row in summary
        ),
        "batch-interference flow-control engagement boundary changed",
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
        "batch-interference cache, preemption, or restart evidence changed",
        errors,
    )

    require(
        analysis["run_count"] == 6
        and analysis["all_data_quality_valid"]
        and analysis["comparison"]["realtime_p95_ttft_increase_ms"] == 15245.064
        and analysis["comparison"]["realtime_p95_ttft_factor"] == 115.285,
        "batch-interference comparison changed",
        errors,
    )
    require(
        analysis["by_arm"]["realtime only"]["realtime_surge_p95_ttft_ms"][
            "median"
        ]
        == 133.395
        and analysis["by_arm"]["realtime with batch already running"][
            "realtime_surge_p95_ttft_ms"
        ]["median"]
        == 15378.459,
        "batch-interference median TTFT changed",
        errors,
    )
    required_metrics = {
        "endpoint_picker_pool_saturation_ratio",
        "endpoint_picker_policy_queue_requests",
        "vllm_running_requests",
        "vllm_waiting_requests",
        "vllm_kv_cache_usage_ratio",
        "vllm_preemptions_total",
        "vllm_prefix_cache_queries_total",
        "vllm_prefix_cache_hits_total",
    }
    require(
        required_metrics <= {row["metric"] for row in metrics},
        "batch-interference system metrics are incomplete",
        errors,
    )
    require(
        config["topology"] == {
            "endpoint_picker_replicas": 1,
            "model_replicas": 1,
            "gpus_per_model_replica": 1,
        }
        and config["hardware"]["gpu_per_model_replica"] == "NVIDIA H100"
        and config["model_service"]["model"] == "GPT-OSS 20B"
        and config["model_service"]["max_num_seqs"] == 128
        and config["endpoint_picker"]["version"]
        == "llm-d Endpoint Picker v0.9.0"
        and config["endpoint_picker"]["detector"] == "request-concurrency"
        and config["endpoint_picker"]["max_concurrency"] == 128
        and config["endpoint_picker"]["headroom"] == 0.10
        and config["endpoint_picker"]["reserved_capacity"] == "not configured"
        and config["endpoint_picker"]["batch_eviction"] == "not configured",
        "batch-interference configuration changed",
        errors,
    )


def validate_mixed_production_workload(errors: list[str]) -> None:
    required = {
        "README.md",
        "analysis.json",
        "request-results.csv",
        "run-config.json",
        "run-evidence.csv",
        "summary.csv",
        "system-metrics.csv",
        "traffic-samples.csv",
        "window-summary.csv",
    }
    found = {path.name for path in MIXED.iterdir() if path.is_file()}
    require(required <= found, "mixed-production files missing", errors)

    summary = read_csv(MIXED / "summary.csv")
    requests = read_csv(MIXED / "request-results.csv")
    traffic = read_csv(MIXED / "traffic-samples.csv")
    metrics = read_csv(MIXED / "system-metrics.csv")
    evidence = read_csv(MIXED / "run-evidence.csv")
    analysis = json.loads((MIXED / "analysis.json").read_text())
    config = json.loads((MIXED / "run-config.json").read_text())

    methods = Counter(row["admission_method"] for row in summary)
    require(
        methods == {"request-count admission": 3, "input-token admission": 3},
        "expected three repeats per mixed-workload admission method",
        errors,
    )
    require(len(summary) == 6 and len(evidence) == 6, "expected six mixed runs", errors)
    require(len(requests) == 9132, "mixed-workload request count changed", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "mixed-workload samples are empty", errors)
    require(
        all(row["http_status"] == "200" for row in requests),
        "mixed-workload request failed",
        errors,
    )
    request_counts = Counter(row["run_name"] for row in requests)
    require(
        all(
            request_counts[row["run_name"]] == int(row["http_200_requests"])
            and row["non_200_requests"] == "0"
            and is_true(row["detector_activated"])
            for row in summary
        ),
        "mixed-workload summaries do not match request evidence",
        errors,
    )
    mixed_tenants = {
        "realtime-chat": ("realtime_surge_p95_ttft_ms", "realtime_surge_p99_ttft_ms"),
        "agentic": ("agentic_surge_p95_ttft_ms", None),
        "standard-long-context": ("standard_long_context_surge_p95_ttft_ms", None),
        "batch-long-context": ("batch_surge_p95_ttft_ms", None),
    }
    for row in summary:
        for tenant, (p95_field, p99_field) in mixed_tenants.items():
            require_request_percentile(
                requests, row["run_name"], tenant, 0.95, row[p95_field],
                f"{tenant} surge p95 TTFT", errors, start_s=80, end_s=135,
            )
            if p99_field:
                require_request_percentile(
                    requests, row["run_name"], tenant, 0.99, row[p99_field],
                    f"{tenant} surge p99 TTFT", errors, start_s=80, end_s=135,
                )

    required_gates = (
        "data_quality_passed",
        "proof_checks_passed",
        "offered_schedule_passed",
        "metric_capture_passed",
        "flow_control_metrics_passed",
        "headers_passed",
        "route_evidence_passed",
        "request_shapes_passed",
        "stream_integrity_passed",
        "flow_control_engaged",
    )
    require(
        all(is_true(row[field]) for row in evidence for field in required_gates),
        "a mixed-workload evidence gate failed",
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
        "mixed-workload cache, preemption, or restart evidence changed",
        errors,
    )

    request_method = analysis["by_admission_method"]["request-count admission"]
    token_method = analysis["by_admission_method"]["input-token admission"]
    require(
        request_method["premium_surge_p95_ttft_ms"]["median"] == 1994.498
        and token_method["premium_surge_p95_ttft_ms"]["median"] == 2913.568
        and request_method["batch_surge_p95_ttft_ms"]["median"] == 8653.621
        and token_method["batch_surge_p95_ttft_ms"]["median"] == 2831.684,
        "mixed-workload latency comparison changed",
        errors,
    )
    require(
        request_method["max_vllm_waiting"]["median"] == 16.0
        and token_method["max_vllm_waiting"]["median"] == 43.0,
        "mixed-workload vLLM waiting comparison changed",
        errors,
    )

    required_metrics = {
        "endpoint_picker_inflight_requests",
        "endpoint_picker_inflight_tokens",
        "endpoint_picker_pool_saturation_ratio",
        "endpoint_picker_policy_queue_requests",
        "vllm_running_requests",
        "vllm_waiting_requests",
        "vllm_kv_cache_usage_ratio",
        "vllm_preemptions_total",
        "vllm_prefix_cache_queries_total",
        "vllm_prefix_cache_hits_total",
    }
    require(
        required_metrics <= {row["metric"] for row in metrics},
        "mixed-workload system metrics are incomplete",
        errors,
    )
    arms = config["endpoint_picker"]["admission_arms"]
    require(
        config["topology"]
        == {
            "endpoint_picker_replicas": 1,
            "model_replicas": 1,
            "gpus_per_model_replica": 1,
        }
        and config["endpoint_picker"]["version"] == "llm-d Endpoint Picker v0.9.0"
        and arms[0]["max_concurrency"] == 128
        and arms[0]["headroom"] == 0.10
        and arms[1]["max_token_concurrency"] == 75000
        and not arms[1]["estimated_output_tokens_included"]
        and config["model_service"]["prefix_cache"] == "disabled"
        and config["execution"]["matched_deterministic_trace"],
        "mixed-workload configuration changed",
        errors,
    )


def validate_selected_workload_shapes(errors: list[str]) -> None:
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
    found = {path.name for path in SHAPES.iterdir() if path.is_file()}
    require(required <= found, "selected-workload-shapes files missing", errors)

    summary = read_csv(SHAPES / "summary.csv")
    requests = read_csv(SHAPES / "request-results.csv")
    traffic = read_csv(SHAPES / "traffic-samples.csv")
    metrics = read_csv(SHAPES / "system-metrics.csv")
    evidence = read_csv(SHAPES / "run-evidence.csv")
    analysis = json.loads((SHAPES / "analysis.json").read_text())
    config = json.loads((SHAPES / "run-config.json").read_text())

    shapes = Counter(row["workload_shape"] for row in summary)
    require(
        shapes == {"chat short output": 3, "agentic longer output": 3},
        "expected three repeats per selected workload shape",
        errors,
    )
    require(len(summary) == 6 and len(evidence) == 6, "expected six selected-workload-shapes runs", errors)
    require(len(requests) == 11403, "selected-workload-shapes request row count changed", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "selected-workload-shapes samples are empty", errors)
    require(
        all(row["http_status"] == "200" for row in requests),
        "selected-workload-shapes request failed",
        errors,
    )
    request_counts = Counter(row["run_name"] for row in requests)
    require(
        all(
            request_counts[row["run_name"]] == int(row["offered_requests"])
            and row["non_200_requests"] == "0"
            for row in summary
        ),
        "selected-workload-shapes summaries do not match request evidence",
        errors,
    )
    shape_tenants = {
        "chat short output": "chat-short-output",
        "agentic longer output": "agentic-longer-output",
    }
    for row in summary:
        tenant = shape_tenants[row["workload_shape"]]
        require_request_percentile(
            requests, row["run_name"], tenant, 0.95, row["surge_p95_ttft_ms"],
            f"{tenant} surge p95 TTFT", errors, start_s=80, end_s=135,
        )
        require_request_percentile(
            requests, row["run_name"], tenant, 0.99, row["surge_p99_ttft_ms"],
            f"{tenant} surge p99 TTFT", errors, start_s=80, end_s=135,
        )

    required_gates = (
        "data_quality_passed",
        "proof_checks_passed",
        "offered_schedule_passed",
        "metric_capture_passed",
        "flow_control_metrics_passed",
        "headers_passed",
        "route_evidence_passed",
        "request_shapes_passed",
        "stream_integrity_passed",
        "flow_control_engaged",
    )
    require(
        all(is_true(row[field]) for row in evidence for field in required_gates),
        "a selected-workload-shapes evidence gate failed",
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
        "selected-workload-shapes cache, preemption, or restart evidence changed",
        errors,
    )

    chat_shape = analysis["by_workload_shape"]["chat short output"]
    agentic_shape = analysis["by_workload_shape"]["agentic longer output"]
    require(
        round(chat_shape["median"]["surge_p95_ttft_ms"], 3) == 420.205
        and round(agentic_shape["median"]["surge_p95_ttft_ms"], 3) == 1352.324,
        "selected-workload-shapes median surge p95 TTFT changed",
        errors,
    )
    require(
        chat_shape["median"]["max_epp_queue"] == 11.0
        and agentic_shape["median"]["max_epp_queue"] == 11.0,
        "selected-workload-shapes median peak EPP queue changed",
        errors,
    )

    required_metrics = {
        "endpoint_picker_pool_saturation_ratio",
        "endpoint_picker_policy_queue_requests",
        "vllm_running_requests",
        "vllm_waiting_requests",
        "vllm_kv_cache_usage_ratio",
        "vllm_preemptions_total",
        "vllm_prefix_cache_queries_total",
        "vllm_prefix_cache_hits_total",
    }
    require(
        required_metrics <= {row["metric"] for row in metrics},
        "selected-workload-shapes system metrics are incomplete",
        errors,
    )
    require(
        config["topology"] == {
            "endpoint_picker_replicas": 1,
            "model_replicas": 1,
            "gpus_per_model_replica": 1,
        }
        and config["endpoint_picker"]["version"] == "llm-d Endpoint Picker v0.9.0"
        and config["endpoint_picker"]["detector"] == "request-concurrency"
        and config["endpoint_picker"]["max_concurrency"] == 128
        and config["endpoint_picker"]["headroom"] == 0.10
        and config["model_service"]["prefix_cache"] == "disabled",
        "selected-workload-shapes configuration changed",
        errors,
    )


def validate_prefix_cache_routing(errors: list[str]) -> None:
    required = {
        "README.md",
        "analysis.json",
        "request-results.csv",
        "run-config.json",
        "run-evidence.csv",
        "routing-balance.csv",
        "summary.csv",
        "system-metrics.csv",
        "traffic-samples.csv",
    }
    found = {path.name for path in PREFIX_CACHE.iterdir() if path.is_file()}
    require(required <= found, "prefix-cache-routing files missing", errors)

    summary = read_csv(PREFIX_CACHE / "summary.csv")
    requests = read_csv(PREFIX_CACHE / "request-results.csv")
    traffic = read_csv(PREFIX_CACHE / "traffic-samples.csv")
    metrics = read_csv(PREFIX_CACHE / "system-metrics.csv")
    evidence = read_csv(PREFIX_CACHE / "run-evidence.csv")
    routing = read_csv(PREFIX_CACHE / "routing-balance.csv")
    analysis = json.loads((PREFIX_CACHE / "analysis.json").read_text())
    config = json.loads((PREFIX_CACHE / "run-config.json").read_text())

    arms = Counter(row["arm"] for row in summary)
    require(
        arms == {"random routing": 3, "prefix-aware routing": 3},
        "expected three repeats per prefix-cache routing arm",
        errors,
    )
    require(len(summary) == 6 and len(evidence) == 6, "expected six prefix-cache-routing runs", errors)
    require(len(routing) == 6, "expected six prefix-cache routing-balance rows", errors)
    require(len(requests) == 44880, "prefix-cache-routing request row count changed", errors)
    require(len(traffic) > 0 and len(metrics) > 0, "prefix-cache-routing samples are empty", errors)

    require(
        all(row["http_status"] in {"200", "429"} for row in requests),
        "prefix-cache-routing request had unexpected status",
        errors,
    )
    require(
        sum(1 for row in requests if row["http_status"] == "200") == 44857,
        "prefix-cache-routing HTTP 200 count changed",
        errors,
    )
    require(
        sum(1 for row in requests if row["http_status"] == "429") == 23,
        "prefix-cache-routing HTTP 429 count changed",
        errors,
    )
    routing_tenants = {
        "realtime-chat": ("realtime_surge_p95_ttft_ms", "realtime_surge_p99_ttft_ms"),
        "agentic": ("agentic_surge_p95_ttft_ms", "agentic_surge_p99_ttft_ms"),
        "standard-long-context": (
            "standard_long_context_surge_p95_ttft_ms",
            "standard_long_context_surge_p99_ttft_ms",
        ),
        "batch-long-context": ("batch_surge_p95_ttft_ms", "batch_surge_p99_ttft_ms"),
    }
    for row in summary:
        require_request_percentile(
            requests, row["run_name"], "realtime-chat", 0.95,
            row["realtime_overall_p95_ttft_ms"], "realtime overall p95 TTFT", errors,
        )
        for tenant, (p95_field, p99_field) in routing_tenants.items():
            require_request_percentile(
                requests, row["run_name"], tenant, 0.95, row[p95_field],
                f"{tenant} surge p95 TTFT", errors, start_s=80, end_s=135,
            )
            require_request_percentile(
                requests, row["run_name"], tenant, 0.99, row[p99_field],
                f"{tenant} surge p99 TTFT", errors, start_s=80, end_s=135,
            )

    base_gates = (
        "data_quality_passed",
        "proof_checks_passed",
        "offered_schedule_passed",
        "metric_capture_passed",
        "flow_control_metrics_passed",
        "headers_passed",
        "route_evidence_passed",
        "request_shapes_passed",
        "stream_integrity_passed",
        "flow_control_engaged",
    )
    require(
        all(is_true(row[field]) for row in evidence for field in base_gates),
        "a prefix-cache-routing evidence gate failed",
        errors,
    )
    require(
        all(row["prefix_cache_declared"] == "on" for row in evidence),
        "prefix cache was not declared on in all runs",
        errors,
    )
    require(
        all(float(row["prefix_cache_queries"]) > 0 for row in evidence),
        "prefix cache queries were zero in at least one run",
        errors,
    )
    require(
        all(
            row["vllm_preemptions"] == "0.0"
            and not is_true(row["endpoint_picker_restarted"])
            for row in evidence
        ),
        "prefix-cache-routing preemption or restart evidence changed",
        errors,
    )

    random_arm = analysis["by_arm"]["random routing"]
    prefix_arm = analysis["by_arm"]["prefix-aware routing"]
    require(
        random_arm["realtime_surge_p95_ttft_ms"]["median"] == 1327.211
        and prefix_arm["realtime_surge_p95_ttft_ms"]["median"] == 1124.916,
        "prefix-cache-routing realtime median p95 TTFT changed",
        errors,
    )
    require(
        random_arm["standard_long_context_surge_p95_ttft_ms"]["median"] == 11252.391
        and prefix_arm["standard_long_context_surge_p95_ttft_ms"]["median"] == 12871.56,
        "prefix-cache-routing standard-long-context median p95 TTFT changed",
        errors,
    )
    require(
        analysis["comparison"]["realtime_p95_ttft_difference_ms"] == -202.295
        and analysis["comparison"]["realtime_p95_ttft_difference_percent"] == -15.242,
        "prefix-cache-routing comparison delta changed",
        errors,
    )
    require(
        analysis["data_publishable"],
        "prefix-cache-routing analysis is not publishable",
        errors,
    )
    require(
        analysis["decision"] == "Keep random routing as the control configuration.",
        "prefix-cache-routing decision changed",
        errors,
    )
    overall = {
        (row["workload"], row["metric"]): row
        for row in analysis["overall_latency_comparison"]
    }
    require(
        overall[("realtime-chat", "p95 TTFT")]
        == {
            "workload": "realtime-chat",
            "metric": "p95 TTFT",
            "random_routing_median_ms": 1194.7,
            "prefix_aware_routing_median_ms": 935.7,
            "change_percent": -21.7,
        },
        "prefix-cache-routing overall realtime comparison changed",
        errors,
    )
    route_medians = {
        arm: round(statistics.median(float(row["route_imbalance_percent"]) for row in routing if row["arm"] == arm), 1)
        for arm in {row["arm"] for row in routing}
    }
    require(
        route_medians == {"random routing": 0.9, "prefix-aware routing": 19.1},
        "prefix-cache-routing route balance changed",
        errors,
    )

    required_metrics = {
        "endpoint_picker_pool_saturation_ratio",
        "endpoint_picker_policy_queue_requests",
        "vllm_running_requests",
        "vllm_waiting_requests",
        "vllm_kv_cache_usage_ratio",
        "vllm_preemptions_total",
        "vllm_prefix_cache_queries_total",
        "vllm_prefix_cache_hits_total",
        "endpoint_picker_prefix_index_entries",
        "endpoint_picker_resident_memory_bytes",
    }
    require(
        required_metrics <= {row["metric"] for row in metrics},
        "prefix-cache-routing system metrics are incomplete",
        errors,
    )
    routing_arms = {arm["name"] for arm in config["endpoint_picker"]["routing_arms"]}
    require(
        routing_arms == {"random routing", "prefix-aware routing"},
        "prefix-cache-routing arm names changed",
        errors,
    )
    require(
        config["topology"] == {
            "endpoint_picker_replicas": 1,
            "model_replicas": 2,
            "gpus_per_model_replica": 1,
        }
        and config["endpoint_picker"]["version"] == "llm-d Endpoint Picker v0.9.0"
        and config["endpoint_picker"]["detector"] == "request-concurrency"
        and config["endpoint_picker"]["max_concurrency"] == 128
        and config["endpoint_picker"]["headroom"] == 0.10
        and config["model_service"]["prefix_cache"] == "enabled",
        "prefix-cache-routing configuration changed",
        errors,
    )


def scan_sensitive_text(errors: list[str]) -> None:
    paths = list(DATA.rglob("*")) + list((ROOT / "pipeline").rglob("*"))
    for path in paths:
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if path.parent == ROOT / "pipeline" and path.name.startswith("validate_"):
            continue
        text = path.read_text(errors="replace")
        for label, pattern in DENYLIST.items():
            if pattern.search(text):
                errors.append(f"{label} found in {path.relative_to(ROOT)}")


def validate_package_visuals(errors: list[str]) -> None:
    package_paths = {
        DATA / "batch-interference",
        DATA / "engine-configuration",
        DATA / "long-context-admission",
        DATA / "long-stability",
        DATA / "mixed-production-workload",
        DATA / "multi-replica-scaling",
        DATA / "prefix-cache-routing",
        DATA / "production-scenarios",
        DATA / "production-scenarios" / "batch-isolation",
        DATA / "production-scenarios" / "consolidation",
        DATA / "production-scenarios" / "priority-tiers",
        DATA / "production-scenarios" / "same-priority-fairness",
        DATA / "request-and-token-admission-calibration",
        DATA / "request-concurrency-priority-tuning",
        DATA / "selected-workload-shapes",
        DATA / "utilization-detector-calibration",
    }
    for package in sorted(package_paths):
        for filename in ("architecture.svg", "results.svg", "tested-config.yaml"):
            require(
                (package / filename).is_file(),
                f"package visual missing: {(package / filename).relative_to(ROOT)}",
                errors,
            )
        readme = package / "README.md"
        readme_text = readme.read_text(errors="replace") if readme.is_file() else ""
        require(
            "<!-- generated:package-visuals -->" in readme_text
            and "](architecture.svg)" in readme_text
            and "](results.svg)" in readme_text
            and "](tested-config.yaml)" in readme_text,
            f"package visual links missing from {readme.relative_to(ROOT)}",
            errors,
        )
    for package in (
        DATA / "production-scenarios" / "priority-tiers",
        DATA / "production-scenarios" / "same-priority-fairness",
    ):
        for filename in ("replay.mp4", "replay-poster.png"):
            require(
                (package / filename).is_file(),
                f"recorded replay missing: {(package / filename).relative_to(ROOT)}",
                errors,
            )


def validate_reproduction_contract(errors: list[str]) -> None:
    runner = ROOT / "pipeline" / "benchmark.py"
    expected_hash = "3811ec26c46bf3a26fa643698ec54bf569bb4bc99c3ea22ca18f805cb077b8e0"
    require(runner.is_file(), "canonical benchmark runner is missing", errors)
    if runner.is_file():
        actual_hash = hashlib.sha256(runner.read_bytes()).hexdigest()
        require(actual_hash == expected_hash, "canonical benchmark runner changed", errors)

    required_pipeline_files = {
        "capture_run_context.py",
        "guidellm_k8s.py",
        "guidellm_scenario_to_run.py",
        "guidellm_trace.py",
        "generate_package_configs.py",
        "generate_package_visuals.py",
        "metrics_capture.py",
        "metrics_preflight.py",
        "package_visual_specs.py",
        "prometheus_validate.py",
        "run-in-cluster.sh",
        "run_guidellm_scenario.py",
        "sync_guidellm_status.py",
    }
    for name in required_pipeline_files:
        require((ROOT / "pipeline" / name).is_file(), f"pipeline helper missing: {name}", errors)
    require(
        (ROOT / "pipeline" / "kubernetes" / "benchmark-runner.yaml").is_file(),
        "benchmark runner manifest is missing",
        errors,
    )

    readmes = [DATA / "README.md"]
    readmes.extend(path for path in DATA.glob("*/README.md"))
    readmes.extend(path for path in (DATA / "production-scenarios").glob("*/README.md"))
    for readme in readmes:
        text = readme.read_text(errors="replace")
        require(
            "## Reproduce" in text or "## Runner and reproduction" in text,
            f"reproduction section missing from {readme.relative_to(ROOT)}",
            errors,
        )
        bash_blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)
        require(
            not any(re.search(r"<[^>]+>", block) for block in bash_blocks),
            f"shell placeholder remains in {readme.relative_to(ROOT)}",
            errors,
        )

    for config_path in DATA.rglob("run-config.json"):
        config = json.loads(config_path.read_text())
        runner_config = config.get("runner", {})
        if runner_config.get("historical_runner"):
            require(
                runner_config.get("source") == "pipeline/archive/benchmark-2026-07.py"
                and runner_config.get("published_sha256")
                == "0d5296e42922f6691db267598afe3ec5ac4f653b96a9935f5fefbf8e4c2dbce3"
                and runner_config.get("executed_sha256")
                == "cfc227b88faa9041ba88c2e352c27fae328a97d1fce6ec0b9c6859c32d136998"
                and runner_config.get("sanitized_deployment_defaults_only") is True,
                f"historical runner provenance changed in {config_path.relative_to(ROOT)}",
                errors,
            )
            continue
        require(
            runner_config.get("sha256") == expected_hash,
            f"runner provenance missing from {config_path.relative_to(ROOT)}",
            errors,
        )
        scenario_name = runner_config.get("scenario_file")
        if scenario_name:
            require(
                (config_path.parent / scenario_name).is_file(),
                f"configured scenario file missing from {config_path.relative_to(ROOT)}",
                errors,
            )

    required_scenarios = {
        DATA / "batch-interference" / "scenarios.json",
        DATA / "long-context-admission" / "scenario.json",
        DATA / "long-stability" / "scenario.json",
        DATA / "mixed-production-workload" / "scenario.json",
        DATA / "multi-replica-scaling" / "scenario.json",
        DATA / "prefix-cache-routing" / "scenario.json",
        DATA / "production-scenarios" / "scenarios.json",
        DATA / "production-scenarios" / "batch-isolation" / "scenario.json",
        DATA / "production-scenarios" / "consolidation" / "scenario.json",
        DATA / "production-scenarios" / "priority-tiers" / "scenario.json",
        DATA / "production-scenarios" / "same-priority-fairness" / "scenario.json",
        DATA / "request-and-token-admission-calibration" / "request-concurrency-scenario.json",
        DATA / "request-and-token-admission-calibration" / "token-admission-scenario.json",
        DATA / "selected-workload-shapes" / "scenarios.json",
        DATA / "utilization-detector-calibration" / "queue-depth-scenario.json",
        DATA / "utilization-detector-calibration" / "kv-threshold-scenario.json",
    }
    for scenario in required_scenarios:
        require(scenario.is_file(), f"scenario definition missing: {scenario.relative_to(ROOT)}", errors)


def main() -> int:
    errors: list[str] = []
    validate_selected_workload_shapes(errors)
    validate_scaling(errors)
    validate_stability(errors)
    validate_batch_interference(errors)
    validate_mixed_production_workload(errors)
    validate_prefix_cache_routing(errors)
    validate_long_context_package(errors, ROOT)
    validate_engine_configuration_package(errors, ROOT)
    validate_utilization_detector_package(errors, ROOT)
    validate_admission_detector_package(errors, ROOT)
    validate_production_scenarios_package(errors, ROOT)
    validate_business_question_answers(errors)
    validate_upstream_report(errors, ROOT)
    validate_package_visuals(errors)
    validate_reproduction_contract(errors)
    scan_sensitive_text(errors)
    if errors:
        print("Stable-upstream promotion validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Stable-upstream promotion validation passed.")
    print("- Selected workload shapes: 6 runs, 11,403 request rows")
    print("- Model pool scaling: 9 runs, 24,462 request rows")
    print("- Long stability: 1 run, 14,889 request rows")
    print("- Batch interference: 6 runs, 3,600 request rows")
    print("- Mixed production workload: 6 runs, 9,132 request rows")
    print("- Prefix-cache routing: 6 runs, 44,880 request rows")
    print("- Long-context admission: 16 runs, 36,906 request rows")
    print("- Engine configuration: 25 runs, 179,278 request rows")
    print("- Utilization detector calibration: 23 runs, 78,674 request rows")
    print("- Request and token admission: 20 runs, 112,165 request rows")
    print("- Production scenarios: 23 runs, 194,923 request rows")
    print("- Grouped visual report: current")
    print("- Per-package architecture and result visuals: complete")
    print("- Traffic, queue, vLLM, cache, and evidence-gate data: complete")
    print("- Runner provenance and reproduction commands: complete")
    print("- Public-content scan: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

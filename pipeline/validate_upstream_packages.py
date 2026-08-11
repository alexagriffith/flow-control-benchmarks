#!/usr/bin/env python3
"""Validate stable-upstream benchmark packages before publication."""

from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path


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


def validate_scaling(errors: list[str]) -> None:
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
    found = {path.name for path in SCALING.iterdir() if path.is_file()}
    require(required <= found, "model-pool-scaling files missing", errors)

    summary = read_csv(SCALING / "summary.csv")
    requests = read_csv(SCALING / "request-results.csv")
    traffic = read_csv(SCALING / "traffic-samples.csv")
    metrics = read_csv(SCALING / "system-metrics.csv")
    evidence = read_csv(SCALING / "run-evidence.csv")
    analysis = json.loads((SCALING / "analysis.json").read_text())
    config = json.loads((SCALING / "run-config.json").read_text())

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
    for path in DATA.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(errors="replace")
        for label, pattern in DENYLIST.items():
            if pattern.search(text):
                errors.append(f"{label} found in {path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    validate_selected_workload_shapes(errors)
    validate_scaling(errors)
    validate_stability(errors)
    validate_batch_interference(errors)
    validate_mixed_production_workload(errors)
    validate_prefix_cache_routing(errors)
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
    print("- Traffic, queue, vLLM, cache, and evidence-gate data: complete")
    print("- Public-content scan: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

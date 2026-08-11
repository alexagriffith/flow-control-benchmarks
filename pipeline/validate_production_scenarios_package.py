#!/usr/bin/env python3
"""Validate the public production-scenarios package."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


SCENARIO_FOLDERS = {
    "priority tiers": "priority-tiers",
    "batch isolation": "batch-isolation",
    "consolidation": "consolidation",
    "same-priority fairness": "same-priority-fairness",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def read_scenario_csvs(package: Path, name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for scenario, folder in SCENARIO_FOLDERS.items():
        scenario_rows = read_csv(package / folder / name)
        if any(row["scenario"] != scenario for row in scenario_rows):
            raise ValueError(f"{folder}/{name} contains another scenario")
        rows.extend(scenario_rows)
    return rows


def is_true(value: str) -> bool:
    return value.strip().lower() == "true"


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_production_scenarios_package(errors: list[str], root: Path) -> None:
    package = root / "benchmark-data" / "upstream-flow-control-v0.9.0" / "production-scenarios"
    required = {"README.md", "analysis.json", "run-config.json"}
    found = {path.name for path in package.iterdir() if path.is_file()}
    require(required <= found, "production-scenario files missing", errors)
    require(not any(package.glob("*.csv")), "production CSVs must be separated by scenario", errors)

    scenario_required = {
        "README.md", "analysis.json", "request-results.csv", "run-config.json",
        "run-evidence.csv", "summary.csv", "system-metrics.csv", "traffic-samples.csv",
        "window-summary.csv",
    }
    for scenario, folder in SCENARIO_FOLDERS.items():
        scenario_path = package / folder
        scenario_found = {path.name for path in scenario_path.iterdir() if path.is_file()}
        require(scenario_required <= scenario_found, f"{scenario} files missing", errors)

    evidence = read_scenario_csvs(package, "run-evidence.csv")
    summary = read_scenario_csvs(package, "summary.csv")
    requests = read_scenario_csvs(package, "request-results.csv")
    traffic = read_scenario_csvs(package, "traffic-samples.csv")
    metrics = read_scenario_csvs(package, "system-metrics.csv")
    windows = read_scenario_csvs(package, "window-summary.csv")
    analysis = json.loads((package / "analysis.json").read_text())
    config = json.loads((package / "run-config.json").read_text())
    readme = (package / "README.md").read_text()

    require(len(evidence) == 23 and len(summary) == 72, "production run coverage changed", errors)
    require(len(requests) == 194923, "production request count changed", errors)
    require(len(traffic) == 17352 and len(metrics) == 210834, "production samples changed", errors)
    require(len(windows) == 216, "production window coverage changed", errors)
    require(
        Counter(row["scenario"] for row in evidence)
        == {"priority tiers": 3, "batch isolation": 4, "consolidation": 9, "same-priority fairness": 7},
        "production scenario coverage changed",
        errors,
    )
    require(
        Counter(row["evidence_role"] for row in evidence)
        == {"selected three-repeat result": 6, "single-run calibration": 2,
            "matched three-repeat comparison": 15},
        "production evidence roles changed",
        errors,
    )
    require(
        all(
            row["http_status"] == "200"
            and row["timeout"] == "false"
            and not row["error_class"]
            and not row["dropped_reason"]
            for row in requests
        ),
        "production request outcomes changed",
        errors,
    )
    gates = (
        "data_quality_passed", "proof_checks_passed", "slo_proof_passed",
        "offered_schedule_passed", "metric_capture_passed", "flow_control_metrics_passed",
        "headers_passed", "route_evidence_passed", "request_shapes_passed",
        "stream_integrity_passed", "flow_control_engaged",
    )
    require(all(is_true(row[field]) for row in evidence for field in gates), "a production proof gate failed", errors)
    require(
        all(
            row["prefix_cache_queries"] == "0.0"
            and row["prefix_cache_hits"] == "0.0"
            and row["vllm_preemptions"] == "0.0"
            for row in evidence
        ),
        "production cache or preemption evidence changed",
        errors,
    )
    require(
        all(row["http_429"] == "0" and row["timeouts"] == "0" and row["errors"] == "0" for row in summary),
        "production summary outcomes changed",
        errors,
    )

    evidence_runs = {row["run_name"] for row in evidence}
    summary_runs = {row["run_name"] for row in summary}
    request_counts = Counter(row["run_name"] for row in requests)
    summary_counts = Counter()
    for row in summary:
        summary_counts[row["run_name"]] += int(row["requests"])
    require(evidence_runs == summary_runs == set(request_counts), "production run names do not align", errors)
    require(request_counts == summary_counts, "request rows do not match run summaries", errors)

    summaries_by_run: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in summary:
        summaries_by_run[row["run_name"]].append(row)
    for row in evidence:
        run_name = row["run_name"]
        observed = json.loads(row["observed_priority_map_json"])
        observed_objectives = json.loads(row["observed_objective_headers_json"])
        expected: dict[str, list[int]] = defaultdict(list)
        for summary_row in summaries_by_run[run_name]:
            priority = int(summary_row["priority"])
            if priority not in expected[summary_row["workload"]]:
                expected[summary_row["workload"]].append(priority)
        expected = {workload: sorted(priorities) for workload, priorities in expected.items()}
        require(observed == expected, f"observed priority map changed for {run_name}", errors)
        expected_objectives = {
            "priority tiers": ["batch objective", "gold realtime objective", "realtime objective", "standard objective"],
            "batch isolation": ["batch objective", "realtime objective", "standard objective"],
            "consolidation": ["realtime objective", "standard objective"],
            "same-priority fairness": ["realtime objective"],
        }[row["scenario"]]
        require(observed_objectives == expected_objectives, f"observed objective headers changed for {run_name}", errors)
        require(
            int(row["route_completion_requests"]) == request_counts[run_name]
            and int(row["route_request_ids_present"]) == request_counts[run_name]
            and not is_true(row["direct_model_bypass_detected"]),
            f"route evidence changed for {run_name}",
            errors,
        )

    selected = analysis["selected_configuration_results"]
    matched = analysis["matched_detector_comparisons"]
    require(
        round(selected["priority tiers"]["platinum realtime"]["median_p95_ttft_ms"]) == 404
        and round(selected["batch isolation"]["realtime"]["median_p95_ttft_ms"]) == 442
        and round(selected["consolidation"]["realtime tenant A"]["median_p95_ttft_ms"]) == 509
        and round(selected["same-priority fairness"]["realtime peer B"]["median_p95_ttft_ms"]) == 527,
        "selected production metrics changed",
        errors,
    )
    require(
        round(matched["consolidation"]["queue depth 2"]["realtime tenant A"]["median_p95_ttft_ms"]) == 4711
        and round(matched["consolidation"]["queue depth 5"]["realtime tenant A"]["median_p95_ttft_ms"]) == 5117
        and round(matched["same-priority fairness"]["queue depth 2"]["realtime peer B"]["median_p95_ttft_ms"]) == 5023,
        "matched detector metrics changed",
        errors,
    )
    require(
        analysis["calibration_only"]["batch queue depth 2"]["realtime"]["runs"] == 1
        and analysis["calibration_only"]["same-priority queue depth 5"]["realtime peer B"]["runs"] == 1,
        "single-run calibration coverage changed",
        errors,
    )
    inventory = {
        (row["scenario"], row["detector"], row["evidence_role"], int(row["repeat"]))
        for row in analysis["retained_run_inventory"]
    }
    evidence_inventory = {
        (row["scenario"], row["detector"], row["evidence_role"], int(row["repeat"]))
        for row in evidence
    }
    require(inventory == evidence_inventory, "analysis does not classify every retained run", errors)
    require(
        analysis["excluded_controls"]
        == [
            "Priority-tier queue-depth controls failed route-count proof.",
            "The queue-depth-5 batch control failed its response-outcome gate.",
        ],
        "production exclusions changed",
        errors,
    )

    required_metrics = {
        "endpoint_picker_policy_queue_requests", "endpoint_picker_pool_saturation_ratio",
        "endpoint_picker_queued_requests_total", "endpoint_picker_policy_queue_seconds_total",
        "vllm_running_requests", "vllm_waiting_requests", "vllm_kv_cache_usage_ratio",
        "vllm_preemptions_total", "vllm_prefix_cache_queries_total",
        "vllm_prefix_cache_hits_total",
    }
    metric_units = {
        "endpoint_picker_average_backend_queue_requests": "requests",
        "endpoint_picker_average_backend_running_requests": "requests",
        "endpoint_picker_average_kv_cache_usage_ratio": "ratio",
        "endpoint_picker_policy_queue_requests": "requests",
        "endpoint_picker_policy_queue_seconds_total": "seconds",
        "endpoint_picker_pool_saturation_ratio": "detector-normalized score",
        "endpoint_picker_queued_requests_total": "requests",
        "ready_model_replicas": "replicas",
        "vllm_generation_tokens_total": "tokens",
        "vllm_kv_cache_usage_ratio": "ratio",
        "vllm_preemptions_total": "requests",
        "vllm_prefix_cache_hits_total": "hits",
        "vllm_prefix_cache_queries_total": "queries",
        "vllm_prompt_tokens_total": "tokens",
        "vllm_running_requests": "requests",
        "vllm_waiting_requests": "requests",
    }
    require(required_metrics <= {row["metric"] for row in metrics}, "production metrics are incomplete", errors)
    require(
        all(row["metric"] in metric_units and row["unit"] == metric_units[row["metric"]] for row in metrics),
        "production metric units are incomplete",
        errors,
    )
    metrics_by_run: dict[str, set[str]] = defaultdict(set)
    for row in metrics:
        metrics_by_run[row["run_name"]].add(row["metric"])
    require(
        all(required_metrics <= metrics_by_run[run_name] for run_name in evidence_runs),
        "a production run is missing required metrics",
        errors,
    )
    require(
        all(float(row["peak_detector_saturation_score"]) >= 1.0 for row in evidence),
        "a retained run did not cross its detector saturation boundary",
        errors,
    )
    required_readme_values = {
        "Platinum 404 ms", "Gold 511 ms", "Silver 656 ms", "Batch 13,264 ms",
        "Realtime 442 ms", "Standard 515 ms", "Batch 13,077 ms",
        "Realtime tenants 509 and 556 ms", "Standard burst 25,892 ms",
        "Overloaded tenant 12,097 ms", "peers 527 and 570 ms",
        "ranges extended to 619 and 675 ms", "4,711 and 4,567 ms",
        "5,117 and 4,906 ms", "5,023 and 4,519 ms",
    }
    require(all(value in readme for value in required_readme_values), "README result values changed", errors)
    require(
        config["topology"]
        == {"endpoint_picker_replicas": 1, "model_replicas": 1, "gpus_per_model_replica": 1}
        and config["endpoint_picker"]["image"] == "ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0"
        and config["endpoint_picker"]["max_concurrency"] == 128
        and config["model_service"]["max_num_sequences"] == 128
        and config["model_service"]["max_num_batched_tokens"] == 8192
        and config["model_service"]["prefix_cache"] == "disabled"
        and config["traffic"]["arrival_mode"] == "open-loop Poisson",
        "production configuration changed",
        errors,
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors: list[str] = []
    validate_production_scenarios_package(errors, root)
    package = root / "benchmark-data" / "upstream-flow-control-v0.9.0" / "production-scenarios"
    forbidden_customer = re.compile(r"ca" + r"pital[ _-]*o" + r"ne|ca" + r"pitalo" + r"ne", re.IGNORECASE)
    for path in package.rglob("*"):
        if path.is_file() and path.suffix in {".csv", ".json", ".md"}:
            require(not forbidden_customer.search(path.read_text(errors="replace")), "forbidden customer identifier found", errors)
    if errors:
        print("Production-scenarios validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Production-scenarios validation passed: 23 runs, 194,923 requests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

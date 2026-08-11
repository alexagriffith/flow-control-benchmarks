#!/usr/bin/env python3
"""Convert GuideLLM JSON into replayable benchmark run directories."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any


CLIENT_FIELDS = [
    "run_id", "scenario", "request_id", "tenant", "priority", "objective", "status",
    "planned_arrival_s", "actual_send_s", "start_s", "ttft_s", "latency_s",
    "stream_chunks", "prompt_tokens", "completion_tokens", "tpot_s", "timeout",
    "error_class", "retry_count", "token_count_source", "dropped_reason",
    "retry_after", "response_detail",
]


HTTP_STATUS_ERROR = re.compile(
    r"HTTPStatusError\(.*?['\"](?P<status>[1-5]\d{2}) [^'\"]+['\"] for url",
    re.DOTALL,
)


def explicit_http_status(error: str) -> str | None:
    """Return a status only when GuideLLM preserved an explicit HTTP response code."""
    match = HTTP_STATUS_ERROR.search(error)
    return match.group("status") if match else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def linear_percentile(values: list[float], q: float) -> float | None:
    """Return the Type-7 linear percentile used by NumPy and R."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def strategy_concurrency(benchmark: dict[str, Any]) -> int:
    strategy = (benchmark.get("config") or {}).get("strategy") or {}
    return int(strategy.get("max_concurrency") or strategy.get("worker_count") or 1)


def request_timing(
    benchmark: dict[str, Any], request: dict[str, Any]
) -> tuple[float | None, float]:
    """Return planned and actual request starts relative to the benchmark epoch."""
    schedule_starts = [
        float(timings["targeted_start"]) - float(settings["relative_timestamp"])
        for bucket in ("successful", "errored", "incomplete")
        for item in ((benchmark.get("requests") or {}).get(bucket, []) or [])
        for timings in [((item.get("info") or {}).get("timings") or {})]
        for settings in [((item.get("info") or {}).get("settings") or {})]
        if timings.get("targeted_start") is not None
        and settings.get("relative_timestamp") is not None
    ]
    started = (
        min(schedule_starts) if schedule_starts else float(benchmark.get("start_time") or 0)
    )
    timings = ((request.get("info") or {}).get("timings") or {})
    targeted_start = timings.get("targeted_start")
    request_start = timings.get("request_start")
    if request_start is None:
        request_start = request.get("request_start_time")
    if request_start is None:
        request_start = started
    relative_timestamp = ((request.get("info") or {}).get("settings") or {}).get(
        "relative_timestamp"
    )
    planned = (
        float(relative_timestamp)
        if relative_timestamp is not None
        else float(targeted_start) - started if targeted_start is not None else None
    )
    return planned, float(request_start) - started


def schedule_fidelity(
    rows: list[dict[str, Any]], p99_limit_ms: float = 100.0,
    max_limit_ms: float = 500.0,
) -> dict[str, Any]:
    lags_ms = [
        max(0.0, float(row["actual_send_s"]) - float(row["planned_arrival_s"])) * 1000.0
        for row in rows
        if row["planned_arrival_s"] not in (None, "")
    ]
    p95_ms = percentile(lags_ms, 0.95)
    p99_ms = linear_percentile(lags_ms, 0.99)
    max_ms = max(lags_ms) if lags_ms else None
    return {
        "valid": len(lags_ms) == len(rows) and bool(rows)
        and p99_ms is not None and p99_ms <= p99_limit_ms
        and max_ms is not None and max_ms <= max_limit_ms,
        "requests": len(rows),
        "requests_with_planned_time": len(lags_ms),
        "send_lag_p95_ms": p95_ms,
        "send_lag_p99_ms": p99_ms,
        "send_lag_percentile_method": "type_7_linear",
        "schedule_gate_version": 2,
        "send_lag_max_ms": max_ms,
        "send_lag_over_100ms": sum(value > 100.0 for value in lags_ms),
        "send_lag_p99_limit_ms": p99_limit_ms,
        "send_lag_max_limit_ms": max_limit_ms,
    }


def request_rows(
    benchmark: dict[str, Any], run_id: str, scenario: str, tenant: str,
    priority: int, objective: str,
) -> list[dict[str, Any]]:
    started = float(benchmark.get("start_time") or 0)
    rows: list[dict[str, Any]] = []
    for bucket, status, error_class in (
        ("successful", "200", None),
        ("errored", "unknown", "guidellm_error"),
        ("incomplete", "unknown", "guidellm_incomplete"),
    ):
        for request in (benchmark.get("requests") or {}).get(bucket, []) or []:
            planned_arrival_s, actual_send_s = request_timing(benchmark, request)
            info = request.get("info") or {}
            timings = info.get("timings") or {}
            latency = request.get("request_latency")
            if latency is None:
                request_end = timings.get("resolve_end") or request.get("request_end_time")
                request_start = timings.get("request_start") or request.get("request_start_time")
                latency = (
                    max(0.0, float(request_end) - float(request_start))
                    if request_end is not None and request_start is not None else 0
                )
            response_detail = info.get("error") or ""
            observed_status = (
                status if bucket == "successful"
                else explicit_http_status(response_detail) or status
            )
            rows.append({
                "run_id": run_id,
                "scenario": scenario,
                "request_id": request.get("request_id") or "",
                "tenant": tenant,
                "priority": priority,
                "objective": objective,
                "status": observed_status,
                "planned_arrival_s": planned_arrival_s,
                "actual_send_s": actual_send_s,
                "start_s": actual_send_s,
                "ttft_s": float(request["time_to_first_token_ms"]) / 1000.0
                if request.get("time_to_first_token_ms") is not None else "",
                "latency_s": latency,
                "stream_chunks": timings.get("token_iterations") or 0,
                "prompt_tokens": request.get("prompt_tokens") or "",
                "completion_tokens": request.get("output_tokens") or "",
                "tpot_s": float(request["time_per_output_token_ms"]) / 1000.0
                if request.get("time_per_output_token_ms") is not None else "",
                "timeout": False,
                "error_class": error_class or "",
                "retry_count": 0,
                "token_count_source": "guidellm_response",
                "dropped_reason": "",
                "retry_after": "",
                "response_detail": response_detail,
            })
    return sorted(rows, key=lambda row: float(row["start_s"]))


def traffic_rows(
    rows: list[dict[str, Any]], duration_s: float, run_id: str, scenario: str,
    tenant: str, priority: int, objective: str, concurrency: int,
) -> list[dict[str, Any]]:
    output = []
    for second in range(max(1, math.ceil(duration_s)) + 1):
        issued = sum(float(row["start_s"]) <= second for row in rows)
        completed = sum(
            float(row["start_s"]) + float(row["latency_s"]) <= second for row in rows
        )
        output.append({
            "run_id": run_id,
            "scenario": scenario,
            "elapsed_s": second,
            "tenant": tenant,
            "arrival_process": "poisson",
            "target_rps": "",
            "target_concurrency": concurrency,
            "priority": priority,
            "objective": objective,
            "issued_requests": issued,
            "completed_requests": completed,
            "outstanding_requests": max(0, issued - completed),
            "send_delay_s": "",
            "safety_ceiling_state": "not_applicable",
        })
    return output


def summary_document(
    rows: list[dict[str, Any]], run_id: str, scenario: str, duration_s: float,
    tenant: str, priority: int, objective: str,
) -> dict[str, Any]:
    successful = [row for row in rows if row["status"] == "200"]
    ttft = [float(row["ttft_s"]) for row in successful if row["ttft_s"] != ""]
    tpot = [float(row["tpot_s"]) for row in successful if row["tpot_s"] != ""]
    latency = [float(row["latency_s"]) for row in successful]
    client_summary = [{
        "run_id": run_id,
        "scenario": scenario,
        "arrival_mode": "poisson",
        "tenant": tenant,
        "priority": priority,
        "objective": objective,
        "duration_s": duration_s,
        "total": len(rows),
        "http_200": len(successful),
        "http_429": sum(row["status"] == "429" for row in rows),
        "errors": len(rows) - len(successful),
        "throughput_rps": len(successful) / duration_s if duration_s > 0 else 0,
        "ttft_p50_s": percentile(ttft, 0.50),
        "ttft_p95_s": percentile(ttft, 0.95),
        "ttft_p99_s": percentile(ttft, 0.99),
        "latency_p95_s": percentile(latency, 0.95),
        "tpot_p95_s": percentile(tpot, 0.95),
    }]
    return {
        "run_id": run_id,
        "scenario": scenario,
        "duration_s": duration_s,
        "arrival_mode": "poisson",
        "source": "guidellm",
        "client_summary": client_summary,
    }


def split_metrics(
    source_path: Path | None, output_path: Path, start_epoch_s: float,
    end_epoch_s: float, run_id: str, scenario: str,
) -> int:
    if source_path is None:
        return 0
    with source_path.open(newline="") as source:
        reader = csv.DictReader(source)
        if "sample_epoch_s" not in (reader.fieldnames or []):
            raise ValueError("metric_samples_long.csv needs sample_epoch_s to split a GuideLLM sweep")
        selected = []
        for row in reader:
            epoch = float(row.get("sample_epoch_s") or 0)
            if start_epoch_s <= epoch <= end_epoch_s:
                row["run_id"] = run_id
                row["scenario"] = scenario
                row["elapsed_s"] = str(epoch - start_epoch_s)
                selected.append(row)
        fieldnames = reader.fieldnames or []
    with output_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)
    return len(selected)


def convert(
    raw_path: Path, output_root: Path, scenario: str, tenant: str,
    priority: int, objective: str, metrics_path: Path | None = None,
) -> list[Path]:
    raw = json.loads(raw_path.read_text())
    output_root.mkdir(parents=True, exist_ok=True)
    created = []
    for index, benchmark in enumerate(raw.get("benchmarks") or [], start=1):
        if not isinstance(benchmark, dict):
            continue
        concurrency = strategy_concurrency(benchmark)
        run_id = f"{scenario}-concurrency-{concurrency}"
        run_dir = output_root / f"{index:02d}-concurrency-{concurrency}"
        run_dir.mkdir(parents=True, exist_ok=True)
        rows = request_rows(benchmark, run_id, scenario, tenant, priority, objective)
        duration_s = float(benchmark.get("duration") or 0)
        with (run_dir / "client_samples.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CLIENT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        traffic = traffic_rows(
            rows, duration_s, run_id, scenario, tenant, priority, objective, concurrency
        )
        with (run_dir / "traffic_samples.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(traffic[0]))
            writer.writeheader()
            writer.writerows(traffic)
        summary = summary_document(
            rows, run_id, scenario, duration_s, tenant, priority, objective
        )
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        (run_dir / "benchmark_config.json").write_text(json.dumps({
            "source": "guidellm",
            "raw_file": str(raw_path),
            "strategy_concurrency": concurrency,
            "tenant": tenant,
            "priority": priority,
            "objective": objective,
        }, indent=2))
        metric_count = split_metrics(
            metrics_path,
            run_dir / "metric_samples_long.csv",
            float(benchmark.get("start_time") or 0),
            float(benchmark.get("end_time") or 0),
            run_id,
            scenario,
        )
        timing_evidence = schedule_fidelity(rows)
        conversion_valid = (
            bool(rows)
            and timing_evidence["valid"]
            and (metrics_path is None or metric_count > 0)
        )
        (run_dir / "conversion.json").write_text(json.dumps({
            "valid": conversion_valid,
            "requests": len(rows),
            "metric_rows": metric_count,
            "arrival_mode": "poisson",
            "schedule_fidelity": timing_evidence,
        }, indent=2))
        created.append(run_dir)
    return created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--priority", required=True, type=int)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--metrics-long", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    convert(
        args.raw, args.output_root, args.scenario, args.tenant,
        args.priority, args.objective, args.metrics_long,
    )

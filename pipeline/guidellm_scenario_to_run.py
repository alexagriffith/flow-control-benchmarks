#!/usr/bin/env python3
"""Merge synchronized per-tenant GuideLLM replays into one auditable run."""

from __future__ import annotations

import argparse
import csv
from dataclasses import fields
import hashlib
import json
import shutil
from pathlib import Path
from statistics import median
from typing import Any

from benchmark import (
    RequestSample,
    Tenant,
    header_evidence,
    metric_delta,
    summarize_samples,
    summarize_windows,
)
from guidellm_to_run import CLIENT_FIELDS, request_rows, schedule_fidelity, traffic_rows
from metrics_capture import discover_metric_names, parse_prometheus


def prefix_cache_evidence(
    prefix: dict[str, Any], metric_names: set[str], mode: str,
) -> dict[str, Any]:
    evidence = {
        "declared": mode,
        "counters_present": bool(metric_names.intersection({
            "vllm:prefix_cache_queries", "vllm:prefix_cache_queries_total",
            "vllm:prefix_cache_hits", "vllm:prefix_cache_hits_total",
        })),
        "queries_delta": prefix["queries_delta"],
        "hits_delta": prefix["hits_delta"],
    }
    if mode == "off":
        evidence["valid"] = (
            evidence["counters_present"]
            and evidence["queries_delta"] == 0
            and evidence["hits_delta"] == 0
        )
    elif mode == "on":
        evidence["valid"] = (
            evidence["counters_present"]
            and evidence["queries_delta"] > 0
            and evidence["hits_delta"] > 0
        )
    else:
        raise ValueError(f"unsupported prefix cache mode: {mode}")
    return evidence


def request_shape_evidence(
    rows: list[dict[str, Any]], tenant_specs: list[dict[str, Any]],
    relative_tolerance: float = 0.10,
) -> dict[str, Any]:
    """Prove that successful requests reached the intended prompt-token shape."""
    tenants = []
    for tenant in tenant_specs:
        tenant_id = str(tenant["fairness_id"])
        expected = int(tenant["input_tokens"])
        observed = [
            int(row["prompt_tokens"])
            for row in rows
            if row["tenant"] == tenant_id
            and row["status"] == "200"
            and row.get("prompt_tokens") not in (None, "")
        ]
        observed_median = median(observed) if observed else None
        maximum_relative_error = (
            max(abs(value - expected) / expected for value in observed)
            if observed and expected else None
        )
        tenants.append({
            "tenant": tenant_id,
            "expected_input_tokens": expected,
            "successful_samples": len(observed),
            "observed_median_prompt_tokens": observed_median,
            "observed_min_prompt_tokens": min(observed) if observed else None,
            "observed_max_prompt_tokens": max(observed) if observed else None,
            "maximum_relative_error": maximum_relative_error,
            "valid": bool(
                observed and maximum_relative_error is not None
                and maximum_relative_error <= relative_tolerance
            ),
        })
    return {
        "valid": bool(tenants and all(item["valid"] for item in tenants)),
        "relative_tolerance": relative_tolerance,
        "tenants": tenants,
    }


def flow_control_engagement_evidence(
    peak_queue_depth: float,
    peak_pool_saturation: float,
    queue_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    queued_count = sum(
        float(item.get("queue_count_delta") or 0) for item in queue_rows
    )
    queued_seconds = sum(
        float(item.get("queue_sum_s_delta") or 0) for item in queue_rows
    )
    return {
        "valid": bool(
            queued_count > 0
            and queued_seconds > 0
            and peak_pool_saturation >= 1.0
        ),
        "peak_queue_depth": peak_queue_depth,
        "peak_pool_saturation": peak_pool_saturation,
        "queued_request_count_delta": queued_count,
        "queued_seconds_delta": queued_seconds,
        "by_tenant": queue_rows,
    }


def envoy_proxy_evidence(
    pre_text: str, post_text: str, cluster_name: str = "epp",
) -> dict[str, Any]:
    def values(text: str) -> dict[str, float]:
        return {
            name: value
            for (name, labels), value in parse_prometheus(text).items()
            if dict(labels).get("envoy_cluster_name") == cluster_name
        }

    pre = values(pre_text)
    post = values(post_text)
    remaining_name = "envoy_cluster_circuit_breakers_default_remaining_rq"
    open_name = "envoy_cluster_circuit_breakers_default_rq_open"
    pending_overflow_name = "envoy_cluster_upstream_rq_pending_overflow"
    connection_overflow_name = "envoy_cluster_upstream_cx_overflow"
    required = (
        remaining_name, open_name, pending_overflow_name, connection_overflow_name,
    )
    missing = [
        name for name in required if name not in pre or name not in post
    ]
    pending_delta = (
        post[pending_overflow_name] - pre[pending_overflow_name]
        if pending_overflow_name not in missing else None
    )
    connection_delta = (
        post[connection_overflow_name] - pre[connection_overflow_name]
        if connection_overflow_name not in missing else None
    )
    valid = bool(
        not missing
        and pre[remaining_name] > 0
        and pre[open_name] == 0
        and post[open_name] == 0
        and pending_delta == 0
        and connection_delta == 0
    )
    return {
        "valid": valid,
        "cluster_name": cluster_name,
        "missing_metrics": missing,
        "configured_request_capacity_observed": pre.get(remaining_name),
        "remaining_requests_after_run": post.get(remaining_name),
        "request_breaker_open_before": pre.get(open_name),
        "request_breaker_open_after": post.get(open_name),
        "request_overflow_delta": pending_delta,
        "connection_overflow_delta": connection_delta,
    }


def runtime_metric_summary(path: Path, envoy_cluster_name: str = "epp") -> dict[str, Any]:
    """Reduce canonical one-second metrics into auditable run peaks."""
    if not path.is_file():
        return {"valid": False, "reason": "missing metric_samples_long.csv"}

    peaks: dict[str, Any] = {
        "sample_count": 0,
        "max_epp_queue": 0.0,
        "max_epp_queue_by_tenant": {},
        "max_pool_saturation": 0.0,
        "max_epp_inflight_requests": 0.0,
        "max_epp_inflight_tokens": 0.0,
        "max_vllm_running": 0.0,
        "max_vllm_waiting": 0.0,
        "max_vllm_kv_cache_usage": 0.0,
        "max_envoy_epp_active_requests": 0.0,
        "max_epp_resident_memory_bytes": 0.0,
        "max_epp_prefix_index_entries": 0.0,
    }
    epp_process_start_times: set[float] = set()
    first_preemptions: float | None = None
    last_preemptions: float | None = None
    current_elapsed: str | None = None
    current: dict[str, Any] = {}

    def flush() -> None:
        nonlocal first_preemptions, last_preemptions
        if current_elapsed is None:
            return
        peaks["sample_count"] += 1
        peaks["max_epp_queue"] = max(
            peaks["max_epp_queue"], current.get("queue_total", 0.0)
        )
        for tenant, value in current.get("queue_by_tenant", {}).items():
            peaks["max_epp_queue_by_tenant"][tenant] = max(
                peaks["max_epp_queue_by_tenant"].get(tenant, 0.0), value
            )
        for source, target in (
            ("pool_saturation", "max_pool_saturation"),
            ("epp_inflight_requests", "max_epp_inflight_requests"),
            ("epp_inflight_tokens", "max_epp_inflight_tokens"),
            ("vllm_running", "max_vllm_running"),
            ("vllm_waiting", "max_vllm_waiting"),
            ("vllm_kv", "max_vllm_kv_cache_usage"),
            ("envoy_active", "max_envoy_epp_active_requests"),
            ("epp_memory", "max_epp_resident_memory_bytes"),
            ("epp_prefix_index", "max_epp_prefix_index_entries"),
        ):
            peaks[target] = max(peaks[target], current.get(source, 0.0))
        preemptions = current.get("vllm_preemptions")
        if preemptions is not None:
            if first_preemptions is None:
                first_preemptions = preemptions
            last_preemptions = preemptions

    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            elapsed_value = row.get("elapsed_s")
            if not elapsed_value:
                continue
            elapsed = f"{float(elapsed_value):.6f}"
            if current_elapsed is not None and elapsed != current_elapsed:
                flush()
                current = {}
            current_elapsed = elapsed
            metric = row.get("metric") or ""
            generation = row.get("metric_generation") or ""
            try:
                value = float(row.get("value") or 0)
                labels = json.loads(row.get("labels_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            if generation == "canonical":
                if metric == "llm_d_epp_flow_control_queue_size":
                    tenant = str(labels.get("fairness_id") or "unknown")
                    queue = current.setdefault("queue_by_tenant", {})
                    queue[tenant] = queue.get(tenant, 0.0) + value
                    current["queue_total"] = current.get("queue_total", 0.0) + value
                elif metric == "llm_d_epp_flow_control_pool_saturation":
                    current["pool_saturation"] = max(
                        current.get("pool_saturation", 0.0), value
                    )
                elif metric == "vllm:num_requests_running":
                    current["vllm_running"] = current.get("vllm_running", 0.0) + value
                elif metric == "vllm:num_requests_waiting":
                    current["vllm_waiting"] = current.get("vllm_waiting", 0.0) + value
                elif metric == "vllm:kv_cache_usage_perc":
                    current["vllm_kv"] = max(current.get("vllm_kv", 0.0), value)
                elif metric == "vllm:num_preemptions_total":
                    current["vllm_preemptions"] = (
                        current.get("vllm_preemptions", 0.0) + value
                    )
                elif metric == "llm_d_epp_prefix_indexer_size":
                    current["epp_prefix_index"] = max(
                        current.get("epp_prefix_index", 0.0), value
                    )
            elif generation == "runtime":
                if metric == "process_resident_memory_bytes":
                    current["epp_memory"] = max(
                        current.get("epp_memory", 0.0), value
                    )
                elif metric == "process_start_time_seconds":
                    epp_process_start_times.add(value)
            elif (
                generation == "exact"
                and metric in ("epp:inflight_requests", "epp:inflight_tokens")
            ):
                current[
                    "epp_inflight_requests"
                    if metric.endswith("requests") else "epp_inflight_tokens"
                ] = value
            elif (
                generation == "proxy"
                and metric == "envoy_cluster_upstream_rq_active"
                and labels.get("envoy_cluster_name") == envoy_cluster_name
            ):
                current["envoy_active"] = current.get("envoy_active", 0.0) + value
    flush()

    peaks["vllm_preemptions_start"] = first_preemptions
    peaks["vllm_preemptions_end"] = last_preemptions
    peaks["vllm_preemptions_delta"] = (
        max(0.0, last_preemptions - first_preemptions)
        if first_preemptions is not None and last_preemptions is not None
        else None
    )
    peaks["epp_process_start_times"] = sorted(epp_process_start_times)
    peaks["epp_process_restart_detected"] = len(epp_process_start_times) > 1
    peaks["valid"] = peaks["sample_count"] > 0
    return peaks


def schedule_epoch(benchmark: dict[str, Any]) -> float:
    values = []
    for bucket in ("successful", "errored", "incomplete"):
        for request in ((benchmark.get("requests") or {}).get(bucket, []) or []):
            info = request.get("info") or {}
            targeted = (info.get("timings") or {}).get("targeted_start")
            relative = (info.get("settings") or {}).get("relative_timestamp")
            if targeted is not None and relative is not None:
                values.append(float(targeted) - float(relative))
    if not values:
        raise ValueError("GuideLLM output has no replay schedule timestamps")
    return median(values)


def planned_schedule_hash(rows: list[dict[str, Any]]) -> str:
    values = sorted(float(row["planned_arrival_s"]) for row in rows)
    encoded = ",".join(f"{value:.9f}" for value in values).encode()
    return hashlib.sha256(encoded).hexdigest()


def scenario_timing(
    epochs: dict[str, float], tenant_specs: list[dict[str, Any]],
) -> tuple[float, float, dict[str, float]]:
    """Recover the shared scenario origin from tenant-normalized GuideLLM traces."""
    first_arrivals = {
        str(tenant["fairness_id"]): float(tenant.get("first_arrival_s", 0.0))
        for tenant in tenant_specs
    }
    scenario_epochs = {
        tenant: epoch - first_arrivals[tenant]
        for tenant, epoch in epochs.items()
    }
    global_epoch = min(scenario_epochs.values())
    spread_ms = (max(scenario_epochs.values()) - global_epoch) * 1000.0
    offsets = {
        tenant: epoch - global_epoch
        for tenant, epoch in epochs.items()
    }
    return global_epoch, spread_ms, offsets


def apply_tenant_offsets(
    rows: list[dict[str, Any]], planned_offset: float, actual_offset: float,
) -> None:
    """Restore the shared timeline while keeping trace timestamps hash-stable."""
    for row in rows:
        row["planned_arrival_s"] = round(
            float(row["planned_arrival_s"]) + planned_offset, 9
        )
        row["actual_send_s"] = float(row["actual_send_s"]) + actual_offset
        row["start_s"] = float(row["start_s"]) + actual_offset


def load_benchmark(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = json.loads(path.read_text())
    benchmarks = [item for item in (raw.get("benchmarks") or []) if isinstance(item, dict)]
    if len(benchmarks) != 1:
        raise ValueError(f"{path} must contain exactly one benchmark")
    return raw, benchmarks[0]


def stream_integrity_evidence(
    rows: list[dict[str, Any]], tenant_specs: list[dict[str, Any]], raw_dir: Path,
) -> dict[str, Any]:
    expected = {
        str(item["fairness_id"]): int(item["output_tokens"])
        for item in tenant_specs
    }
    tenant_checks = []
    for tenant, expected_tokens in expected.items():
        selected = [row for row in rows if row["tenant"] == tenant]
        successful = [row for row in selected if row["status"] == "200"]
        exact = sum(
            row["completion_tokens"] not in (None, "")
            and int(row["completion_tokens"]) == expected_tokens
            for row in successful
        )
        tenant_checks.append({
            "tenant": tenant,
            "requests": len(selected),
            "completed_requests": len(successful),
            "non_200_requests": len(selected) - len(successful),
            "expected_completion_tokens": expected_tokens,
            "exact_completion_tokens": exact,
            "valid": bool(selected) and exact == len(successful),
        })
    marker = "GUIDELLM_RECOVERED_MULTILINE_SSE"
    recovered = sum(
        path.read_text(errors="replace").count(marker)
        for path in raw_dir.glob("*.log")
    )
    return {
        "valid": bool(tenant_checks) and all(item["valid"] for item in tenant_checks),
        "recovered_multiline_sse_events": recovered,
        "tenants": tenant_checks,
    }


def convert_scenario(
    manifest_path: Path, raw_dir: Path, metrics_dir: Path, run_dir: Path,
    run_id: str | None = None,
    prefix_cache_mode: str = "off",
    envoy_cluster_name: str = "epp",
) -> Path:
    manifest = json.loads(manifest_path.read_text())
    scenario = str(manifest["scenario"])
    run_id = run_id or scenario
    duration_s = float(manifest["duration_s"])
    tenant_specs = manifest.get("tenants") or []
    if not tenant_specs:
        raise ValueError("trace manifest has no tenants")

    loaded: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]] = []
    for tenant in tenant_specs:
        raw_path = raw_dir / f"{tenant['fairness_id']}.json"
        raw, benchmark = load_benchmark(raw_path)
        loaded.append((tenant, raw, benchmark, raw_path))

    epochs = {tenant["fairness_id"]: schedule_epoch(benchmark) for tenant, _raw, benchmark, _path in loaded}
    global_epoch, epoch_spread_ms, schedule_offsets = scenario_timing(
        epochs, tenant_specs
    )

    all_rows: list[dict[str, Any]] = []
    schedule_checks = []
    for tenant, _raw, benchmark, _path in loaded:
        tenant_id = str(tenant["fairness_id"])
        rows = request_rows(
            benchmark, run_id, scenario, tenant_id, int(tenant["priority"]),
            str(tenant["objective"]),
        )
        apply_tenant_offsets(
            rows,
            float(tenant.get("first_arrival_s", 0.0)),
            schedule_offsets[tenant_id],
        )
        observed_hash = planned_schedule_hash(rows)
        timing = schedule_fidelity(rows)
        strategy = (benchmark.get("config") or {}).get("strategy") or {}
        time_scale = float(strategy.get("time_scale") or 1.0)
        check = {
            "tenant": tenant_id,
            "planned_requests": int(tenant["planned_requests"]),
            "dispatched_requests": len(rows),
            "all_dispatched": len(rows) == int(tenant["planned_requests"]),
            "expected_schedule_sha256": tenant["schedule_sha256"],
            "observed_schedule_sha256": observed_hash,
            "schedule_hash_matches": observed_hash == tenant["schedule_sha256"],
            "time_scale": time_scale,
            "time_scale_valid": time_scale == 1.0,
            **timing,
        }
        check["valid"] = all((
            check["all_dispatched"], check["schedule_hash_matches"],
            check["time_scale_valid"], timing["valid"],
        ))
        schedule_checks.append(check)
        all_rows.extend(rows)
    all_rows.sort(key=lambda row: float(row["start_s"]))
    stream_integrity = stream_integrity_evidence(all_rows, tenant_specs, raw_dir)
    shape_evidence = request_shape_evidence(all_rows, tenant_specs)

    run_dir.mkdir(parents=True, exist_ok=True)
    raw_out = run_dir / "guidellm-raw"
    raw_out.mkdir(exist_ok=True)
    for _tenant, _raw, _benchmark, path in loaded:
        shutil.copy2(path, raw_out / path.name)
    for path in raw_dir.glob("*.log"):
        shutil.copy2(path, raw_out / path.name)
    shutil.copy2(manifest_path, run_dir / "trace_manifest.json")
    for name in (
        "metric_samples_long.csv", "metric_preflight.json", "metric_capture_health.json",
        "pre_epp.prom", "pre_vllm.prom", "pre_envoy.prom",
        "post_epp.prom", "post_vllm.prom", "post_envoy.prom",
    ):
        source = metrics_dir / name
        if source.exists():
            shutil.copy2(source, run_dir / name)

    with (run_dir / "client_samples.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLIENT_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    traffic = []
    for tenant in tenant_specs:
        tenant_rows = [row for row in all_rows if row["tenant"] == tenant["fairness_id"]]
        traffic.extend(traffic_rows(
            tenant_rows, duration_s, run_id, scenario, str(tenant["fairness_id"]),
            int(tenant["priority"]), str(tenant["objective"]), 10240,
        ))
    with (run_dir / "traffic_samples.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(traffic[0]))
        writer.writeheader()
        writer.writerows(traffic)

    samples = [RequestSample(**{
        field.name: (None if row[field.name] == "" and field.name in {
            "planned_arrival_s", "ttft_s", "prompt_tokens", "completion_tokens",
            "tpot_s", "error_class", "token_count_source", "dropped_reason",
            "retry_after", "response_detail",
        } else row[field.name])
        for field in fields(RequestSample)
    }) for row in all_rows]
    summary_rows = summarize_samples(run_id, scenario, samples, duration_s, 0, "poisson")
    window_rows = summarize_windows(
        run_id, scenario, samples, manifest.get("analysis_windows") or [], "poisson"
    )

    metric_health_path = metrics_dir / "metric_capture_health.json"
    metric_health = json.loads(metric_health_path.read_text()) if metric_health_path.exists() else {
        "valid": False, "reason": "missing metric_capture_health.json"
    }
    pre_epp = (metrics_dir / "pre_epp.prom").read_text() if (metrics_dir / "pre_epp.prom").exists() else ""
    pre_vllm = (metrics_dir / "pre_vllm.prom").read_text() if (metrics_dir / "pre_vllm.prom").exists() else ""
    post_epp = (metrics_dir / "post_epp.prom").read_text() if (metrics_dir / "post_epp.prom").exists() else ""
    post_vllm = (metrics_dir / "post_vllm.prom").read_text() if (metrics_dir / "post_vllm.prom").exists() else ""
    pre_envoy = (metrics_dir / "pre_envoy.prom").read_text() if (metrics_dir / "pre_envoy.prom").exists() else ""
    post_envoy = (metrics_dir / "post_envoy.prom").read_text() if (metrics_dir / "post_envoy.prom").exists() else ""
    tenant_models = [Tenant(
        fairness_id=str(item["fairness_id"]), priority=int(item["priority"]),
        phases=item.get("phases") or [], objective=str(item["objective"]),
        input_tokens=int(item["input_tokens"]), output_tokens=int(item["output_tokens"]),
    ) for item in tenant_specs]
    deltas = metric_delta(pre_epp + "\n" + pre_vllm, post_epp + "\n" + post_vllm, {
        item.fairness_id for item in tenant_models
    })
    runtime_metrics = runtime_metric_summary(
        metrics_dir / "metric_samples_long.csv", envoy_cluster_name
    )
    proxy_evidence = envoy_proxy_evidence(
        pre_envoy, post_envoy, envoy_cluster_name
    )
    deltas["envoy_proxy"] = proxy_evidence
    headers = header_evidence(post_epp, tenant_models)
    prefix = deltas["vllm"]["prefix_cache"]
    metric_names = discover_metric_names(pre_vllm + "\n" + post_vllm)
    cache_evidence = prefix_cache_evidence(prefix, metric_names, prefix_cache_mode)
    schedule_valid = epoch_spread_ms <= 50.0 and all(item["valid"] for item in schedule_checks)
    active_metrics = metric_health.get("active_flow_metrics") or {"valid": False}
    data_quality_checks_valid = all((
        schedule_valid, bool(metric_health.get("valid")), headers["valid"],
        bool(active_metrics.get("valid")), cache_evidence["valid"],
        stream_integrity["valid"], proxy_evidence["valid"], shape_evidence["valid"],
    ))
    peak_queue_depth = float(runtime_metrics.get("max_epp_queue") or 0)
    peak_pool_saturation = float(runtime_metrics.get("max_pool_saturation") or 0)
    engagement = flow_control_engagement_evidence(
        peak_queue_depth,
        peak_pool_saturation,
        deltas["endpoint_picker_queue"],
    )
    flow_control_engaged = bool(engagement["valid"])
    proof_checks_valid = data_quality_checks_valid and flow_control_engaged
    statuses = {status: sum(row["status"] == status for row in all_rows) for status in {
        row["status"] for row in all_rows
    }}
    all_successful = statuses == {"200": len(all_rows)}
    preconditions = {
        "run_id": run_id,
        "scenario": scenario,
        "arrival_mode": "poisson",
        "source": "guidellm_replay",
        "run_started_epoch_s": global_epoch,
        "run_ended_epoch_s": max(float(benchmark.get("end_time") or global_epoch) for _tenant, _raw, benchmark, _path in loaded),
        "tenant_start_epoch_spread_ms": epoch_spread_ms,
        "offered_schedule": {"valid": schedule_valid, "tenants": schedule_checks},
        "metric_capture": metric_health,
        "envoy_proxy_evidence": proxy_evidence,
        "header_evidence": headers,
        "flow_control_engagement": engagement,
        "cache_evidence": cache_evidence,
        **({"cache_off_evidence": cache_evidence} if prefix_cache_mode == "off" else {}),
        "stream_integrity": stream_integrity,
        "request_shape_evidence": shape_evidence,
        "proof_checks_valid": proof_checks_valid,
        "data_quality_valid": data_quality_checks_valid,
        "data_quality_reason": (
            "valid" if data_quality_checks_valid else "data-quality checks failed"
        ),
        "http_statuses": statuses,
        "slo_proof_valid": proof_checks_valid and all_successful,
        "slo_proof_reason": (
            "valid" if proof_checks_valid and all_successful
            else "flow control did not engage" if data_quality_checks_valid and not flow_control_engaged
            else "proof checks failed" if not proof_checks_valid else "non-200 requests present"
        ),
    }
    (run_dir / "preconditions.json").write_text(json.dumps(preconditions, indent=2) + "\n")
    (run_dir / "summary.json").write_text(json.dumps({
        "run_id": run_id,
        "scenario": scenario,
        "duration_s": duration_s,
        "arrival_mode": "poisson",
        "source": "guidellm_replay",
        "client_summary": summary_rows,
        "window_summary": window_rows,
        "metric_delta": deltas,
        "runtime_metrics": runtime_metrics,
    }, indent=2) + "\n")
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--metrics-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--prefix-cache-mode", choices=("off", "on"), default="off")
    parser.add_argument("--envoy-cluster-name", default="epp")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    convert_scenario(
        args.manifest, args.raw_dir, args.metrics_dir, args.run_dir, args.run_id,
        args.prefix_cache_mode,
        args.envoy_cluster_name,
    )

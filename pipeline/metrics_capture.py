#!/usr/bin/env python3
"""Shared EPP, Envoy, and vLLM metrics capture for benchmark runs."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable


MetricKey = tuple[str, tuple[tuple[str, str], ...]]

EPP_PREFIXES = (
    "llm_d_epp_",
    "llm_d_router_epp_",
    "inference_extension_",
    "inference_pool_",
    "inference_objective_",
    "llm_d_inference_scheduler_",
)

EPP_RUNTIME_PREFIXES = (
    "go_memstats_",
    "process_",
)

EPP_RUNTIME_METRICS = (
    "go_goroutines",
)

ENVOY_PREFIXES = (
    "envoy_cluster_",
    "envoy_http_",
)

ENVOY_REQUIRED = (
    "envoy_cluster_circuit_breakers_default_rq_open",
    "envoy_cluster_circuit_breakers_default_rq_pending_open",
    "envoy_cluster_circuit_breakers_default_remaining_rq",
    "envoy_cluster_circuit_breakers_default_remaining_pending",
    "envoy_cluster_upstream_rq_active",
    "envoy_cluster_upstream_rq_pending_overflow",
    "envoy_cluster_upstream_cx_overflow",
)

METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "epp_info": ("llm_d_epp_info", "inference_extension_info"),
    "epp_average_kv_cache": (
        "llm_d_epp_average_kv_cache_utilization",
        "inference_pool_average_kv_cache_utilization",
    ),
    "epp_average_queue": (
        "llm_d_epp_average_queue_size",
        "inference_pool_average_queue_size",
    ),
    "epp_average_running": (
        "llm_d_epp_average_running_requests",
        "inference_pool_average_running_requests",
    ),
    "epp_ready_endpoints": (
        "llm_d_epp_ready_endpoints",
        "inference_pool_ready_pods",
    ),
    "epp_flow_queue_duration": (
        "llm_d_epp_flow_control_request_queue_duration_seconds",
        "inference_extension_flow_control_request_queue_duration_seconds",
    ),
    "epp_flow_queue_size": (
        "llm_d_epp_flow_control_queue_size",
        "inference_extension_flow_control_queue_size",
    ),
    "epp_flow_queue_bytes": (
        "llm_d_epp_flow_control_queue_bytes",
        "inference_extension_flow_control_queue_bytes",
    ),
    "epp_pool_saturation": (
        "llm_d_epp_flow_control_pool_saturation",
        "inference_extension_flow_control_pool_saturation",
    ),
    "epp_flow_requests": ("llm_d_epp_flow_control_requests_total",),
    "epp_prefix_indexer_size": ("llm_d_epp_prefix_indexer_size",),
    "epp_prefix_indexer_hit_ratio": ("llm_d_epp_prefix_indexer_hit_ratio",),
    "epp_prefix_indexer_hit_bytes": ("llm_d_epp_prefix_indexer_hit_bytes",),
    "epp_process_resident_memory": ("process_resident_memory_bytes",),
    "epp_process_start_time": ("process_start_time_seconds",),
    "vllm_running": ("vllm:num_requests_running",),
    "vllm_waiting": ("vllm:num_requests_waiting",),
    "vllm_kv_cache": (
        "vllm:kv_cache_usage_perc",
        "vllm:gpu_cache_usage_perc",
    ),
    "vllm_preemptions": ("vllm:num_preemptions_total", "vllm:num_preemptions"),
    "vllm_prompt_tokens": ("vllm:prompt_tokens_total", "vllm:prompt_tokens"),
    "vllm_generation_tokens": (
        "vllm:generation_tokens_total",
        "vllm:generation_tokens",
    ),
    "vllm_prefix_queries": (
        "vllm:prefix_cache_queries",
        "vllm:prefix_cache_queries_total",
    ),
    "vllm_prefix_hits": (
        "vllm:prefix_cache_hits",
        "vllm:prefix_cache_hits_total",
    ),
}

BASE_REQUIRED = (
    "epp_info",
    "epp_average_kv_cache",
    "epp_average_queue",
    "epp_average_running",
    "epp_ready_endpoints",
    "epp_process_resident_memory",
    "epp_process_start_time",
    "vllm_running",
    "vllm_waiting",
    "vllm_kv_cache",
    "vllm_preemptions",
    "vllm_prompt_tokens",
    "vllm_generation_tokens",
    "vllm_prefix_queries",
    "vllm_prefix_hits",
)

FLOW_CONTROL_REQUIRED = (
    "epp_pool_saturation",
)

ACTIVE_FLOW_CONTROL_REQUIRED = (
    "epp_flow_queue_duration",
    "epp_flow_queue_size",
    "epp_flow_queue_bytes",
)

VLLM_REQUIRED = tuple(
    concept for concept in BASE_REQUIRED if concept.startswith("vllm_")
)

EPP_REQUIRED = tuple(
    concept for concept in BASE_REQUIRED if concept.startswith("epp_")
)

PREFIX_CACHE_REQUIRED = (
    "epp_prefix_indexer_size",
    "epp_prefix_indexer_hit_ratio",
    "epp_prefix_indexer_hit_bytes",
)


def parse_labels(label_text: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2)
        for match in re.finditer(r'([a-zA-Z_:][a-zA-Z0-9_:]*)="([^"\\]*(?:\\.[^"\\]*)*)"', label_text)
    }


def parse_prometheus(text: str) -> dict[MetricKey, float]:
    parsed: dict[MetricKey, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        metric_part, value_text = parts[0], parts[1]
        try:
            value = float(value_text)
        except ValueError:
            continue
        if "{" in metric_part:
            name, label_part = metric_part.split("{", 1)
            labels = parse_labels(label_part.rstrip("}"))
        else:
            name, labels = metric_part, {}
        parsed[(name, tuple(sorted(labels.items())))] = value
    return parsed


def discover_metric_names(text: str) -> set[str]:
    names = {key[0] for key in parse_prometheus(text)}
    for line in text.splitlines():
        match = re.match(r"^# (?:HELP|TYPE) ([^ ]+)", line)
        if match:
            names.add(match.group(1))
    return names


def resolve_concepts(epp_text: str, vllm_text: str) -> dict[str, str | None]:
    names = discover_metric_names(epp_text) | discover_metric_names(vllm_text)
    return {
        concept: next((name for name in aliases if name in names), None)
        for concept, aliases in METRIC_ALIASES.items()
    }


def build_preflight_report(
    epp_text: str,
    vllm_text: str,
    require_flow_control: bool,
    require_prefix_cache: bool = False,
) -> dict[str, Any]:
    resolved = resolve_concepts(epp_text, vllm_text)
    required = list(BASE_REQUIRED)
    if require_flow_control:
        required.extend(FLOW_CONTROL_REQUIRED)
    if require_prefix_cache:
        required.extend(PREFIX_CACHE_REQUIRED)
    missing = [concept for concept in required if resolved.get(concept) is None]
    return {
        "valid": not missing,
        "require_flow_control": require_flow_control,
        "require_prefix_cache": require_prefix_cache,
        "required_concepts": required,
        "missing_concepts": missing,
        "resolved_metrics": resolved,
        "optional_flow_request_outcomes_present": resolved["epp_flow_requests"] is not None,
    }


def build_vllm_target_report(text: str) -> dict[str, Any]:
    resolved = resolve_concepts("", text)
    sampled_names = {name for name, _labels in parse_prometheus(text)}
    missing = [
        concept for concept in VLLM_REQUIRED
        if resolved.get(concept) not in sampled_names
    ]
    return {
        "valid": not missing,
        "required_concepts": list(VLLM_REQUIRED),
        "missing_concepts": missing,
        "resolved_metrics": {
            concept: resolved.get(concept) for concept in VLLM_REQUIRED
        },
    }


def build_epp_target_report(
    text: str, require_flow_control: bool = True
) -> dict[str, Any]:
    resolved = resolve_concepts(text, "")
    sampled_names = {name for name, _labels in parse_prometheus(text)}
    required = list(EPP_REQUIRED)
    if require_flow_control:
        required.extend(FLOW_CONTROL_REQUIRED)
    missing = [
        concept for concept in required
        if resolved.get(concept) not in sampled_names
    ]
    return {
        "valid": not missing,
        "required_concepts": required,
        "missing_concepts": missing,
        "resolved_metrics": {
            concept: resolved.get(concept) for concept in required
        },
    }


def build_active_flow_report(epp_text: str) -> dict[str, Any]:
    resolved = resolve_concepts(epp_text, "")
    missing = [
        concept for concept in ACTIVE_FLOW_CONTROL_REQUIRED if not resolved.get(concept)
    ]
    return {
        "valid": not missing,
        "source": "endpoint_picker",
        "required_concepts": list(ACTIVE_FLOW_CONTROL_REQUIRED),
        "missing_concepts": missing,
        "resolved_metrics": {
            concept: resolved.get(concept) for concept in ACTIVE_FLOW_CONTROL_REQUIRED
        },
    }


def build_envoy_preflight_report(
    envoy_text: str,
    cluster_name: str,
    expected_remaining_requests: int | None = None,
) -> dict[str, Any]:
    parsed = parse_prometheus(envoy_text)
    names = discover_metric_names(envoy_text)
    missing = [name for name in ENVOY_REQUIRED if name not in names]
    cluster_rows = {
        name: value
        for (name, labels), value in parsed.items()
        if dict(labels).get("envoy_cluster_name") == cluster_name
    }
    remaining = cluster_rows.get(
        "envoy_cluster_circuit_breakers_default_remaining_rq"
    )
    expected_matches = (
        expected_remaining_requests is None
        or remaining == float(expected_remaining_requests)
    )
    return {
        "valid": not missing and bool(cluster_rows) and expected_matches,
        "cluster_name": cluster_name,
        "required_metrics": list(ENVOY_REQUIRED),
        "missing_metrics": missing,
        "cluster_metrics_present": bool(cluster_rows),
        "remaining_requests": remaining,
        "expected_remaining_requests": expected_remaining_requests,
        "expected_remaining_requests_match": expected_matches,
    }


def memory_limit_report(
    peak_bytes: float, limit_bytes: int | None, max_fraction: float,
) -> dict[str, Any]:
    fraction = peak_bytes / limit_bytes if limit_bytes else None
    return {
        "valid": limit_bytes is None or bool(fraction is not None and fraction < max_fraction),
        "peak_resident_memory_bytes": peak_bytes,
        "container_limit_bytes": limit_bytes,
        "peak_fraction_of_limit": fraction,
        "maximum_allowed_fraction": max_fraction,
    }


def metric_source_accepts(source: str, name: str) -> bool:
    if source == "vllm":
        return name.startswith("vllm:")
    if source == "envoy":
        return name.startswith(ENVOY_PREFIXES)
    return (
        name.startswith(EPP_PREFIXES)
        or name.startswith(EPP_RUNTIME_PREFIXES)
        or name in EPP_RUNTIME_METRICS
    )


def metric_generation(name: str) -> str:
    if name.startswith("envoy_"):
        return "proxy"
    if name.startswith(("llm_d_epp_", "llm_d_router_epp_", "vllm:")):
        return "canonical"
    if name.startswith(EPP_RUNTIME_PREFIXES) or name in EPP_RUNTIME_METRICS:
        return "runtime"
    return "legacy"


def long_rows(
    text: str,
    source: str,
    elapsed_s: float,
    run_id: str,
    scenario: str,
    sample_epoch_s: float | None = None,
    extra_labels: dict[str, str] | None = None,
) -> Iterable[dict[str, Any]]:
    for (name, labels), value in parse_prometheus(text).items():
        if not metric_source_accepts(source, name):
            continue
        row_labels = dict(labels)
        row_labels.update(extra_labels or {})
        yield {
            "run_id": run_id,
            "scenario": scenario,
            "elapsed_s": round(elapsed_s, 6),
            "sample_epoch_s": round(sample_epoch_s, 6) if sample_epoch_s is not None else "",
            "source": source,
            "metric_generation": metric_generation(name),
            "metric": name,
            "labels_json": json.dumps(row_labels, sort_keys=True, separators=(",", ":")),
            "value": value,
        }


def named_urls(values: list[str]) -> list[tuple[str, str]]:
    targets = []
    for index, value in enumerate(values, start=1):
        if "=" in value and not value.startswith(("http://", "https://")):
            name, url = value.split("=", 1)
        else:
            name, url = f"vllm-{index}", value
        if not name or not url.startswith(("http://", "https://")):
            raise ValueError(f"invalid named metrics URL: {value}")
        targets.append((name, url))
    if len({name for name, _url in targets}) != len(targets):
        raise ValueError("vLLM metrics target names must be unique")
    return targets


def label_prometheus_samples(text: str, target: str) -> str:
    """Add a target label so equal series from different pods do not collide."""
    escaped = target.replace("\\", r"\\").replace('"', r'\"')
    output = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            output.append(line)
            continue
        match = re.match(r"^([^\s{]+)(\{[^}]*\})?(\s+.*)$", line)
        if not match:
            output.append(line)
            continue
        name, labels, suffix = match.groups()
        target_label = f'metrics_target="{escaped}"'
        if labels:
            inner = labels[1:-1]
            labels = "{" + (inner + "," if inner else "") + target_label + "}"
        else:
            labels = "{" + target_label + "}"
        output.append(name + labels + suffix)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def combine_target_metrics(payloads: dict[str, str]) -> str:
    return "\n".join(
        label_prometheus_samples(payload, target).rstrip("\n")
        for target, payload in payloads.items()
    ) + "\n"


def scrape_url(
    url: str,
    token: str | None = None,
    timeout_s: float = 5.0,
    insecure_https: bool = False,
) -> str:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    context = ssl._create_unverified_context() if insecure_https and url.startswith("https") else None
    with urllib.request.urlopen(request, timeout=timeout_s, context=context) as response:
        return response.read().decode("utf-8", "replace")


def parse_inflight_plugin_state(text: str, plugin_name: str = "inflight-load") -> dict[str, Any]:
    payload = json.loads(text)
    plugin = payload.get("plugins", {}).get(plugin_name, {})
    endpoints = plugin.get("state", {}).get("endpoints", [])
    if not isinstance(endpoints, list):
        raise ValueError(f"invalid {plugin_name} endpoint state")
    rows = [
        {
            "endpoint": str(endpoint.get("endpoint", "unknown")),
            "requests": int(endpoint.get("requests", 0)),
            "tokens": int(endpoint.get("tokens", 0)),
        }
        for endpoint in endpoints
    ]
    return {
        "timestamp": payload.get("timestamp"),
        "plugin": plugin_name,
        "endpoints": rows,
        "requests": sum(row["requests"] for row in rows),
        "tokens": sum(row["tokens"] for row in rows),
    }


def inflight_state_rows(
    state: dict[str, Any], elapsed: float, run_id: str, scenario: str,
    sample_epoch_s: float,
) -> list[dict[str, Any]]:
    """Record the detector's exact request and input-token state."""
    labels = json.dumps(
        {"plugin": state["plugin"]}, sort_keys=True, separators=(",", ":")
    )
    base = {
        "run_id": run_id,
        "scenario": scenario,
        "elapsed_s": round(elapsed, 6),
        "sample_epoch_s": round(sample_epoch_s, 6),
        "source": "epp",
        "metric_generation": "exact",
        "labels_json": labels,
    }
    return [
        {**base, "metric": "epp:inflight_requests", "value": state["requests"]},
        {**base, "metric": "epp:inflight_tokens", "value": state["tokens"]},
    ]


def load_token(value: str | None, path: str | None) -> str | None:
    if value:
        return value
    if path:
        return Path(path).read_text().strip()
    return None


def record(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    token = load_token(args.epp_token, args.epp_token_file)
    plugin_state_url = args.epp_plugin_state_url or (
        args.epp_url.rsplit("/", 1)[0] + "/debug/plugins/state"
    )
    vllm_targets = named_urls(args.vllm_url)
    epp_pre = scrape_url(args.epp_url, token, args.timeout, args.insecure_https)
    vllm_pre_by_target = {
        name: scrape_url(url, None, args.timeout, args.insecure_https)
        for name, url in vllm_targets
    }
    vllm_pre = combine_target_metrics(vllm_pre_by_target)
    envoy_pre = (
        scrape_url(args.envoy_url, None, args.timeout, args.insecure_https)
        if args.envoy_url else ""
    )
    (out_dir / "pre_epp.prom").write_text(epp_pre)
    (out_dir / "pre_vllm.prom").write_text(vllm_pre)
    if args.envoy_url:
        (out_dir / "pre_envoy.prom").write_text(envoy_pre)

    report = build_preflight_report(
        epp_pre, vllm_pre, args.require_flow_control, args.require_prefix_cache
    )
    report["vllm_targets"] = {
        name: {"url": url, **build_vllm_target_report(vllm_pre_by_target[name])}
        for name, url in vllm_targets
    }
    report["valid"] = bool(
        report["valid"]
        and all(item["valid"] for item in report["vllm_targets"].values())
    )
    try:
        plugin_state = parse_inflight_plugin_state(scrape_url(
            plugin_state_url, token, args.timeout, args.insecure_https
        ))
        report["epp_plugin_state"] = {
            "valid": True,
            "url": plugin_state_url,
            "plugin": plugin_state["plugin"],
        }
    except Exception as exc:
        report["epp_plugin_state"] = {
            "valid": False,
            "url": plugin_state_url,
            "error": type(exc).__name__,
        }
        report["valid"] = False
    if args.envoy_url or args.require_envoy:
        envoy_report = build_envoy_preflight_report(
            envoy_pre,
            args.envoy_cluster_name,
            args.expected_envoy_remaining_requests,
        )
        report["envoy_proxy"] = envoy_report
        report["valid"] = bool(report["valid"] and envoy_report["valid"])
    (out_dir / "metric_preflight.json").write_text(json.dumps(report, indent=2))
    if not report["valid"] and not args.allow_missing:
        return 2
    if args.preflight_only:
        return 0

    fieldnames = [
        "run_id", "scenario", "elapsed_s", "sample_epoch_s", "source", "metric_generation",
        "metric", "labels_json", "value",
    ]
    started = time.monotonic()
    samples = 0
    plugin_state_samples = 0
    vllm_target_samples = {name: 0 for name, _url in vllm_targets}
    epp_process_start_times: set[float] = set()
    epp_peak_resident_memory_bytes = 0.0
    skipped_intervals = 0
    sample_durations: list[float] = []
    errors: list[dict[str, Any]] = []
    next_sample = started
    with (out_dir / "metric_samples_long.csv").open("w", newline="") as handle, \
            ThreadPoolExecutor(max_workers=len(vllm_targets) + 3) as executor:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        while time.monotonic() - started < args.duration:
            if args.stop_file and Path(args.stop_file).exists():
                break
            sleep_s = next_sample - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            elapsed = time.monotonic() - started
            sample_epoch_s = time.time()
            sample_started = time.monotonic()
            sources = [("epp", "epp", args.epp_url, token)]
            sources.extend(
                ("vllm", name, url, None) for name, url in vllm_targets
            )
            if args.envoy_url:
                sources.append(("envoy", "envoy", args.envoy_url, None))
            futures = {
                (source, target): executor.submit(
                    scrape_url, url, auth, args.timeout, args.insecure_https
                )
                for source, target, url, auth in sources
            }
            plugin_future = executor.submit(
                scrape_url, plugin_state_url, token, args.timeout, args.insecure_https
            )
            for source, target, _url, _auth in sources:
                try:
                    payload = futures[(source, target)].result()
                    if source == "vllm":
                        sample_report = build_vllm_target_report(payload)
                        if not sample_report["valid"]:
                            errors.append({
                                "elapsed_s": elapsed,
                                "source": source,
                                "target": target,
                                "error": "MissingRequiredMetrics",
                                "missing_concepts": sample_report["missing_concepts"],
                            })
                            continue
                    elif source == "epp":
                        sample_report = build_epp_target_report(
                            payload, args.require_flow_control
                        )
                        if not sample_report["valid"]:
                            errors.append({
                                "elapsed_s": elapsed,
                                "source": source,
                                "target": target,
                                "error": "MissingRequiredMetrics",
                                "missing_concepts": sample_report["missing_concepts"],
                            })
                            continue
                        parsed = parse_prometheus(payload)
                        runtime_values = {
                            name: value for (name, _labels), value in parsed.items()
                        }
                        start_name = sample_report["resolved_metrics"].get(
                            "epp_process_start_time"
                        )
                        memory_name = sample_report["resolved_metrics"].get(
                            "epp_process_resident_memory"
                        )
                        if start_name:
                            epp_process_start_times.add(runtime_values[start_name])
                        if memory_name:
                            epp_peak_resident_memory_bytes = max(
                                epp_peak_resident_memory_bytes,
                                runtime_values[memory_name],
                            )
                    elif source == "envoy":
                        sample_report = build_envoy_preflight_report(
                            payload, args.envoy_cluster_name, None
                        )
                        if not sample_report["valid"]:
                            errors.append({
                                "elapsed_s": elapsed,
                                "source": source,
                                "target": target,
                                "error": "MissingRequiredMetrics",
                                "missing_metrics": sample_report["missing_metrics"],
                            })
                            continue
                    writer.writerows(long_rows(
                        payload, source, elapsed, args.run_id, args.scenario, sample_epoch_s,
                        {"metrics_target": target} if source == "vllm" else None,
                    ))
                    if source == "vllm":
                        vllm_target_samples[target] += 1
                except Exception as exc:
                    errors.append({
                        "elapsed_s": elapsed, "source": source, "target": target,
                        "error": type(exc).__name__,
                    })
            try:
                state = parse_inflight_plugin_state(plugin_future.result())
                writer.writerows(inflight_state_rows(
                    state, elapsed, args.run_id, args.scenario, sample_epoch_s
                ))
                plugin_state_samples += 1
            except Exception as exc:
                errors.append({
                    "elapsed_s": elapsed,
                    "source": "epp_plugin_state",
                    "error": type(exc).__name__,
                })
            handle.flush()
            samples += 1
            sample_durations.append(time.monotonic() - sample_started)
            next_sample += args.interval
            while next_sample <= time.monotonic():
                next_sample += args.interval
                skipped_intervals += 1

    epp_post = scrape_url(args.epp_url, token, args.timeout, args.insecure_https)
    vllm_post_by_target = {
        name: scrape_url(url, None, args.timeout, args.insecure_https)
        for name, url in vllm_targets
    }
    vllm_post = combine_target_metrics(vllm_post_by_target)
    envoy_post = (
        scrape_url(args.envoy_url, None, args.timeout, args.insecure_https)
        if args.envoy_url else ""
    )
    (out_dir / "post_epp.prom").write_text(epp_post)
    (out_dir / "post_vllm.prom").write_text(vllm_post)
    if args.envoy_url:
        (out_dir / "post_envoy.prom").write_text(envoy_post)
    post_vllm_targets = {
        name: build_vllm_target_report(vllm_post_by_target[name])
        for name, _url in vllm_targets
    }
    post_epp = build_epp_target_report(epp_post, args.require_flow_control)
    memory = memory_limit_report(
        epp_peak_resident_memory_bytes,
        args.epp_memory_limit_bytes,
        args.max_epp_memory_fraction,
    )
    health = {
        "valid": bool(
            not errors and samples > 0 and skipped_intervals == 0
            and all(count == samples for count in vllm_target_samples.values())
            and all(item["valid"] for item in post_vllm_targets.values())
            and post_epp["valid"]
            and len(epp_process_start_times) == 1
            and memory["valid"]
        ),
        "samples": samples,
        "interval_s": args.interval,
        "duration_s": args.duration,
        "errors": errors,
        "skipped_intervals": skipped_intervals,
        "max_sample_duration_s": max(sample_durations, default=0.0),
        "epp_plugin_state_samples": plugin_state_samples,
        "missing_inflight_request_samples": samples - plugin_state_samples,
        "missing_inflight_token_samples": samples - plugin_state_samples,
        "vllm_target_samples": vllm_target_samples,
        "missing_samples_by_vllm_target": {
            name: samples - count for name, count in vllm_target_samples.items()
        },
        "post_vllm_targets": post_vllm_targets,
        "post_epp": post_epp,
        "epp_process_start_times": sorted(epp_process_start_times),
        "epp_process_restart_detected": len(epp_process_start_times) > 1,
        "epp_peak_resident_memory_bytes": epp_peak_resident_memory_bytes,
        "epp_memory_limit": memory,
        "preflight": report,
        "active_flow_metrics": build_active_flow_report(epp_post),
    }
    if args.envoy_url:
        health["envoy_proxy"] = build_envoy_preflight_report(
            envoy_post, args.envoy_cluster_name, None
        )
        health["valid"] = bool(health["valid"] and health["envoy_proxy"]["valid"])
    (out_dir / "metric_capture_health.json").write_text(json.dumps(health, indent=2))
    return 0 if health["valid"] else 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epp-url", default=os.getenv("EPP_METRICS_URL", "http://localhost:9090/metrics"))
    parser.add_argument("--epp-plugin-state-url", default=os.getenv("EPP_PLUGIN_STATE_URL"))
    parser.add_argument(
        "--vllm-url", action="append",
        help="Repeat as name=URL to scrape every vLLM pod directly",
    )
    parser.add_argument("--envoy-url", default=os.getenv("ENVOY_METRICS_URL"))
    parser.add_argument("--envoy-cluster-name", default="epp")
    parser.add_argument("--expected-envoy-remaining-requests", type=int)
    parser.add_argument("--epp-memory-limit-bytes", type=int)
    parser.add_argument("--max-epp-memory-fraction", type=float, default=0.85)
    parser.add_argument("--epp-token", default=os.getenv("EPP_METRICS_TOKEN"))
    parser.add_argument("--epp-token-file", default=os.getenv("EPP_METRICS_TOKEN_FILE"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--stop-file", help="Finish cleanly when this file appears")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--require-flow-control", action="store_true")
    parser.add_argument("--require-prefix-cache", action="store_true")
    parser.add_argument("--require-envoy", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--insecure-https", action="store_true")
    args = parser.parse_args()
    if not args.vllm_url:
        args.vllm_url = [os.getenv("VLLM_METRICS_URL", "http://localhost:8001/metrics")]
    return args


if __name__ == "__main__":
    raise SystemExit(record(parse_args()))

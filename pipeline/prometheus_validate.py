#!/usr/bin/env python3
"""Verify that Prometheus is scraping every benchmark proof metric."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import metrics_capture


def api_get(
    base_url: str,
    path: str,
    token: str | None,
    timeout_s: float,
    insecure_https: bool,
) -> dict[str, Any]:
    request = urllib.request.Request(base_url.rstrip("/") + path)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    context = ssl._create_unverified_context() if insecure_https else None
    with urllib.request.urlopen(request, timeout=timeout_s, context=context) as response:
        payload = json.loads(response.read())
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus API returned {payload.get('status')!r}")
    return payload


def metric_query(namespace: str, pod_prefix: str) -> str:
    names = sorted({
        name
        for aliases in metrics_capture.METRIC_ALIASES.values()
        for name in aliases
    })
    escaped_names = "|".join(names)
    escaped_prefix = pod_prefix
    return (
        f'{{__name__=~"({escaped_names})(_bucket|_sum|_count)?",namespace="{namespace}",'
        f'pod=~"{escaped_prefix}.*"}}'
    )


def queried_metric_names(query_payload: dict[str, Any]) -> set[str]:
    return {
        str(item.get("metric", {}).get("__name__"))
        for item in query_payload.get("data", {}).get("result", [])
        if item.get("metric", {}).get("__name__")
    }


def resolve_queried_concepts(names: set[str]) -> dict[str, str | None]:
    return {
        concept: next((
            alias
            for alias in aliases
            if any(
                name == alias or name in {
                    alias + "_bucket", alias + "_sum", alias + "_count"
                }
                for name in names
            )
        ), None)
        for concept, aliases in metrics_capture.METRIC_ALIASES.items()
    }


def build_range_report(
    query_payload: dict[str, Any],
    require_flow_control: bool,
    require_active_flow: bool,
) -> dict[str, Any]:
    result = query_payload.get("data", {}).get("result", [])
    names = {
        str(item.get("metric", {}).get("__name__"))
        for item in result
        if item.get("metric", {}).get("__name__") and item.get("values")
    }
    resolved = resolve_queried_concepts(names)
    required = list(metrics_capture.BASE_REQUIRED)
    if require_flow_control:
        required.extend(metrics_capture.FLOW_CONTROL_REQUIRED)
    if require_active_flow:
        required.extend(metrics_capture.ACTIVE_FLOW_CONTROL_REQUIRED)
    coverage = {
        concept: sum(
            len(item.get("values", []))
            for item in result
            if (
                item.get("metric", {}).get("__name__") == resolved.get(concept)
                or item.get("metric", {}).get("__name__") in {
                    str(resolved.get(concept)) + "_bucket",
                    str(resolved.get(concept)) + "_sum",
                    str(resolved.get(concept)) + "_count",
                }
            )
        )
        for concept in required
    }
    missing = [concept for concept in required if not resolved.get(concept)]
    empty = [concept for concept, samples in coverage.items() if samples == 0]
    return {
        "valid": not missing and not empty,
        "require_flow_control": require_flow_control,
        "require_active_flow": require_active_flow,
        "resolved_metrics": resolved,
        "sample_count_by_concept": coverage,
        "missing_metrics": missing,
        "empty_metrics": empty,
    }


def target_summary(
    targets_payload: dict[str, Any],
    namespace: str,
    services: tuple[str, str],
) -> dict[str, Any]:
    matched: dict[str, list[dict[str, Any]]] = {service: [] for service in services}
    for target in targets_payload.get("data", {}).get("activeTargets", []):
        labels = target.get("labels", {})
        discovered = target.get("discoveredLabels", {})
        target_namespace = labels.get("namespace") or discovered.get("__meta_kubernetes_namespace")
        service = labels.get("service") or discovered.get("__meta_kubernetes_service_name")
        if target_namespace == namespace and service in matched:
            matched[service].append({
                "health": target.get("health"),
                "last_error": target.get("lastError"),
                "scrape_url": target.get("scrapeUrl"),
            })
    missing = [service for service, targets in matched.items() if not targets]
    unhealthy = [
        service
        for service, targets in matched.items()
        if targets and not any(target["health"] == "up" for target in targets)
    ]
    return {
        "valid": not missing and not unhealthy,
        "services": matched,
        "missing_services": missing,
        "unhealthy_services": unhealthy,
    }


def build_report(
    targets_payload: dict[str, Any],
    query_payload: dict[str, Any],
    namespace: str,
    services: tuple[str, str],
    require_flow_control: bool,
    require_active_flow: bool = False,
) -> dict[str, Any]:
    names = queried_metric_names(query_payload)
    resolved = resolve_queried_concepts(names)
    required = list(metrics_capture.BASE_REQUIRED)
    if require_flow_control:
        required.extend(metrics_capture.FLOW_CONTROL_REQUIRED)
    if require_active_flow:
        required.extend(metrics_capture.ACTIVE_FLOW_CONTROL_REQUIRED)
    missing_metrics = [concept for concept in required if not resolved.get(concept)]
    targets = target_summary(targets_payload, namespace, services)
    return {
        "valid": targets["valid"] and not missing_metrics,
        "namespace": namespace,
        "require_flow_control": require_flow_control,
        "require_active_flow": require_active_flow,
        "targets": targets,
        "resolved_metrics": resolved,
        "missing_metrics": missing_metrics,
        "queried_metric_names": sorted(names),
    }


def main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    token = metrics_capture.load_token(args.token, args.token_file)
    targets = api_get(
        args.url, "/api/v1/targets", token, args.timeout, args.insecure_https
    )
    query = metric_query(args.namespace, args.pod_prefix)
    query_payload = api_get(
        args.url,
        "/api/v1/query?" + urllib.parse.urlencode({"query": query}),
        token,
        args.timeout,
        args.insecure_https,
    )
    report = build_report(
        targets,
        query_payload,
        args.namespace,
        (args.epp_service, args.vllm_service),
        args.require_flow_control,
        args.require_active_flow,
    )
    (out_dir / "prometheus_targets.json").write_text(json.dumps(targets, indent=2))
    (out_dir / "prometheus_query.json").write_text(json.dumps(query_payload, indent=2))
    (out_dir / "prometheus_validation.json").write_text(json.dumps(report, indent=2))
    return 0 if report["valid"] else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("PROMETHEUS_URL", "https://localhost:29090"))
    parser.add_argument("--token", default=os.getenv("PROMETHEUS_TOKEN"))
    parser.add_argument("--token-file", default=os.getenv("PROMETHEUS_TOKEN_FILE"))
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--pod-prefix", required=True)
    parser.add_argument("--epp-service", required=True)
    parser.add_argument("--vllm-service", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--require-flow-control", action="store_true")
    parser.add_argument("--require-active-flow", action="store_true")
    parser.add_argument("--insecure-https", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))

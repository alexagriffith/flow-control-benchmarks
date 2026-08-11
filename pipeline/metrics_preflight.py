#!/usr/bin/env python3
"""Run the repeatable direct-and-Prometheus metric gate."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

import metrics_capture
import prometheus_validate


def combined_report(
    direct: dict[str, Any], prometheus: dict[str, Any]
) -> dict[str, Any]:
    return {
        "valid": bool(direct.get("valid") and prometheus.get("valid")),
        "direct_metrics": direct,
        "prometheus_metrics": prometheus,
    }


def main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    epp_token = metrics_capture.load_token(args.epp_token, args.epp_token_file)
    prometheus_token = metrics_capture.load_token(
        args.prometheus_token, args.prometheus_token_file
    )

    epp_text = metrics_capture.scrape_url(
        args.epp_url, epp_token, args.timeout, args.insecure_https
    )
    vllm_text = metrics_capture.scrape_url(
        args.vllm_url, None, args.timeout, args.insecure_https
    )
    (out_dir / "direct_epp.prom").write_text(epp_text)
    (out_dir / "direct_vllm.prom").write_text(vllm_text)
    direct = metrics_capture.build_preflight_report(
        epp_text, vllm_text, args.require_flow_control
    )
    (out_dir / "direct_metrics_validation.json").write_text(
        json.dumps(direct, indent=2)
    )

    targets = prometheus_validate.api_get(
        args.prometheus_url,
        "/api/v1/targets",
        prometheus_token,
        args.timeout,
        args.insecure_prometheus_https,
    )
    query = prometheus_validate.metric_query(args.namespace, args.pod_prefix)
    query_payload = prometheus_validate.api_get(
        args.prometheus_url,
        "/api/v1/query?" + urllib.parse.urlencode({"query": query}),
        prometheus_token,
        args.timeout,
        args.insecure_prometheus_https,
    )
    prometheus = prometheus_validate.build_report(
        targets,
        query_payload,
        args.namespace,
        (args.epp_service, args.vllm_service),
        args.require_flow_control,
        False,
    )
    (out_dir / "prometheus_targets.json").write_text(json.dumps(targets, indent=2))
    (out_dir / "prometheus_query.json").write_text(json.dumps(query_payload, indent=2))
    (out_dir / "prometheus_metrics_validation.json").write_text(
        json.dumps(prometheus, indent=2)
    )

    result = combined_report(direct, prometheus)
    (out_dir / "metrics_preflight.json").write_text(json.dumps(result, indent=2))
    return 0 if result["valid"] else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epp-url", default=os.getenv("EPP_METRICS_URL"))
    parser.add_argument("--vllm-url", default=os.getenv("VLLM_METRICS_URL"))
    parser.add_argument("--prometheus-url", default=os.getenv("PROMETHEUS_URL"))
    parser.add_argument("--epp-token", default=os.getenv("EPP_METRICS_TOKEN"))
    parser.add_argument("--epp-token-file", default=os.getenv("EPP_METRICS_TOKEN_FILE"))
    parser.add_argument("--prometheus-token", default=os.getenv("PROMETHEUS_TOKEN"))
    parser.add_argument("--prometheus-token-file", default=os.getenv("PROMETHEUS_TOKEN_FILE"))
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--pod-prefix", required=True)
    parser.add_argument("--epp-service", required=True)
    parser.add_argument("--vllm-service", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--require-flow-control", action="store_true")
    parser.add_argument("--insecure-https", action="store_true")
    parser.add_argument("--insecure-prometheus-https", action="store_true")
    args = parser.parse_args()
    missing = [
        name
        for name in ("epp_url", "vllm_url", "prometheus_url")
        if not getattr(args, name)
    ]
    if missing:
        parser.error("required metric URLs missing: " + ", ".join(missing))
    return args


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))

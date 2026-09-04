#!/usr/bin/env python3
"""Validate the live RHAII 3.5 service contract before replay traffic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def named(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    match = next((item for item in items if item.get("name") == name), None)
    if not match:
        raise ValueError(f"missing named item: {name}")
    return match


def validate(
    service: dict[str, Any], scheduler: dict[str, Any], args: argparse.Namespace,
) -> dict[str, Any]:
    spec = service.get("spec", {})
    inline = (
        spec.get("router", {}).get("scheduler", {}).get("config", {}).get("inline")
    )
    if not isinstance(inline, dict):
        raise ValueError("LLMInferenceService has no inline EndpointPickerConfig")
    plugins = inline.get("plugins", [])
    detector_name = inline.get("flowControl", {}).get(
        "saturationDetector", {}
    ).get("pluginRef")
    detector = named(plugins, str(detector_name))
    parameters = detector.get("parameters", {})
    model_container = named(spec.get("template", {}).get("containers", []), "main")
    model_environment = {
        item.get("name"): item.get("value")
        for item in model_container.get("env", [])
    }
    scheduler_container = named(
        scheduler.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []),
        "main",
    )
    checks = {
        "flow_control_enabled": "flowControl" in inline.get("featureGates", []),
        "detector_type": detector.get("type") == "concurrency-detector",
        "concurrency_mode": parameters.get("concurrencyMode") == "requests",
        "max_concurrency": parameters.get("maxConcurrency") == args.expected_max_concurrency,
        "headroom": parameters.get("headroom") == args.expected_headroom,
        "request_ttl": inline.get("flowControl", {}).get("defaultRequestTTL") == args.expected_ttl,
        "model": spec.get("model", {}).get("name") == args.expected_model,
        "replicas": spec.get("replicas") == args.expected_replicas,
        "prefix_cache_disabled": "--no-enable-prefix-caching" in str(
            model_environment.get("VLLM_ADDITIONAL_ARGS", "")
        ),
        "scheduler_image_declared": bool(scheduler_container.get("image")),
    }
    return {
        "valid": all(checks.values()),
        "checks": checks,
        "observed": {
            "service": service.get("metadata", {}).get("name"),
            "model": spec.get("model", {}).get("name"),
            "replicas": spec.get("replicas"),
            "detector": detector_name,
            "concurrency_mode": parameters.get("concurrencyMode"),
            "max_concurrency": parameters.get("maxConcurrency"),
            "headroom": parameters.get("headroom"),
            "request_ttl": inline.get("flowControl", {}).get("defaultRequestTTL"),
            "scheduler_image": scheduler_container.get("image"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-json", type=Path, required=True)
    parser.add_argument("--scheduler-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-model", default="openai/gpt-oss-20b")
    parser.add_argument("--expected-max-concurrency", type=int, default=28)
    parser.add_argument("--expected-headroom", type=int, default=0)
    parser.add_argument("--expected-ttl", default="3s")
    parser.add_argument("--expected-replicas", type=int, default=1)
    args = parser.parse_args()
    report = validate(
        json.loads(args.service_json.read_text()),
        json.loads(args.scheduler_json.read_text()),
        args,
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

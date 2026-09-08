#!/usr/bin/env python3
"""Replay synchronized GuideLLM traces without assuming a router topology."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_DIR))

from guidellm_k8s import backend_config, job_document, kubectl, resource_name  # noqa: E402


def replay_name(prefix: str, fairness_id: str) -> str:
    """Return a collision-resistant Kubernetes name."""
    logical = f"{prefix}-{fairness_id}".lower()
    candidate = resource_name(prefix, fairness_id)
    if len(logical) <= 63:
        return candidate
    digest = hashlib.sha256(logical.encode()).hexdigest()[:8]
    return f"{candidate[:54].rstrip('-')}-{digest}"


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text())
    tenants = manifest.get("tenants")
    if not isinstance(tenants, list) or not tenants:
        raise ValueError("manifest must define at least one tenant")
    for tenant in tenants:
        trace = path.parent / str(tenant["trace_file"])
        content = trace.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        expected_hash = tenant.get("file_sha256")
        if expected_hash and actual_hash != expected_hash:
            raise ValueError(f"trace hash mismatch: {trace}")
        line_count = len(content.splitlines())
        if line_count != int(tenant["planned_requests"]):
            raise ValueError(
                f"trace request count mismatch: {trace}: "
                f"expected {tenant['planned_requests']}, found {line_count}"
            )
    return manifest


def copy_from_pod(namespace: str, pod: str, source: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["kubectl", "cp", f"{namespace}/{pod}:{source}", str(destination),
         "-c", "guidellm"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"could not copy {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--start-delay-s", type=float, default=60.0)
    parser.add_argument("--drain-timeout-s", type=int, default=300)
    parser.add_argument("--worker-processes", type=int, default=4)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    resources_dir = args.run_dir / "resources"
    raw_dir = args.run_dir / "raw"
    resources_dir.mkdir(exist_ok=True)
    raw_dir.mkdir(exist_ok=True)
    wrapper = Path(__file__).resolve().parents[1] / "guidellm_fixed_start.py"
    fixed_epoch = time.time() + args.start_delay_s
    active_deadline = math.ceil(
        args.start_delay_s + float(manifest["duration_s"])
        + args.drain_timeout_s + 120
    )
    resources: list[tuple[str, str, dict[str, Any]]] = []
    pods: dict[str, str] = {}

    try:
        for tenant in manifest["tenants"]:
            fairness_id = str(tenant["fairness_id"])
            name = replay_name(args.prefix, fairness_id)
            configmap = f"{name}-config"[:63].rstrip("-")
            trace = args.manifest.parent / str(tenant["trace_file"])
            kubectl(args.namespace, ["delete", "job", name, "--ignore-not-found=true"])
            kubectl(args.namespace, [
                "delete", "configmap", configmap, "--ignore-not-found=true",
            ])
            kubectl(args.namespace, [
                "create", "configmap", configmap,
                f"--from-file=trace.jsonl={trace}",
                f"--from-file=guidellm_fixed_start.py={wrapper}",
            ])
            document = job_document(
                args.namespace,
                name,
                configmap,
                fixed_epoch,
                backend_config(args.endpoint, args.model, tenant, http2=False),
                active_deadline,
                worker_processes=args.worker_processes,
                drain_after_done=True,
                recover_multiline_sse=True,
            )
            container = document["spec"]["template"]["spec"]["containers"][0]
            container["resources"] = {
                "requests": {"cpu": "500m", "memory": "2Gi"},
                "limits": {"cpu": "4", "memory": "8Gi"},
            }
            (resources_dir / f"{name}.json").write_text(
                json.dumps(document, indent=2) + "\n"
            )
            kubectl(args.namespace, ["apply", "-f", "-"], input_text=json.dumps(document))
            resources.append((name, configmap, tenant))

        for name, _configmap, _tenant in resources:
            remaining = fixed_epoch - time.time()
            if remaining <= 0:
                raise RuntimeError("fixed GuideLLM start passed before all pods were ready")
            result = kubectl(args.namespace, [
                "wait", "pod", "-l", f"job-name={name}",
                "--for=condition=Ready", f"--timeout={math.ceil(remaining)}s",
            ], check=False)
            if result.returncode:
                raise RuntimeError(f"GuideLLM pod did not become ready: {name}")
            pods[name] = kubectl(args.namespace, [
                "get", "pod", "-l", f"job-name={name}",
                "-o", "jsonpath={.items[0].metadata.name}",
            ]).stdout.strip()
        if fixed_epoch - time.time() < 5:
            raise RuntimeError("less than five seconds remain before the synchronized start")

        runtime = {
            "manifest": str(args.manifest),
            "scenario": manifest["scenario"],
            "fixed_start_epoch": fixed_epoch,
            "endpoint": args.endpoint,
            "model": args.model,
            "namespace": args.namespace,
            "pods": pods,
        }
        (resources_dir / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
        deadline = time.monotonic() + active_deadline - 120
        pending = set(pods)
        while pending and time.monotonic() < deadline:
            for name in list(pending):
                done = kubectl(args.namespace, [
                    "exec", pods[name], "-c", "guidellm", "--",
                    "test", "-f", "/tmp/done",
                ], check=False)
                if done.returncode == 0:
                    code = kubectl(args.namespace, [
                        "exec", pods[name], "-c", "guidellm", "--",
                        "cat", "/tmp/exit-code",
                    ]).stdout.strip()
                    if code != "0":
                        raise RuntimeError(f"GuideLLM failed: {name}, exit {code}")
                    pending.remove(name)
            if pending:
                time.sleep(5)
        if pending:
            raise RuntimeError("GuideLLM jobs did not drain: " + ", ".join(sorted(pending)))

        for name, _configmap, tenant in resources:
            fairness_id = str(tenant["fairness_id"])
            pod = pods[name]
            logs = kubectl(args.namespace, ["logs", pod, "-c", "guidellm"], check=False)
            (raw_dir / f"{fairness_id}.log").write_text(logs.stdout + logs.stderr)
            copy_from_pod(args.namespace, pod, "/tmp/raw.json", raw_dir / f"{fairness_id}.json")
            kubectl(args.namespace, [
                "exec", pod, "-c", "guidellm", "--", "touch", "/tmp/collected",
            ])
            completed = kubectl(args.namespace, [
                "wait", f"job/{name}", "--for=condition=complete", "--timeout=60s",
            ], check=False)
            if completed.returncode:
                raise RuntimeError(f"GuideLLM job did not finish cleanly: {name}")
        return 0
    finally:
        for name, configmap, _tenant in resources:
            kubectl(args.namespace, [
                "delete", "job", name, "--ignore-not-found=true", "--wait=false",
            ], check=False)
            kubectl(args.namespace, [
                "delete", "configmap", configmap, "--ignore-not-found=true",
            ], check=False)


if __name__ == "__main__":
    raise SystemExit(main())

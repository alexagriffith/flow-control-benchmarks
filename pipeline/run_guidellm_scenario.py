#!/usr/bin/env python3
"""Run synchronized GuideLLM tenant traces with continuous metric capture."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from guidellm_k8s import (
    GUIDELLM_IMAGE,
    backend_config,
    command,
    job_document,
    kubectl,
    resource_name,
)
from guidellm_scenario_to_run import convert_scenario

METRIC_ARTIFACTS = (
    "pre_epp.prom",
    "pre_vllm.prom",
    "pre_envoy.prom",
    "metric_preflight.json",
    "metric_samples_long.csv",
    "post_epp.prom",
    "post_vllm.prom",
    "post_envoy.prom",
    "metric_capture_health.json",
)


def stream_copy_from_pod(
    namespace: str, pod: str, remote_path: str, local_path: Path,
    attempts: int = 3,
) -> None:
    """Copy a compressed artifact without buffering it in kubectl or tar."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = local_path.with_name(local_path.name + ".partial")
    compressed = local_path.with_name(local_path.name + ".partial.gz")
    detail = ""
    for attempt in range(1, attempts + 1):
        compressed.unlink(missing_ok=True)
        with compressed.open("wb") as handle:
            completed = subprocess.run(
                ["kubectl", "-n", namespace, "exec", pod, "--", "gzip", "-c", remote_path],
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode == 0:
            try:
                with gzip.open(compressed, "rb") as source, temporary.open("wb") as target:
                    shutil.copyfileobj(source, target)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                compressed.unlink(missing_ok=True)
                detail = f"decompression failed: {type(exc).__name__}: {exc}"
            else:
                compressed.unlink(missing_ok=True)
                temporary.replace(local_path)
                return
        else:
            detail = completed.stderr.decode(errors="replace").strip()
        if attempt < attempts:
            time.sleep(attempt * 2)
    temporary.unlink(missing_ok=True)
    compressed.unlink(missing_ok=True)
    raise RuntimeError(f"artifact stream copy failed for {remote_path}: {detail}")


def preserve_metric_artifacts(
    namespace: str,
    runner_pod: str,
    remote_metrics: str,
    metrics_dir: Path,
) -> dict[str, list[str]]:
    """Copy every available metric artifact before remote cleanup."""
    preserved: list[str] = []
    missing: list[str] = []
    errors: list[str] = []
    for filename in METRIC_ARTIFACTS:
        remote_file = f"{remote_metrics}/{filename}"
        present = kubectl(namespace, [
            "exec", runner_pod, "--", "test", "-f", remote_file,
        ], check=False)
        if present.returncode != 0:
            missing.append(filename)
            continue
        try:
            stream_copy_from_pod(
                namespace, runner_pod, remote_file, metrics_dir / filename
            )
            preserved.append(filename)
        except Exception as exc:
            errors.append(f"{filename}: {type(exc).__name__}: {exc}")
    return {"preserved": preserved, "missing": missing, "errors": errors}


def preserve_client_artifacts(
    namespace: str,
    pods: dict[str, str],
    resources: list[tuple[str, str, dict[str, Any]]],
    raw_dir: Path,
) -> dict[str, list[str]]:
    """Copy every available client result and log without masking the first failure."""
    preserved: list[str] = []
    missing: list[str] = []
    errors: list[str] = []
    for name, _configmap, tenant in resources:
        pod = pods.get(name)
        tenant_id = str(tenant["fairness_id"])
        if not pod:
            missing.append(f"{tenant_id}.json")
            continue
        try:
            logs = kubectl(namespace, ["logs", pod], check=False)
            (raw_dir / f"{tenant_id}.log").write_text(logs.stdout + logs.stderr)
        except Exception as exc:
            errors.append(f"{tenant_id}.log: {type(exc).__name__}: {exc}")
        present = kubectl(namespace, [
            "exec", pod, "--", "test", "-f", "/tmp/raw.json",
        ], check=False)
        if present.returncode != 0:
            missing.append(f"{tenant_id}.json")
            continue
        try:
            stream_copy_from_pod(
                namespace, pod, "/tmp/raw.json", raw_dir / f"{tenant_id}.json"
            )
            preserved.append(f"{tenant_id}.json")
        except Exception as exc:
            errors.append(f"{tenant_id}.json: {type(exc).__name__}: {exc}")
    return {"preserved": preserved, "missing": missing, "errors": errors}


def pods_for_service(namespace: str, service: str) -> list[dict[str, Any]]:
    selector = json.loads(kubectl(namespace, [
        "get", "service", service, "-o", "jsonpath={.spec.selector}",
    ]).stdout)
    label_selector = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
    pods = json.loads(kubectl(namespace, [
        "get", "pods", "-l", label_selector, "-o", "json",
    ]).stdout)["items"]
    ready = [
        pod for pod in pods
        if pod.get("status", {}).get("phase") == "Running"
        and any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
    ]
    return sorted(ready, key=lambda pod: str(pod["metadata"]["name"]))


def pod_for_service(namespace: str, service: str) -> dict[str, Any]:
    running = pods_for_service(namespace, service)
    if len(running) != 1:
        raise RuntimeError(
            f"expected one running pod for service {service}, found {len(running)}"
        )
    return running[0]


def vllm_metrics_targets(
    model_pods: list[dict[str, Any]], port: int,
) -> list[tuple[str, str]]:
    targets = []
    for pod in model_pods:
        name = str(pod["metadata"]["name"])
        ip = str(pod.get("status", {}).get("podIP") or "")
        if not ip:
            raise RuntimeError(f"model pod has no IP: {name}")
        targets.append((name, f"http://{ip}:{port}/metrics"))
    return targets


def container_identity(pod: dict[str, Any], name: str) -> tuple[str, str]:
    images = {
        str(container["name"]): str(container["image"])
        for container in pod.get("spec", {}).get("containers", [])
    }
    image_ids = {
        str(status["name"]): str(status["imageID"])
        for status in pod.get("status", {}).get("containerStatuses", [])
    }
    if name not in images or name not in image_ids:
        raise RuntimeError(f"container identity missing for {name}")
    return images[name], image_ids[name]


def container_health_evidence(pod: dict[str, Any], name: str) -> dict[str, Any]:
    spec = next(
        item for item in pod.get("spec", {}).get("containers", [])
        if item.get("name") == name
    )
    status = next(
        item for item in pod.get("status", {}).get("containerStatuses", [])
        if item.get("name") == name
    )
    terminated = (status.get("lastState") or {}).get("terminated") or {}
    return {
        "pod": pod["metadata"]["name"],
        "pod_created_at": pod["metadata"].get("creationTimestamp"),
        "ready": bool(status.get("ready")),
        "restart_count": int(status.get("restartCount", 0)),
        "last_termination_reason": terminated.get("reason"),
        "last_exit_code": terminated.get("exitCode"),
        "last_finished_at": terminated.get("finishedAt"),
        "resources": spec.get("resources") or {},
        "args": spec.get("args") or [],
    }


def parse_kubernetes_memory(value: str) -> int:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGTPE]i?|)", value)
    if not match:
        raise ValueError(f"unsupported Kubernetes memory quantity: {value}")
    amount = float(match.group(1))
    unit = match.group(2)
    if not unit:
        return int(amount)
    power = "KMGTPE".index(unit[0]) + 1
    base = 1024 if unit.endswith("i") else 1000
    return int(amount * (base ** power))


def endpoint_picker_health_transition(
    before: dict[str, Any], after: dict[str, Any],
) -> dict[str, Any]:
    valid = bool(
        before.get("pod") == after.get("pod")
        and after.get("ready") is True
        and before.get("restart_count") == after.get("restart_count")
    )
    return {
        "valid": valid,
        "same_pod": before.get("pod") == after.get("pod"),
        "ready_after": after.get("ready") is True,
        "restart_count_before": before.get("restart_count"),
        "restart_count_after": after.get("restart_count"),
        "last_termination_reason": after.get("last_termination_reason"),
    }


def runtime_proof(epp_config: str, vllm_args: str) -> dict[str, Any]:
    detector = next((
        name for name in (
            "concurrency-detector", "utilization-detector", "queue-depth",
            "kv-cache-utilization",
        )
        if name in epp_config
    ), None)
    prefix_cache_off = "--no-enable-prefix-caching" in vllm_args
    prefix_cache_on = bool(re.search(r"(?:^|\s)--enable-prefix-caching(?:\s|$)", vllm_args))
    picker = next((
        name for name in ("random-picker", "max-score-picker", "weighted-random-picker")
        if re.search(rf"type:\s*{name}(?:\s|$)", epp_config)
    ), None)
    token_producer_declared = bool(re.search(
        r"type:\s*(?:token-producer|tokenizer)(?:\s|$)", epp_config
    ))
    token_producer_vllm = bool(re.search(r"(?m)^\s+vllm:\s*$", epp_config))
    return {
        "flow_control_gate": "flowControl" in epp_config,
        "detector": detector,
        "priority_bands": all(
            re.search(rf"priority:\s*{priority}(?:\s|$)", epp_config)
            for priority in (100, 50, 0, -10)
        ),
        "prefix_cache_off": prefix_cache_off,
        "prefix_cache_on": prefix_cache_on,
        "prefix_cache_mode": (
            "invalid" if prefix_cache_off and prefix_cache_on
            else "off" if prefix_cache_off
            else "on" if prefix_cache_on
            else "unspecified"
        ),
        "max_num_seqs_128": "--max-num-seqs 128" in vllm_args,
        "picker": picker,
        "prefix_cache_scorer": bool(re.search(
            r"type:\s*prefix-cache-scorer(?:\s|$)", epp_config
        )),
        "prefix_auto_tune": match.group(1) == "true" if (
            match := re.search(r"autoTune:\s*(true|false)", epp_config)
        ) else None,
        "prefix_block_size_tokens": int(match.group(1)) if (
            match := re.search(r"blockSizeTokens:\s*([0-9]+)", epp_config)
        ) else None,
        "prefix_max_tokens_to_match": int(match.group(1)) if (
            match := re.search(r"maxPrefixTokensToMatch:\s*([0-9]+)", epp_config)
        ) else None,
        "prefix_lru_capacity_per_server": int(match.group(1)) if (
            match := re.search(r"lruCapacityPerServer:\s*([0-9]+)", epp_config)
        ) else None,
        "queue_depth_threshold": int(match.group(1)) if (
            match := re.search(r"queueDepthThreshold:\s*([0-9]+)", epp_config)
        ) else None,
        "max_concurrency": int(match.group(1)) if (
            match := re.search(r"maxConcurrency:\s*([0-9]+)", epp_config)
        ) else None,
        "concurrency_mode": match.group(1) if (
            match := re.search(r"concurrencyMode:\s*([a-z]+)", epp_config)
        ) else None,
        "max_token_concurrency": int(match.group(1)) if (
            match := re.search(r"maxTokenConcurrency:\s*([0-9]+)", epp_config)
        ) else None,
        "add_estimated_output_tokens": match.group(1) == "true" if (
            match := re.search(r"addEstimatedOutputTokens:\s*(true|false)", epp_config)
        ) else None,
        "headroom": float(match.group(1)) if (
            match := re.search(r"headroom:\s*([0-9.]+)", epp_config)
        ) else None,
        "token_producer_backend": (
            "vllm" if token_producer_declared and token_producer_vllm
            else "estimate" if token_producer_declared
            else "auto-estimate"
        ),
        "token_producer_model": match.group(1) if (
            match := re.search(r"modelName:\s*[\"']?([^\s\"']+)", epp_config)
        ) else None,
        "token_producer_url": match.group(1) if (
            match := re.search(r"url:\s*[\"']?([^\s\"']+)", epp_config)
        ) else None,
    }


def process_config_proof(config: dict[str, Any]) -> dict[str, Any]:
    flow_control = config.get("flowControl") or {}
    detector = (flow_control.get("saturationDetector") or {}).get("pluginRef")
    plugin = next((
        item for item in config.get("plugins", [])
        if item.get("name") == detector
    ), {})
    parameters = plugin.get("parameters") or {}
    producer_name = parameters.get("inFlightLoadProducerName")
    producer = next((
        item for item in config.get("plugins", [])
        if item.get("name") == producer_name
    ), {})
    producer_parameters = producer.get("parameters") or {}
    prefix_producer = next((
        item for item in config.get("plugins", [])
        if item.get("type") == "approx-prefix-cache-producer"
    ), {})
    prefix_parameters = prefix_producer.get("parameters") or {}
    priorities = {
        item.get("priority") for item in flow_control.get("priorityBands", [])
    }
    plugin_types = {
        str(item.get("name") or item.get("type")): str(item.get("type"))
        for item in config.get("plugins", [])
    }
    picker = next((
        plugin_type for plugin_type in (
            "random-picker", "max-score-picker", "weighted-random-picker"
        )
        if plugin_type in plugin_types.values()
    ), None)
    token_producer = next((
        item for item in config.get("plugins", [])
        if item.get("type") in ("token-producer", "tokenizer")
    ), {})
    token_parameters = token_producer.get("parameters") or {}
    token_vllm = token_parameters.get("vllm") or {}
    return {
        "flow_control_gate": "flowControl" in config.get("featureGates", []),
        "detector": detector,
        "priority_bands": priorities == {100, 50, 0, -10},
        "queue_depth_threshold": parameters.get("queueDepthThreshold"),
        "max_concurrency": parameters.get("maxConcurrency"),
        "concurrency_mode": parameters.get("concurrencyMode"),
        "max_token_concurrency": parameters.get("maxTokenConcurrency"),
        "add_estimated_output_tokens": producer_parameters.get(
            "addEstimatedOutputTokens"
        ),
        "headroom": parameters.get("headroom"),
        "picker": picker,
        "prefix_cache_scorer": "prefix-cache-scorer" in plugin_types.values(),
        "prefix_auto_tune": prefix_parameters.get("autoTune"),
        "prefix_block_size_tokens": prefix_parameters.get("blockSizeTokens"),
        "prefix_max_tokens_to_match": prefix_parameters.get(
            "maxPrefixTokensToMatch"
        ),
        "prefix_lru_capacity_per_server": prefix_parameters.get(
            "lruCapacityPerServer"
        ),
        "token_producer_backend": (
            "vllm" if token_vllm
            else "estimate" if token_producer
            else "auto-estimate"
        ),
        "token_producer_model": token_parameters.get("modelName"),
        "token_producer_url": token_vllm.get("url"),
    }


def loaded_epp_process_config(namespace: str, pod: str) -> dict[str, Any]:
    """Read the config the EPP logged at process startup, not its mounted file."""
    process = subprocess.Popen(
        ["kubectl", "-n", namespace, "logs", pod, "-c", "epp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    loaded: dict[str, Any] | None = None
    try:
        assert process.stdout is not None
        for line_number, line in enumerate(process.stdout, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("msg") == "Raw config after phase one" and isinstance(
                record.get("config"), dict
            ):
                loaded = record["config"]
                break
            if line_number >= 2_000:
                break
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if loaded is None:
        raise RuntimeError(f"EPP startup configuration not found in logs for {pod}")
    return loaded


def apply_epp_config(args: argparse.Namespace) -> None:
    if not args.apply_epp_config:
        return
    config = args.apply_epp_config.resolve().read_text()
    kubectl(args.namespace, [
        "patch", "configmap", args.epp_configmap, "--type=merge", "-p",
        json.dumps({"data": {"epp-config.yaml": config}}),
    ])
    kubectl(args.namespace, ["rollout", "restart", f"deployment/{args.epp_deployment}"])
    kubectl(args.namespace, [
        "rollout", "status", f"deployment/{args.epp_deployment}", "--timeout=300s",
    ])


def warmup(
    namespace: str, runner_pod: str, endpoint: str, model: str,
    tenants: list[dict[str, Any]], shared_prefix_fraction: float | None = None,
    shared_prefix_group: str = "shared", shared_prefix_group_mode: str = "shared",
    remote_wrapper: str | None = None,
) -> dict[str, Any]:
    script = """
import importlib.util,json,sys,urllib.request
endpoint,model,objective,fairness_id,input_tokens,output_tokens,fraction,group,wrapper=sys.argv[1:]
prompt=" ".join("x" for _ in range(int(input_tokens)))
if fraction:
 spec=importlib.util.spec_from_file_location("guidellm_fixed_start",wrapper)
 module=importlib.util.module_from_spec(spec)
 spec.loader.exec_module(module)
 prompt=module.shared_prefix_prompt(prompt,"shape-preflight-"+fairness_id,float(fraction),group)
body=json.dumps({"model":model,"prompt":prompt,"max_tokens":int(output_tokens),"stream":False}).encode()
request=urllib.request.Request(endpoint+"/v1/completions",data=body,headers={
 "content-type":"application/json","x-llm-d-inference-fairness-id":"warmup-"+fairness_id,
 "x-llm-d-inference-objective":objective,"x-gateway-inference-fairness-id":"warmup",
 "x-gateway-inference-objective":objective})
with urllib.request.urlopen(request,timeout=300) as response:
 assert response.status==200
 result=json.loads(response.read())
 print(json.dumps({"status":response.status,"prompt_tokens":result["usage"]["prompt_tokens"]}))
"""
    evidence = []
    for tenant in tenants:
        input_tokens = int(tenant["input_tokens"])
        output_tokens = int(tenant["output_tokens"])
        fairness_id = str(tenant["fairness_id"])
        objective = str(tenant["objective"])
        group = (
            f"{shared_prefix_group}-{fairness_id}"
            if shared_prefix_group_mode == "tenant" else shared_prefix_group
        )
        completed = kubectl(namespace, [
            "exec", runner_pod, "--", "python", "-c", script,
            endpoint, model, objective, fairness_id, str(input_tokens),
            str(output_tokens),
            str(shared_prefix_fraction) if shared_prefix_fraction is not None else "",
            group, remote_wrapper or "",
        ])
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        observed = int(result["prompt_tokens"])
        relative_error = abs(observed - input_tokens) / input_tokens
        item = {
            "tenant": fairness_id,
            "expected_input_tokens": input_tokens,
            "observed_prompt_tokens": observed,
            "relative_error": relative_error,
            "valid": relative_error <= 0.10,
        }
        evidence.append(item)
    return {
        "valid": bool(evidence and all(item["valid"] for item in evidence)),
        "relative_tolerance": 0.10,
        "tenants": evidence,
    }


def merge_route_evidence(
    preconditions: dict[str, Any], route: dict[str, Any],
) -> dict[str, Any]:
    prior_data_quality_valid = bool(preconditions.setdefault(
        "pre_route_data_quality_valid", preconditions.get("data_quality_valid")
    ))
    prior_data_quality_reason = preconditions.setdefault(
        "pre_route_data_quality_reason", preconditions.get("data_quality_reason")
    )
    prior_slo_proof_valid = bool(preconditions.setdefault(
        "pre_route_slo_proof_valid", preconditions.get("slo_proof_valid")
    ))
    prior_slo_proof_reason = preconditions.setdefault(
        "pre_route_slo_proof_reason", preconditions.get("slo_proof_reason")
    )
    transport_complete = (
        route.get(
            "all_client_requests_observed_at_gateway",
            route.get("count_matches") is True
            and route.get("direct_vllm_bypass_detected") is False,
        ) is True
        and (route.get("model_route_validation") or {"valid": True})["valid"] is True
    )
    preconditions["route_evidence"] = route
    preconditions["data_quality_valid"] = bool(
        prior_data_quality_valid and transport_complete
    )
    if preconditions["data_quality_valid"]:
        preconditions["data_quality_reason"] = "valid"
    elif not prior_data_quality_valid:
        preconditions["data_quality_reason"] = (
            prior_data_quality_reason or "proof checks failed"
        )
    else:
        preconditions["data_quality_reason"] = "route evidence failed"
    preconditions["slo_proof_valid"] = bool(
        prior_slo_proof_valid and route.get("valid")
    )
    if preconditions["slo_proof_valid"]:
        preconditions["slo_proof_reason"] = "valid"
    elif route.get("valid") is False:
        preconditions["slo_proof_reason"] = "client and gateway outcomes differ"
    else:
        preconditions["slo_proof_reason"] = (
            prior_slo_proof_reason or "proof checks failed"
        )
    return preconditions


def validate_model_routes(
    route: dict[str, Any], model_pods: list[dict[str, Any]], require_every_pod: bool,
) -> dict[str, Any]:
    expected = {str(item["pod_ip"]) for item in model_pods}
    counts = route.get("destination_counts") or route.get("upstream_counts") or {}
    observed = {str(value).rsplit(":", 1)[0] for value in counts}
    unexpected = sorted(observed - expected)
    missing = sorted(expected - observed)
    return {
        "valid": bool(observed) and not unexpected and (not require_every_pod or not missing),
        "expected_pod_ips": sorted(expected),
        "observed_pod_ips": sorted(observed),
        "unexpected_pod_ips": unexpected,
        "model_pods_without_requests": missing,
        "require_every_pod": require_every_pod,
    }


def capture_route_evidence(
    args: argparse.Namespace, run_dir: Path, result_dir: Path,
    envoy_log_file: Path | None = None,
) -> None:
    context_dir = run_dir / "run-context"
    capture_script = Path(__file__).with_name("capture_run_context.py").resolve()
    command_args = [
        sys.executable, str(capture_script),
        "--namespace", args.namespace,
        "--experiment", args.resource_experiment or args.prefix,
        "--epp-deployment", args.epp_deployment,
        "--preconditions", str(result_dir / "preconditions.json"),
        "--out-dir", str(context_dir),
    ]
    if envoy_log_file is not None:
        command_args.extend(["--envoy-log-file", str(envoy_log_file)])
    captured = command(command_args, check=False)
    route_path = context_dir / "route_evidence.json"
    if not route_path.is_file():
        raise RuntimeError(
            "route evidence capture failed: " + (captured.stderr or captured.stdout)
        )
    route = json.loads(route_path.read_text())
    runtime = json.loads((run_dir / "runtime_preflight.json").read_text())
    route["model_route_validation"] = validate_model_routes(
        route, runtime["model_pods"], runtime["model_replicas"] > 1
    )
    route["valid"] = bool(
        route.get("valid") and route["model_route_validation"]["valid"]
    )
    route_path.write_text(json.dumps(route, indent=2) + "\n")
    preconditions_path = result_dir / "preconditions.json"
    preconditions = json.loads(preconditions_path.read_text())
    preconditions = merge_route_evidence(preconditions, route)
    preconditions_path.write_text(json.dumps(preconditions, indent=2) + "\n")


def prepare_run_directory(run_dir: Path) -> None:
    if run_dir.exists():
        unexpected = [path for path in run_dir.iterdir() if path.name != "README.md"]
        if unexpected:
            raise ValueError(f"run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)


def tenant_fixed_epoch(base_epoch: float, tenant: dict[str, Any]) -> float:
    """Preserve a tenant's initial idle period after GuideLLM normalizes its trace."""
    return base_epoch + float(tenant.get("first_arrival_s", 0.0))


def run(args: argparse.Namespace) -> Path:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    tenants = manifest["tenants"]
    scenario = str(manifest["scenario"])
    run_dir = args.run_dir.resolve()
    prepare_run_directory(run_dir)
    raw_dir = run_dir / "raw-input"
    metrics_dir = run_dir / "metrics-input"
    raw_dir.mkdir()
    metrics_dir.mkdir()

    apply_epp_config(args)
    epp_config = kubectl(args.namespace, [
        "get", "configmap", args.epp_configmap,
        "-o", "jsonpath={.data.epp-config\\.yaml}",
    ]).stdout
    vllm_args = kubectl(args.namespace, [
        "get", "configmap", args.vllm_args_configmap,
        "-o", "jsonpath={.data.args}",
    ]).stdout
    proof = runtime_proof(epp_config, vllm_args)
    if not all((
        proof["flow_control_gate"], proof["detector"], proof["priority_bands"],
        proof["prefix_cache_mode"] == args.expected_prefix_cache,
        proof["max_num_seqs_128"],
    )):
        raise RuntimeError(f"runtime preflight failed: {proof}")
    if args.expected_detector and proof["detector"] != args.expected_detector:
        raise RuntimeError(f"expected detector {args.expected_detector}, found {proof['detector']}")
    if args.expected_queue_depth is not None and proof["queue_depth_threshold"] != args.expected_queue_depth:
        raise RuntimeError(
            f"expected queue depth {args.expected_queue_depth}, found {proof['queue_depth_threshold']}"
        )
    if args.expected_max_concurrency is not None and proof["max_concurrency"] != args.expected_max_concurrency:
        raise RuntimeError(
            f"expected max concurrency {args.expected_max_concurrency}, found {proof['max_concurrency']}"
        )
    if args.expected_concurrency_mode and proof["concurrency_mode"] != args.expected_concurrency_mode:
        raise RuntimeError(
            f"expected concurrency mode {args.expected_concurrency_mode}, "
            f"found {proof['concurrency_mode']}"
        )
    if (
        args.expected_max_token_concurrency is not None
        and proof["max_token_concurrency"] != args.expected_max_token_concurrency
    ):
        raise RuntimeError(
            f"expected max token concurrency {args.expected_max_token_concurrency}, "
            f"found {proof['max_token_concurrency']}"
        )
    if args.expected_add_estimated_output_tokens is not None:
        expected_output = args.expected_add_estimated_output_tokens == "true"
        if proof["add_estimated_output_tokens"] != expected_output:
            raise RuntimeError(
                f"expected addEstimatedOutputTokens {expected_output}, "
                f"found {proof['add_estimated_output_tokens']}"
            )
    if args.expected_headroom is not None and proof["headroom"] != args.expected_headroom:
        raise RuntimeError(f"expected headroom {args.expected_headroom}, found {proof['headroom']}")
    if args.expected_picker and proof["picker"] != args.expected_picker:
        raise RuntimeError(f"expected picker {args.expected_picker}, found {proof['picker']}")
    token_expectations = {
        "token_producer_backend": args.expected_token_producer_backend,
        "token_producer_model": args.expected_token_producer_model,
        "token_producer_url": args.expected_token_producer_url,
    }
    for field, expected in token_expectations.items():
        if expected is not None and proof[field] != expected:
            raise RuntimeError(f"expected {field} {expected}, found {proof[field]}")
    if args.expected_prefix_cache == "on" and not proof["prefix_cache_scorer"]:
        raise RuntimeError("cache-on runs require prefix-cache-scorer")
    prefix_expectations = {
        "prefix_auto_tune": (
            args.expected_prefix_auto_tune == "true"
            if args.expected_prefix_auto_tune is not None else None
        ),
        "prefix_block_size_tokens": args.expected_prefix_block_size_tokens,
        "prefix_max_tokens_to_match": args.expected_prefix_max_tokens_to_match,
        "prefix_lru_capacity_per_server": args.expected_prefix_lru_capacity_per_server,
    }
    for field, expected in prefix_expectations.items():
        if expected is not None and proof[field] != expected:
            raise RuntimeError(f"expected {field} {expected}, found {proof[field]}")
    proof["epp_configmap"] = args.epp_configmap
    proof["vllm_args_configmap"] = args.vllm_args_configmap
    proof["guidellm_image"] = GUIDELLM_IMAGE
    proof["guidellm_http_version"] = args.http_version
    proof["guidellm_worker_processes_per_tenant"] = args.guidellm_worker_processes
    proof["guidellm_mp_poll_interval_s"] = args.guidellm_mp_poll_interval_s
    proof["guidellm_drain_after_done"] = args.drain_after_done
    proof["guidellm_connection_close"] = args.connection_close
    proof["guidellm_recover_multiline_sse"] = args.recover_multiline_sse
    proof["shared_prefix_fraction"] = args.shared_prefix_fraction
    proof["shared_prefix_group"] = args.shared_prefix_group
    proof["shared_prefix_group_mode"] = args.shared_prefix_group_mode
    epp_pod = pod_for_service(args.namespace, args.epp_service)
    model_pods = pods_for_service(args.namespace, args.model_service)
    if len(model_pods) != args.expected_model_replicas:
        raise RuntimeError(
            f"expected {args.expected_model_replicas} ready model pods, found {len(model_pods)}"
        )
    process_proof = process_config_proof(loaded_epp_process_config(
        args.namespace, str(epp_pod["metadata"]["name"])
    ))
    compared_fields = (
        "flow_control_gate", "detector", "priority_bands",
        "queue_depth_threshold", "max_concurrency", "concurrency_mode",
        "max_token_concurrency", "add_estimated_output_tokens", "headroom",
        "picker", "prefix_cache_scorer",
        "prefix_auto_tune", "prefix_block_size_tokens",
        "prefix_max_tokens_to_match", "prefix_lru_capacity_per_server",
    )
    proof["endpoint_picker_pod"] = epp_pod["metadata"]["name"]
    proof["endpoint_picker_pod_created_at"] = epp_pod["metadata"]["creationTimestamp"]
    proof["endpoint_picker_health_pre"] = container_health_evidence(epp_pod, "epp")
    memory_limit = proof["endpoint_picker_health_pre"].get("resources", {}).get(
        "limits", {}
    ).get("memory")
    if not memory_limit:
        raise RuntimeError("Endpoint Picker memory limit is required")
    proof["endpoint_picker_memory_limit_bytes"] = parse_kubernetes_memory(memory_limit)
    if args.expected_epp_memory_limit is not None:
        expected_memory_limit = parse_kubernetes_memory(args.expected_epp_memory_limit)
        if proof["endpoint_picker_memory_limit_bytes"] != expected_memory_limit:
            raise RuntimeError(
                "expected Endpoint Picker memory limit "
                f"{args.expected_epp_memory_limit}, found {memory_limit}"
            )
        proof["expected_endpoint_picker_memory_limit"] = args.expected_epp_memory_limit
    proof["endpoint_picker_max_memory_fraction"] = args.max_epp_memory_fraction
    proof["loaded_process_config"] = process_proof
    proof["process_config_matches_configmap"] = all(
        proof[field] == process_proof[field] for field in compared_fields
    )
    if not proof["process_config_matches_configmap"]:
        raise RuntimeError(
            "running EPP config does not match ConfigMap: "
            f"configmap={proof}, process={process_proof}"
        )
    for field, expected in token_expectations.items():
        if expected is not None and process_proof[field] != expected:
            raise RuntimeError(
                f"running EPP expected {field} {expected}, found {process_proof[field]}"
            )
    proof["endpoint_picker_image"], proof["endpoint_picker_image_id"] = (
        container_identity(epp_pod, "epp")
    )
    proof["model_replicas"] = len(model_pods)
    proof["model_pods"] = []
    for model_pod in model_pods:
        image, image_id = container_identity(model_pod, "main")
        proof["model_pods"].append({
            "name": model_pod["metadata"]["name"],
            "created_at": model_pod["metadata"]["creationTimestamp"],
            "pod_ip": model_pod["status"]["podIP"],
            "image": image,
            "image_id": image_id,
        })
    proof["model_image"] = proof["model_pods"][0]["image"]
    proof["model_image_id"] = proof["model_pods"][0]["image_id"]
    if len({item["image_id"] for item in proof["model_pods"]}) != 1:
        raise RuntimeError("model replicas do not use the same image digest")
    proof["upstream_v0_9_0_image"] = "llm-d-router-endpoint-picker:v0.9.0" in proof[
        "endpoint_picker_image"
    ]
    if not proof["upstream_v0_9_0_image"]:
        raise RuntimeError(f"unexpected Endpoint Picker image: {proof['endpoint_picker_image']}")
    (run_dir / "runtime_preflight.json").write_text(json.dumps(proof, indent=2) + "\n")
    (run_dir / "epp-config.yaml").write_text(epp_config)
    (run_dir / "vllm-args.txt").write_text(vllm_args + "\n")

    metrics_script = Path(__file__).with_name("metrics_capture.py").resolve()
    wrapper = Path(__file__).with_name("guidellm_fixed_start.py").resolve()
    remote_script = f"/tmp/{args.prefix}-metrics_capture.py"
    remote_wrapper = f"/tmp/{args.prefix}-guidellm_fixed_start.py"
    remote_metrics = f"/tmp/{args.prefix}-metrics"
    remote_stop = f"/tmp/{args.prefix}-stop"
    model_metric_targets = vllm_metrics_targets(model_pods, args.vllm_metrics_port)
    vllm_metric_args = [
        value
        for name, url in model_metric_targets
        for value in ("--vllm-url", f"{name}={url}")
    ]
    cache_metric_args = (
        ["--require-prefix-cache"] if args.expected_prefix_cache == "on" else []
    )
    epp_memory_args = [
        "--epp-memory-limit-bytes", str(proof["endpoint_picker_memory_limit_bytes"]),
        "--max-epp-memory-fraction", str(args.max_epp_memory_fraction),
    ]
    kubectl(args.namespace, ["cp", str(metrics_script), f"{args.runner_pod}:{remote_script}"])
    kubectl(args.namespace, ["cp", str(wrapper), f"{args.runner_pod}:{remote_wrapper}"])
    kubectl(args.namespace, [
        "exec", args.runner_pod, "--", "rm", "-rf", remote_metrics, remote_stop,
    ])
    def run_metric_preflight(extra: list[str]) -> subprocess.CompletedProcess[str]:
        return kubectl(args.namespace, [
            "exec", args.runner_pod, "--", "python", remote_script,
            "--epp-url", args.epp_metrics_url, *vllm_metric_args,
            "--epp-plugin-state-url", args.epp_plugin_state_url,
            "--envoy-url", args.envoy_metrics_url,
            "--envoy-cluster-name", args.envoy_cluster_name,
            "--expected-envoy-remaining-requests", str(args.expected_envoy_remaining_requests),
            *epp_memory_args,
            "--run-id", args.prefix, "--scenario", scenario, "--out-dir", remote_metrics,
            "--require-flow-control", "--require-envoy", *extra,
            "--preflight-only",
        ], check=False)

    preflight = run_metric_preflight([])
    if preflight.returncode != 0:
        raise RuntimeError(f"base metric preflight failed: {preflight.stderr or preflight.stdout}")

    proof["request_shape_preflight"] = warmup(
        args.namespace, args.runner_pod, args.endpoint, args.model, tenants,
        args.shared_prefix_fraction, args.shared_prefix_group,
        args.shared_prefix_group_mode, remote_wrapper,
    )
    (run_dir / "runtime_preflight.json").write_text(json.dumps(proof, indent=2) + "\n")
    if not proof["request_shape_preflight"]["valid"]:
        raise RuntimeError(
            "request-shape preflight failed: "
            f"{proof['request_shape_preflight']}"
        )
    if cache_metric_args:
        preflight = run_metric_preflight(cache_metric_args)
        if preflight.returncode != 0:
            raise RuntimeError(
                "cache metric preflight failed after warmup: "
                f"{preflight.stderr or preflight.stdout}"
            )
    context_dir = run_dir / "run-context"
    context_dir.mkdir(parents=True, exist_ok=True)
    envoy_log_path = context_dir / "envoy-access.log"
    envoy_log_error_path = context_dir / "envoy-access-capture.log"
    envoy_log_handle = envoy_log_path.open("w")
    envoy_log_error_handle = envoy_log_error_path.open("w")
    envoy_log_process = subprocess.Popen([
        "kubectl", "-n", args.namespace, "logs",
        str(epp_pod["metadata"]["name"]), "-c", "envoy", "-f", "--since=5s",
    ], stdout=envoy_log_handle, stderr=envoy_log_error_handle, text=True)

    fixed_epoch = time.time() + args.start_delay_s
    max_capture_s = int(args.start_delay_s + float(manifest["duration_s"]) + args.drain_timeout_s)
    metric_log = (run_dir / "metric-capture.log").open("w")
    metric_process = subprocess.Popen([
        "kubectl", "-n", args.namespace, "exec", args.runner_pod, "--",
        "python", remote_script,
        "--epp-url", args.epp_metrics_url, *vllm_metric_args,
        "--epp-plugin-state-url", args.epp_plugin_state_url,
        "--envoy-url", args.envoy_metrics_url,
        "--envoy-cluster-name", args.envoy_cluster_name,
        "--expected-envoy-remaining-requests", str(args.expected_envoy_remaining_requests),
        *epp_memory_args,
        "--run-id", args.prefix, "--scenario", scenario, "--out-dir", remote_metrics,
        "--duration", str(max_capture_s), "--interval", "1", "--require-flow-control",
        "--require-envoy", *cache_metric_args,
        "--stop-file", remote_stop,
    ], stdout=metric_log, stderr=subprocess.STDOUT, text=True)

    memory_script = Path(__file__).with_name("kubernetes_container_memory.py").resolve()
    memory_stop = run_dir / ".kubernetes-memory-stop"
    memory_stop.unlink(missing_ok=True)
    memory_log = (run_dir / "kubernetes-memory-capture.log").open("w")
    memory_process = subprocess.Popen([
        sys.executable, str(memory_script),
        "--namespace", args.namespace,
        "--pod", str(epp_pod["metadata"]["name"]),
        "--container", "epp",
        "--run-id", args.prefix,
        "--scenario", scenario,
        "--out-dir", str(metrics_dir),
        "--memory-limit-bytes", str(proof["endpoint_picker_memory_limit_bytes"]),
        "--max-memory-fraction", str(args.max_epp_memory_fraction),
        "--duration", str(max_capture_s),
        "--interval", str(args.kubernetes_memory_interval_s),
        "--stop-file", str(memory_stop),
    ], stdout=memory_log, stderr=subprocess.STDOUT, text=True)

    resources: list[tuple[str, str, dict[str, Any]]] = []
    pods: dict[str, str] = {}
    metric_artifact_report: dict[str, list[str]] | None = None
    client_artifact_report: dict[str, list[str]] | None = None
    epp_health_post: dict[str, Any] | None = None
    memory_return: int | None = None
    memory_health: dict[str, Any] | None = None
    try:
        for tenant in tenants:
            tenant_id = str(tenant["fairness_id"])
            name = resource_name(args.prefix, tenant_id)
            configmap = f"{name}-config"[:63].rstrip("-")
            trace = manifest_path.parent / str(tenant["trace_file"])
            kubectl(args.namespace, [
                "create", "configmap", configmap,
                f"--from-file=trace.jsonl={trace}",
                f"--from-file=guidellm_fixed_start.py={wrapper}",
            ])
            document = job_document(
                args.namespace, name, configmap, tenant_fixed_epoch(fixed_epoch, tenant),
                backend_config(
                    args.endpoint, args.model, tenant,
                    http2=args.http_version == "2",
                    connection_close=args.connection_close,
                ),
                args.artifact_retention_s,
                args.guidellm_worker_processes,
                args.drain_after_done,
                args.recover_multiline_sse,
                {
                    "GUIDELLM_SHARED_PREFIX_FRACTION": str(args.shared_prefix_fraction),
                    "GUIDELLM_SHARED_PREFIX_GROUP": (
                        f"{args.shared_prefix_group}-{tenant_id}"
                        if args.shared_prefix_group_mode == "tenant"
                        else args.shared_prefix_group
                    ),
                } if args.shared_prefix_fraction is not None else None,
                poll_interval_s=args.guidellm_mp_poll_interval_s,
            )
            kubectl(args.namespace, ["apply", "-f", "-"], input_text=json.dumps(document))
            resources.append((name, configmap, tenant))

        for name, _configmap, _tenant in resources:
            waited = kubectl(args.namespace, [
                "wait", f"pod", "-l", f"job-name={name}", "--for=condition=Ready",
                f"--timeout={args.start_delay_s}s",
            ], check=False)
            if waited.returncode != 0:
                raise RuntimeError(f"GuideLLM pod did not become ready: {name}")
            pods[name] = kubectl(args.namespace, [
                "get", "pods", "-l", f"job-name={name}",
                "-o", "jsonpath={.items[0].metadata.name}",
            ]).stdout

        pending = set(pods)
        deadline = time.monotonic() + max_capture_s
        while pending and time.monotonic() < deadline:
            for name in list(pending):
                pod = pods[name]
                done = kubectl(args.namespace, [
                    "exec", pod, "--", "test", "-f", "/tmp/done",
                ], check=False)
                if done.returncode == 0:
                    exit_code = kubectl(args.namespace, [
                        "exec", pod, "--", "cat", "/tmp/exit-code",
                    ]).stdout.strip()
                    if exit_code != "0":
                        raise RuntimeError(f"GuideLLM worker failed: {name}, exit {exit_code}")
                    pending.remove(name)
            if pending:
                time.sleep(2)
        if pending:
            raise RuntimeError("GuideLLM workers did not drain: " + ", ".join(sorted(pending)))

        kubectl(args.namespace, ["exec", args.runner_pod, "--", "touch", remote_stop])
        memory_stop.touch()
        metric_return = metric_process.wait(timeout=30)
        memory_return = memory_process.wait(timeout=30)
        metric_log.close()
        memory_log.close()
        memory_health_path = metrics_dir / "kubernetes_container_memory_health.json"
        if memory_health_path.is_file():
            memory_health = json.loads(memory_health_path.read_text())

        metric_artifact_report = preserve_metric_artifacts(
            args.namespace, args.runner_pod, remote_metrics, metrics_dir
        )
        client_artifact_report = preserve_client_artifacts(
            args.namespace, pods, resources, raw_dir
        )
        for pod in pods.values():
            kubectl(args.namespace, ["exec", pod, "--", "touch", "/tmp/collected"])
        for name, _configmap, _tenant in resources:
            waited = kubectl(args.namespace, [
                "wait", f"job/{name}", "--for=condition=complete", "--timeout=30s",
            ], check=False)
            if waited.returncode != 0:
                raise RuntimeError(f"GuideLLM job did not finish cleanly: {name}")
        epp_health_post = container_health_evidence(
            pod_for_service(args.namespace, args.epp_service), "epp"
        )
        epp_health_post["transition"] = endpoint_picker_health_transition(
            proof["endpoint_picker_health_pre"], epp_health_post
        )
        (run_dir / "endpoint-picker-health-post.json").write_text(
            json.dumps(epp_health_post, indent=2) + "\n"
        )
        shutil.copy2(manifest_path, run_dir / "trace_manifest.source.json")
        if (
            metric_return != 0
            or metric_artifact_report["missing"]
            or metric_artifact_report["errors"]
            or client_artifact_report["missing"]
            or client_artifact_report["errors"]
            or not epp_health_post["transition"]["valid"]
            or memory_return != 0
            or not memory_health
            or not memory_health.get("valid")
        ):
            failure = {
                "metric_capture_exit_code": metric_return,
                "metric_artifacts": metric_artifact_report,
                "client_artifacts": client_artifact_report,
                "endpoint_picker_health": epp_health_post,
                "kubernetes_container_memory_exit_code": memory_return,
                "kubernetes_container_memory": memory_health,
            }
            (run_dir / "run_failure.json").write_text(
                json.dumps(failure, indent=2) + "\n"
            )
            raise RuntimeError(f"metric capture failed: {failure}")
        result_dir = convert_scenario(
            manifest_path, raw_dir, metrics_dir, run_dir / "result", args.prefix,
            args.expected_prefix_cache, args.envoy_cluster_name,
        )
        for name in ("runtime_preflight.json", "epp-config.yaml", "vllm-args.txt"):
            shutil.copy2(run_dir / name, result_dir / name)
        for name in (
            "kubernetes_container_memory.csv",
            "kubernetes_container_memory_health.json",
        ):
            shutil.copy2(metrics_dir / name, result_dir / name)
        preconditions_path = result_dir / "preconditions.json"
        preconditions = json.loads(preconditions_path.read_text())
        preconditions["kubernetes_container_memory"] = memory_health
        preconditions["data_quality_valid"] = bool(
            preconditions.get("data_quality_valid") and memory_health.get("valid")
        )
        preconditions_path.write_text(json.dumps(preconditions, indent=2) + "\n")
        summary_path = result_dir / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary.setdefault("runtime_metrics", {}).update({
            "max_epp_container_working_set_bytes": memory_health[
                "peak_working_set_bytes"
            ],
            "max_epp_container_usage_bytes": memory_health["peak_usage_bytes"],
            "max_epp_container_memory_fraction": memory_health[
                "peak_working_set_fraction_of_limit"
            ],
        })
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        if envoy_log_process.poll() is None:
            envoy_log_process.terminate()
            envoy_log_process.wait(timeout=10)
        envoy_log_handle.close()
        envoy_log_error_handle.close()
        capture_route_evidence(args, run_dir, result_dir, envoy_log_path)
        return result_dir
    except Exception as exc:
        failure_path = run_dir / "run_failure.json"
        if not failure_path.exists():
            failure_path.write_text(json.dumps({
                "error_type": type(exc).__name__,
                "error": str(exc),
                "metric_artifacts": metric_artifact_report,
                "client_artifacts": client_artifact_report,
            }, indent=2) + "\n")
        raise
    finally:
        if envoy_log_process.poll() is None:
            envoy_log_process.terminate()
            try:
                envoy_log_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                envoy_log_process.kill()
                envoy_log_process.wait(timeout=5)
        if not envoy_log_handle.closed:
            envoy_log_handle.close()
        if not envoy_log_error_handle.closed:
            envoy_log_error_handle.close()
        if metric_process.poll() is None:
            kubectl(args.namespace, ["exec", args.runner_pod, "--", "touch", remote_stop], check=False)
            try:
                metric_process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                metric_process.terminate()
                metric_process.wait(timeout=10)
        if not metric_log.closed:
            metric_log.close()
        memory_stop.touch()
        if memory_process.poll() is None:
            try:
                memory_process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                memory_process.terminate()
                memory_process.wait(timeout=10)
        if not memory_log.closed:
            memory_log.close()
        memory_stop.unlink(missing_ok=True)
        if epp_health_post is None:
            try:
                epp_health_post = container_health_evidence(
                    pod_for_service(args.namespace, args.epp_service), "epp"
                )
                epp_health_post["transition"] = endpoint_picker_health_transition(
                    proof["endpoint_picker_health_pre"], epp_health_post
                )
            except Exception as exc:
                epp_health_post = {
                    "valid": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            (run_dir / "endpoint-picker-health-post.json").write_text(
                json.dumps(epp_health_post, indent=2) + "\n"
            )
        # Preserve evidence from every failure path before deleting pod-side files.
        final_metric_report = preserve_metric_artifacts(
            args.namespace, args.runner_pod, remote_metrics, metrics_dir
        )
        final_client_report = preserve_client_artifacts(
            args.namespace, pods, resources, raw_dir
        )
        failure_path = run_dir / "run_failure.json"
        if failure_path.exists():
            failure = json.loads(failure_path.read_text())
            failure["final_metric_artifact_preservation"] = final_metric_report
            failure["final_client_artifact_preservation"] = final_client_report
            failure_path.write_text(json.dumps(failure, indent=2) + "\n")
        for name, configmap, _tenant in resources:
            kubectl(args.namespace, ["delete", "job", name, "--ignore-not-found=true"], check=False)
            kubectl(args.namespace, ["delete", "configmap", configmap, "--ignore-not-found=true"], check=False)
        kubectl(args.namespace, [
            "exec", args.runner_pod, "--", "rm", "-rf",
            remote_metrics, remote_script, remote_wrapper, remote_stop,
        ], check=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--prefix", required=True)
    parser.add_argument(
        "--resource-experiment",
        help="Kubernetes experiment label used to capture stable-lane resources",
    )
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--runner-pod", default="flow-control-benchmark-runner")
    parser.add_argument("--epp-configmap", default="flow-control-epp-config")
    parser.add_argument("--epp-deployment", default="flow-control-epp")
    parser.add_argument("--apply-epp-config", type=Path)
    parser.add_argument("--expected-detector", choices=("utilization-detector", "concurrency-detector"))
    parser.add_argument("--expected-queue-depth", type=int)
    parser.add_argument("--expected-max-concurrency", type=int)
    parser.add_argument("--expected-concurrency-mode", choices=("requests", "tokens"))
    parser.add_argument("--expected-max-token-concurrency", type=int)
    parser.add_argument(
        "--expected-add-estimated-output-tokens", choices=("true", "false")
    )
    parser.add_argument("--expected-headroom", type=float)
    parser.add_argument(
        "--expected-picker",
        choices=("random-picker", "max-score-picker", "weighted-random-picker"),
    )
    parser.add_argument(
        "--expected-token-producer-backend",
        choices=("auto-estimate", "estimate", "vllm"),
    )
    parser.add_argument("--expected-token-producer-model")
    parser.add_argument("--expected-token-producer-url")
    parser.add_argument("--expected-prefix-cache", choices=("off", "on"), default="off")
    parser.add_argument("--expected-prefix-auto-tune", choices=("true", "false"))
    parser.add_argument("--expected-prefix-block-size-tokens", type=int)
    parser.add_argument("--expected-prefix-max-tokens-to-match", type=int)
    parser.add_argument("--expected-prefix-lru-capacity-per-server", type=int)
    parser.add_argument("--expected-model-replicas", type=int, default=1)
    parser.add_argument("--vllm-args-configmap", default="flow-control-vllm-args")
    parser.add_argument("--endpoint", default="http://flow-control-epp.default.svc.cluster.local:8080")
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--http-version", choices=("1", "2"), default="2")
    parser.add_argument("--guidellm-worker-processes", type=int, default=10)
    parser.add_argument("--guidellm-mp-poll-interval-s", type=float, default=0.01)
    parser.add_argument("--drain-after-done", action="store_true")
    parser.add_argument("--connection-close", action="store_true")
    parser.add_argument("--recover-multiline-sse", action="store_true")
    parser.add_argument("--epp-service", default="flow-control-epp")
    parser.add_argument("--model-service", default="flow-control-model")
    parser.add_argument("--epp-metrics-url", default="http://flow-control-epp.default.svc.cluster.local:9090/metrics")
    parser.add_argument("--epp-plugin-state-url", default="http://flow-control-epp.default.svc.cluster.local:9090/debug/plugins/state")
    parser.add_argument("--vllm-metrics-port", type=int, default=8000)
    parser.add_argument("--envoy-metrics-url", default="http://flow-control-epp.default.svc.cluster.local:19000/stats/prometheus")
    parser.add_argument("--envoy-cluster-name", default="epp")
    parser.add_argument("--expected-envoy-remaining-requests", type=int, default=10000)
    parser.add_argument("--max-epp-memory-fraction", type=float, default=0.85)
    parser.add_argument("--expected-epp-memory-limit")
    parser.add_argument("--kubernetes-memory-interval-s", type=float, default=5.0)
    parser.add_argument("--start-delay-s", type=int, default=90)
    parser.add_argument("--drain-timeout-s", type=int, default=300)
    parser.add_argument("--artifact-retention-s", type=int, default=86400)
    parser.add_argument("--shared-prefix-fraction", type=float)
    parser.add_argument("--shared-prefix-group", default="shared-context")
    parser.add_argument(
        "--shared-prefix-group-mode", choices=("shared", "tenant"), default="shared"
    )
    args = parser.parse_args()
    if args.shared_prefix_fraction is not None and not 0.0 < args.shared_prefix_fraction < 1.0:
        parser.error("--shared-prefix-fraction must be between 0 and 1")
    if args.kubernetes_memory_interval_s <= 0:
        parser.error("--kubernetes-memory-interval-s must be positive")
    if args.guidellm_mp_poll_interval_s <= 0:
        parser.error("--guidellm-mp-poll-interval-s must be positive")
    return args


if __name__ == "__main__":
    run(parse_args())

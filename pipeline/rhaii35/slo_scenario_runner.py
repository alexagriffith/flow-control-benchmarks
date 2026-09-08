#!/usr/bin/env python3
"""Run one canonical benchmark scenario with per-request SLO headers."""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import csv
import hashlib
import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any


ADAPTER_VERSION = "rhaii35-slo-adapter-v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_canonical(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"canonical runner does not exist: {path}")
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("canonical_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load canonical runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def run_async_callable_in_thread(
    executor: concurrent.futures.ThreadPoolExecutor,
    target: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run an async sampler on an isolated event loop and propagate failures."""
    ready = threading.Event()
    state: dict[str, Any] = {}

    def worker() -> Any:
        async def invoke() -> Any:
            state["loop"] = asyncio.get_running_loop()
            state["task"] = asyncio.create_task(target(*args, **kwargs))
            ready.set()
            return await state["task"]

        return asyncio.run(invoke())

    future = executor.submit(worker)
    try:
        while not ready.is_set() and not future.done():
            await asyncio.sleep(0.001)
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        while not ready.is_set() and not future.done():
            await asyncio.sleep(0.001)
        if ready.is_set() and not future.done():
            state["loop"].call_soon_threadsafe(state["task"].cancel)
            while not future.done():
                await asyncio.sleep(0.001)
        raise


def option_value(arguments: list[str], name: str) -> str:
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"missing required canonical option {name}") from exc


def load_tenant_specs(path: Path) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text())
    scenarios = document.get("scenarios")
    if document.get("schema_version") != 1 or not isinstance(scenarios, list):
        raise ValueError("scenario file must use schema_version 1 and contain scenarios")

    specs: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        for value in scenario.get("tenants", []):
            class_id = str(value.get("fairness_id", "")).strip()
            if not class_id or class_id in specs:
                raise ValueError(f"invalid or duplicate SLO class ID: {class_id!r}")
            deadline_class = str(value.get("deadline_class", "")).strip()
            if not deadline_class:
                raise ValueError(f"{class_id} needs deadline_class")
            slo_ttft_ms = value.get("slo_ttft_ms")
            if slo_ttft_ms is not None and int(slo_ttft_ms) <= 0:
                raise ValueError(f"{class_id}.slo_ttft_ms must be positive or null")
            specs[class_id] = dict(value)
    if not specs:
        raise ValueError("scenario file has no SLO classes")
    return specs


def effective_slo_ms(spec: dict[str, Any], mode: str) -> int | None:
    return 500 if mode == "equal" else spec.get("slo_ttft_ms")


def build_schedule_cache(target: Any) -> Any:
    cache: dict[tuple[str, int, int], list[float]] = {}

    def cached(
        phases: list[dict[str, Any]], duration_s: int, seed: int
    ) -> list[float]:
        key = (json.dumps(phases, sort_keys=True, separators=(",", ":")), duration_s, seed)
        if key not in cache:
            cache[key] = target(phases, duration_s, seed)
        return cache[key]

    return cached


def flow_ids(tenant_specs: dict[str, dict[str, Any]], default: str) -> set[str]:
    return {
        str(spec.get("flow_fairness_id", default))
        for spec in tenant_specs.values()
    }


def shared_flow_header_evidence(
    benchmark: Any,
    metrics_text: str,
    tenants: list[Any],
) -> dict[str, Any]:
    """Validate the flow IDs sent on the wire instead of the local class IDs."""
    observed: dict[str, set[int]] = {}
    for (_name, labels), _value in benchmark.parse_prometheus(metrics_text).items():
        label_map = benchmark.labels_to_dict(labels)
        fairness_id = label_map.get("fairness_id")
        priority_text = label_map.get("priority")
        if fairness_id is None or priority_text is None:
            continue
        try:
            observed.setdefault(fairness_id, set()).add(int(priority_text))
        except ValueError:
            continue

    expected: dict[str, set[int]] = {}
    for tenant in tenants:
        expected.setdefault(tenant.flow_fairness_id, set()).add(tenant.priority)
    missing = [
        {"fairness_id": fairness_id, "expected_priority": priority}
        for fairness_id, priorities in expected.items()
        for priority in sorted(priorities)
        if priority not in observed.get(fairness_id, set())
    ]
    return {
        "valid": not missing,
        "expected": {key: sorted(values) for key, values in expected.items()},
        "observed": {
            key: sorted(observed.get(key, set()))
            for key in expected
        },
        "missing": missing,
    }


class HeaderSession:
    """Replace class IDs with one flow ID and attach the class SLO deadline."""

    def __init__(self, session: Any, tenant: Any):
        self._session = session
        self._tenant = tenant

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def post(self, *args: Any, **kwargs: Any) -> Any:
        headers = dict(kwargs.get("headers") or {})
        shared = self._tenant.flow_fairness_id
        headers["x-llm-d-inference-fairness-id"] = shared
        headers.pop("x-gateway-inference-fairness-id", None)
        headers.pop("x-gateway-inference-objective", None)
        headers.pop("x-llm-d-slo-ttft-ms", None)
        headers.pop("x-slo-ttft-ms", None)
        if self._tenant.slo_ttft_ms is not None:
            headers["x-llm-d-slo-ttft-ms"] = str(self._tenant.slo_ttft_ms)
        kwargs["headers"] = headers
        return self._session.post(*args, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--canonical-runner", type=Path, required=True)
    parser.add_argument("--slo-header-mode", choices=("mixed", "equal"), required=True)
    parser.add_argument("--shared-flow-id", default="slo-ordering-shared-flow")
    known, remaining = parser.parse_known_args()

    scenario_path = Path(option_value(remaining, "--scenario-file"))
    output_dir = Path(option_value(remaining, "--output-dir"))
    tenant_specs = load_tenant_specs(scenario_path)
    benchmark = load_canonical(known.canonical_runner)

    original_load_scenario = benchmark.load_scenario_file

    def load_scenario(path: str) -> Any:
        definitions, windows = original_load_scenario(path)
        for _scenario, tenants, _duration in definitions:
            for tenant in tenants:
                spec = tenant_specs[tenant.fairness_id]
                tenant.flow_fairness_id = spec.get(
                    "flow_fairness_id", known.shared_flow_id
                )
                tenant.deadline_class = spec["deadline_class"]
                tenant.slo_ttft_ms = effective_slo_ms(spec, known.slo_header_mode)
        return definitions, windows

    benchmark.load_scenario_file = load_scenario
    expected_flow_ids = flow_ids(tenant_specs, known.shared_flow_id)
    original_metric_delta = benchmark.metric_delta

    def shared_flow_metric_delta(
        before: str, after: str, _tenant_ids: set[str]
    ) -> Any:
        return original_metric_delta(before, after, expected_flow_ids)

    benchmark.metric_delta = shared_flow_metric_delta

    def header_evidence(metrics_text: str, tenants: list[Any]) -> dict[str, Any]:
        return shared_flow_header_evidence(benchmark, metrics_text, tenants)

    benchmark.header_evidence = header_evidence
    original_send_one = benchmark.send_one

    async def send_one(*args: Any, **kwargs: Any) -> Any:
        session = args[0] if args else kwargs["session"]
        tenant = args[3] if len(args) > 3 else kwargs["tenant"]
        proxied = HeaderSession(session, tenant)
        if args:
            args = (proxied, *args[1:])
        else:
            kwargs["session"] = proxied
        return await original_send_one(*args, **kwargs)

    benchmark.send_one = send_one

    cached_schedule = build_schedule_cache(benchmark.poisson_arrival_schedule)
    benchmark.poisson_arrival_schedule = cached_schedule
    original_run_workload = benchmark.run_workload

    async def prewarmed_run_workload(*args: Any, **kwargs: Any) -> Any:
        tenants = args[2] if len(args) > 2 else kwargs["tenants"]
        duration_s = args[3] if len(args) > 3 else kwargs["duration_s"]
        traffic_seed = args[10] if len(args) > 10 else kwargs.get("traffic_seed", 42)
        arrival_mode = args[13] if len(args) > 13 else kwargs.get(
            "arrival_mode", "closed_loop"
        )
        if arrival_mode == "poisson":
            for tenant in tenants:
                seed = benchmark.tenant_traffic_seed(traffic_seed, tenant.fairness_id)
                cached_schedule(tenant.phases, duration_s, seed)
        return await original_run_workload(*args, **kwargs)

    benchmark.run_workload = prewarmed_run_workload

    original_checkpoint = benchmark.write_partial_run_artifacts
    checkpoint_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    metric_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    checkpoint_futures: list[concurrent.futures.Future[Any]] = []
    original_metric_sampler = benchmark.metric_sampler

    def nonblocking_checkpoint(
        run_dir: Path,
        samples: list[Any],
        metric_rows: list[dict[str, Any]],
        metric_long_rows: list[dict[str, Any]],
        concurrency_log: list[dict[str, Any]],
        traffic_log: list[dict[str, Any]],
    ) -> None:
        if checkpoint_futures and not checkpoint_futures[-1].done():
            return
        if checkpoint_futures:
            checkpoint_futures[-1].result()
        checkpoint_futures.append(
            checkpoint_executor.submit(
                original_checkpoint,
                run_dir,
                list(samples),
                list(metric_rows),
                list(metric_long_rows),
                list(concurrency_log),
                list(traffic_log),
            )
        )

    benchmark.write_partial_run_artifacts = nonblocking_checkpoint

    async def isolated_metric_sampler(*args: Any, **kwargs: Any) -> Any:
        return await run_async_callable_in_thread(
            metric_executor, original_metric_sampler, *args, **kwargs
        )

    benchmark.metric_sampler = isolated_metric_sampler
    sys.argv = [str(known.canonical_runner), *remaining]
    status = 0
    try:
        asyncio.run(benchmark.main())
    except SystemExit as exc:
        status = int(exc.code or 0)
    finally:
        metric_executor.shutdown(wait=True)
        checkpoint_executor.shutdown(wait=True)
        for future in checkpoint_futures:
            future.result()

    if status != 0:
        return status

    benchmark_config_path = output_dir / "benchmark_config.json"
    if benchmark_config_path.is_file():
        benchmark_config = json.loads(benchmark_config_path.read_text())
        benchmark_config["headers"] = [
            "x-llm-d-inference-objective",
            "x-llm-d-inference-fairness-id",
            "x-llm-d-slo-ttft-ms",
        ]
        benchmark_config["slo_adapter"] = {
            "version": ADAPTER_VERSION,
            "header_mode": known.slo_header_mode,
            "shared_flow_id": known.shared_flow_id,
        }
        benchmark_config_path.write_text(
            json.dumps(benchmark_config, indent=2) + "\n"
        )

    evidence_rows = []
    for samples_path in output_dir.rglob("client_samples.csv"):
        with samples_path.open() as stream:
            for row in csv.DictReader(stream):
                spec = tenant_specs[row["tenant"]]
                slo_ms = effective_slo_ms(spec, known.slo_header_mode)
                evidence_rows.append(
                    {
                        "run_id": row["run_id"],
                        "scenario": row["scenario"],
                        "request_id": row["request_id"],
                        "class_id": row["tenant"],
                        "flow_fairness_id": spec.get(
                            "flow_fairness_id", known.shared_flow_id
                        ),
                        "priority": int(row["priority"]),
                        "objective": row["objective"],
                        "deadline_class": spec["deadline_class"],
                        "slo_header": (
                            "x-llm-d-slo-ttft-ms" if slo_ms is not None else None
                        ),
                        "slo_ttft_ms": slo_ms,
                        "planned_arrival_s": row["planned_arrival_s"],
                        "actual_send_s": row["actual_send_s"],
                        "status": row["status"],
                        "ttft_s": row["ttft_s"],
                        "tpot_s": row["tpot_s"],
                    }
                )

    evidence_path = output_dir / "slo_request_evidence.jsonl"
    evidence_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in evidence_rows)
    )
    contract = {
        "schemaVersion": 1,
        "valid": bool(evidence_rows),
        "adapterVersion": ADAPTER_VERSION,
        "adapterSha256": sha256(Path(__file__)),
        "headerMode": known.slo_header_mode,
        "sharedFlowId": known.shared_flow_id,
        "requests": len(evidence_rows),
        "classes": sorted(tenant_specs),
        "canonicalRunner": str(known.canonical_runner),
        "canonicalRunnerSha256": sha256(known.canonical_runner),
        "scenarioSha256": sha256(scenario_path),
    }
    (output_dir / "slo-adapter-contract.json").write_text(
        json.dumps(contract, indent=2) + "\n"
    )
    return 0 if contract["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

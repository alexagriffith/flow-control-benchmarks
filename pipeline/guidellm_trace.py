#!/usr/bin/env python3
"""Export deterministic scenario traffic as finite GuideLLM replay traces."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmark import load_scenario_file, poisson_arrival_schedule, tenant_traffic_seed


def schedule_hash(arrivals: list[float]) -> str:
    encoded = ",".join(f"{value:.9f}" for value in arrivals).encode()
    return hashlib.sha256(encoded).hexdigest()


def export_trace(
    scenario_file: Path, scenario_name: str, out_dir: Path, traffic_seed: int = 42,
) -> Path:
    definitions, analysis_windows = load_scenario_file(str(scenario_file))
    selected = [item for item in definitions if item[0] == scenario_name]
    if len(selected) != 1:
        names = ", ".join(item[0] for item in definitions)
        raise ValueError(f"unknown scenario {scenario_name!r}; available: {names}")

    _name, tenants, duration_s = selected[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source": str(scenario_file),
        "scenario": scenario_name,
        "duration_s": duration_s,
        "traffic_seed": traffic_seed,
        "arrival_mode": "poisson_replay",
        "analysis_windows": analysis_windows.get(scenario_name, []),
        "tenants": [],
    }
    for tenant in tenants:
        seed = tenant_traffic_seed(traffic_seed, tenant.fairness_id)
        arrivals = poisson_arrival_schedule(tenant.phases, duration_s, seed)
        if not arrivals:
            raise ValueError(f"{scenario_name}/{tenant.fairness_id} produced no arrivals")
        trace_path = out_dir / f"{tenant.fairness_id}.jsonl"
        input_tokens = tenant.input_tokens or 512
        output_tokens = tenant.output_tokens or 128
        with trace_path.open("w") as handle:
            for arrival in arrivals:
                handle.write(json.dumps({
                    "timestamp": round(arrival, 9),
                    "input_length": input_tokens,
                    "output_length": output_tokens,
                }, separators=(",", ":")) + "\n")
        manifest["tenants"].append({
            "fairness_id": tenant.fairness_id,
            "priority": tenant.priority,
            "objective": tenant.inference_objective,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "planned_requests": len(arrivals),
            "first_arrival_s": round(arrivals[0], 9),
            "schedule_sha256": schedule_hash(arrivals),
            "traffic_seed": seed,
            "trace_file": trace_path.name,
            "phases": tenant.phases,
        })

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-file", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--traffic-seed", type=int, default=42)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    export_trace(args.scenario_file, args.scenario, args.out_dir, args.traffic_seed)

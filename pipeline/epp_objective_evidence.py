#!/usr/bin/env python3
"""Verify EPP resolved every objective and priority expected by a scenario."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def expected_pairs(scenario_file: str, scenario_name: str) -> set[tuple[str, int]]:
    payload = json.loads(Path(scenario_file).read_text())
    scenario = next(
        (item for item in payload["scenarios"] if item["name"] == scenario_name),
        None,
    )
    if scenario is None:
        raise ValueError(f"scenario not found: {scenario_name}")
    return {(tenant["objective"], int(tenant["priority"])) for tenant in scenario["tenants"]}


def analyze(log_text: str, expected: set[tuple[str, int]]) -> dict[str, Any]:
    counts: Counter[tuple[str, int]] = Counter()
    priorities_by_objective: dict[str, set[int]] = defaultdict(set)
    malformed_lines = 0
    for raw_line in log_text.splitlines():
        try:
            item = json.loads(raw_line.rstrip("\r"))
        except json.JSONDecodeError:
            malformed_lines += 1
            continue
        if item.get("msg") != "LLM request assembled":
            continue
        objective = item.get("objectiveKey")
        priority = item.get("priority")
        if not isinstance(objective, str) or not isinstance(priority, int):
            continue
        pair = (objective, priority)
        counts[pair] += 1
        priorities_by_objective[objective].add(priority)

    expected_objectives = {name for name, _ in expected}
    missing = sorted(expected - set(counts))
    mismatched = sorted(
        (objective, sorted(priorities))
        for objective, priorities in priorities_by_objective.items()
        if objective in expected_objectives
        and any((objective, priority) not in expected for priority in priorities)
    )
    return {
        "valid": not missing and not mismatched,
        "source": "epp_request_logs",
        "expected": [
            {"objective": objective, "priority": priority}
            for objective, priority in sorted(expected, key=lambda pair: pair[1], reverse=True)
        ],
        "observed": [
            {"objective": objective, "priority": priority, "request_count": count}
            for (objective, priority), count in sorted(counts.items(), key=lambda item: item[0][1], reverse=True)
            if (objective, priority) in expected
        ],
        "missing": [
            {"objective": objective, "priority": priority}
            for objective, priority in missing
        ],
        "mismatched": [
            {"objective": objective, "observed_priorities": priorities}
            for objective, priorities in mismatched
        ],
        "malformed_lines": malformed_lines,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--scenario-file", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = analyze(Path(args.log).read_text(), expected_pairs(args.scenario_file, args.scenario))
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

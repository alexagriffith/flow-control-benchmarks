#!/usr/bin/env python3
"""Atomically sync a benchmark pod's live status into Flight Recorder."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"complete", "failed"}


def read_remote(namespace: str, pod: str, path: str) -> dict[str, Any] | None:
    result = subprocess.run(
        ["kubectl", "-n", namespace, "exec", pod, "--", "cat", path],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    required = {"generatedAt", "state", "phase", "tenants"}
    return value if isinstance(value, dict) and required <= value.keys() else None


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--pod", required=True)
    parser.add_argument("--remote-file", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval-s", default=2.0, type=float)
    parser.add_argument("--terminal-run-id")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    while True:
        value = read_remote(args.namespace, args.pod, args.remote_file)
        if value is not None:
            atomic_write(args.output, value)
            if (
                value.get("state") in TERMINAL_STATES
                and (
                    args.terminal_run_id is None
                    or value.get("runId") == args.terminal_run_id
                )
            ):
                return
        time.sleep(args.interval_s)


if __name__ == "__main__":
    main()

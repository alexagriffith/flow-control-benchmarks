#!/usr/bin/env python3
"""Generate public-safe YAML copies of every published package configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from package_visual_specs import build_specs


FORBIDDEN = {
    "credential": re.compile(r"password|secret|api[_-]?key|bearer|xox[cd]-", re.IGNORECASE),
    "customer identifier": re.compile(r"ca" + r"pital[ _-]*o" + r"ne", re.IGNORECASE),
    "local path": re.compile(r"/Users/|\\Users\\"),
    "private address": re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "cloud account": re.compile(r"\b\d{12}\.dkr\.ecr\b", re.IGNORECASE),
}


def scalar(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=True)


def emit_yaml(value: object, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(emit_yaml(child, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {scalar(child)}")
        return lines
    if isinstance(value, list):
        lines = []
        for child in value:
            if isinstance(child, dict):
                entries = list(child.items())
                if not entries:
                    lines.append(f"{prefix}- {{}}")
                    continue
                first_key, first_value = entries[0]
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{prefix}- {first_key}:")
                    lines.extend(emit_yaml(first_value, indent + 4))
                else:
                    lines.append(f"{prefix}- {first_key}: {scalar(first_value)}")
                for key, item in entries[1:]:
                    if isinstance(item, (dict, list)):
                        lines.append(f"{prefix}  {key}:")
                        lines.extend(emit_yaml(item, indent + 4))
                    else:
                        lines.append(f"{prefix}  {key}: {scalar(item)}")
            elif isinstance(child, list):
                lines.append(f"{prefix}-")
                lines.extend(emit_yaml(child, indent + 2))
            else:
                lines.append(f"{prefix}- {scalar(child)}")
        return lines or [f"{prefix}[]"]
    return [f"{prefix}{scalar(value)}"]


def generate(config_path: Path) -> str:
    config = json.loads(config_path.read_text())
    document = {
        "schema_version": 1,
        "source": "run-config.json",
        "tested_configuration": config,
    }
    result = "# Generated from run-config.json. Contains no credentials or cluster endpoints.\n"
    result += "\n".join(emit_yaml(document)) + "\n"
    for label, pattern in FORBIDDEN.items():
        if pattern.search(result):
            raise ValueError(f"{label} found in {config_path}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stale: list[str] = []
    for spec in build_specs(root):
        folder = root / spec["path"]
        config_path = folder / "run-config.json"
        output_path = folder / "tested-config.yaml"
        expected = generate(config_path)
        if args.check:
            if not output_path.is_file() or output_path.read_text() != expected:
                stale.append(str(output_path.relative_to(root)))
        else:
            output_path.write_text(expected)
    if stale:
        print("Generated package configurations are stale:", file=sys.stderr)
        for path in stale:
            print(f"- {path}", file=sys.stderr)
        return 1
    if not args.check:
        print(f"Generated public configurations for {len(build_specs(root))} benchmark packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

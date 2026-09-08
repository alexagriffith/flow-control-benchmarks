#!/usr/bin/env python3
"""Generate the exact synthetic Batch input used by the accepted Soft-PT study."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUESTS = 4752
PROMPT_WORDS = 502
EXPECTED_SHA256 = "6aad5d8d87d9a53ab13bd219c83730b6b9b2792746639bf83a47f57ae6edffa0"


def row(index: int) -> dict:
    return {
        "custom_id": f"dispatch-batch-{index:04d}",
        "method": "POST",
        "url": "/v1/completions",
        "body": {
            "model": "openai/gpt-oss-20b",
            "prompt": f"guidellm-trace-request-{index}: " + " ".join(["w"] * PROMPT_WORDS),
            "max_tokens": 128,
            "ignore_eos": True,
            "stream": False,
        },
    }


def payload() -> bytes:
    return b"".join(
        json.dumps(row(index), separators=(",", ":")).encode() + b"\n"
        for index in range(REQUESTS)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/soft-pt-batch-input.jsonl"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    content = payload()
    digest = hashlib.sha256(content).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"unexpected generated SHA-256: {digest}")
    if args.check:
        print(json.dumps({"valid": True, "requests": REQUESTS, "sha256": digest}))
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(json.dumps({"output": str(args.output), "requests": REQUESTS, "sha256": digest}))


if __name__ == "__main__":
    main()

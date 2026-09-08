#!/usr/bin/env python3
"""Check atomic reserve, overflow, idempotency, release, and cleanup."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import uuid

from classifier_proxy import Classifier, load_policy


async def preflight(args: argparse.Namespace) -> None:
    policy = load_policy(args.policy)
    original_prefix = policy["redis_key_prefix"]
    policy["redis_key_prefix"] = f"{original_prefix}:preflight:{uuid.uuid4().hex}"
    classifier = Classifier(
        policy,
        "classifying-quota",
        "http://unused",
        args.redis_host,
        args.redis_port,
        args.reserve_lua.read_text(),
        args.settle_lua.read_text(),
    )
    try:
        reserved = [await classifier.reserve(f"r{index}") for index in range(10)]
        overflow = await classifier.reserve("overflow")
        duplicate = await classifier.reserve("r0")
        released = await classifier.settle("r0", "release")
        replacement = await classifier.reserve("replacement")
        assert all(row[0] == "reserved" for row in reserved), reserved
        assert overflow[0] == "overflow", overflow
        assert duplicate[0] == "reserved" and duplicate[2] == "existing", duplicate
        assert released[0] == "released", released
        assert replacement[0] == "reserved", replacement
        print(json.dumps({
            "valid": True,
            "reserved": len(reserved),
            "overflow": overflow[0],
            "duplicate": duplicate[2],
            "release": released[0],
            "replacement": replacement[0],
        }, indent=2, sort_keys=True))
    finally:
        keys = await classifier.scan_keys(f"{policy['redis_key_prefix']}:*")
        if keys:
            await classifier.redis_command("DEL", *keys)


def parse_args() -> argparse.Namespace:
    root = pathlib.Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=pathlib.Path, default=root / "policy.json")
    parser.add_argument("--redis-host", required=True)
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--reserve-lua", type=pathlib.Path,
                        default=root / "token_bucket_reserve.lua")
    parser.add_argument("--settle-lua", type=pathlib.Path,
                        default=root / "token_bucket_settle.lua")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(preflight(parse_args()))

#!/usr/bin/env python3
"""Delete only the Redis keys owned by one Soft-PT replay policy."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib

from classifier_proxy import Classifier, load_policy


async def reset(policy_path: pathlib.Path, redis_host: str, redis_port: int) -> None:
    policy = load_policy(policy_path)
    classifier = Classifier(policy, "classifying-quota", "http://unused", redis_host,
                            redis_port, "", "")
    pattern = f"{policy['redis_key_prefix']}:*"
    keys = await classifier.scan_keys(pattern)
    if keys:
        await classifier.redis_command("DEL", *keys)
    remaining = await classifier.scan_keys(pattern)
    print(json.dumps({"pattern": pattern, "deleted": len(keys), "remaining": len(remaining)}))
    if remaining:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--redis-host", required=True)
    parser.add_argument("--redis-port", type=int, default=6379)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(reset(args.policy, args.redis_host, args.redis_port))

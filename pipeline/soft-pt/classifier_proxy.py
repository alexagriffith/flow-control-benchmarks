#!/usr/bin/env python3
"""Soft-PT classifier and streaming forward proxy used by the accepted study."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import time
from collections import Counter
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web


MODES = ("no-quota", "classifying-quota", "blocking-quota")
OBJECTIVE_HEADERS = {
    "x-llm-d-inference-objective",
    "x-gateway-inference-objective",
}
REQUEST_ID_HEADERS = ("x-request-id", "x-benchmark-request-id")


def load_policy(path: str | pathlib.Path) -> dict[str, Any]:
    policy = json.loads(pathlib.Path(path).read_text())
    required = {
        "synthetic_tenant",
        "frozen_request",
        "entitlement",
        "objectives",
        "redis_key_prefix",
        "redis_failure_policy",
    }
    missing = sorted(required - set(policy))
    if missing:
        raise ValueError("policy is missing: " + ", ".join(missing))
    if policy["redis_failure_policy"] != "never-promote":
        raise ValueError("redis_failure_policy must be never-promote")
    return policy


def encode_command(parts: list[str]) -> bytes:
    chunks = [f"*{len(parts)}\r\n".encode()]
    for part in parts:
        data = str(part).encode()
        chunks.extend((f"${len(data)}\r\n".encode(), data, b"\r\n"))
    return b"".join(chunks)


async def read_resp(reader: asyncio.StreamReader) -> Any:
    prefix = await reader.readexactly(1)
    line = (await reader.readline()).rstrip(b"\r\n")
    if prefix == b"+":
        return line.decode()
    if prefix == b"-":
        raise RuntimeError(line.decode())
    if prefix == b":":
        return int(line)
    if prefix == b"$":
        size = int(line)
        if size < 0:
            return None
        return (await reader.readexactly(size + 2))[:-2].decode()
    if prefix == b"*":
        return [await read_resp(reader) for _ in range(int(line))]
    raise RuntimeError(f"unknown Redis response prefix {prefix!r}")


class Classifier:
    def __init__(
        self,
        policy: dict[str, Any],
        mode: str,
        upstream: str,
        redis_host: str,
        redis_port: int,
        reserve_lua: str,
        settle_lua: str,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unsupported mode: {mode}")
        self.policy = policy
        self.mode = mode
        self.upstream = upstream.rstrip("/")
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.reserve_lua = reserve_lua
        self.settle_lua = settle_lua
        self.counters: Counter[str] = Counter()

    async def redis_command(self, *parts: str) -> Any:
        reader, writer = await asyncio.open_connection(
            self.redis_host, self.redis_port
        )
        try:
            writer.write(encode_command(list(parts)))
            await writer.drain()
            return await read_resp(reader)
        finally:
            writer.close()
            await writer.wait_closed()

    async def scan_keys(self, pattern: str) -> list[str]:
        """List only keys owned by this replay without blocking Redis."""
        cursor = "0"
        keys: list[str] = []
        while True:
            cursor, page = await self.redis_command(
                "SCAN", cursor, "MATCH", pattern, "COUNT", "100"
            )
            keys.extend(page)
            if cursor == "0":
                return keys

    def redis_keys(self, request_id: str) -> tuple[str, str]:
        prefix = self.policy["redis_key_prefix"]
        tenant = self.policy["synthetic_tenant"]
        return f"{prefix}:bucket:{tenant}", f"{prefix}:reservation:{request_id}"

    async def reserve(self, request_id: str) -> list[Any]:
        bucket, reservation = self.redis_keys(request_id)
        entitlement = self.policy["entitlement"]
        cost_milli = (
            self.policy["frozen_request"]["estimated_normalized_tokens"] * 1000
        )
        rate_milli_per_ms = entitlement["rate_normalized_tokens_per_second"]
        burst_milli = entitlement["burst_normalized_tokens"] * 1000
        return await self.redis_command(
            "EVAL",
            self.reserve_lua,
            "2",
            bucket,
            reservation,
            str(cost_milli),
            str(rate_milli_per_ms),
            str(burst_milli),
            "7200",
        )

    async def settle(self, request_id: str, action: str) -> list[Any]:
        bucket, reservation = self.redis_keys(request_id)
        burst_milli = self.policy["entitlement"]["burst_normalized_tokens"] * 1000
        return await self.redis_command(
            "EVAL",
            self.settle_lua,
            "2",
            bucket,
            reservation,
            action,
            str(burst_milli),
        )

    async def classify(
        self, tenant: str, request_id: str, caller_objective: str | None
    ) -> tuple[str, str, float, str | None]:
        overflow = self.policy["objectives"]["overflow"]
        if self.mode == "no-quota" or tenant != self.policy["synthetic_tenant"]:
            return "overflow", overflow, 0.0, caller_objective
        if not request_id:
            raise web.HTTPBadRequest(
                text='{"error":"missing request ID"}',
                content_type="application/json",
            )
        started = time.perf_counter()
        try:
            result = await self.reserve(request_id)
            decision = str(result[0])
        except Exception:
            self.counters["redis_error"] += 1
            decision = (
                "overflow" if self.mode == "classifying-quota" else "rejected"
            )
        redis_ms = (time.perf_counter() - started) * 1000
        objective = (
            self.policy["objectives"]["reserved"]
            if decision == "reserved"
            else overflow
        )
        return decision, objective, redis_ms, caller_objective


def request_id(request: web.Request) -> str:
    query_value = request.query.get("benchmark_request_id", "")
    if query_value:
        return query_value
    for name in REQUEST_ID_HEADERS:
        value = request.headers.get(name, "")
        if value:
            return value
    return ""


def objective_headers(headers: Any, objective: str) -> dict[str, str]:
    result = {
        key: value
        for key, value in headers.items()
        if key.lower() not in OBJECTIVE_HEADERS
        and key.lower() not in {"host", "content-length"}
    }
    result["x-llm-d-inference-objective"] = objective
    result["x-gateway-inference-objective"] = objective
    return result


async def completions(request: web.Request) -> web.StreamResponse:
    classifier: Classifier = request.app["classifier"]
    current_id = request_id(request)
    tenant = request.headers.get("x-llm-d-inference-fairness-id", "")
    caller_objective = request.headers.get("x-llm-d-inference-objective")
    decision, objective, redis_ms, caller_objective = await classifier.classify(
        tenant, current_id, caller_objective
    )
    classifier.counters[f"decision_{decision}"] += 1
    if (
        decision in {"overflow", "rejected"}
        and classifier.mode == "blocking-quota"
        and tenant == classifier.policy["synthetic_tenant"]
    ):
        event = {
            "request_id": current_id,
            "tenant": tenant,
            "mode": classifier.mode,
            "decision": "rejected",
            "objective": None,
            "redis_ms": round(redis_ms, 3),
            "caller_objective": caller_objective,
            "status": 429,
            "forwarded": False,
        }
        print(json.dumps(event, sort_keys=True), flush=True)
        return web.json_response(
            {
                "error": {
                    "type": "pt_quota_exhausted",
                    "message": "synthetic entitlement exhausted",
                }
            },
            status=429,
        )

    body = await request.read()
    headers = objective_headers(request.headers, objective)
    session: ClientSession = request.app["session"]
    async with session.post(
        classifier.upstream + request.rel_url.path_qs,
        data=body,
        headers=headers,
    ) as upstream:
        response = web.StreamResponse(
            status=upstream.status,
            headers={
                "content-type": upstream.headers.get(
                    "content-type", "application/json"
                )
            },
        )
        await response.prepare(request)
        byte_count = 0
        async for chunk in upstream.content.iter_any():
            byte_count += len(chunk)
            await response.write(chunk)
        if decision == "reserved":
            await classifier.settle(
                current_id, "settle" if upstream.status == 200 else "release"
            )
        classifier.counters[f"upstream_{upstream.status}"] += 1
        event = {
            "request_id": current_id,
            "tenant": tenant,
            "mode": classifier.mode,
            "decision": decision,
            "objective": objective,
            "redis_ms": round(redis_ms, 3),
            "caller_objective": caller_objective,
            "status": upstream.status,
            "forwarded": True,
            "response_bytes": byte_count,
        }
        print(json.dumps(event, sort_keys=True), flush=True)
        try:
            await response.write_eof()
        except ConnectionResetError:
            pass
        return response


async def health(request: web.Request) -> web.Response:
    classifier: Classifier = request.app["classifier"]
    return web.json_response({"ok": True, "mode": classifier.mode})


async def metrics(request: web.Request) -> web.Response:
    classifier: Classifier = request.app["classifier"]
    lines = [
        f'soft_pt_classifier_events_total{{event="{key}"}} {value}'
        for key, value in sorted(classifier.counters.items())
    ]
    return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")


def create_app(classifier: Classifier) -> web.Application:
    async def session_context(app: web.Application):
        app["session"] = ClientSession(timeout=ClientTimeout(total=180))
        yield
        await app["session"].close()

    app = web.Application()
    app["classifier"] = classifier
    app.cleanup_ctx.append(session_context)
    app.router.add_get("/healthz", health)
    app.router.add_get("/metrics", metrics)
    app.router.add_post("/v1/completions", completions)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        default=os.environ.get("PT_POLICY_FILE", "policy.json"),
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=os.environ.get("PT_MODE", "no-quota"),
    )
    parser.add_argument("--upstream", default=os.environ.get("PT_UPSTREAM"))
    parser.add_argument(
        "--redis-host",
        default=os.environ.get("PT_REDIS_HOST", "redis"),
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=int(os.environ.get("PT_REDIS_PORT", "6379")),
    )
    parser.add_argument(
        "--reserve-lua",
        default=os.environ.get("PT_RESERVE_LUA", "token_bucket_reserve.lua"),
    )
    parser.add_argument(
        "--settle-lua",
        default=os.environ.get("PT_SETTLE_LUA", "token_bucket_settle.lua"),
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PT_PORT", "8090"))
    )
    args = parser.parse_args()
    if not args.upstream:
        parser.error("--upstream or PT_UPSTREAM is required")
    return args


def build_classifier(args: argparse.Namespace) -> Classifier:
    return Classifier(
        load_policy(args.policy),
        args.mode,
        args.upstream,
        args.redis_host,
        args.redis_port,
        pathlib.Path(args.reserve_lua).read_text(),
        pathlib.Path(args.settle_lua).read_text(),
    )


if __name__ == "__main__":
    arguments = parse_args()
    web.run_app(
        create_app(build_classifier(arguments)), host="0.0.0.0", port=arguments.port
    )

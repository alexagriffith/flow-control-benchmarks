#!/usr/bin/env python3
"""Run GuideLLM with a shared start epoch for synchronized tenant replays."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

async def fixed_start(_environment: Any) -> float:
    value = os.environ.get("GUIDELLM_FIXED_START_EPOCH")
    if not value:
        raise RuntimeError("GUIDELLM_FIXED_START_EPOCH is required")
    return float(value)


async def draining_request_updates(
    worker_group: Any,
    poll_interval: float | None = None,
) -> AsyncIterator[tuple[Any, Any, Any, Any]]:
    """Yield every buffered completion before honoring scheduler shutdown."""
    if poll_interval is None:
        from guidellm.settings import settings

        poll_interval = settings.mp_poll_interval
    while True:
        if worker_group.error_event.is_set():
            detail = (
                worker_group._worker_error_details
                or "an error occurred in one of the worker processes"
            )
            raise RuntimeError(f"error_event is set in WorkerProcessGroup: {detail}")

        try:
            yield await worker_group.messaging.get(timeout=poll_interval)
        except asyncio.TimeoutError:
            if not worker_group.shutdown_event.is_set():
                continue
            receive_stopped = worker_group.messaging.receive_stopped_event
            if receive_stopped is not None and receive_stopped.is_set():
                break


def extract_line_with_diagnostics(extractor: Any, handler: Any, line: str) -> Any:
    """Preserve non-sensitive evidence when GuideLLM sees malformed SSE JSON."""
    pending = getattr(handler, "_guidellm_pending_sse", None)
    if pending is not None:
        candidate = pending + "\\n" + line
        try:
            result = extractor(handler, candidate)
        except Exception as error:
            if error.__class__.__name__ != "JSONDecodeError":
                raise
            delattr(handler, "_guidellm_pending_sse")
            digest = hashlib.sha256(candidate.encode()).hexdigest()
            raise ValueError(
                "unrecoverable multiline SSE JSON: "
                f"length={len(candidate)} sha256={digest}"
            ) from error
        delattr(handler, "_guidellm_pending_sse")
        digest = hashlib.sha256(candidate.encode()).hexdigest()
        print(
            "GUIDELLM_RECOVERED_MULTILINE_SSE "
            f"length={len(candidate)} sha256={digest}",
            file=sys.stderr,
            flush=True,
        )
        return result
    try:
        return extractor(handler, line)
    except Exception as error:
        if error.__class__.__name__ != "JSONDecodeError":
            raise
        if os.environ.get("GUIDELLM_RECOVER_MULTILINE_SSE") == "1":
            setattr(handler, "_guidellm_pending_sse", line)
            return {}
        digest = hashlib.sha256(line.encode()).hexdigest()
        raise ValueError(
            "malformed SSE JSON: "
            f"length={len(line)} sha256={digest} tail={line[-80:]!r}"
        ) from error


def add_request_id_header(
    request_kwargs: dict[str, Any], request_id: str,
) -> dict[str, Any]:
    """Attach GuideLLM's request ID without replacing an explicit caller ID."""
    headers = dict(request_kwargs.get("headers") or {})
    headers.setdefault("x-request-id", request_id)
    request_kwargs["headers"] = headers
    return request_kwargs


SHARED_PREFIX_WORDS = (
    "policy", "record", "account", "service", "request", "context", "history",
    "instruction", "evidence", "summary", "analysis", "customer", "workflow",
)


def shared_prefix_prompt(
    prompt: str, request_id: str, fraction: float, group: str,
) -> str:
    """Replace part of a prompt with reusable context while keeping a unique suffix."""
    if not 0.0 < fraction < 1.0:
        raise ValueError("shared prefix fraction must be between 0 and 1")
    words = prompt.split()
    if len(words) < 16:
        raise ValueError("shared prefix mode requires at least 16 prompt words")
    prefix_count = min(int(len(words) * fraction), len(words) - 8)
    shared = [f"prefix-group-{group}"]
    shared.extend(
        SHARED_PREFIX_WORDS[index % len(SHARED_PREFIX_WORDS)]
        for index in range(prefix_count - 1)
    )
    # Keep GuideLLM's token-calibrated suffix. One request marker makes the
    # first suffix token unique without multiplying a UUID across every word.
    suffix = [f"request-{request_id}", *words[prefix_count + 1:]]
    return " ".join(shared + suffix)


def apply_shared_prefix(
    arguments: Any, request_kwargs: dict[str, Any], request_id: str,
) -> dict[str, Any]:
    fraction_text = os.environ.get("GUIDELLM_SHARED_PREFIX_FRACTION")
    if not fraction_text:
        return request_kwargs
    group = os.environ.get("GUIDELLM_SHARED_PREFIX_GROUP", "shared")
    body = dict(request_kwargs.get("json") or {})
    prompt = body.get("prompt")
    if not isinstance(prompt, str):
        raise ValueError("shared prefix mode requires a string prompt")
    body["prompt"] = shared_prefix_prompt(
        prompt, request_id, float(fraction_text), group
    )
    request_kwargs["json"] = body
    if isinstance(getattr(arguments, "body", None), dict):
        arguments.body["prompt"] = body["prompt"]
    return request_kwargs


def drain_done_line(original: Any, handler: Any, line: str) -> int | None:
    """Keep reading HTTP/1 after [DONE] when the diagnostic control is enabled."""
    if os.environ.get("GUIDELLM_DRAIN_AFTER_DONE") == "1" and line == "data: [DONE]":
        return 0
    return original(handler, line)


def configure_guidellm() -> Any:
    from guidellm.cli import cli
    from guidellm.backends.openai.http import OpenAIHTTPBackend
    from guidellm.backends.openai.request_handlers import TextCompletionsRequestHandler
    from guidellm.scheduler.environments import NonDistributedEnvironment
    from guidellm.scheduler.worker_group import WorkerProcessGroup

    original_extract_line = TextCompletionsRequestHandler.extract_line_data
    original_add_streaming_line = TextCompletionsRequestHandler.add_streaming_line
    original_prepare_request = OpenAIHTTPBackend._prepare_resolve_request

    def diagnostic_extract_line(handler: Any, line: str) -> Any:
        return extract_line_with_diagnostics(original_extract_line, handler, line)

    def diagnostic_add_streaming_line(handler: Any, line: str) -> int | None:
        return drain_done_line(original_add_streaming_line, handler, line)

    async def prepare_request_with_id(
        backend: Any, request: Any, history: Any = None,
    ) -> tuple[Any, Any, dict[str, Any]]:
        handler, arguments, request_kwargs = await original_prepare_request(
            backend, request, history
        )
        request_kwargs = add_request_id_header(request_kwargs, request.request_id)
        return handler, arguments, apply_shared_prefix(
            arguments, request_kwargs, request.request_id
        )

    NonDistributedEnvironment.sync_run_start = fixed_start
    WorkerProcessGroup.request_updates = draining_request_updates
    TextCompletionsRequestHandler.extract_line_data = diagnostic_extract_line
    TextCompletionsRequestHandler.add_streaming_line = diagnostic_add_streaming_line
    OpenAIHTTPBackend._prepare_resolve_request = prepare_request_with_id
    return cli

if __name__ == "__main__":
    configure_guidellm()()

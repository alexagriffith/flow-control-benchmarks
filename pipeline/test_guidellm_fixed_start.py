#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import os
import unittest
from threading import Event
from unittest.mock import patch

from guidellm_fixed_start import (
    add_request_id_header,
    apply_shared_prefix,
    drain_done_line,
    draining_request_updates,
    extract_line_with_diagnostics,
    shared_prefix_prompt,
)


class FakeMessaging:
    def __init__(self) -> None:
        self.calls = 0
        self.receive_stopped_event = Event()

    async def get(self, timeout: float) -> tuple[str, ...]:
        del timeout
        self.calls += 1
        if self.calls == 1:
            raise asyncio.TimeoutError
        if self.calls == 2:
            self.receive_stopped_event.set()
            return ("final-completion",)
        raise asyncio.TimeoutError


class FakeWorkerGroup:
    def __init__(self) -> None:
        self.error_event = Event()
        self.shutdown_event = Event()
        self.shutdown_event.set()
        self.messaging = FakeMessaging()
        self._worker_error_details = None


class GuideLlmDrainTests(unittest.IsolatedAsyncioTestCase):
    def test_done_line_can_drain_http_response(self) -> None:
        original = lambda _handler, _line: None
        with patch.dict(os.environ, {"GUIDELLM_DRAIN_AFTER_DONE": "1"}):
            self.assertEqual(drain_done_line(original, object(), "data: [DONE]"), 0)
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(drain_done_line(original, object(), "data: [DONE]"))

    async def test_shutdown_waits_for_final_buffered_completion(self) -> None:
        updates = [
            item async for item in draining_request_updates(FakeWorkerGroup(), 0.001)
        ]
        self.assertEqual(updates, [("final-completion",)])

    def test_malformed_stream_line_keeps_length_hash_and_tail(self) -> None:
        def extractor(_handler: object, line: str) -> object:
            return json.loads(line)

        with self.assertRaisesRegex(
            ValueError, r"malformed SSE JSON: length=6 sha256=.* tail='\{\"x\":1'",
        ):
            extract_line_with_diagnostics(extractor, object(), '{"x":1')

    def test_multiline_json_can_be_reassembled_and_audited(self) -> None:
        class Handler:
            pass

        def extractor(_handler: object, line: str) -> object:
            return json.loads(line.removeprefix("data: "))

        handler = Handler()
        with patch.dict(os.environ, {"GUIDELLM_RECOVER_MULTILINE_SSE": "1"}):
            self.assertEqual(
                extract_line_with_diagnostics(extractor, handler, 'data: {"text":"'),
                {},
            )
            with patch("sys.stderr"):
                self.assertEqual(
                    extract_line_with_diagnostics(extractor, handler, '"}'),
                    {"text": "\n"},
                )

    def test_request_id_header_is_added(self) -> None:
        request = add_request_id_header({"headers": {"x-test": "yes"}}, "request-1")

        self.assertEqual(request["headers"]["x-request-id"], "request-1")
        self.assertEqual(request["headers"]["x-test"], "yes")

    def test_explicit_request_id_is_preserved(self) -> None:
        request = add_request_id_header(
            {"headers": {"x-request-id": "caller-id"}}, "request-1"
        )

        self.assertEqual(request["headers"]["x-request-id"], "caller-id")

    def test_shared_prefix_reuses_context_but_keeps_requests_unique(self) -> None:
        prompt_a = " ".join(f"alpha-{index}" for index in range(100))
        prompt_b = " ".join(f"beta-{index}" for index in range(100))
        rewritten_a = shared_prefix_prompt(prompt_a, "a", 0.75, "group")
        rewritten_b = shared_prefix_prompt(prompt_b, "b", 0.75, "group")

        self.assertEqual(rewritten_a.split()[:75], rewritten_b.split()[:75])
        self.assertEqual(rewritten_a.split()[75], "request-a")
        self.assertEqual(rewritten_b.split()[75], "request-b")
        self.assertNotEqual(rewritten_a, rewritten_b)
        self.assertEqual(len(rewritten_a.split()), 100)
        self.assertEqual(len(rewritten_b.split()), 100)

    def test_shared_prefix_updates_request_and_audit_arguments(self) -> None:
        class Arguments:
            body = {"prompt": " ".join(f"word-{index}" for index in range(100))}

        kwargs = {"json": dict(Arguments.body)}
        with patch.dict(os.environ, {
            "GUIDELLM_SHARED_PREFIX_FRACTION": "0.75",
            "GUIDELLM_SHARED_PREFIX_GROUP": "group",
        }):
            updated = apply_shared_prefix(Arguments, kwargs, "request-1")

        self.assertEqual(updated["json"]["prompt"], Arguments.body["prompt"])
        self.assertIn("request-request-1", Arguments.body["prompt"])


if __name__ == "__main__":
    unittest.main()

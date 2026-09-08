#!/usr/bin/env python3
"""Capture prefill, decode, and Endpoint Picker metrics in one CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import time
from pathlib import Path

import aiohttp


SAMPLER_VERSION = "rhaii35-pd-stage-sampler-v1"
LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>[^\s]+)$"
)

VLLM_METRICS = {
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:num_requests_waiting_by_reason",
    "vllm:kv_cache_usage_perc",
    "vllm:request_success_total",
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
    "vllm:external_prefix_cache_queries_total",
    "vllm:external_prefix_cache_hits_total",
    "vllm:prompt_tokens_by_source_total",
    "vllm:nixl_xfer_time_seconds_count",
    "vllm:nixl_xfer_time_seconds_sum",
    "vllm:nixl_bytes_transferred_count",
    "vllm:nixl_bytes_transferred_sum",
    "vllm:nixl_num_failed_transfers_total",
    "vllm:nixl_num_failed_notifications_total",
    "vllm:nixl_num_kv_expired_reqs_total",
    "vllm:time_to_first_token_seconds_count",
    "vllm:time_to_first_token_seconds_sum",
    "vllm:request_queue_time_seconds_count",
    "vllm:request_queue_time_seconds_sum",
    "vllm:request_prefill_time_seconds_count",
    "vllm:request_prefill_time_seconds_sum",
    "vllm:request_decode_time_seconds_count",
    "vllm:request_decode_time_seconds_sum",
}

ENDPOINT_PICKER_METRICS = {
    "llm_d_epp_flow_control_pool_saturation",
    "llm_d_epp_flow_control_queue_size",
    "llm_d_epp_flow_control_queue_bytes",
    "llm_d_epp_flow_control_requests_total",
    "llm_d_epp_flow_control_stale_endpoints",
    "llm_d_epp_inflight_requests",
    "llm_d_epp_inflight_tokens",
    "llm_d_epp_disagg_decision_total",
    "llm_d_epp_average_queue_size",
    "llm_d_epp_per_endpoint_queue_size",
}


def parse_rows(text: str, selected: set[str]):
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = LINE_RE.match(line)
        if not match or match.group("name") not in selected:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        yield match.group("name"), match.group("labels") or "", value


async def fetch(
    session: aiohttp.ClientSession,
    source: str,
    url: str,
    token: str | None,
    insecure_https: bool,
):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    ssl_option = False if insecure_https else None
    async with session.get(url, headers=headers, ssl=ssl_option) as response:
        response.raise_for_status()
        return source, await response.text()


async def main_async(args: argparse.Namespace) -> int:
    token = os.environ.get("METRICS_TOKEN")
    if args.token_file:
        token = Path(args.token_file).read_text().strip()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    errors_path = output.with_suffix(output.suffix + ".errors.json")
    contract_path = output.with_suffix(output.suffix + ".contract.json")

    sources = {
        "prefill": (args.prefill_url, None, VLLM_METRICS),
        "decode": (args.decode_url, None, VLLM_METRICS),
        "endpoint-picker": (
            args.endpoint_picker_url,
            token,
            ENDPOINT_PICKER_METRICS,
        ),
    }
    errors: list[dict[str, object]] = []
    consecutive_failures = {source: 0 for source in sources}
    max_consecutive_failures = {source: 0 for source in sources}
    source_samples = {source: 0 for source in sources}
    metric_rows = 0
    started = time.monotonic()
    sample = 0

    with output.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["sample", "elapsed_s", "unix_s", "source", "metric", "labels", "value"]
        )
        timeout = aiohttp.ClientTimeout(total=args.request_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                elapsed = time.monotonic() - started
                if elapsed > args.duration or (
                    args.stop_file and Path(args.stop_file).exists()
                ):
                    break
                results = await asyncio.gather(
                    *(
                        fetch(
                            session,
                            source,
                            url,
                            source_token,
                            args.insecure_https,
                        )
                        for source, (url, source_token, _) in sources.items()
                    ),
                    return_exceptions=True,
                )
                now = time.time()
                for source, result in zip(sources, results):
                    if isinstance(result, Exception):
                        consecutive_failures[source] += 1
                        max_consecutive_failures[source] = max(
                            max_consecutive_failures[source], consecutive_failures[source]
                        )
                        errors.append(
                            {
                                "sample": sample,
                                "elapsed_s": round(elapsed, 6),
                                "source": source,
                                "error": repr(result),
                            }
                        )
                        continue
                    consecutive_failures[source] = 0
                    source_samples[source] += 1
                    _, text = result
                    selected = sources[source][2]
                    for metric, labels, value in parse_rows(text, selected):
                        writer.writerow(
                            [
                                sample,
                                f"{elapsed:.6f}",
                                f"{now:.6f}",
                                source,
                                metric,
                                labels,
                                value,
                            ]
                        )
                        metric_rows += 1
                stream.flush()
                sample += 1
                next_sample = started + sample * args.interval
                await asyncio.sleep(max(0.0, next_sample - time.monotonic()))

    valid = (
        metric_rows > 0
        and all(source_samples.values())
        and not any(count > 1 for count in max_consecutive_failures.values())
    )
    errors_path.write_text(json.dumps(errors, indent=2) + "\n")
    contract_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "samplerVersion": SAMPLER_VERSION,
                "samplerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "valid": valid,
                "samples": sample,
                "metricRows": metric_rows,
                "successfulSamplesBySource": source_samples,
                "maxConsecutiveFailuresBySource": max_consecutive_failures,
            },
            indent=2,
        )
        + "\n"
    )
    return 0 if valid else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefill-url", required=True)
    parser.add_argument("--decode-url", required=True)
    parser.add_argument("--endpoint-picker-url", required=True)
    parser.add_argument("--token-file", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--stop-file", default="")
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--insecure-https", action="store_true")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.duration <= 0:
        parser.error("--duration must be positive")
    if args.request_timeout <= 0:
        parser.error("--request-timeout must be positive")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))

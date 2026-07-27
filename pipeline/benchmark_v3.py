#!/usr/bin/env python3
"""
Flow-control Benchmark v3.

Runs inside the llm-test cluster from the existing guidellm runner pod.
Captures:
  - deterministic measured-token prompts
  - client-side request metrics per tenant
  - pre/post endpoint-picker and vLLM metrics
  - periodic vLLM/EPP gauge samples during each run
  - per-second concurrency samples (target vs actual) for traffic shape graphs

v3 changes from v2:
  - Seeded noisy sinusoidal (deterministic, reproducible across repeats)
  - Concurrency samples CSV output (enables traffic shape visualization)
  - New scenario: consolidation_demo (Phase 1 premium + Phase 2 standard pressure)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import re
import ssl
import statistics
import time
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import aiohttp


MODEL_NAME = "openai/gpt-oss-20b"
BASE_URL = "http://inference-gateway-istio.redhat-ods-applications.svc.cluster.local/llm-test/gpt-oss-20b-fc"
COMPLETIONS_URL = BASE_URL + "/v1/completions"
TOKENIZE_URL = BASE_URL + "/tokenize"
EPP_METRICS_URL = "http://gpt-oss-20b-fc-epp-service.llm-test.svc.cluster.local:9090/metrics"
VLLM_METRICS_URL = "https://gpt-oss-20b-fc-kserve-workload-svc.llm-test.svc.cluster.local:8000/metrics"

OBJECTIVES = {
    100: "gpt-oss-premium",
    0: "gpt-oss-standard",
    -10: "gpt-oss-batch",
}


@dataclass
class Tenant:
    fairness_id: str
    priority: int
    phases: list[dict[str, Any]]
    objective: str | None = None

    @property
    def inference_objective(self) -> str:
        return self.objective or OBJECTIVES[self.priority]


@dataclass
class RequestSample:
    run_id: str
    scenario: str
    tenant: str
    priority: int
    objective: str
    status: str
    start_s: float
    ttft_s: float | None
    latency_s: float
    stream_chunks: int


def now_s() -> float:
    return time.monotonic()


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, math.ceil(q * len(xs)) - 1))
    return xs[idx]


def histogram_quantile(delta_buckets: dict[float, float], count: float, q: float) -> float | None:
    if count <= 0:
        return None
    target = count * q
    for le in sorted(delta_buckets):
        if delta_buckets[le] >= target:
            return le
    return None


def parse_labels(label_text: str) -> dict[str, str]:
    labels = {}
    for part in re.finditer(r'([a-zA-Z_:][a-zA-Z0-9_:]*)="([^"]*)"', label_text):
        labels[part.group(1)] = part.group(2)
    return labels


def parse_prometheus(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    parsed: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        metric_part, _, value_part = line.rpartition(" ")
        try:
            value = float(value_part)
        except ValueError:
            continue
        if "{" in metric_part:
            name, label_part = metric_part.split("{", 1)
            labels = parse_labels(label_part.rstrip("}"))
        else:
            name, labels = metric_part, {}
        parsed[(name, tuple(sorted(labels.items())))] = value
    return parsed


def labels_to_dict(labels: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return dict(labels)


def scrape_url(url: str, token: str | None = None, attempts: int = 4, delay_s: float = 2.0) -> str:
    allow_failures = os.environ.get("ALLOW_METRIC_SCRAPE_FAILURES") == "1"
    max_attempts = 1 if allow_failures else attempts
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        ctx = ssl._create_unverified_context() if url.startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            time.sleep(delay_s * attempt)
    if allow_failures:
        return ""
    raise last_exc or RuntimeError(f"failed to scrape {url}")


async def tokenize_count(session: aiohttp.ClientSession, prompt: str) -> int:
    async with session.post(
        TOKENIZE_URL,
        json={"model": MODEL_NAME, "prompt": prompt},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        data = await resp.json()
        if resp.status != 200:
            raise RuntimeError(f"tokenize failed {resp.status}: {data}")
        return int(data["count"])


async def build_prompt(session: aiohttp.ClientSession, target_tokens: int, seed: int) -> tuple[str, int]:
    rng = random.Random(seed)
    lead = (
        f"flow-control benchmark sample {seed}. "
        f"Trace id {rng.randrange(10**12):012d}. "
        "Summarize the operational signal in plain English. "
    )
    unit = "inference traffic queue priority latency throughput capacity fairness "

    lo, hi = 0, target_tokens + 64
    best = lead
    best_count = await tokenize_count(session, best)
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = lead + (unit * mid)
        count = await tokenize_count(session, candidate)
        if count <= target_tokens:
            best, best_count = candidate, count
            lo = mid + 1
        else:
            hi = mid - 1

    # Pad with a token that is usually one token in this tokenizer.
    # Verify every append with /tokenize; stop if it overshoots.
    while best_count < target_tokens:
        candidate = best + " hello"
        count = await tokenize_count(session, candidate)
        if count > target_tokens or count == best_count:
            break
        best, best_count = candidate, count

    return best, best_count


async def build_prompt_pool(target_tokens: int, pool_size: int) -> list[dict[str, Any]]:
    connector = aiohttp.TCPConnector(limit=16, limit_per_host=16)
    async with aiohttp.ClientSession(connector=connector) as session:
        prompts = []
        for i in range(pool_size):
            prompt, count = await build_prompt(session, target_tokens, seed=10_000 + i)
            prompts.append({"id": i, "prompt": prompt, "tokens": count})
            if i % 50 == 0:
                print(json.dumps({"event": "prompt_pool_progress", "built": i + 1, "pool_size": pool_size, "tokens": count}), flush=True)
        return prompts


def target_for_phase(phases: list[dict[str, Any]], elapsed: float, rng: random.Random | None = None) -> int:
    """Calculate target concurrency for the current elapsed time.

    When pattern is noisy_sinusoidal, uses the provided seeded RNG for
    deterministic noise (reproducible across repeats with the same seed).
    """
    for phase in phases:
        start = float(phase["start_s"])
        end = start + float(phase["duration_s"])
        if start <= elapsed < end:
            if phase.get("pattern") == "noisy_sinusoidal":
                phase_elapsed = elapsed - start
                center = float(phase["center"])
                amplitude = float(phase["amplitude"])
                period = float(phase.get("period_s", 20))
                phase_offset = float(phase.get("phase_offset", 0))
                wave = center + amplitude * math.sin((phase_elapsed / period) * 2 * math.pi + (phase_offset * 2 * math.pi))
                if rng is not None:
                    noise = rng.gauss(0, max(0.2, center * 0.05))
                    spike = rng.randint(1, max(1, int(center * 0.2))) if rng.random() < 0.04 else 0
                else:
                    noise = random.gauss(0, max(0.2, center * 0.05))
                    spike = random.randint(1, max(1, int(center * 0.2))) if random.random() < 0.04 else 0
                wave += noise + spike
                if phase.get("ramp_s"):
                    wave *= min(1.0, max(0.0, phase_elapsed / float(phase["ramp_s"])))
                return max(0, int(round(wave)))
            return int(phase["concurrency"])
    return 0


async def send_one(
    session: aiohttp.ClientSession,
    run_id: str,
    scenario: str,
    tenant: Tenant,
    prompt: str,
    output_tokens: int,
    start_zero: float,
    samples: list[RequestSample],
):
    start = now_s()
    ttft = None
    chunks = 0
    status = "Unknown"
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "max_tokens": output_tokens,
        "stream": True,
        "ignore_eos": True,
    }
    headers = {
        "x-gateway-inference-fairness-id": tenant.fairness_id,
        "x-gateway-inference-objective": tenant.inference_objective,
    }
    try:
        async with session.post(
            COMPLETIONS_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            status = str(resp.status)
            if resp.status == 200:
                async for chunk in resp.content.iter_any():
                    if chunk:
                        chunks += 1
                        if ttft is None:
                            ttft = now_s() - start
            else:
                await resp.read()
    except asyncio.TimeoutError:
        status = "Timeout"
    except asyncio.CancelledError:
        status = "Cancelled"
    except Exception as exc:
        status = f"Error:{type(exc).__name__}"
    finally:
        samples.append(
            RequestSample(
                run_id=run_id,
                scenario=scenario,
                tenant=tenant.fairness_id,
                priority=tenant.priority,
                objective=tenant.inference_objective,
                status=status,
                start_s=start - start_zero,
                ttft_s=ttft,
                latency_s=now_s() - start,
                stream_chunks=chunks,
            )
        )


async def tenant_driver(
    session: aiohttp.ClientSession,
    run_id: str,
    scenario: str,
    tenant: Tenant,
    prompts: list[dict[str, Any]],
    output_tokens: int,
    duration_s: int,
    drain_timeout_s: int,
    start_zero: float,
    samples: list[RequestSample],
    concurrency_log: list[dict[str, Any]],
    traffic_seed: int = 42,
):
    inflight: set[asyncio.Task] = set()
    prompt_idx = 0
    rng = random.Random(traffic_seed + hash(tenant.fairness_id))
    last_log_s = 0.0
    try:
        while now_s() - start_zero < duration_s:
            elapsed = now_s() - start_zero
            target = target_for_phase(tenant.phases, elapsed, rng)
            inflight = {task for task in inflight if not task.done()}
            actual = len(inflight)

            # Record concurrency sample once per second
            if elapsed - last_log_s >= 1.0:
                concurrency_log.append({
                    "elapsed_s": round(elapsed, 3),
                    "tenant": tenant.fairness_id,
                    "target_concurrency": target,
                    "actual_inflight": actual,
                })
                last_log_s = elapsed

            while len(inflight) < target:
                prompt = prompts[prompt_idx % len(prompts)]["prompt"]
                prompt_idx += 1
                task = asyncio.create_task(
                    send_one(session, run_id, scenario, tenant, prompt, output_tokens, start_zero, samples)
                )
                inflight.add(task)
            await asyncio.sleep(0.05)
    finally:
        inflight = {task for task in inflight if not task.done()}
        if inflight:
            done, pending = await asyncio.wait(inflight, timeout=drain_timeout_s)
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            if done:
                await asyncio.gather(*done, return_exceptions=True)


async def metric_sampler(run_id: str, scenario: str, duration_s: int, start_zero: float, token: str | None, out_rows: list[dict[str, Any]]):
    loop = asyncio.get_event_loop()
    while now_s() - start_zero < duration_s:
        elapsed = now_s() - start_zero
        row = {"run_id": run_id, "scenario": scenario, "elapsed_s": round(elapsed, 3)}
        try:
            vllm_text = await loop.run_in_executor(None, scrape_url, VLLM_METRICS_URL)
            vllm = parse_prometheus(vllm_text)
            for (name, labels), value in vllm.items():
                if name in {"vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:gpu_cache_usage_perc"}:
                    row[name] = value
        except Exception as exc:
            row["vllm_scrape_error"] = type(exc).__name__
        try:
            epp_text = await loop.run_in_executor(None, scrape_url, EPP_METRICS_URL, token)
            epp = parse_prometheus(epp_text)
            for (name, labels), value in epp.items():
                if name in {"inference_extension_flow_control_queue_size", "inference_extension_flow_control_queue_bytes"}:
                    label_dict = labels_to_dict(labels)
                    fid = label_dict.get("fairness_id", "unknown")
                    row[f"{name}|{fid}"] = value
        except Exception as exc:
            row["epp_scrape_error"] = type(exc).__name__
        out_rows.append(row)
        await asyncio.sleep(1)


def summarize_samples(run_id: str, scenario: str, samples: list[RequestSample], duration_s: int) -> list[dict[str, Any]]:
    rows = []
    by_tenant: dict[str, list[RequestSample]] = defaultdict(list)
    for sample in samples:
        by_tenant[sample.tenant].append(sample)
    for tenant, tenant_samples in sorted(by_tenant.items()):
        ttfts = [s.ttft_s for s in tenant_samples if s.ttft_s is not None and s.status == "200"]
        lats = [s.latency_s for s in tenant_samples if s.status == "200"]
        counts = Counter(s.status for s in tenant_samples)
        rows.append({
            "run_id": run_id,
            "scenario": scenario,
            "tenant": tenant,
            "priority": tenant_samples[0].priority if tenant_samples else None,
            "objective": tenant_samples[0].objective if tenant_samples else None,
            "duration_s": duration_s,
            "total": len(tenant_samples),
            "http_200": counts.get("200", 0),
            "http_429": counts.get("429", 0),
            "http_503": counts.get("503", 0),
            "timeouts": counts.get("Timeout", 0),
            "errors": sum(v for k, v in counts.items() if k.startswith("Error")),
            "throughput_rps": len(tenant_samples) / duration_s if duration_s else 0,
            "ttft_p50_s": percentile(ttfts, 0.50),
            "ttft_p95_s": percentile(ttfts, 0.95),
            "ttft_p99_s": percentile(ttfts, 0.99),
            "latency_p50_s": percentile(lats, 0.50),
            "latency_p95_s": percentile(lats, 0.95),
            "latency_p99_s": percentile(lats, 0.99),
        })
    return rows


def metric_delta(pre: str, post: str, tenants: set[str]) -> dict[str, Any]:
    before = parse_prometheus(pre)
    after = parse_prometheus(post)
    result: dict[str, Any] = {
        "vllm": {},
        "endpoint_picker_queue": [],
    }

    for metric in ["vllm:prompt_tokens_total", "vllm:generation_tokens_total"]:
        total = 0.0
        for key, after_val in after.items():
            if key[0] == metric:
                total += after_val - before.get(key, 0.0)
        result["vllm"][metric] = total

    success = {}
    for key, after_val in after.items():
        name, labels = key
        if name == "vllm:request_success_total":
            label_dict = labels_to_dict(labels)
            reason = label_dict.get("finished_reason", "unknown")
            success[reason] = success.get(reason, 0.0) + after_val - before.get(key, 0.0)
    result["vllm"]["request_success_delta"] = success

    for hist in ["vllm:time_to_first_token_seconds", "vllm:e2e_request_latency_seconds"]:
        bucket_deltas: dict[float, float] = {}
        count = 0.0
        sum_delta = 0.0
        for key, after_val in after.items():
            name, labels = key
            if not name.startswith(hist):
                continue
            before_val = before.get(key, 0.0)
            label_dict = labels_to_dict(labels)
            if name.endswith("_bucket"):
                le_text = label_dict.get("le")
                if le_text and le_text != "+Inf":
                    bucket_deltas[float(le_text)] = bucket_deltas.get(float(le_text), 0.0) + after_val - before_val
            elif name.endswith("_count"):
                count += after_val - before_val
            elif name.endswith("_sum"):
                sum_delta += after_val - before_val
        result["vllm"][hist] = {
            "count": count,
            "sum": sum_delta,
            "mean_s": sum_delta / count if count else None,
            "p50_s": histogram_quantile(bucket_deltas, count, 0.50),
            "p95_s": histogram_quantile(bucket_deltas, count, 0.95),
            "p99_s": histogram_quantile(bucket_deltas, count, 0.99),
        }

    queue_acc: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"sum": 0.0, "count": 0.0})
    for key, after_val in after.items():
        name, labels = key
        if not name.startswith("inference_extension_flow_control_request_queue_duration_seconds_"):
            continue
        label_dict = labels_to_dict(labels)
        fid = label_dict.get("fairness_id")
        if fid not in tenants:
            continue
        pri = label_dict.get("priority", "")
        before_val = before.get(key, 0.0)
        if name.endswith("_sum"):
            queue_acc[(fid, pri)]["sum"] += after_val - before_val
        elif name.endswith("_count"):
            queue_acc[(fid, pri)]["count"] += after_val - before_val
    for (fid, pri), vals in sorted(queue_acc.items()):
        count = vals["count"]
        result["endpoint_picker_queue"].append({
            "fairness_id": fid,
            "priority": pri,
            "queue_count_delta": count,
            "queue_sum_s_delta": vals["sum"],
            "queue_mean_ms": (vals["sum"] / count * 1000) if count else None,
        })
    return result


async def run_workload(
    run_id: str,
    scenario: str,
    tenants: list[Tenant],
    duration_s: int,
    drain_timeout_s: int,
    prompts: list[dict[str, Any]],
    output_tokens: int,
    token: str | None,
    out_dir: Path,
    traffic_seed: int = 42,
) -> dict[str, Any]:
    print(json.dumps({"event": "run_start", "run_id": run_id, "scenario": scenario, "duration_s": duration_s, "drain_timeout_s": drain_timeout_s}), flush=True)
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    pre_epp = scrape_url(EPP_METRICS_URL, token)
    pre_vllm = scrape_url(VLLM_METRICS_URL)
    (run_dir / "pre_epp.prom").write_text(pre_epp)
    (run_dir / "pre_vllm.prom").write_text(pre_vllm)

    samples: list[RequestSample] = []
    metric_rows: list[dict[str, Any]] = []
    concurrency_log: list[dict[str, Any]] = []
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        start_zero = now_s()
        tasks = [
            asyncio.create_task(tenant_driver(
                session, run_id, scenario, tenant, prompts, output_tokens,
                duration_s, drain_timeout_s, start_zero, samples,
                concurrency_log, traffic_seed,
            ))
            for tenant in tenants
        ]
        sampler_task = asyncio.create_task(metric_sampler(run_id, scenario, duration_s + drain_timeout_s, start_zero, token, metric_rows))
        while now_s() - start_zero < duration_s:
            elapsed = int(now_s() - start_zero)
            if elapsed % 30 == 0:
                print(json.dumps({"event": "run_progress", "run_id": run_id, "scenario": scenario, "elapsed_s": elapsed, "samples": len(samples)}), flush=True)
            await asyncio.sleep(1)
        await asyncio.gather(*tasks, return_exceptions=True)
        sampler_task.cancel()
        await asyncio.gather(sampler_task, return_exceptions=True)

    post_epp = scrape_url(EPP_METRICS_URL, token)
    post_vllm = scrape_url(VLLM_METRICS_URL)
    (run_dir / "post_epp.prom").write_text(post_epp)
    (run_dir / "post_vllm.prom").write_text(post_vllm)

    with (run_dir / "client_samples.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(samples[0]).keys()) if samples else list(RequestSample("", "", "", 0, "", "", 0, 0, 0, 0).__dict__.keys()))
        writer.writeheader()
        for sample in samples:
            writer.writerow(asdict(sample))

    if metric_rows:
        fieldnames = sorted({key for row in metric_rows for key in row})
        with (run_dir / "metric_samples.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(metric_rows)

    if concurrency_log:
        with (run_dir / "concurrency_samples.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["elapsed_s", "tenant", "target_concurrency", "actual_inflight"])
            writer.writeheader()
            writer.writerows(concurrency_log)

    tenants_set = {tenant.fairness_id for tenant in tenants}
    summary = {
        "run_id": run_id,
        "scenario": scenario,
        "duration_s": duration_s,
        "drain_timeout_s": drain_timeout_s,
        "tenants": [asdict(t) for t in tenants],
        "client_summary": summarize_samples(run_id, scenario, samples, duration_s),
        "metric_delta": metric_delta(pre_epp + "\n" + pre_vllm, post_epp + "\n" + post_vllm, tenants_set),
        "metric_sample_summary": summarize_metric_samples(metric_rows),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"event": "run_complete", "run_id": run_id, "scenario": scenario, "samples": len(samples)}), flush=True)
    return summary


def summarize_metric_samples(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key in ["vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:gpu_cache_usage_perc"]:
        vals = [float(row[key]) for row in rows if key in row and row[key] not in ("", None)]
        if vals:
            result[key] = {
                "max": max(vals),
                "mean": statistics.mean(vals),
                "p95": percentile(vals, 0.95),
            }
    return result


def scenario_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    half = scenario_duration // 2
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "test1_single_endpoint_then_double",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": scenario_duration, "concurrency": 8}]),
                Tenant("premium-tenant-b", 100, [{"start_s": 0, "duration_s": half, "concurrency": 0}, {"start_s": half, "duration_s": half, "concurrency": 8}]),
            ],
            scenario_duration,
        ),
        (
            "test2_priority_differentiation",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 6, "amplitude": 2, "period_s": 55, "phase_offset": 0.0}]),
                Tenant("premium-tenant-b", 100, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 6, "amplitude": 2, "period_s": 42, "phase_offset": 0.3}]),
                Tenant("premium-tenant-c", 100, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 4, "amplitude": 1, "period_s": 38, "phase_offset": 0.6}]),
                Tenant("standard-tenant-a", 0, [{"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal", "center": 8, "amplitude": 3, "period_s": 30, "phase_offset": 0.1}, {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal", "center": 55, "amplitude": 15, "period_s": 35, "phase_offset": 0.1}, {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal", "center": 8, "amplitude": 3, "period_s": 30, "phase_offset": 0.1}]),
                Tenant("standard-tenant-b", 0, [{"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal", "center": 8, "amplitude": 3, "period_s": 28, "phase_offset": 0.5}, {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal", "center": 55, "amplitude": 15, "period_s": 28, "phase_offset": 0.5}, {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal", "center": 8, "amplitude": 3, "period_s": 28, "phase_offset": 0.5}]),
            ],
            scenario_duration,
        ),
        (
            "test3_fairness_three_premium_one_spike",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal", "center": 10, "amplitude": 3, "period_s": 50, "phase_offset": 0.0}, {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal", "center": 60, "amplitude": 15, "period_s": 50, "phase_offset": 0.0}, {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal", "center": 10, "amplitude": 3, "period_s": 50, "phase_offset": 0.0}]),
                Tenant("premium-tenant-b", 100, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 10, "amplitude": 3, "period_s": 37, "phase_offset": 0.33}]),
                Tenant("premium-tenant-c", 100, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 10, "amplitude": 3, "period_s": 43, "phase_offset": 0.66}]),
            ],
            scenario_duration,
        ),
        (
            "test4_priority_inversion_batch_surge",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 10, "amplitude": 3, "phase_offset": 0.0}]),
                Tenant("standard-tenant-a", 0, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 10, "amplitude": 3, "phase_offset": 0.35}]),
                Tenant("batch-tenant-a", -10, [{"start_s": 0, "duration_s": half, "concurrency": 0}, {"start_s": half, "duration_s": half, "pattern": "noisy_sinusoidal", "center": 30, "amplitude": 8, "phase_offset": 0.15, "ramp_s": 10}]),
            ],
            scenario_duration,
        ),
    ]


def pressure_scenario_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Pressure-scaled scenario definitions based on the 160-concurrency saturation knee."""
    half = scenario_duration // 2
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "pressure_test1_single_endpoint_then_double",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": scenario_duration, "concurrency": 80}]),
                Tenant("premium-tenant-b", 100, [{"start_s": 0, "duration_s": half, "concurrency": 0}, {"start_s": half, "duration_s": half, "concurrency": 80}]),
            ],
            scenario_duration,
        ),
        (
            "pressure_test2_priority_differentiation",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": scenario_duration, "concurrency": 6}]),
                Tenant("premium-tenant-b", 100, [{"start_s": 0, "duration_s": scenario_duration, "concurrency": 5}]),
                Tenant("premium-tenant-c", 100, [{"start_s": 0, "duration_s": scenario_duration, "concurrency": 5}]),
                Tenant("standard-tenant-a", 0, [{"start_s": 0, "duration_s": quarter, "concurrency": 8}, {"start_s": quarter, "duration_s": middle, "concurrency": 72}, {"start_s": quarter + middle, "duration_s": quarter, "concurrency": 8}]),
                Tenant("standard-tenant-b", 0, [{"start_s": 0, "duration_s": quarter, "concurrency": 8}, {"start_s": quarter, "duration_s": middle, "concurrency": 72}, {"start_s": quarter + middle, "duration_s": quarter, "concurrency": 8}]),
            ],
            scenario_duration,
        ),
        (
            "pressure_test3_fairness_three_premium_one_spike",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": quarter, "concurrency": 16}, {"start_s": quarter, "duration_s": middle, "concurrency": 96}, {"start_s": quarter + middle, "duration_s": quarter, "concurrency": 16}]),
                Tenant("premium-tenant-b", 100, [{"start_s": 0, "duration_s": quarter, "concurrency": 16}, {"start_s": quarter, "duration_s": middle, "concurrency": 32}, {"start_s": quarter + middle, "duration_s": quarter, "concurrency": 16}]),
                Tenant("premium-tenant-c", 100, [{"start_s": 0, "duration_s": quarter, "concurrency": 16}, {"start_s": quarter, "duration_s": middle, "concurrency": 32}, {"start_s": quarter + middle, "duration_s": quarter, "concurrency": 16}]),
            ],
            scenario_duration,
        ),
        (
            "pressure_test4_priority_inversion_batch_surge",
            [
                Tenant("premium-tenant-a", 100, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 16, "amplitude": 4, "phase_offset": 0.0}]),
                Tenant("standard-tenant-a", 0, [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal", "center": 16, "amplitude": 4, "phase_offset": 0.35}]),
                Tenant("batch-tenant-a", -10, [{"start_s": 0, "duration_s": half, "concurrency": 0}, {"start_s": half, "duration_s": half, "pattern": "noisy_sinusoidal", "center": 140, "amplitude": 20, "phase_offset": 0.15, "ramp_s": 10}]),
            ],
            scenario_duration,
        ),
    ]


def sweep_defs(points: list[int], duration: int) -> list[tuple[str, list[Tenant], int]]:
    return [
        (
            f"saturation_concurrency_{point}",
            [Tenant("sweep-standard", 0, [{"start_s": 0, "duration_s": duration, "concurrency": point}])],
            duration,
        )
        for point in points
    ]


def consolidation_demo_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Scenario 1: GPU Consolidation + Flow Control demo — calibrated for max_num_seqs=48.

    Phase 1 (0 to half): Two premium tenants, noisy sinusoidal center=8 each (~16 total).
      Well within maxseq48 capacity → SLA maintained, consolidation is safe.
    Phase 2 (half to end): Same premiums + standard ramps in at center=35.
      Total ~51, pushing past maxseq48 cap → flow control activates to protect premium.
    """
    half = scenario_duration // 2
    return [
        (
            "consolidation_demo",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 3, "period_s": 50, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 3, "period_s": 37, "phase_offset": 0.25},
                ]),
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": half, "concurrency": 0},
                    {"start_s": half, "duration_s": half, "pattern": "noisy_sinusoidal",
                     "center": 35, "amplitude": 10, "period_s": 25, "phase_offset": 0.5, "ramp_s": 15},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test2_priority_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Test 2: Priority Differentiation with noisy sinusoidal traffic.

    Same concurrency levels as the clean-pass flat-traffic run that produced
    sub-second results with clear priority separation on max_num_seqs=128.
    Premium at center=6, standard surging from center=8 to center=40 mid-test.
    """
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "test2_priority_noisy",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 6, "amplitude": 2, "period_s": 47, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 5, "amplitude": 2, "period_s": 53, "phase_offset": 0.3},
                ]),
                Tenant("premium-tenant-c", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 5, "amplitude": 2, "period_s": 41, "phase_offset": 0.6},
                ]),
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 3, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 72, "amplitude": 15, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 3, "period_s": 30, "phase_offset": 0.1},
                ]),
                Tenant("standard-tenant-b", 0, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 3, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 72, "amplitude": 15, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 3, "period_s": 35, "phase_offset": 0.5},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test3_fairness_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Test 3: Fairness among premium tenants with noisy sinusoidal traffic.

    Same concurrency as clean-pass: tenant-a spikes to center=48 (peaks ~72-96),
    tenants b and c stay at center=24 (peaks ~32). All premium priority=100.
    Proves round-robin fairness within same priority band under load.
    """
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "test3_fairness_noisy",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 96, "amplitude": 16, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "period_s": 45, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "period_s": 55, "phase_offset": 0.35},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 32, "amplitude": 8, "period_s": 55, "phase_offset": 0.35},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "period_s": 55, "phase_offset": 0.35},
                ]),
                Tenant("premium-tenant-c", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "period_s": 60, "phase_offset": 0.7},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 32, "amplitude": 8, "period_s": 60, "phase_offset": 0.7},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "period_s": 60, "phase_offset": 0.7},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test3_fairness_saturated_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Test 3 rerun: same-priority fairness with sustained noisy saturation.

    The prior noisy run was clean but did not queue: running averaged ~70 and
    waiting stayed at 0. This variant keeps the traffic realistic but raises
    the mid-test centers and narrows amplitude so the valleys stay above the
    max_num_seqs=128 boundary.
    """
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "test3_fairness_noisy_saturated",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 24, "amplitude": 4, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 80, "amplitude": 8, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 24, "amplitude": 4, "period_s": 45, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 24, "amplitude": 4, "period_s": 55, "phase_offset": 0.35},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 40, "amplitude": 4, "period_s": 55, "phase_offset": 0.35},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 24, "amplitude": 4, "period_s": 55, "phase_offset": 0.35},
                ]),
                Tenant("premium-tenant-c", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 24, "amplitude": 4, "period_s": 60, "phase_offset": 0.7},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 40, "amplitude": 4, "period_s": 60, "phase_offset": 0.7},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 24, "amplitude": 4, "period_s": 60, "phase_offset": 0.7},
                ]),
            ],
            scenario_duration,
        ),
    ]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--input-tokens", type=int, default=512)
    parser.add_argument("--output-tokens", type=int, default=128)
    parser.add_argument("--prompt-pool-size", type=int, default=384)
    parser.add_argument("--sweep-duration", type=int, default=30)
    parser.add_argument("--sweep-points", default="8,16,24,32,48,64,96,128,160")
    parser.add_argument("--scenario-duration", type=int, default=300)
    parser.add_argument("--drain-timeout", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--skip-sweep", action="store_true")
    parser.add_argument("--skip-scenarios", action="store_true")
    parser.add_argument("--pressure-scenarios", action="store_true")
    parser.add_argument("--consolidation-demo", action="store_true",
                        help="Run the 2-phase consolidation demo (premium baseline → standard pressure)")
    parser.add_argument("--test2-noisy", action="store_true",
                        help="Run Test 2 priority differentiation with noisy sinusoidal at calibrated concurrency")
    parser.add_argument("--test3-noisy", action="store_true",
                        help="Run Test 3 fairness with noisy sinusoidal at calibrated concurrency")
    parser.add_argument("--test3-noisy-saturated", action="store_true",
                        help="Run Test 3 fairness with noisy sinusoidal traffic calibrated for sustained saturation")
    parser.add_argument("--scenario-filter", default="")
    parser.add_argument("--warmup-duration", type=int, default=0)
    parser.add_argument("--warmup-concurrency", type=int, default=16)
    parser.add_argument("--stabilization-repeats", type=int, default=0,
                        help="Run each selected scenario this many extra times before counted repeats")
    parser.add_argument("--traffic-seed", type=int, default=42,
                        help="Seed for deterministic noisy sinusoidal traffic (same seed = same pattern)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("METRICS_TOKEN")

    config = {
        "model_name": MODEL_NAME,
        "base_url": BASE_URL,
        "input_tokens_target": args.input_tokens,
        "output_tokens": args.output_tokens,
        "prompt_pool_size": args.prompt_pool_size,
        "sweep_duration_s": args.sweep_duration,
        "scenario_duration_s": args.scenario_duration,
        "drain_timeout_s": args.drain_timeout,
        "repeats": args.repeats,
        "warmup_duration_s": args.warmup_duration,
        "warmup_concurrency": args.warmup_concurrency,
        "stabilization_repeats": args.stabilization_repeats,
        "warmup_included_in_summaries": False,
        "traffic_seed": args.traffic_seed,
        "headers": ["x-gateway-inference-objective", "x-gateway-inference-fairness-id"],
        "objectives": OBJECTIVES,
    }
    (out_dir / "benchmark_config.json").write_text(json.dumps(config, indent=2))

    print(json.dumps({"event": "benchmark_start", **config}), flush=True)
    prompts = await build_prompt_pool(args.input_tokens, args.prompt_pool_size)
    (out_dir / "prompt_pool.json").write_text(json.dumps(prompts, indent=2))
    prompt_counts = Counter(p["tokens"] for p in prompts)
    print(json.dumps({"event": "prompt_pool_complete", "token_counts": dict(prompt_counts)}), flush=True)

    if args.warmup_duration > 0:
        warmup_tenants = [
            Tenant("warmup-standard", 0, [{"start_s": 0, "duration_s": args.warmup_duration, "concurrency": args.warmup_concurrency}])
        ]
        warmup_summary = await run_workload(
            "00-warmup_standard",
            "warmup_standard",
            warmup_tenants,
            args.warmup_duration,
            args.drain_timeout,
            prompts,
            args.output_tokens,
            token,
            out_dir,
            args.traffic_seed,
        )
        (out_dir / "warmup_summary.json").write_text(json.dumps(warmup_summary, indent=2))
        await asyncio.sleep(5)

    all_defs: list[tuple[str, list[Tenant], int]] = []
    if not args.skip_sweep:
        sweep_points = [int(point.strip()) for point in args.sweep_points.split(",") if point.strip()]
        all_defs.extend(sweep_defs(sweep_points, args.sweep_duration))
    if not args.skip_scenarios:
        if args.consolidation_demo:
            all_defs.extend(consolidation_demo_defs(args.scenario_duration))
        if args.test2_noisy:
            all_defs.extend(noisy_test2_priority_defs(args.scenario_duration))
        if args.test3_noisy:
            all_defs.extend(noisy_test3_fairness_defs(args.scenario_duration))
        if args.test3_noisy_saturated:
            all_defs.extend(noisy_test3_fairness_saturated_defs(args.scenario_duration))
        if not (args.consolidation_demo or args.test2_noisy or args.test3_noisy or args.test3_noisy_saturated):
            if args.pressure_scenarios:
                all_defs.extend(pressure_scenario_defs(args.scenario_duration))
            else:
                all_defs.extend(scenario_defs(args.scenario_duration))
    if args.scenario_filter:
        wanted = {item.strip() for item in args.scenario_filter.split(",") if item.strip()}
        all_defs = [item for item in all_defs if item[0] in wanted]

    stabilization_summaries = []
    if args.stabilization_repeats > 0:
        for scenario_idx, (scenario, tenants, duration) in enumerate(all_defs, start=1):
            for repeat in range(1, args.stabilization_repeats + 1):
                run_id = f"stabilization-{scenario_idx:02d}-{scenario}-s{repeat:02d}"
                summary = await run_workload(run_id, scenario, tenants, duration, args.drain_timeout, prompts, args.output_tokens, token, out_dir, args.traffic_seed)
                summary["stabilization_repeat"] = repeat
                stabilization_summaries.append(summary)
                await asyncio.sleep(5)
        (out_dir / "stabilization_summaries.json").write_text(json.dumps(stabilization_summaries, indent=2))

    summaries = []
    expanded_defs = []
    for scenario, tenants, duration in all_defs:
        for repeat in range(1, args.repeats + 1):
            expanded_defs.append((scenario, tenants, duration, repeat))

    for idx, (scenario, tenants, duration, repeat) in enumerate(expanded_defs, start=1):
        repeat_suffix = f"-r{repeat:02d}" if args.repeats > 1 else ""
        run_id = f"{idx:02d}-{scenario}{repeat_suffix}"
        summary = await run_workload(run_id, scenario, tenants, duration, args.drain_timeout, prompts, args.output_tokens, token, out_dir, args.traffic_seed)
        summary["repeat"] = repeat
        summaries.append(summary)
        await asyncio.sleep(5)

    (out_dir / "all_summaries.json").write_text(json.dumps(summaries, indent=2))

    with (out_dir / "summary.csv").open("w", newline="") as f:
        fieldnames = [
            "run_id", "repeat", "scenario", "tenant", "priority", "objective", "duration_s", "total", "http_200",
            "http_429", "http_503", "timeouts", "errors", "throughput_rps", "ttft_p50_s", "ttft_p95_s",
            "ttft_p99_s", "latency_p50_s", "latency_p95_s", "latency_p99_s",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for summary in summaries:
            for row in summary["client_summary"]:
                row_with_repeat = {"repeat": summary.get("repeat", 1), **row}
                writer.writerow(row_with_repeat)

    print(json.dumps({"event": "benchmark_complete", "runs": len(summaries), "output_dir": str(out_dir)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
multi-tenant flow-control Benchmark v3.

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


ENDPOINT_NAME = os.environ.get("ENDPOINT_NAME", "gpt-oss-20b-fc")
MODEL_NAME = "openai/gpt-oss-20b"
BASE_URL = os.environ.get(
    "BASE_URL",
    f"http://inference-gateway-istio.redhat-ods-applications.svc.cluster.local/llm-test/{ENDPOINT_NAME}",
)
COMPLETIONS_URL = BASE_URL + "/v1/completions"
TOKENIZE_URL = BASE_URL + "/tokenize"
EPP_METRICS_URL = os.environ.get(
    "EPP_METRICS_URL",
    f"http://{ENDPOINT_NAME}-epp-service.llm-test.svc.cluster.local:9090/metrics",
)
VLLM_METRICS_URL = os.environ.get(
    "VLLM_METRICS_URL",
    f"https://{ENDPOINT_NAME}-kserve-workload-svc.llm-test.svc.cluster.local:8000/metrics",
)

# Objective (priority-tier) names sent in the x-gateway-inference-objective header.
# These MUST match the InferenceObjective resources bound to the target pool, or
# the gate defaults every request to priority 0 (no prioritization). The upstream
# endpoint uses a different name prefix, so make it overridable per endpoint.
#   OBJECTIVE_PREFIX=gpt-oss             -> gpt-oss-premium/standard/batch (fc)
#   OBJECTIVE_PREFIX=gpt-oss-upstream-cc -> gpt-oss-upstream-cc-premium/... (upstream-cc)
_OBJ_PREFIX = os.environ.get("OBJECTIVE_PREFIX", "gpt-oss")
OBJECTIVES = {
    100: f"{_OBJ_PREFIX}-premium",
    0: f"{_OBJ_PREFIX}-standard",
    -10: f"{_OBJ_PREFIX}-batch",
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


def metric_label_suffix(labels: tuple[tuple[str, str], ...]) -> str:
    return "|".join(f"{key}={value}" for key, value in labels)


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


# v4: realistic prefix structure.
#
# v3 gave every prompt the same fixed lead ("multi-tenant flow-control
# benchmark sample ...") and then padded with the SAME repeated `unit`
# string, so after a short unique head every prompt was byte-identical.
# vLLM's block-level prefix cache matched that long shared tail, driving the
# measured prefix-cache hit rate to ~97% under load. For a benchmark whose
# subject is queueing, that deflates TTFT (prefill is skipped) and makes
# service time non-stationary over a run.
#
# v4 fixes it two ways:
#   1. A SHORT fixed system preamble (~15 tokens) models the realistic shared
#      prefix a bank actually has (a system prompt), not 90% of the body.
#   2. The body is drawn from a large word pool with a per-prompt seeded
#      shuffle, so each prompt's first ~40 tokens and its body are unique.
# Result: shared-prefix fraction is a small, realistic head rather than the
# whole prompt. With APC off (the discovery/spine default) this does not
# matter; with APC on (the cache-characterization arm) the measured hit rate
# should now look like production (single-digit to low-double-digit percent),
# not 97%.

SHARED_PREAMBLE = "You are a multi-tenant operations assistant. Answer concisely. "

# ~200 distinct words so the head and body of each prompt differ. Domain-flavored
# but deliberately varied so no two seeds produce the same token sequence.
_WORD_POOL = (
    "inference traffic queue priority latency throughput capacity fairness "
    "tenant scheduler dispatch admission backlog saturation replica gateway "
    "premium standard batch deferrable interactive burst quota headroom drain "
    "warmup steady baseline concurrency percentile histogram gauge counter scrape "
    "region cluster node device utilization memory bandwidth kernel decode prefill "
    "token prompt session request response stream chunk timeout retry backoff "
    "policy band ordering round robin strict weighted proportional isolation neighbor "
    "signal operational summary plain english trace identifier sample calibration "
    "objective threshold detector concurrency inflight limit reject shed preempt "
    "customer platform workload occupancy residency slot turnover pressure spike valley "
    "morning peak offpeak forecast anomaly incident mitigation rollback canary shadow "
    "audit compliance ledger account balance statement fraud alert dispute merchant "
).split()


async def build_prompt(session: aiohttp.ClientSession, target_tokens: int, seed: int) -> tuple[str, int]:
    rng = random.Random(seed)
    # Unique head: shuffled words + a per-prompt trace id, so the FIRST tokens
    # of every prompt differ. This is what breaks the pathological prefix match.
    head_words = _WORD_POOL[:]
    rng.shuffle(head_words)
    lead = (
        SHARED_PREAMBLE
        + f"Ticket {rng.randrange(10**12):012d}. "
        + " ".join(head_words[:24])
        + ". Summarize the operational signal. "
    )

    # Body: keep extending with fresh randomly-drawn words (verified via
    # /tokenize) until we hit the target. Because words are drawn per-append
    # from a shuffled pool with a per-prompt seed, the body is unique too.
    best = lead
    best_count = await tokenize_count(session, best)
    # Coarse fill: append chunks of random words, halving chunk size near target.
    chunk = 32
    while best_count < target_tokens and chunk >= 1:
        addition = " " + " ".join(rng.choice(_WORD_POOL) for _ in range(chunk))
        candidate = best + addition
        count = await tokenize_count(session, candidate)
        if count <= target_tokens:
            best, best_count = candidate, count
        else:
            chunk //= 2

    # Fine fill: single-word appends until exactly at target.
    while best_count < target_tokens:
        candidate = best + " " + rng.choice(_WORD_POOL)
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


# v4: global traffic multiplier. Scales every scenario's per-tenant target
# concurrency uniformly, so one flag re-levels a whole scenario without editing
# hardcoded centers. 1.0 = the scenario as authored (the "hot" level that peaks
# past the 128 knee). ~0.82 lands peaks AT the knee for the clean spine. Applied
# at the single chokepoint below so sinusoidal and flat phases scale identically.
TRAFFIC_SCALE = 1.0


def target_for_phase(phases: list[dict[str, Any]], elapsed: float, rng: random.Random | None = None) -> int:
    """Calculate target concurrency for the current elapsed time.

    When pattern is noisy_sinusoidal, uses the provided seeded RNG for
    deterministic noise (reproducible across repeats with the same seed).
    The result is multiplied by the module-level TRAFFIC_SCALE.
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
                return max(0, int(round(wave * TRAFFIC_SCALE)))
            return max(0, int(round(int(phase["concurrency"]) * TRAFFIC_SCALE)))
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
                # Stop latency timing at the OpenAI stream terminator. Waiting for
                # connection close via iter_any() produced a false 12.4–12.5 s e2e mode.
                async for line in resp.content:
                    if not line:
                        continue
                    chunks += 1
                    if ttft is None:
                        ttft = now_s() - start
                    if line.strip() == b"data: [DONE]":
                        break
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
    sample_interval_s: float = 0.5,
):
    inflight: set[asyncio.Task] = set()
    prompt_idx = 0
    rng = random.Random(traffic_seed + hash(tenant.fairness_id))
    next_log_s = 0.0
    try:
        while now_s() - start_zero < duration_s:
            elapsed = now_s() - start_zero
            target = target_for_phase(tenant.phases, elapsed, rng)
            inflight = {task for task in inflight if not task.done()}
            actual = len(inflight)

            if elapsed >= next_log_s:
                concurrency_log.append({
                    "elapsed_s": round(elapsed, 3),
                    "tenant": tenant.fairness_id,
                    "target_concurrency": target,
                    "actual_inflight": actual,
                })
                while next_log_s <= elapsed:
                    next_log_s += sample_interval_s

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


async def metric_sampler(
    run_id: str,
    scenario: str,
    duration_s: int,
    start_zero: float,
    token: str | None,
    out_rows: list[dict[str, Any]],
    sample_interval_s: float = 0.5,
):
    loop = asyncio.get_event_loop()
    next_sample_at = start_zero
    while now_s() - start_zero < duration_s:
        wait_s = next_sample_at - now_s()
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        sample_started = now_s()
        if sample_started - start_zero >= duration_s:
            break

        row = {
            "run_id": run_id,
            "scenario": scenario,
            "elapsed_s": round(sample_started - start_zero, 3),
            "sample_interval_s": sample_interval_s,
            "sample_lag_s": round(max(0.0, sample_started - next_sample_at), 4),
        }
        vllm_result, epp_result = await asyncio.gather(
            loop.run_in_executor(None, scrape_url, VLLM_METRICS_URL),
            loop.run_in_executor(None, scrape_url, EPP_METRICS_URL, token),
            return_exceptions=True,
        )

        if isinstance(vllm_result, Exception):
            row["vllm_scrape_error"] = type(vllm_result).__name__
        else:
            vllm = parse_prometheus(vllm_result)
            vllm_totals: dict[str, float] = defaultdict(float)
            vllm_cache_values: list[float] = []
            for (name, labels), value in vllm.items():
                if name in {
                    "vllm:num_requests_running",
                    "vllm:num_requests_waiting",
                    "vllm:gpu_cache_usage_perc",
                    "vllm:num_preemptions_total",
                }:
                    suffix = metric_label_suffix(labels)
                    if suffix:
                        row[f"{name}|{suffix}"] = value
                    if name == "vllm:gpu_cache_usage_perc":
                        vllm_cache_values.append(value)
                    else:
                        vllm_totals[name] += value
            row.update(vllm_totals)
            if vllm_cache_values:
                row["vllm:gpu_cache_usage_perc"] = max(vllm_cache_values)

        if isinstance(epp_result, Exception):
            row["epp_scrape_error"] = type(epp_result).__name__
        else:
            epp = parse_prometheus(epp_result)
            for (name, labels), value in epp.items():
                if name in {"inference_extension_flow_control_queue_size", "inference_extension_flow_control_queue_bytes"}:
                    label_dict = labels_to_dict(labels)
                    fid = label_dict.get("fairness_id", "unknown")
                    priority = label_dict.get("priority", "unknown")
                    suffix = metric_label_suffix(labels) or f"fairness_id={fid}|priority={priority}"
                    row[f"{name}|{suffix}"] = value
                # v4: also log the gate's live saturation signal + pool queue, so the
                # timeseries shows exactly when the gate closes and the queue forms.
                elif name in {"inference_extension_flow_control_pool_saturation",
                              "inference_pool_average_queue_size"}:
                    row[name] = value

        row["scrape_duration_s"] = round(now_s() - sample_started, 4)
        out_rows.append(row)
        next_sample_at += sample_interval_s
        # Preserve a fixed cadence without issuing a burst of catch-up scrapes when
        # one scrape takes longer than the configured interval.
        while next_sample_at <= now_s():
            next_sample_at += sample_interval_s


def summarize_samples(run_id: str, scenario: str, samples: list[RequestSample], duration_s: int, trim_s: float = 0.0) -> list[dict[str, Any]]:
    # v4: steady-state trim. Requests that STARTED within the first `trim_s`
    # seconds are excluded from percentiles so the KV/APC warmup transient and
    # the closed-loop ramp-in do not pollute steady-state latency. Counts and
    # throughput are still reported over the full window (labeled), but the
    # percentiles the writeup quotes come from the steady-state window only.
    rows = []
    by_tenant: dict[str, list[RequestSample]] = defaultdict(list)
    for sample in samples:
        by_tenant[sample.tenant].append(sample)
    for tenant, tenant_samples in sorted(by_tenant.items()):
        steady = [s for s in tenant_samples if s.start_s >= trim_s]
        ttfts = [s.ttft_s for s in steady if s.ttft_s is not None and s.status == "200"]
        lats = [s.latency_s for s in steady if s.status == "200"]
        counts = Counter(s.status for s in tenant_samples)
        n_steady = len(ttfts)
        # v4: below 500 the writeup uses p90 + the distribution, not p95 (plan sec 4).
        low_n = n_steady < 500
        rows.append({
            "run_id": run_id,
            "scenario": scenario,
            "tenant": tenant,
            "priority": tenant_samples[0].priority if tenant_samples else None,
            "objective": tenant_samples[0].objective if tenant_samples else None,
            "duration_s": duration_s,
            "trim_s": trim_s,
            "total": len(tenant_samples),
            "n_steady_ttft": n_steady,
            "low_n_use_p90": low_n,
            "http_200": counts.get("200", 0),
            "http_429": counts.get("429", 0),
            "http_503": counts.get("503", 0),
            "timeouts": counts.get("Timeout", 0),
            "errors": sum(v for k, v in counts.items() if k.startswith("Error")),
            "throughput_rps": len(tenant_samples) / duration_s if duration_s else 0,
            "ttft_p50_s": percentile(ttfts, 0.50),
            "ttft_p90_s": percentile(ttfts, 0.90),
            "ttft_p95_s": percentile(ttfts, 0.95),
            "ttft_p99_s": percentile(ttfts, 0.99),
            "latency_p50_s": percentile(lats, 0.50),
            "latency_p90_s": percentile(lats, 0.90),
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

    # v4: APC (automatic prefix caching) hit rate over the run window.
    # These counters are in *tokens*. hit_rate = hits / queries measured as a
    # delta so it reflects THIS run, not lifetime. If prefix caching is off
    # the counters stay flat and hit_rate is None. A high hit rate here is the
    # flag that TTFT is a cache result, not a scheduling result.
    apc = {}
    for metric in ["vllm:prefix_cache_queries_total", "vllm:prefix_cache_hits_total"]:
        total = 0.0
        for key, after_val in after.items():
            if key[0] == metric:
                total += after_val - before.get(key, 0.0)
        apc[metric] = total
    queries = apc.get("vllm:prefix_cache_queries_total", 0.0)
    hits = apc.get("vllm:prefix_cache_hits_total", 0.0)
    apc["hit_rate"] = (hits / queries) if queries > 0 else None
    result["vllm"]["prefix_cache"] = apc

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
    trim_s: float = 0.0,
    metric_sample_interval_s: float = 0.5,
) -> dict[str, Any]:
    print(json.dumps({"event": "run_start", "run_id": run_id, "scenario": scenario, "duration_s": duration_s, "drain_timeout_s": drain_timeout_s, "trim_s": trim_s}), flush=True)
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
                concurrency_log, traffic_seed, metric_sample_interval_s,
            ))
            for tenant in tenants
        ]
        sampler_task = asyncio.create_task(metric_sampler(
            run_id,
            scenario,
            duration_s + drain_timeout_s,
            start_zero,
            token,
            metric_rows,
            metric_sample_interval_s,
        ))
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
        "metric_sample_interval_s": metric_sample_interval_s,
        "tenants": [asdict(t) for t in tenants],
        "client_summary": summarize_samples(run_id, scenario, samples, duration_s, trim_s),
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
    preemptions = [float(row["vllm:num_preemptions_total"]) for row in rows
                   if row.get("vllm:num_preemptions_total") not in ("", None)]
    if preemptions:
        result["vllm:num_preemptions_total"] = {
            "start": preemptions[0],
            "end": preemptions[-1],
            "delta": max(0.0, preemptions[-1] - preemptions[0]),
        }
    scrape_durations = [float(row["scrape_duration_s"]) for row in rows
                        if row.get("scrape_duration_s") not in ("", None)]
    sample_lags = [float(row["sample_lag_s"]) for row in rows
                   if row.get("sample_lag_s") not in ("", None)]
    if scrape_durations:
        result["capture_timing"] = {
            "samples": len(rows),
            "scrape_duration_p95_s": percentile(scrape_durations, 0.95),
            "sample_lag_p95_s": percentile(sample_lags, 0.95) if sample_lags else None,
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


def noisy_test1_consolidation_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Scenario 1 consolidation: three-phase noisy sinusoidal (RUNBOOK Run 2).

    Phase 1 (0-100s): premium-a alone, mean ~10, peaks ~16.
    Phase 2 (100-200s): premium-b joins, phase-shifted; combined peak ~24-28.
    Phase 3 (200-300s): standard-a ramps ~24-32 to push waiting positive.
    """
    return [
        (
            "test1_consolidation_noisy",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 6, "period_s": 120, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": 100, "concurrency": 0},
                    {"start_s": 100, "duration_s": scenario_duration - 100, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 6, "period_s": 120, "phase_offset": 0.5},
                ]),
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": 200, "concurrency": 0},
                    {"start_s": 200, "duration_s": scenario_duration - 200, "pattern": "noisy_sinusoidal",
                     "center": 28, "amplitude": 4, "period_s": 40, "phase_offset": 0.2, "ramp_s": 10},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test3_fairness_saturated_clean_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Scenario 4 fairness, SATURATED with a crisp burster-vs-peers design.

    All three tenants priority 100 (same band). Two peers hold a light steady load;
    ONE burster floods to saturate the pool. The fairness claim: round-robin within
    the band bounds the burster's SHARE so it cannot starve its peers — the burster
    is served slower / gets less throughput than if it could hog. Report PER-TENANT
    (burster vs peers), not aggregate premium (aggregate is dominated by the burster
    and hides the fairness effect).
    Saturating: peers ~12 each (~24) + burster floods to ~110 -> aggregate >128.
    """
    third = scenario_duration // 3
    return [
        (
            "test3_fairness_saturated_clean",
            [
                # the burster: light, then floods to saturate
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": third, "pattern": "noisy_sinusoidal",
                     "center": 12, "amplitude": 3, "period_s": 30, "phase_offset": 0.0},
                    {"start_s": third, "duration_s": scenario_duration - third, "pattern": "noisy_sinusoidal",
                     "center": 110, "amplitude": 15, "period_s": 35, "phase_offset": 0.0, "ramp_s": 10},
                ]),
                # two peers: steady light load the whole run
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 12, "amplitude": 2, "period_s": 47, "phase_offset": 0.33},
                ]),
                Tenant("premium-tenant-c", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 12, "amplitude": 2, "period_s": 53, "phase_offset": 0.66},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test1_consolidation_saturated_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Scenario 1 consolidation, SATURATED so the gate actually works.

    The old consolidation kept the pool at ~12/128 (waiting=0), so gate-on == gate-off
    (gate idle — not a flow-control test). This version:
    - Two PREMIUM tenants packed onto the GPU (~15 each = ~30) — the consolidation.
    - A STANDARD tenant floods in early and hard (ramps to ~110) so aggregate exceeds
      the 128 cap -> waiting POSITIVE -> the gate must defend the two premiums.
    Story: consolidate premium work; when a noisy standard neighbor saturates the pool,
    flow control protects the premiums (gate on) vs standard dragging them down (off).
    Calibrated for a 120s run (phases sized to fractions, not fixed 100/200s marks).
    """
    third = scenario_duration // 3
    return [
        (
            "test1_consolidation_saturated",
            [
                # two premiums, packed, present the whole run
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 15, "amplitude": 4, "period_s": 45, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 15, "amplitude": 4, "period_s": 55, "phase_offset": 0.4},
                ]),
                # standard: calm first third (consolidation-only), then floods to saturate
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": third, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 3, "period_s": 30, "phase_offset": 0.2},
                    {"start_s": third, "duration_s": scenario_duration - third, "pattern": "noisy_sinusoidal",
                     "center": 110, "amplitude": 15, "period_s": 35, "phase_offset": 0.2, "ramp_s": 10},
                ]),
            ],
            scenario_duration,
        ),
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


def noisy_test2_tiers_slo_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Test 2 SLO: premium kept LIGHT so it holds p95 < 300ms while standard floods.

    Target (repo success criteria): premium p95 TTFT inside 300ms, standard p95
    clearly above premium, vLLM waiting POSITIVE (real pressure), zero non-200 on
    premium. The gate must defend premium against a genuine standard flood.

    Design: premium ~8 each (~24 total) — light enough that, when the gate lets it
    jump the queue, it slips through under 300ms. Standard floods to ~55 each
    (~110) mid-run, ramped, so aggregate exceeds 128 and waiting goes positive.
    The waiting is standard's; premium stays interactive because the gate
    prioritizes it. This is the SLO story, distinct from the heavy-load tuned
    version (which shows separation but premium sits ~900ms).
    """
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "test2_tiers_slo",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 2, "period_s": 47, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 2, "period_s": 53, "phase_offset": 0.3},
                ]),
                Tenant("premium-tenant-c", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 8, "amplitude": 2, "period_s": 41, "phase_offset": 0.6},
                ]),
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 3, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter, "duration_s": 10, "pattern": "noisy_sinusoidal",
                     "center": 32, "amplitude": 5, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter + 10, "duration_s": middle - 10, "pattern": "noisy_sinusoidal",
                     "center": 55, "amplitude": 10, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 3, "period_s": 30, "phase_offset": 0.1},
                ]),
                Tenant("standard-tenant-b", 0, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 3, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter, "duration_s": 10, "pattern": "noisy_sinusoidal",
                     "center": 32, "amplitude": 5, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter + 10, "duration_s": middle - 10, "pattern": "noisy_sinusoidal",
                     "center": 55, "amplitude": 10, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 3, "period_s": 35, "phase_offset": 0.5},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test2_tiers_tuned_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Test 2 TUNED: raised baseline so the pool holds the queue-forming point.

    Problem with the original: baseline total was ~32 concurrent, so the pool was
    near-empty except during the surge, and the gate only arbitrated ~18% of the
    run. The surge then blew past 128 into pure GPU-slot-bound territory, where
    the gate can't help and premium's tail rises with standard's.

    Fix (max_num_seqs stays 128):
      - premium ~12 each (~36 total) so premium genuinely competes and the gate
        must actively defend it.
      - standard baseline ~35 each (~70 total) so the pool sits near-full
        (~106 aggregate) with waiting positive for most of the run.
      - surge to ~50 each so aggregate reaches ~136 — a gentle push over the
        128 cap, not a slam to ~144+.
    Target: waiting > 0 sustained (gate active most of the run), running near but
    not pinned at 128, so premium-vs-standard latency separation is visible.
    """
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "test2_tiers_tuned",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 12, "amplitude": 3, "period_s": 47, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 12, "amplitude": 3, "period_s": 53, "phase_offset": 0.3},
                ]),
                Tenant("premium-tenant-c", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 12, "amplitude": 3, "period_s": 41, "phase_offset": 0.6},
                ]),
                # Standard surge climbs 35 -> (42) -> 50 via a short intermediate
                # phase, instead of a hard step. The harness ramp_s multiplier
                # ramps from 0 (would empty the pool at the boundary), so we
                # smooth the onset with an explicit 10s bridge phase at center 42.
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 35, "amplitude": 6, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter, "duration_s": 10, "pattern": "noisy_sinusoidal",
                     "center": 42, "amplitude": 6, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter + 10, "duration_s": middle - 10, "pattern": "noisy_sinusoidal",
                     "center": 50, "amplitude": 8, "period_s": 30, "phase_offset": 0.1},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 35, "amplitude": 6, "period_s": 30, "phase_offset": 0.1},
                ]),
                Tenant("standard-tenant-b", 0, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 35, "amplitude": 6, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter, "duration_s": 10, "pattern": "noisy_sinusoidal",
                     "center": 42, "amplitude": 6, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter + 10, "duration_s": middle - 10, "pattern": "noisy_sinusoidal",
                     "center": 50, "amplitude": 8, "period_s": 35, "phase_offset": 0.5},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 35, "amplitude": 6, "period_s": 35, "phase_offset": 0.5},
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


def noisy_test3_fairness_isolation_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Test 3 isolation-mode shape: light peers, only A floods.

    B/C stay near 10-12 in-flight so their own load does not confound peer
    queueing. A alone floods to >=60 in-flight for a 150 s mid-window.
    Pair with queueDepthThreshold=1 (isolation) or 4 (utilization control).
    """
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    return [
        (
            "test3_fairness_noisy_isolation",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 11, "amplitude": 2, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": 70, "amplitude": 10, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": 11, "amplitude": 2, "period_s": 45, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 11, "amplitude": 1, "period_s": 55, "phase_offset": 0.35},
                ]),
                Tenant("premium-tenant-c", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 11, "amplitude": 1, "period_s": 60, "phase_offset": 0.7},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test3_fairness_isolation_hot_defs(
    scenario_duration: int,
    peer_center: int = 11,
    a_center: int = 120,
    a_amplitude: int = 20,
    peer_flat: bool = False,
) -> list[tuple[str, list[Tenant], int]]:
    """Hotter A flood for isolation/utilization dial probes. Tunable peer/A load."""
    quarter = scenario_duration // 4
    middle = scenario_duration - (quarter * 2)
    peer_amp = 0 if peer_flat else max(1, peer_center // 8)
    calm_center = max(4, peer_center)
    if peer_flat:
        peer_b = [{"start_s": 0, "duration_s": scenario_duration, "concurrency": peer_center}]
        peer_c = [{"start_s": 0, "duration_s": scenario_duration, "concurrency": peer_center}]
    else:
        peer_b = [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                   "center": peer_center, "amplitude": peer_amp, "period_s": 55, "phase_offset": 0.35}]
        peer_c = [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                   "center": peer_center, "amplitude": peer_amp, "period_s": 60, "phase_offset": 0.7}]
    return [
        (
            "test3_fairness_noisy_isolation_hot",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": calm_center, "amplitude": max(1, calm_center // 4), "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": a_center, "amplitude": a_amplitude, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": quarter + middle, "duration_s": quarter, "pattern": "noisy_sinusoidal",
                     "center": calm_center, "amplitude": max(1, calm_center // 4), "period_s": 45, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, peer_b),
                Tenant("premium-tenant-c", 100, peer_c),
            ],
            scenario_duration,
        ),
    ]


def noisy_test1_phase3_probe_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Short phase-3 pressure probe: premiums steady, standard hotter to force waiting."""
    return [
        (
            "test1_phase3_probe",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 6, "period_s": 120, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 10, "amplitude": 6, "period_s": 120, "phase_offset": 0.5},
                ]),
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 48, "amplitude": 8, "period_s": 40, "phase_offset": 0.2},
                ]),
            ],
            scenario_duration,
        ),
    ]


def noisy_test4_batch_defs(scenario_duration: int) -> list[tuple[str, list[Tenant], int]]:
    """Test 4 / batch isolation: premium+standard steady, batch surges mid-window.

    Matches clean-pass / pressure_test4 noisy shape (batch center 140).
    """
    half = scenario_duration // 2
    return [
        (
            "test4_batch_noisy",
            [
                Tenant("premium-tenant-a", 100, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "phase_offset": 0.0},
                ]),
                Tenant("standard-tenant-a", 0, [
                    {"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                     "center": 16, "amplitude": 4, "phase_offset": 0.35},
                ]),
                Tenant("batch-tenant-a", -10, [
                    {"start_s": 0, "duration_s": half, "concurrency": 0},
                    {"start_s": half, "duration_s": half, "pattern": "noisy_sinusoidal",
                     "center": 140, "amplitude": 20, "phase_offset": 0.15, "ramp_s": 10},
                ]),
            ],
            scenario_duration,
        ),
    ]


def _matched_pair_windows(scenario_duration: int) -> tuple[int, int]:
    """90 s calm / spike / 90 s calm when duration allows; else quarter/middle/quarter."""
    if scenario_duration >= 270:
        calm = 90
        middle = scenario_duration - (calm * 2)
    else:
        calm = scenario_duration // 4
        middle = scenario_duration - (calm * 2)
    return calm, middle


def noisy_test3_matched_pair_defs(
    scenario_duration: int,
    peer_center: int = 4,
    a_center: int = 150,
    a_amplitude: int = 20,
    peer_flat: bool = True,
    a_priority: int = 100,
) -> list[tuple[str, list[Tenant], int]]:
    """RUNBOOK-PAIR Scenario 3: identical traffic; a_priority selects one-band vs two-band."""
    calm, middle = _matched_pair_windows(scenario_duration)
    calm_center = max(4, peer_center)
    if peer_flat:
        peer_b = [{"start_s": 0, "duration_s": scenario_duration, "concurrency": peer_center}]
        peer_c = [{"start_s": 0, "duration_s": scenario_duration, "concurrency": peer_center}]
    else:
        peer_amp = max(1, peer_center // 8)
        peer_b = [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                   "center": peer_center, "amplitude": peer_amp, "period_s": 55, "phase_offset": 0.35}]
        peer_c = [{"start_s": 0, "duration_s": scenario_duration, "pattern": "noisy_sinusoidal",
                   "center": peer_center, "amplitude": peer_amp, "period_s": 60, "phase_offset": 0.7}]
    scenario = "test3_matched_pair_one_band" if a_priority == 100 else "test3_matched_pair_two_band"
    return [
        (
            scenario,
            [
                Tenant("premium-tenant-a", a_priority, [
                    {"start_s": 0, "duration_s": calm, "pattern": "noisy_sinusoidal",
                     "center": calm_center, "amplitude": max(1, calm_center // 4), "period_s": 45, "phase_offset": 0.0},
                    {"start_s": calm, "duration_s": middle, "pattern": "noisy_sinusoidal",
                     "center": a_center, "amplitude": a_amplitude, "period_s": 45, "phase_offset": 0.0},
                    {"start_s": calm + middle, "duration_s": calm, "pattern": "noisy_sinusoidal",
                     "center": calm_center, "amplitude": max(1, calm_center // 4), "period_s": 45, "phase_offset": 0.0},
                ]),
                Tenant("premium-tenant-b", 100, peer_b),
                Tenant("premium-tenant-c", 100, peer_c),
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
    parser.add_argument("--test3-fair-sat", action="store_true",
                        help="Fairness SATURATED clean (one burster floods 2 peers, report per-tenant)")
    parser.add_argument("--test1-consol-sat", action="store_true",
                        help="Consolidation SATURATED (standard floods 2 premiums so the gate works)")
    parser.add_argument("--test2-slo", action="store_true",
                        help="Test 2 SLO: premium light so premium p95<300ms while standard floods")
    parser.add_argument("--test2-tuned", action="store_true",
                        help="Test 2 tiers with RAISED baseline (pool holds queue-forming point; premium heavier)")
    parser.add_argument("--test2-noisy", action="store_true",
                        help="Run Test 2 priority differentiation with noisy sinusoidal at calibrated concurrency")
    parser.add_argument("--test3-noisy", action="store_true",
                        help="Run Test 3 fairness with noisy sinusoidal at calibrated concurrency")
    parser.add_argument("--test3-noisy-saturated", action="store_true",
                        help="Run Test 3 fairness with noisy sinusoidal traffic calibrated for sustained saturation")
    parser.add_argument("--test3-noisy-isolation", action="store_true",
                        help="Run Test 3 fairness with light peers and A-only flood (isolation/utilization dial traffic)")
    parser.add_argument("--fix0-smoke", action="store_true",
                        help="60s concurrency-8 streaming latency smoke (Fix 0: no 12.4s e2e mode)")
    parser.add_argument("--test1-consolidation-noisy", action="store_true",
                        help="Run Scenario 1 three-phase noisy consolidation (RUNBOOK Run 2)")
    parser.add_argument("--test3-noisy-isolation-hot", action="store_true",
                        help="Hotter A-flood isolation/utilization dial probe (light B/C)")
    parser.add_argument("--peer-center", type=int, default=11,
                        help="Peer B/C concurrency center for --test3-noisy-isolation-hot")
    parser.add_argument("--a-center", type=int, default=120,
                        help="Tenant A mid-window concurrency center for --test3-noisy-isolation-hot")
    parser.add_argument("--a-amplitude", type=int, default=20,
                        help="Tenant A mid-window amplitude for --test3-noisy-isolation-hot")
    parser.add_argument("--peer-flat", action="store_true",
                        help="Use flat concurrency for peers instead of noisy sinusoidal")
    parser.add_argument("--test3-matched-pair-one-band", action="store_true",
                        help="RUNBOOK-PAIR Run 1: A/B/C all priority 100, probe-7 traffic shape")
    parser.add_argument("--test3-matched-pair-two-band", action="store_true",
                        help="RUNBOOK-PAIR Run 2: A at priority 0, B/C at 100, same traffic as one-band")
    parser.add_argument("--test4-noisy", action="store_true",
                        help="Test 4 batch isolation with noisy sinusoidal (premium/standard 16, batch 140)")
    parser.add_argument("--test1-phase3-probe", action="store_true",
                        help="Short phase-3 pressure probe with hotter standard traffic")
    parser.add_argument("--scenario-filter", default="")
    parser.add_argument("--warmup-duration", type=int, default=0)
    parser.add_argument("--warmup-concurrency", type=int, default=16)
    parser.add_argument("--stabilization-repeats", type=int, default=0,
                        help="Run each selected scenario this many extra times before counted repeats")
    parser.add_argument("--traffic-seed", type=int, default=42,
                        help="Seed for deterministic noisy sinusoidal traffic (same seed = same pattern)")
    parser.add_argument("--steady-state-trim-s", type=float, default=30.0,
                        help="Exclude requests that STARTED within this many seconds of run start "
                             "from steady-state percentiles (default 30). Counts/throughput still full-window.")
    parser.add_argument("--vllm-prefix-caching", default="unknown", choices=["on", "off", "unknown"],
                        help="Record the deployed vLLM prefix-caching state for provenance. Does NOT change "
                             "the server; set the server flag separately. The harness also measures the actual "
                             "hit rate from vLLM counters, which is the ground truth.")
    parser.add_argument("--traffic-scale", type=float, default=1.0,
                        help="Uniform multiplier on every scenario's target concurrency. 1.0 = as authored "
                             "(hot, peaks past the 128 knee). ~0.82 lands peaks at the knee for the clean spine.")
    parser.add_argument("--metric-sample-interval-s", type=float, default=0.5,
                        help="EPP, vLLM, and client-concurrency sample cadence in seconds (default 0.5; "
                             "minimum 0.1). Lower values increase metrics-endpoint and harness overhead.")
    args = parser.parse_args()
    if args.metric_sample_interval_s < 0.1:
        parser.error("--metric-sample-interval-s must be at least 0.1 seconds")

    global TRAFFIC_SCALE
    TRAFFIC_SCALE = args.traffic_scale

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
        "flow_control_mode": os.environ.get("FLOW_CONTROL_MODE", ""),
        "queue_depth_threshold": os.environ.get("QUEUE_DEPTH_THRESHOLD", ""),
        "steady_state_trim_s": args.steady_state_trim_s,
        "vllm_prefix_caching_declared": args.vllm_prefix_caching,
        "traffic_scale": args.traffic_scale,
        "metric_sample_interval_s": args.metric_sample_interval_s,
        "harness_version": "v4",
        "peer_center": args.peer_center,
        "a_center": args.a_center,
        "a_amplitude": args.a_amplitude,
        "peer_flat": args.peer_flat,
        "matched_pair_one_band": args.test3_matched_pair_one_band,
        "matched_pair_two_band": args.test3_matched_pair_two_band,
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
            metric_sample_interval_s=args.metric_sample_interval_s,
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
        if args.test1_consolidation_noisy:
            all_defs.extend(noisy_test1_consolidation_defs(args.scenario_duration))
        if args.test3_fair_sat:
            all_defs.extend(noisy_test3_fairness_saturated_clean_defs(args.scenario_duration))
        if args.test1_consol_sat:
            all_defs.extend(noisy_test1_consolidation_saturated_defs(args.scenario_duration))
        if args.test2_slo:
            all_defs.extend(noisy_test2_tiers_slo_defs(args.scenario_duration))
        if args.test2_tuned:
            all_defs.extend(noisy_test2_tiers_tuned_defs(args.scenario_duration))
        if args.test2_noisy:
            all_defs.extend(noisy_test2_priority_defs(args.scenario_duration))
        if args.test3_noisy:
            all_defs.extend(noisy_test3_fairness_defs(args.scenario_duration))
        if args.test3_noisy_saturated:
            all_defs.extend(noisy_test3_fairness_saturated_defs(args.scenario_duration))
        if args.test3_noisy_isolation:
            all_defs.extend(noisy_test3_fairness_isolation_defs(args.scenario_duration))
        if args.test3_noisy_isolation_hot:
            all_defs.extend(
                noisy_test3_fairness_isolation_hot_defs(
                    args.scenario_duration,
                    peer_center=args.peer_center,
                    a_center=args.a_center,
                    a_amplitude=args.a_amplitude,
                    peer_flat=args.peer_flat,
                )
            )
        if args.test3_matched_pair_one_band:
            all_defs.extend(
                noisy_test3_matched_pair_defs(
                    args.scenario_duration,
                    peer_center=args.peer_center,
                    a_center=args.a_center,
                    a_amplitude=args.a_amplitude,
                    peer_flat=args.peer_flat,
                    a_priority=100,
                )
            )
        if args.test3_matched_pair_two_band:
            all_defs.extend(
                noisy_test3_matched_pair_defs(
                    args.scenario_duration,
                    peer_center=args.peer_center,
                    a_center=args.a_center,
                    a_amplitude=args.a_amplitude,
                    peer_flat=args.peer_flat,
                    a_priority=0,
                )
            )
        if args.test4_noisy:
            all_defs.extend(noisy_test4_batch_defs(args.scenario_duration))
        if args.test1_phase3_probe:
            all_defs.extend(noisy_test1_phase3_probe_defs(args.scenario_duration))
        if args.fix0_smoke:
            all_defs.append(
                (
                    "fix0_latency_smoke",
                    [
                        Tenant(
                            "premium-tenant-a",
                            100,
                            [{"start_s": 0, "duration_s": 60, "concurrency": 8}],
                        )
                    ],
                    60,
                )
            )
        selected_flags = (
            args.consolidation_demo
            or args.test1_consolidation_noisy
            or args.test1_phase3_probe
            or args.test2_noisy
            or args.test2_tuned
            or args.test2_slo
            or args.test1_consol_sat
            or args.test3_fair_sat
            or args.test3_noisy
            or args.test3_noisy_saturated
            or args.test3_noisy_isolation
            or args.test3_noisy_isolation_hot
            or args.test3_matched_pair_one_band
            or args.test3_matched_pair_two_band
            or args.test4_noisy
            or args.fix0_smoke
        )
        if not selected_flags:
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
                summary = await run_workload(
                    run_id, scenario, tenants, duration, args.drain_timeout, prompts,
                    args.output_tokens, token, out_dir, args.traffic_seed,
                    args.steady_state_trim_s, args.metric_sample_interval_s,
                )
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
        summary = await run_workload(
            run_id, scenario, tenants, duration, args.drain_timeout, prompts,
            args.output_tokens, token, out_dir, args.traffic_seed,
            args.steady_state_trim_s, args.metric_sample_interval_s,
        )
        summary["repeat"] = repeat
        summaries.append(summary)
        await asyncio.sleep(5)

    (out_dir / "all_summaries.json").write_text(json.dumps(summaries, indent=2))

    with (out_dir / "summary.csv").open("w", newline="") as f:
        fieldnames = [
            "run_id", "repeat", "scenario", "tenant", "priority", "objective", "duration_s", "trim_s",
            "total", "n_steady_ttft", "low_n_use_p90", "http_200",
            "http_429", "http_503", "timeouts", "errors", "throughput_rps",
            "ttft_p50_s", "ttft_p90_s", "ttft_p95_s", "ttft_p99_s",
            "latency_p50_s", "latency_p90_s", "latency_p95_s", "latency_p99_s",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for summary in summaries:
            for row in summary["client_summary"]:
                row_with_repeat = {"repeat": summary.get("repeat", 1), **row}
                writer.writerow(row_with_repeat)

    print(json.dumps({"event": "benchmark_complete", "runs": len(summaries), "output_dir": str(out_dir)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

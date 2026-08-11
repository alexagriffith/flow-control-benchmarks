#!/usr/bin/env python3
"""Canonical multi-tenant flow-control benchmark runner.

Runs from a benchmark client that can reach the OpenAI-compatible inference
gateway and the EPP/vLLM metrics endpoints.
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
import hashlib
import json
import math
import os
import random
import re
import ssl
import statistics
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict, fields, replace
from pathlib import Path
from typing import Any

import aiohttp
import metrics_capture
import prometheus_validate


ENDPOINT_NAME = os.environ.get("ENDPOINT_NAME", "gpt-oss-20b-fc")
MODEL_NAME = os.environ.get("MODEL_NAME", "openai/gpt-oss-20b")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
COMPLETIONS_URL = BASE_URL + "/v1/completions"
TOKENIZE_URL = os.environ.get("TOKENIZE_URL", BASE_URL + "/tokenize")
EPP_METRICS_URL = os.environ.get("EPP_METRICS_URL", "http://localhost:9090/metrics")
EPP_PLUGIN_STATE_URL = os.environ.get(
    "EPP_PLUGIN_STATE_URL",
    EPP_METRICS_URL.rsplit("/", 1)[0] + "/debug/plugins/state",
)
VLLM_METRICS_URL = os.environ.get("VLLM_METRICS_URL", "http://localhost:8001/metrics")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "")
MetricKey = tuple[str, tuple[tuple[str, str], ...]]

# Objective (priority-tier) names sent in the inference objective headers.
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
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def inference_objective(self) -> str:
        return self.objective or OBJECTIVES[self.priority]


@dataclass
class RequestSample:
    run_id: str
    scenario: str
    request_id: str
    tenant: str
    priority: int
    objective: str
    status: str
    planned_arrival_s: float | None
    actual_send_s: float
    start_s: float
    ttft_s: float | None
    latency_s: float
    stream_chunks: int
    prompt_tokens: int | None
    completion_tokens: int | None
    tpot_s: float | None
    timeout: bool
    error_class: str | None
    retry_count: int
    token_count_source: str | None
    dropped_reason: str | None
    retry_after: str | None
    response_detail: str | None


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
    for attempt in range(1, 4):
        try:
            async with session.post(
                TOKENIZE_URL,
                json={"model": MODEL_NAME, "prompt": prompt},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"tokenize failed {resp.status}: {data}")
                return int(data["count"])
        except (TimeoutError, aiohttp.ClientError):
            if attempt == 3:
                raise
            await asyncio.sleep(0.5 * attempt)
    raise RuntimeError("tokenize failed after retries")


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

    # Build one deterministic unique body, then binary-search its prefix. This
    # keeps long-context setup bounded instead of issuing one tokenize request
    # for every small chunk appended.
    body_words = [rng.choice(_WORD_POOL) for _ in range(max(256, target_tokens * 2))]
    best = lead
    best_count = await tokenize_count(session, best)
    low, high = 0, len(body_words)
    while low <= high:
        middle = (low + high) // 2
        candidate = lead + (" " + " ".join(body_words[:middle]) if middle else "")
        count = await tokenize_count(session, candidate)
        if count <= target_tokens:
            best, best_count = candidate, count
            low = middle + 1
        else:
            high = middle - 1

    # Tokenization can merge at the final word boundary. Try varied words until
    # the exact target is reached; this loop is normally only a few iterations.
    while best_count < target_tokens:
        advanced = False
        candidates = _WORD_POOL[:]
        rng.shuffle(candidates)
        for word in candidates:
            candidate = best + " " + word
            count = await tokenize_count(session, candidate)
            if best_count < count <= target_tokens:
                best, best_count = candidate, count
                advanced = True
                break
        if not advanced:
            break

    return best, best_count


async def build_prompt_pool(target_tokens: int, pool_size: int) -> list[dict[str, Any]]:
    connector = aiohttp.TCPConnector(limit=4, limit_per_host=4)
    async with aiohttp.ClientSession(connector=connector) as session:
        built = await asyncio.gather(*(
            build_prompt(session, target_tokens, seed=10_000 + index)
            for index in range(pool_size)
        ))
        return [
            {"id": index, "prompt": prompt, "tokens": count}
            for index, (prompt, count) in enumerate(built)
        ]


def validate_prompt_pool(
    prompts: list[dict[str, Any]], target_tokens: int, pool_size: int
) -> list[dict[str, Any]]:
    if len(prompts) < pool_size:
        raise ValueError(f"prompt pool has {len(prompts)} entries; {pool_size} required")
    selected = prompts[:pool_size]
    invalid = [item.get("id") for item in selected if item.get("tokens") != target_tokens]
    if invalid:
        raise ValueError(f"prompt pool token count mismatch for IDs: {invalid[:5]}")
    return selected


async def cached_prompt_pool(
    target_tokens: int, pool_size: int, cache_dir: Path | None
) -> list[dict[str, Any]]:
    cache_path = (
        cache_dir / f"prompt_pool_{target_tokens}_{pool_size}.json"
        if cache_dir else None
    )
    if cache_path and cache_path.exists():
        prompts = validate_prompt_pool(
            json.loads(cache_path.read_text()), target_tokens, pool_size
        )
        print(json.dumps({
            "event": "prompt_pool_cache_hit",
            "path": str(cache_path),
            "tokens": target_tokens,
            "pool_size": pool_size,
        }), flush=True)
        return prompts
    prompts = await build_prompt_pool(target_tokens, pool_size)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(prompts, indent=2))
        temporary.replace(cache_path)
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
            target = float(phase["concurrency"])
            if phase.get("ramp_s"):
                target *= min(
                    1.0,
                    max(0.0, (elapsed - start) / float(phase["ramp_s"])),
                )
            return max(0, int(round(target * TRAFFIC_SCALE)))
    return 0


def rate_for_phase(phases: list[dict[str, Any]], elapsed: float, rng: random.Random | None = None) -> float:
    """Calculate target RPS for open-loop arrivals.

    Every active Poisson phase must set rate_rps. A concurrency value cannot be
    converted into a request rate without knowing service time, so treating one
    as the other can silently send the wrong load.
    """
    for phase in phases:
        start = float(phase["start_s"])
        end = start + float(phase["duration_s"])
        if start <= elapsed < end:
            if phase.get("rate_pattern") == "noisy_sinusoidal":
                phase_elapsed = elapsed - start
                center = float(phase["rate_center"])
                amplitude = float(phase["rate_amplitude"])
                period = float(phase.get("period_s", 20))
                phase_offset = float(phase.get("phase_offset", 0))
                rate = center + amplitude * math.sin(
                    (phase_elapsed / period) * 2 * math.pi + (phase_offset * 2 * math.pi)
                )
                noise_scale = float(phase.get("rate_noise", max(0.05, center * 0.05)))
                if rng is not None:
                    rate += rng.gauss(0, noise_scale)
                    if rng.random() < float(phase.get("rate_spike_probability", 0.04)):
                        rate += rng.uniform(0, max(0.1, center * 0.2))
                if phase.get("ramp_s"):
                    rate *= min(1.0, max(0.0, phase_elapsed / float(phase["ramp_s"])))
                return max(0.0, rate * TRAFFIC_SCALE)
            if "rate_rps" in phase:
                rate = float(phase["rate_rps"])
                if phase.get("ramp_s"):
                    phase_elapsed = elapsed - start
                    rate *= min(1.0, max(0.0, phase_elapsed / float(phase["ramp_s"])))
                return max(0.0, rate * TRAFFIC_SCALE)
            raise ValueError("Poisson phases require an explicit rate_rps")
    return 0.0


def deterministic_rate_rng(seed: int, elapsed: float, bucket_s: float = 0.5) -> random.Random:
    """Return repeatable noise for a fixed traffic-time bucket."""
    bucket = max(0, int(elapsed / bucket_s))
    stable_seed = int.from_bytes(
        hashlib.sha256(f"{seed}:rate:{bucket}".encode()).digest()[:8],
        "big",
    )
    return random.Random(stable_seed)


def tenant_traffic_seed(traffic_seed: int, fairness_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{traffic_seed}:{fairness_id}".encode()).digest()[:8],
        "big",
    )


def poisson_arrival_schedule(
    phases: list[dict[str, Any]],
    duration_s: int,
    seed: int,
) -> list[float]:
    """Precompute a non-homogeneous Poisson schedule from integrated rate."""
    arrivals: list[float] = []
    arrival_rng = random.Random(seed)
    hazard_remaining = arrival_rng.expovariate(1.0)
    elapsed = 0.0
    integration_step_s = 0.01

    while elapsed < duration_s:
        interval_end = min(float(duration_s), elapsed + integration_step_s)
        midpoint = elapsed + ((interval_end - elapsed) / 2.0)
        rate = rate_for_phase(
            phases,
            midpoint,
            deterministic_rate_rng(seed, midpoint),
        )
        interval_hazard = rate * (interval_end - elapsed)
        interval_start = elapsed

        while rate > 0 and hazard_remaining <= interval_hazard:
            arrival = interval_start + (hazard_remaining / rate)
            if arrival < duration_s:
                arrivals.append(arrival)
            interval_hazard -= hazard_remaining
            interval_start = arrival
            hazard_remaining = arrival_rng.expovariate(1.0)

        hazard_remaining -= interval_hazard
        elapsed = interval_end
    return arrivals


def offered_schedule_evidence(
    tenants: list[Tenant],
    duration_s: int,
    traffic_seed: int,
    samples: list[RequestSample],
    arrival_mode: str,
    send_lag_p99_limit_ms: float = 100.0,
    send_lag_max_limit_ms: float = 500.0,
) -> dict[str, Any]:
    if arrival_mode != "poisson":
        return {"valid": True, "applicable": False, "tenants": []}
    observed = Counter(sample.tenant for sample in samples)
    samples_by_tenant: dict[str, list[RequestSample]] = defaultdict(list)
    for sample in samples:
        samples_by_tenant[sample.tenant].append(sample)
    rows = []
    for tenant in tenants:
        schedule = poisson_arrival_schedule(
            tenant.phases,
            duration_s,
            tenant_traffic_seed(traffic_seed, tenant.fairness_id),
        )
        encoded = ",".join(f"{value:.9f}" for value in schedule).encode()
        send_lags_ms = [
            max(0.0, sample.actual_send_s - sample.planned_arrival_s) * 1000.0
            for sample in samples_by_tenant[tenant.fairness_id]
            if sample.planned_arrival_s is not None
        ]
        send_lag_p99_ms = percentile(send_lags_ms, 0.99)
        send_lag_max_ms = max(send_lags_ms) if send_lags_ms else None
        schedule_fidelity_valid = bool(send_lags_ms) and (
            send_lag_p99_ms is not None
            and send_lag_p99_ms <= send_lag_p99_limit_ms
            and send_lag_max_ms is not None
            and send_lag_max_ms <= send_lag_max_limit_ms
        )
        rows.append({
            "tenant": tenant.fairness_id,
            "planned_requests": len(schedule),
            "dispatched_requests": observed[tenant.fairness_id],
            "schedule_sha256": hashlib.sha256(encoded).hexdigest(),
            "all_dispatched": observed[tenant.fairness_id] == len(schedule),
            "send_lag_p95_ms": percentile(send_lags_ms, 0.95),
            "send_lag_p99_ms": send_lag_p99_ms,
            "send_lag_max_ms": send_lag_max_ms,
            "send_lag_over_100ms": sum(value > 100.0 for value in send_lags_ms),
            "schedule_fidelity_valid": schedule_fidelity_valid,
        })
    return {
        "valid": all(
            row["all_dispatched"] and row["schedule_fidelity_valid"]
            for row in rows
        ),
        "applicable": True,
        "traffic_seed": traffic_seed,
        "send_lag_p99_limit_ms": send_lag_p99_limit_ms,
        "send_lag_max_limit_ms": send_lag_max_limit_ms,
        "tenants": rows,
    }


def load_for_phase(
    phases: list[dict[str, Any]],
    elapsed: float,
    arrival_mode: str,
    rng: random.Random | None = None,
) -> tuple[int, float]:
    """Return only the load target used by the configured arrival process."""
    if arrival_mode == "poisson":
        return 0, rate_for_phase(phases, elapsed, rng)
    return target_for_phase(phases, elapsed, rng), 0.0


def validate_arrival_configuration(tenants: list[Tenant], arrival_mode: str) -> None:
    if arrival_mode != "poisson":
        return
    missing = [
        f"{tenant.fairness_id}[{index}]"
        for tenant in tenants
        for index, phase in enumerate(tenant.phases)
        if "rate_rps" not in phase and phase.get("rate_pattern") != "noisy_sinusoidal"
    ]
    if missing:
        raise ValueError(
            "Poisson mode requires rate_rps in every phase; missing: " + ", ".join(missing)
        )


def load_tenant_shapes(path: str) -> dict[str, dict[str, int]]:
    if not path:
        return {}
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict):
        raise ValueError("tenant shapes file must contain an object")
    result: dict[str, dict[str, int]] = {}
    for tenant_id, value in document.items():
        if not isinstance(value, dict):
            raise ValueError(f"tenant shape for {tenant_id} must be an object")
        shape: dict[str, int] = {}
        for field_name in ("input_tokens", "output_tokens"):
            if field_name not in value:
                continue
            token_count = int(value[field_name])
            if token_count < 1:
                raise ValueError(f"{tenant_id}.{field_name} must be positive")
            shape[field_name] = token_count
        result[str(tenant_id)] = shape
    return result


def load_analysis_windows(path: str) -> dict[str, list[dict[str, Any]]]:
    if not path:
        return {}
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict):
        raise ValueError("analysis windows file must contain an object")
    result: dict[str, list[dict[str, Any]]] = {}
    for scenario, windows in document.items():
        if not isinstance(windows, list):
            raise ValueError(f"analysis windows for {scenario} must be a list")
        checked = []
        for window in windows:
            if not isinstance(window, dict):
                raise ValueError(f"analysis window for {scenario} must be an object")
            name = str(window.get("name", "")).strip()
            start_s = float(window.get("start_s", -1))
            end_s = float(window.get("end_s", -1))
            if not name or start_s < 0 or end_s <= start_s:
                raise ValueError(f"invalid analysis window for {scenario}")
            checked.append({"name": name, "start_s": start_s, "end_s": end_s})
        result[str(scenario)] = checked
    return result


def load_poisson_phases(path: str) -> dict[str, dict[str, list[dict[str, Any]]]]:
    if not path:
        return {}
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict):
        raise ValueError("Poisson phases file must contain an object")
    return document


def load_scenario_file(
    path: str,
) -> tuple[list[tuple[str, list[Tenant], int]], dict[str, list[dict[str, Any]]]]:
    """Load complete, auditable workload scenarios from one JSON file."""
    if not path:
        return [], {}
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("scenario file must be an object with schema_version 1")
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenario file must contain a non-empty scenarios list")

    definitions: list[tuple[str, list[Tenant], int]] = []
    analysis_windows: dict[str, list[dict[str, Any]]] = {}
    seen_scenarios: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("each scenario must be an object")
        name = str(scenario.get("name", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ValueError(f"invalid scenario name: {name!r}")
        if name in seen_scenarios:
            raise ValueError(f"duplicate scenario name: {name}")
        seen_scenarios.add(name)
        duration_s = int(scenario.get("duration_s", 0))
        if duration_s < 1:
            raise ValueError(f"{name}.duration_s must be positive")

        tenant_values = scenario.get("tenants")
        if not isinstance(tenant_values, list) or not tenant_values:
            raise ValueError(f"{name}.tenants must be a non-empty list")
        tenants: list[Tenant] = []
        seen_tenants: set[str] = set()
        for value in tenant_values:
            if not isinstance(value, dict):
                raise ValueError(f"{name} tenant entries must be objects")
            fairness_id = str(value.get("fairness_id", "")).strip()
            if not fairness_id or fairness_id in seen_tenants:
                raise ValueError(f"{name} has an invalid or duplicate fairness_id: {fairness_id!r}")
            seen_tenants.add(fairness_id)
            priority = int(value.get("priority"))
            objective = value.get("objective")
            if objective is None and priority not in OBJECTIVES:
                raise ValueError(f"{name}/{fairness_id} needs an objective for priority {priority}")
            phases = value.get("phases")
            if not isinstance(phases, list) or not phases:
                raise ValueError(f"{name}/{fairness_id}.phases must be a non-empty list")
            checked_phases: list[dict[str, Any]] = []
            for phase in phases:
                if not isinstance(phase, dict):
                    raise ValueError(f"{name}/{fairness_id} phases must be objects")
                start_s = float(phase.get("start_s", -1))
                phase_duration_s = float(phase.get("duration_s", 0))
                if start_s < 0 or phase_duration_s <= 0 or start_s + phase_duration_s > duration_s:
                    raise ValueError(f"{name}/{fairness_id} has a phase outside the scenario duration")
                checked_phases.append(dict(phase))

            input_tokens = value.get("input_tokens")
            output_tokens = value.get("output_tokens")
            if input_tokens is not None and int(input_tokens) < 1:
                raise ValueError(f"{name}/{fairness_id}.input_tokens must be positive")
            if output_tokens is not None and int(output_tokens) < 1:
                raise ValueError(f"{name}/{fairness_id}.output_tokens must be positive")
            tenants.append(Tenant(
                fairness_id=fairness_id,
                priority=priority,
                phases=checked_phases,
                objective=str(objective) if objective is not None else None,
                input_tokens=int(input_tokens) if input_tokens is not None else None,
                output_tokens=int(output_tokens) if output_tokens is not None else None,
            ))

        checked_windows: list[dict[str, Any]] = []
        for window in scenario.get("analysis_windows", []):
            if not isinstance(window, dict):
                raise ValueError(f"{name}.analysis_windows entries must be objects")
            window_name = str(window.get("name", "")).strip()
            start_s = float(window.get("start_s", -1))
            end_s = float(window.get("end_s", -1))
            if not window_name or start_s < 0 or end_s <= start_s or end_s > duration_s:
                raise ValueError(f"{name} has an invalid analysis window")
            checked_windows.append({"name": window_name, "start_s": start_s, "end_s": end_s})

        definitions.append((name, tenants, duration_s))
        analysis_windows[name] = checked_windows
    return definitions, analysis_windows


def apply_poisson_phases(
    definitions: list[tuple[str, list[Tenant], int]],
    overrides: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    scenarios = {scenario: tenants for scenario, tenants, _duration in definitions}
    unknown_scenarios = sorted(set(overrides) - set(scenarios))
    if unknown_scenarios:
        raise ValueError("Poisson phases reference unknown scenarios: " + ", ".join(unknown_scenarios))
    allowed = {
        "rate_rps", "rate_pattern", "rate_center", "rate_amplitude",
        "rate_noise", "rate_spike_probability",
    }
    for scenario, tenant_overrides in overrides.items():
        tenants = {tenant.fairness_id: tenant for tenant in scenarios[scenario]}
        unknown_tenants = sorted(set(tenant_overrides) - set(tenants))
        if unknown_tenants:
            raise ValueError(
                f"{scenario} Poisson phases reference unknown tenants: " + ", ".join(unknown_tenants)
            )
        for tenant_id, phases in tenant_overrides.items():
            tenant = tenants[tenant_id]
            if len(phases) != len(tenant.phases):
                raise ValueError(
                    f"{scenario}/{tenant_id} must define {len(tenant.phases)} Poisson phases"
                )
            for base, override in zip(tenant.phases, phases):
                if not isinstance(override, dict) or not set(override).issubset(allowed):
                    raise ValueError(f"invalid Poisson phase for {scenario}/{tenant_id}")
                base.update(override)


def apply_tenant_shapes(
    definitions: list[tuple[str, list[Tenant], int]],
    shapes: dict[str, dict[str, int]],
) -> None:
    known = {tenant.fairness_id for _scenario, tenants, _duration in definitions for tenant in tenants}
    unknown = sorted(set(shapes) - known)
    if unknown:
        raise ValueError("tenant shapes reference unknown tenants: " + ", ".join(unknown))
    for _scenario, tenants, _duration in definitions:
        for tenant in tenants:
            shape = shapes.get(tenant.fairness_id, {})
            tenant.input_tokens = shape.get("input_tokens", tenant.input_tokens)
            tenant.output_tokens = shape.get("output_tokens", tenant.output_tokens)


def required_prompt_targets(
    default_input_tokens: int,
    definitions: list[tuple[str, list[Tenant], int]],
) -> list[int]:
    return sorted({
        default_input_tokens,
        *(
            tenant.input_tokens
            for _scenario, tenants, _duration in definitions
            for tenant in tenants
            if tenant.input_tokens is not None
        ),
    })


def required_warmup_shapes(
    default_input_tokens: int,
    default_output_tokens: int,
    definitions: list[tuple[str, list[Tenant], int]],
) -> list[tuple[int, int]]:
    if not definitions:
        return [(default_input_tokens, default_output_tokens)]
    return sorted({
        (
            tenant.input_tokens or default_input_tokens,
            tenant.output_tokens or default_output_tokens,
        )
        for _scenario, tenants, _duration in definitions
        for tenant in tenants
    })


def header_evidence(metrics_text: str, tenants: list[Tenant]) -> dict[str, Any]:
    observed: dict[str, set[int]] = defaultdict(set)
    for (_name, labels), _value in parse_prometheus(metrics_text).items():
        label_map = labels_to_dict(labels)
        fairness_id = label_map.get("fairness_id")
        priority_text = label_map.get("priority")
        if fairness_id is None or priority_text is None:
            continue
        try:
            observed[fairness_id].add(int(priority_text))
        except ValueError:
            continue
    expected = {tenant.fairness_id: tenant.priority for tenant in tenants}
    missing = [
        {"fairness_id": fairness_id, "expected_priority": priority}
        for fairness_id, priority in expected.items()
        if priority not in observed.get(fairness_id, set())
    ]
    return {
        "valid": not missing,
        "expected": expected,
        "observed": {key: sorted(values) for key, values in observed.items() if key in expected},
        "missing": missing,
    }


def flow_control_engagement(
    rows: list[dict[str, Any]],
    queue_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    queue_peak = 0.0
    saturation_peak = 0.0
    for row in rows:
        for key, value in row.items():
            if value in (None, ""):
                continue
            if key.startswith((
                "llm_d_epp_flow_control_queue_size",
                "inference_extension_flow_control_queue_size",
            )):
                queue_peak = max(queue_peak, float(value))
            if key in (
                "llm_d_epp_flow_control_pool_saturation",
                "inference_extension_flow_control_pool_saturation",
            ):
                saturation_peak = max(saturation_peak, float(value))
    queue_wait_count = sum(
        float(item.get("queue_count_delta") or 0)
        for item in (queue_evidence or [])
    )
    return {
        "valid": queue_wait_count > 0,
        "queue_peak": queue_peak,
        "saturation_peak": saturation_peak,
        "queued_request_count_delta": queue_wait_count,
    }


def counter_delta(before: dict[MetricKey, float], after: dict[MetricKey, float], names: tuple[str, ...]) -> float:
    return sum(
        after_value - before.get(key, 0.0)
        for key, after_value in after.items()
        if key[0] in names
    )


def _latest_traffic_by_tenant(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        tenant = str(row.get("tenant", ""))
        if tenant:
            latest[tenant] = row
    return latest


def _queue_by_tenant(row: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for key, value in row.items():
        if not key.startswith((
            "llm_d_epp_flow_control_queue_size|",
            "inference_extension_flow_control_queue_size|",
        )):
            continue
        labels = labels_to_dict(tuple(
            tuple(part.split("=", 1))
            for part in key.split("|")[1:]
            if "=" in part
        ))
        result[labels.get("fairness_id", "unknown")] += float(value or 0)
    return result


def build_live_status(
    run_id: str,
    scenario: str,
    stage_id: str | None,
    state: str,
    phase: str,
    elapsed_s: float,
    tenants: list[Tenant],
    samples: list[RequestSample],
    traffic_rows: list[dict[str, Any]],
    metric_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    recent_start = max(0.0, elapsed_s - 30.0)
    rate_start = max(0.0, elapsed_s - 5.0)
    recent = [
        sample
        for sample in samples
        if sample.start_s + sample.latency_s >= recent_start
    ]
    completed_recent = [
        sample for sample in samples
        if sample.status == "200" and sample.start_s + sample.latency_s >= rate_start
    ]
    latest_traffic = _latest_traffic_by_tenant(traffic_rows)
    latest_metrics = metric_rows[-1] if metric_rows else {}
    queue_by_tenant = _queue_by_tenant(latest_metrics)
    queue_peak_by_tenant: dict[str, float] = defaultdict(float)
    epp_queue_peak = 0.0
    for metric_row in metric_rows:
        queue_values = _queue_by_tenant(metric_row)
        epp_queue_peak = max(epp_queue_peak, sum(queue_values.values()))
        for tenant_id, value in queue_values.items():
            queue_peak_by_tenant[tenant_id] = max(queue_peak_by_tenant[tenant_id], value)
    interval = max(1.0, min(5.0, elapsed_s))

    tenant_rows = []
    for tenant in tenants:
        tenant_recent = [sample for sample in recent if sample.tenant == tenant.fairness_id]
        tenant_completed = [sample for sample in completed_recent if sample.tenant == tenant.fairness_id]
        ttfts_ms = [
            sample.ttft_s * 1000
            for sample in tenant_recent
            if sample.status == "200" and sample.ttft_s is not None
        ]
        traffic = latest_traffic.get(tenant.fairness_id, {})
        target_rps = traffic.get("target_rps")
        tenant_rows.append({
            "id": tenant.fairness_id,
            "priority": tenant.priority,
            "offeredRps": float(target_rps) if target_rps not in (None, "") else 0.0,
            "servedRps": len(tenant_completed) / interval,
            "active": int(traffic.get("outstanding_requests") or 0),
            "queued": queue_by_tenant.get(tenant.fairness_id, 0.0),
            "queuedPeak": queue_peak_by_tenant.get(tenant.fairness_id, 0.0),
            "p95TtftMs": percentile(ttfts_ms, 0.95),
        })

    all_ttfts_ms = [
        sample.ttft_s * 1000
        for sample in recent
        if sample.status == "200" and sample.ttft_s is not None
    ]
    return {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runId": run_id,
        "scenario": scenario,
        "stageId": stage_id,
        "state": state,
        "elapsedS": round(elapsed_s, 3),
        "phase": phase,
        "offeredRps": sum(row["offeredRps"] for row in tenant_rows),
        "servedRps": len(completed_recent) / interval,
        "activeRequests": sum(row["active"] for row in tenant_rows),
        "eppQueued": sum(queue_by_tenant.values()),
        "eppQueuedPeak": epp_queue_peak,
        "vllmRunning": float(latest_metrics.get("vllm:num_requests_running") or 0),
        "vllmWaiting": float(latest_metrics.get("vllm:num_requests_waiting") or 0),
        "kvCacheUsage": float(next((
            latest_metrics[key]
            for key in metrics_capture.METRIC_ALIASES["vllm_kv_cache"]
            if latest_metrics.get(key) not in (None, "")
        ), 0)),
        "p95TtftMs": percentile(all_ttfts_ms, 0.95),
        "errors": sum(1 for sample in samples if sample.status not in ("200", "429")),
        "rejections": sum(1 for sample in samples if sample.status == "429"),
        "tenants": tenant_rows,
    }


def write_live_status(path: Path | None, status: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2))
    temporary.replace(path)


def write_partial_run_artifacts(
    run_dir: Path,
    samples: list[RequestSample],
    metric_rows: list[dict[str, Any]],
    metric_long_rows: list[dict[str, Any]],
    concurrency_log: list[dict[str, Any]],
    traffic_log: list[dict[str, Any]],
) -> None:
    artifacts: list[tuple[str, list[str], list[dict[str, Any]]]] = []
    if samples:
        artifacts.append((
            "client_samples.partial.csv",
            [field.name for field in fields(RequestSample)],
            [asdict(sample) for sample in samples],
        ))
    if metric_rows:
        artifacts.append((
            "metric_samples.partial.csv",
            sorted({key for row in metric_rows for key in row}),
            metric_rows,
        ))
    if metric_long_rows:
        artifacts.append((
            "metric_samples_long.partial.csv",
            [
                "run_id", "scenario", "elapsed_s", "sample_epoch_s", "source",
                "metric_generation", "metric", "labels_json", "value",
            ],
            metric_long_rows,
        ))
    if concurrency_log:
        artifacts.append((
            "concurrency_samples.partial.csv",
            ["elapsed_s", "tenant", "target_concurrency", "actual_inflight"],
            concurrency_log,
        ))
    if traffic_log:
        artifacts.append((
            "traffic_samples.partial.csv",
            [
                "run_id", "scenario", "elapsed_s", "tenant", "arrival_process",
                "target_rps", "target_concurrency", "issued_requests",
                "completed_requests", "outstanding_requests", "send_delay_s",
                "safety_ceiling_state",
            ],
            traffic_log,
        ))
    for name, fieldnames, rows in artifacts:
        path = run_dir / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)


def remove_partial_run_artifacts(run_dir: Path) -> None:
    for path in run_dir.glob("*.partial.csv"):
        path.unlink()


def parse_stream_line(line: bytes) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith(b"data:"):
        return None
    payload = stripped[len(b"data:"):].strip()
    if payload == b"[DONE]":
        return {"done": True}
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


def completion_tokens_from_usage(event: dict[str, Any] | None) -> int | None:
    if not event:
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("completion_tokens")
    return int(value) if isinstance(value, (int, float)) else None


def stream_completion_error(
    saw_done: bool, completion_tokens: int | None,
) -> str | None:
    if not saw_done:
        return "IncompleteStream"
    if completion_tokens is None:
        return "MissingUsage"
    return None


def compute_tpot_s(
    latency_s: float,
    ttft_s: float | None,
    completion_tokens: int | None,
) -> float | None:
    """Decode-only time-per-output-token.

    Deliberately takes completion_tokens (from server usage), never a stream
    chunk count: SSE chunk boundaries do not map 1:1 to decoded tokens, so
    stream_chunks must never reach this computation. Returns None unless both
    the first-token latency and a real completion-token count are available.
    """
    if completion_tokens is None or ttft_s is None:
        return None
    return (latency_s - ttft_s) / max(1, completion_tokens - 1)


def compute_slo_proof_valid(
    arrival_mode: str,
    safety_state: dict[str, Any] | None,
    metric_rows_present: bool,
    samples: list["RequestSample"],
    proof_checks_valid: bool = True,
) -> bool:
    """Honest SLO-proof gate.

    An SLO proof is only meaningful for an open-loop (poisson) run that stayed
    under its outstanding safety ceiling, produced server metric samples and
    client samples, and where every recorded request completed cleanly (HTTP
    200, no timeouts, and no error_class). Closed-loop runs describe an offered
    concurrency shape, not a proof, so they are never marked valid here.
    """
    if arrival_mode != "poisson":
        return False
    if safety_state:
        return False
    if not metric_rows_present or not samples or not proof_checks_valid:
        return False
    for sample in samples:
        if sample.status != "200" or sample.timeout or sample.error_class is not None:
            return False
    return True


def slo_proof_reason(
    arrival_mode: str,
    safety_state: dict[str, Any] | None,
    metric_rows_present: bool,
    samples: list["RequestSample"],
    proof_checks_valid: bool = True,
) -> str:
    """Human-readable justification mirroring compute_slo_proof_valid()."""
    if arrival_mode != "poisson":
        return "closed_loop_offered_concurrency_shape_not_a_proof"
    if safety_state:
        return "outstanding_safety_ceiling_hit"
    if not metric_rows_present:
        return "no_metric_samples"
    if not samples:
        return "no_client_samples"
    if not proof_checks_valid:
        return "required_header_cache_or_flow_control_proof_failed"
    if any(s.status != "200" or s.timeout or s.error_class is not None for s in samples):
        return "non_200_responses_or_request_errors_present"
    return "valid"


async def send_one(
    session: aiohttp.ClientSession,
    run_id: str,
    scenario: str,
    tenant: Tenant,
    prompt: str,
    prompt_tokens: int | None,
    output_tokens: int,
    start_zero: float,
    samples: list[RequestSample],
    request_id: str | None = None,
    planned_arrival_s: float | None = None,
    retry_count: int = 0,
):
    start = now_s()
    ttft = None
    chunks = 0
    status = "Unknown"
    timeout = False
    error_class = None
    completion_tokens = None
    stream_done = False
    token_count_source = None
    dropped_reason = None
    retry_after = None
    response_detail = None
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "max_tokens": output_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "ignore_eos": True,
    }
    headers = {
        "x-llm-d-inference-fairness-id": tenant.fairness_id,
        "x-llm-d-inference-objective": tenant.inference_objective,
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
            dropped_reason = resp.headers.get("x-llm-d-request-dropped-reason")
            retry_after = resp.headers.get("Retry-After")
            if resp.status == 200:
                # Stop latency timing at the OpenAI stream terminator. Waiting for
                # connection close via iter_any() produced a false 12.4–12.5 s e2e mode.
                async for line in resp.content:
                    if not line:
                        continue
                    chunks += 1
                    event = parse_stream_line(line)
                    if event and event.get("done"):
                        stream_done = True
                        break
                    if ttft is None:
                        ttft = now_s() - start
                    from_usage = completion_tokens_from_usage(event)
                    if from_usage is not None:
                        completion_tokens = from_usage
                        token_count_source = "stream_usage"
                    if chunks % 16 == 0:
                        await asyncio.sleep(0)
                if stream_error := stream_completion_error(
                    stream_done, completion_tokens,
                ):
                    status = f"Error:{stream_error}"
                    error_class = stream_error
                    response_detail = (
                        "HTTP 200 stream ended without [DONE]"
                        if stream_error == "IncompleteStream"
                        else "HTTP 200 stream ended without completion-token usage"
                    )
            else:
                body = await resp.read()
                response_detail = " ".join(body.decode(errors="replace").split())[:500] or None
    except asyncio.TimeoutError:
        status = "Timeout"
        timeout = True
        error_class = "Timeout"
    except asyncio.CancelledError:
        status = "Cancelled"
        error_class = "Cancelled"
    except Exception as exc:
        error_class = type(exc).__name__
        status = f"Error:{error_class}"
    finally:
        latency_s = now_s() - start
        tpot_s = compute_tpot_s(latency_s, ttft, completion_tokens)
        samples.append(
            RequestSample(
                run_id=run_id,
                scenario=scenario,
                request_id=request_id or str(uuid.uuid4()),
                tenant=tenant.fairness_id,
                priority=tenant.priority,
                objective=tenant.inference_objective,
                status=status,
                planned_arrival_s=planned_arrival_s,
                actual_send_s=start - start_zero,
                start_s=start - start_zero,
                ttft_s=ttft,
                latency_s=latency_s,
                stream_chunks=chunks,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tpot_s=tpot_s,
                timeout=timeout,
                error_class=error_class,
                retry_count=retry_count,
                token_count_source=token_count_source,
                dropped_reason=dropped_reason,
                retry_after=retry_after,
                response_detail=response_detail,
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
    traffic_log: list[dict[str, Any]],
    arrival_mode: str = "closed_loop",
    outstanding_safety_ceiling: int = 10_000,
    safety_state: dict[str, Any] | None = None,
    traffic_seed: int = 42,
    sample_interval_s: float = 0.5,
):
    inflight: set[asyncio.Task] = set()
    prompt_idx = 0
    stable_seed = tenant_traffic_seed(traffic_seed, tenant.fairness_id)
    rng = random.Random(stable_seed)
    planned_arrivals = (
        poisson_arrival_schedule(tenant.phases, duration_s, stable_seed)
        if arrival_mode == "poisson"
        else []
    )
    next_arrival_index = 0
    issued_requests = 0
    completed_requests = 0
    safety_ceiling_state = "ok"
    current_target = 0
    logger_stop = asyncio.Event()

    def request_done(task: asyncio.Task) -> None:
        nonlocal completed_requests
        inflight.discard(task)
        completed_requests += 1
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            # send_one records request failures; retrieving the result prevents
            # an unobserved task exception from being reported by asyncio.
            pass

    def start_request(planned_arrival_s: float | None = None) -> None:
        nonlocal prompt_idx, issued_requests
        prompt_row = prompts[prompt_idx % len(prompts)]
        prompt_idx += 1
        request_id = f"{run_id}-{tenant.fairness_id}-{issued_requests + 1}"
        task = asyncio.create_task(
            send_one(
                session, run_id, scenario, tenant,
                prompt_row["prompt"], prompt_row.get("tokens"),
                output_tokens, start_zero, samples,
                request_id=request_id,
                planned_arrival_s=planned_arrival_s,
            )
        )
        inflight.add(task)
        task.add_done_callback(request_done)
        issued_requests += 1

    async def sample_runtime() -> None:
        next_sample_at = start_zero
        while not logger_stop.is_set():
            wait_s = next_sample_at - now_s()
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            elapsed = now_s() - start_zero
            if elapsed >= duration_s:
                break
            if arrival_mode == "poisson":
                target = 0
                target_rps = rate_for_phase(
                    tenant.phases,
                    elapsed,
                    deterministic_rate_rng(stable_seed, elapsed),
                )
            else:
                target = current_target
                target_rps = 0.0
            concurrency_log.append({
                "elapsed_s": round(elapsed, 3),
                "tenant": tenant.fairness_id,
                "target_concurrency": target,
                "actual_inflight": len(inflight),
            })
            traffic_log.append({
                "run_id": run_id,
                "scenario": scenario,
                "elapsed_s": round(elapsed, 3),
                "tenant": tenant.fairness_id,
                "arrival_process": "poisson" if arrival_mode == "poisson" else "closed_loop",
                "target_rps": round(target_rps, 6) if arrival_mode == "poisson" else "",
                "target_concurrency": target,
                "issued_requests": issued_requests,
                "completed_requests": completed_requests,
                "outstanding_requests": len(inflight),
                "send_delay_s": "",
                "safety_ceiling_state": safety_ceiling_state,
            })
            next_sample_at += sample_interval_s
            while next_sample_at <= now_s():
                next_sample_at += sample_interval_s

    logger_task = asyncio.create_task(sample_runtime())
    try:
        if arrival_mode == "poisson":
            while next_arrival_index < len(planned_arrivals):
                planned_arrival_s = planned_arrivals[next_arrival_index]
                planned_arrival_at = start_zero + planned_arrival_s
                wait_s = planned_arrival_at - now_s()
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
                elapsed = now_s() - start_zero
                if len(inflight) >= outstanding_safety_ceiling:
                    safety_ceiling_state = "hit"
                    if safety_state is not None:
                        safety_state[tenant.fairness_id] = {
                            "state": "hit",
                            "elapsed_s": round(elapsed, 3),
                            "outstanding_requests": len(inflight),
                            "safety_ceiling": outstanding_safety_ceiling,
                        }
                    break
                dispatch_rate = rate_for_phase(
                    tenant.phases,
                    planned_arrival_s,
                    deterministic_rate_rng(stable_seed, planned_arrival_s),
                )
                send_delay_s = max(0.0, now_s() - planned_arrival_at)
                start_request(planned_arrival_s)
                traffic_log.append({
                    "run_id": run_id,
                    "scenario": scenario,
                    "elapsed_s": round(elapsed, 3),
                    "tenant": tenant.fairness_id,
                    "arrival_process": "poisson",
                    "target_rps": round(dispatch_rate, 6),
                    "target_concurrency": 0,
                    "issued_requests": issued_requests,
                    "completed_requests": completed_requests,
                    "outstanding_requests": len(inflight),
                    "send_delay_s": round(send_delay_s, 6),
                    "safety_ceiling_state": safety_ceiling_state,
                })
                next_arrival_index += 1
                # Let the request task enter aiohttp before scheduling the next arrival.
                await asyncio.sleep(0)
            if safety_ceiling_state != "hit":
                await logger_task
        else:
            while now_s() - start_zero < duration_s:
                elapsed = now_s() - start_zero
                current_target, _ = load_for_phase(
                    tenant.phases, elapsed, arrival_mode, rng
                )
                while len(inflight) < current_target:
                    start_request()
                await asyncio.sleep(0.05)
            await logger_task
    finally:
        logger_stop.set()
        if not logger_task.done():
            await logger_task
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
    out_long_rows: list[dict[str, Any]],
    sample_interval_s: float = 0.5,
):
    loop = asyncio.get_event_loop()
    next_sample_at = start_zero
    while now_s() - start_zero < duration_s:
        wait_s = next_sample_at - now_s()
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        sample_started = now_s()
        sample_epoch_s = time.time()
        if sample_started - start_zero >= duration_s:
            break

        row = {
            "run_id": run_id,
            "scenario": scenario,
            "elapsed_s": round(sample_started - start_zero, 3),
            "sample_epoch_s": round(sample_epoch_s, 6),
            "sample_interval_s": sample_interval_s,
            "sample_lag_s": round(max(0.0, sample_started - next_sample_at), 4),
        }
        vllm_result, epp_result, plugin_state_result = await asyncio.gather(
            loop.run_in_executor(None, scrape_url, VLLM_METRICS_URL),
            loop.run_in_executor(None, scrape_url, EPP_METRICS_URL, token),
            loop.run_in_executor(None, scrape_url, EPP_PLUGIN_STATE_URL, token),
            return_exceptions=True,
        )

        if isinstance(vllm_result, Exception):
            row["vllm_scrape_error"] = type(vllm_result).__name__
        else:
            out_long_rows.extend(metrics_capture.long_rows(
                vllm_result, "vllm", sample_started - start_zero, run_id, scenario,
                sample_epoch_s,
            ))
            vllm = parse_prometheus(vllm_result)
            vllm_totals: dict[str, float] = defaultdict(float)
            vllm_cache_values: list[float] = []
            resolved = metrics_capture.resolve_concepts("", vllm_result)
            selected_vllm = {
                resolved[concept]
                for concept in (
                    "vllm_running", "vllm_waiting", "vllm_kv_cache", "vllm_preemptions"
                )
                if resolved[concept]
            }
            for (name, labels), value in vllm.items():
                if name in selected_vllm:
                    suffix = metric_label_suffix(labels)
                    if suffix:
                        row[f"{name}|{suffix}"] = value
                    if name == resolved["vllm_kv_cache"]:
                        vllm_cache_values.append(value)
                    else:
                        vllm_totals[name] += value
            row.update(vllm_totals)
            if vllm_cache_values and resolved["vllm_kv_cache"]:
                row[resolved["vllm_kv_cache"]] = max(vllm_cache_values)

        if isinstance(epp_result, Exception):
            row["epp_scrape_error"] = type(epp_result).__name__
        else:
            out_long_rows.extend(metrics_capture.long_rows(
                epp_result, "epp", sample_started - start_zero, run_id, scenario,
                sample_epoch_s,
            ))
            epp = parse_prometheus(epp_result)
            resolved = metrics_capture.resolve_concepts(epp_result, "")
            queue_metrics = {
                resolved["epp_flow_queue_size"],
                resolved["epp_flow_queue_bytes"],
            } - {None}
            pool_metrics = {
                resolved["epp_pool_saturation"],
                resolved["epp_average_queue"],
                resolved["epp_average_kv_cache"],
                resolved["epp_average_running"],
                resolved["epp_ready_endpoints"],
            } - {None}
            for (name, labels), value in epp.items():
                if name in queue_metrics:
                    label_dict = labels_to_dict(labels)
                    fid = label_dict.get("fairness_id", "unknown")
                    priority = label_dict.get("priority", "unknown")
                    suffix = metric_label_suffix(labels) or f"fairness_id={fid}|priority={priority}"
                    row[f"{name}|{suffix}"] = value
                elif name in pool_metrics:
                    row[name] = value

        if isinstance(plugin_state_result, Exception):
            row["epp_plugin_state_error"] = type(plugin_state_result).__name__
        else:
            try:
                state = metrics_capture.parse_inflight_plugin_state(plugin_state_result)
                row["epp:inflight_requests"] = state["requests"]
                row["epp:inflight_tokens"] = state["tokens"]
                for endpoint in state["endpoints"]:
                    suffix = f"endpoint={endpoint['endpoint']}"
                    row[f"epp:inflight_requests|{suffix}"] = endpoint["requests"]
                    row[f"epp:inflight_tokens|{suffix}"] = endpoint["tokens"]
                    for metric in ("requests", "tokens"):
                        out_long_rows.append({
                            "run_id": run_id,
                            "scenario": scenario,
                            "elapsed_s": round(sample_started - start_zero, 6),
                            "sample_epoch_s": round(sample_epoch_s, 6),
                            "source": "epp_debug",
                            "metric_generation": "canonical",
                            "metric": f"epp:inflight_{metric}",
                            "labels_json": json.dumps(
                                {"endpoint": endpoint["endpoint"]},
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            "value": endpoint[metric],
                        })
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                row["epp_plugin_state_error"] = type(exc).__name__

        row["scrape_duration_s"] = round(now_s() - sample_started, 4)
        out_rows.append(row)
        next_sample_at += sample_interval_s
        # Preserve a fixed cadence without issuing a burst of catch-up scrapes when
        # one scrape takes longer than the configured interval.
        while next_sample_at <= now_s():
            next_sample_at += sample_interval_s


def summarize_samples(run_id: str, scenario: str, samples: list[RequestSample], duration_s: int, trim_s: float = 0.0, arrival_mode: str = "closed_loop") -> list[dict[str, Any]]:
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
        tpots = [s.tpot_s for s in steady if s.tpot_s is not None and s.status == "200"]
        counts = Counter(s.status for s in tenant_samples)
        active_completions = [
            s for s in tenant_samples
            if s.status == "200" and s.start_s + s.latency_s <= duration_s
        ]
        steady_completions = [
            s for s in active_completions if s.start_s >= trim_s
        ]
        steady_duration_s = max(0.0, duration_s - trim_s)
        n_steady = len(ttfts)
        # v4: below 500 the writeup uses p90 + the distribution, not p95 (plan sec 4).
        low_n = n_steady < 500
        http_other = sum(
            count
            for status, count in counts.items()
            if status.isdigit() and status not in {"200", "429", "503"}
        )
        rows.append({
            "run_id": run_id,
            "scenario": scenario,
            "arrival_mode": arrival_mode,
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
            "http_other": http_other,
            "non_200": len(tenant_samples) - counts.get("200", 0),
            "status_counts": dict(sorted(counts.items())),
            "timeouts": counts.get("Timeout", 0),
            "errors": sum(v for k, v in counts.items() if k.startswith("Error")),
            "active_window_http_200": len(active_completions),
            "drain_window_http_200": max(0, counts.get("200", 0) - len(active_completions)),
            "throughput_rps": len(active_completions) / duration_s if duration_s else 0,
            "steady_throughput_rps": (
                len(steady_completions) / steady_duration_s if steady_duration_s else 0
            ),
            "ttft_p50_s": percentile(ttfts, 0.50),
            "ttft_p90_s": percentile(ttfts, 0.90),
            "ttft_p95_s": percentile(ttfts, 0.95),
            "ttft_p99_s": percentile(ttfts, 0.99),
            "latency_p50_s": percentile(lats, 0.50),
            "latency_p90_s": percentile(lats, 0.90),
            "latency_p95_s": percentile(lats, 0.95),
            "latency_p99_s": percentile(lats, 0.99),
            "tpot_p50_s": percentile(tpots, 0.50),
            "tpot_p90_s": percentile(tpots, 0.90),
            "tpot_p95_s": percentile(tpots, 0.95),
            "tpot_p99_s": percentile(tpots, 0.99),
        })
    return rows


def summarize_windows(
    run_id: str,
    scenario: str,
    samples: list[RequestSample],
    windows: list[dict[str, Any]],
    arrival_mode: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    def offered_at(sample: RequestSample) -> float:
        if arrival_mode == "poisson" and sample.planned_arrival_s is not None:
            return sample.planned_arrival_s
        return sample.start_s

    for window in windows:
        start_s = float(window["start_s"])
        end_s = float(window["end_s"])
        started_in_window = [
            sample for sample in samples if start_s <= offered_at(sample) < end_s
        ]
        window_samples = [
            replace(sample, start_s=sample.start_s - start_s)
            for sample in started_in_window
        ]
        for row in summarize_samples(
            run_id,
            scenario,
            window_samples,
            duration_s=max(1, int(end_s - start_s)),
            trim_s=0,
            arrival_mode=arrival_mode,
        ):
            tenant = row["tenant"]
            completed_in_window = [
                sample
                for sample in samples
                if sample.tenant == tenant
                and sample.status == "200"
                and start_s <= sample.start_s + sample.latency_s < end_s
            ]
            drained_after_window = [
                sample
                for sample in started_in_window
                if sample.tenant == tenant
                and sample.status == "200"
                and sample.start_s + sample.latency_s >= end_s
            ]
            window_duration_s = max(1, int(end_s - start_s))
            row["active_window_http_200"] = len(completed_in_window)
            row["drain_window_http_200"] = len(drained_after_window)
            row["throughput_rps"] = len(completed_in_window) / window_duration_s
            row["steady_throughput_rps"] = row["throughput_rps"]
            rows.append({
                "window": window["name"],
                "window_start_s": start_s,
                "window_end_s": end_s,
                **row,
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
    queries = counter_delta(before, after, (
        "vllm:prefix_cache_queries", "vllm:prefix_cache_queries_total",
    ))
    hits = counter_delta(before, after, (
        "vllm:prefix_cache_hits", "vllm:prefix_cache_hits_total",
    ))
    apc = {
        "queries_delta": queries,
        "hits_delta": hits,
    }
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

    resolved = metrics_capture.resolve_concepts(post, "")
    queue_duration_name = resolved["epp_flow_queue_duration"]
    queue_acc: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"sum": 0.0, "count": 0.0})
    for key, after_val in after.items():
        name, labels = key
        if not queue_duration_name or not name.startswith(queue_duration_name + "_"):
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


def prometheus_validation(
    run_dir: Path,
    require_flow_control: bool,
    require_active_flow: bool = False,
    start_epoch_s: float | None = None,
    end_epoch_s: float | None = None,
) -> dict[str, Any]:
    required = os.environ.get("EXPECT_PROMETHEUS", "1") != "0"
    if not PROMETHEUS_URL:
        report = {"valid": not required, "skipped": True, "reason": "PROMETHEUS_URL missing"}
        (run_dir / "prometheus_validation.json").write_text(json.dumps(report, indent=2))
        return report

    namespace = os.environ.get("PROMETHEUS_NAMESPACE", "")
    pod_prefix = os.environ.get("PROMETHEUS_POD_PREFIX", "")
    epp_service = os.environ.get("PROMETHEUS_EPP_SERVICE", "")
    vllm_service = os.environ.get("PROMETHEUS_VLLM_SERVICE", "")
    missing_settings = [
        name for name, value in (
            ("PROMETHEUS_NAMESPACE", namespace),
            ("PROMETHEUS_POD_PREFIX", pod_prefix),
            ("PROMETHEUS_EPP_SERVICE", epp_service),
            ("PROMETHEUS_VLLM_SERVICE", vllm_service),
        ) if not value
    ]
    if missing_settings:
        report = {"valid": False, "missing_settings": missing_settings}
        (run_dir / "prometheus_validation.json").write_text(json.dumps(report, indent=2))
        return report

    token = metrics_capture.load_token(
        os.environ.get("PROMETHEUS_TOKEN"),
        os.environ.get("PROMETHEUS_TOKEN_FILE"),
    )
    insecure = os.environ.get("PROMETHEUS_INSECURE_HTTPS", "0") == "1"
    query = prometheus_validate.metric_query(namespace, pod_prefix)
    if start_epoch_s is not None and end_epoch_s is not None:
        query_payload = prometheus_validate.api_get(
            PROMETHEUS_URL,
            "/api/v1/query_range?" + urllib.parse.urlencode({
                "query": query,
                "start": f"{start_epoch_s:.6f}",
                "end": f"{end_epoch_s:.6f}",
                "step": os.environ.get("PROMETHEUS_QUERY_STEP_S", "5"),
            }),
            token,
            15.0,
            insecure,
        )
        (run_dir / "prometheus_metric_samples.json").write_text(
            json.dumps(query_payload, indent=2)
        )
        report = prometheus_validate.build_range_report(
            query_payload, require_flow_control, require_active_flow
        )
    else:
        targets = prometheus_validate.api_get(
            PROMETHEUS_URL, "/api/v1/targets", token, 15.0, insecure
        )
        query_payload = prometheus_validate.api_get(
            PROMETHEUS_URL,
            "/api/v1/query?" + urllib.parse.urlencode({"query": query}),
            token,
            15.0,
            insecure,
        )
        (run_dir / "prometheus_targets.json").write_text(json.dumps(targets, indent=2))
        (run_dir / "prometheus_metric_snapshot.json").write_text(
            json.dumps(query_payload, indent=2)
        )
        report = prometheus_validate.build_report(
            targets,
            query_payload,
            namespace,
            (epp_service, vllm_service),
            require_flow_control,
            require_active_flow,
        )
    (run_dir / "prometheus_validation.json").write_text(json.dumps(report, indent=2))
    return report


async def run_workload(
    run_id: str,
    scenario: str,
    tenants: list[Tenant],
    duration_s: int,
    drain_timeout_s: int,
    prompt_pools: dict[int, list[dict[str, Any]]],
    default_input_tokens: int,
    default_output_tokens: int,
    token: str | None,
    out_dir: Path,
    traffic_seed: int = 42,
    trim_s: float = 0.0,
    metric_sample_interval_s: float = 0.5,
    arrival_mode: str = "closed_loop",
    outstanding_safety_ceiling: int = 10_000,
    poisson_send_lag_p99_limit_ms: float = 100.0,
    poisson_send_lag_max_limit_ms: float = 500.0,
    prefix_caching_declared: str = "unknown",
    analysis_windows: list[dict[str, Any]] | None = None,
    live_status_path: Path | None = None,
    stage_id: str | None = None,
    require_flow_control_proof: bool | None = None,
) -> dict[str, Any]:
    print(json.dumps({
        "event": "run_start",
        "run_id": run_id,
        "scenario": scenario,
        "duration_s": duration_s,
        "drain_timeout_s": drain_timeout_s,
        "trim_s": trim_s,
        "arrival_mode": arrival_mode,
        "poisson_send_lag_p99_limit_ms": poisson_send_lag_p99_limit_ms,
        "poisson_send_lag_max_limit_ms": poisson_send_lag_max_limit_ms,
    }), flush=True)
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_live_status(live_status_path, build_live_status(
        run_id, scenario, stage_id, "warming", "Metric preflight", 0,
        tenants, [], [], [],
    ))

    pre_epp = scrape_url(EPP_METRICS_URL, token)
    pre_vllm = scrape_url(VLLM_METRICS_URL)
    (run_dir / "pre_epp.prom").write_text(pre_epp)
    (run_dir / "pre_vllm.prom").write_text(pre_vllm)
    require_flow_control = (
        os.environ.get("EXPECT_FLOW_CONTROL", "1") != "0"
        if require_flow_control_proof is None
        else require_flow_control_proof
    )
    metric_preflight = metrics_capture.build_preflight_report(
        pre_epp, pre_vllm, require_flow_control
    )
    (run_dir / "metric_preflight.json").write_text(json.dumps(metric_preflight, indent=2))
    if not metric_preflight["valid"] and os.environ.get("ALLOW_MISSING_REQUIRED_METRICS") != "1":
        missing = ", ".join(metric_preflight["missing_concepts"])
        raise RuntimeError(f"required metrics missing before run: {missing}")
    prometheus_preflight = prometheus_validation(run_dir, require_flow_control)
    (run_dir / "prometheus_preflight.json").write_text(
        json.dumps(prometheus_preflight, indent=2)
    )
    if not prometheus_preflight["valid"]:
        raise RuntimeError("Prometheus metrics or scrape targets failed before run")
    run_started_epoch_s = time.time()

    samples: list[RequestSample] = []
    metric_rows: list[dict[str, Any]] = []
    metric_long_rows: list[dict[str, Any]] = []
    concurrency_log: list[dict[str, Any]] = []
    traffic_log: list[dict[str, Any]] = []
    safety_state: dict[str, Any] = {}
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        start_zero = now_s()
        tasks = [
            asyncio.create_task(tenant_driver(
                session, run_id, scenario, tenant,
                prompt_pools[tenant.input_tokens or default_input_tokens],
                tenant.output_tokens or default_output_tokens,
                duration_s, drain_timeout_s, start_zero, samples,
                concurrency_log, traffic_log, arrival_mode, outstanding_safety_ceiling,
                safety_state, traffic_seed, metric_sample_interval_s,
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
            metric_long_rows,
            metric_sample_interval_s,
        ))
        while now_s() - start_zero < duration_s:
            elapsed = int(now_s() - start_zero)
            write_live_status(live_status_path, build_live_status(
                run_id,
                scenario,
                stage_id,
                "warming" if elapsed < trim_s else "running",
                "Warmup excluded from results" if elapsed < trim_s else "Counted traffic",
                elapsed,
                tenants,
                samples,
                traffic_log,
                metric_rows,
            ))
            if elapsed % 30 == 0:
                if elapsed > 0:
                    write_partial_run_artifacts(
                        run_dir,
                        samples,
                        metric_rows,
                        metric_long_rows,
                        concurrency_log,
                        traffic_log,
                    )
                print(json.dumps({"event": "run_progress", "run_id": run_id, "scenario": scenario, "elapsed_s": elapsed, "samples": len(samples)}), flush=True)
            await asyncio.sleep(1)
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [result for result in task_results if isinstance(result, Exception)]
        if failures:
            write_live_status(live_status_path, build_live_status(
                run_id, scenario, stage_id, "failed", "Traffic driver failed",
                now_s() - start_zero, tenants, samples, traffic_log, metric_rows,
            ))
            raise RuntimeError(
                "traffic driver failed: " + ", ".join(type(failure).__name__ for failure in failures)
            )
        sampler_task.cancel()
        await asyncio.gather(sampler_task, return_exceptions=True)

    post_epp = scrape_url(EPP_METRICS_URL, token)
    post_vllm = scrape_url(VLLM_METRICS_URL)
    (run_dir / "post_epp.prom").write_text(post_epp)
    (run_dir / "post_vllm.prom").write_text(post_vllm)
    active_flow_metrics = metrics_capture.build_active_flow_report(post_epp)
    (run_dir / "active_flow_metrics.json").write_text(
        json.dumps(active_flow_metrics, indent=2)
    )
    run_ended_epoch_s = time.time()
    prometheus_settle_s = float(os.environ.get("PROMETHEUS_SCRAPE_SETTLE_S", "6"))
    if PROMETHEUS_URL and prometheus_settle_s > 0:
        await asyncio.sleep(prometheus_settle_s)
    prometheus_postflight = prometheus_validation(
        run_dir,
        require_flow_control,
        require_active_flow=require_flow_control,
        start_epoch_s=run_started_epoch_s,
        end_epoch_s=time.time(),
    )
    (run_dir / "prometheus_postflight.json").write_text(
        json.dumps(prometheus_postflight, indent=2)
    )

    tenants_set = {tenant.fairness_id for tenant in tenants}
    metric_evidence = metric_delta(pre_epp + "\n" + pre_vllm, post_epp + "\n" + post_vllm, tenants_set)
    headers_evidence = header_evidence(post_epp, tenants)
    engagement_evidence = flow_control_engagement(
        metric_rows, metric_evidence["endpoint_picker_queue"]
    )
    prefix_delta = metric_evidence["vllm"]["prefix_cache"]
    observed_metric_names = {key[0] for key in parse_prometheus(pre_vllm + "\n" + post_vllm)}
    cache_counters_present = bool(observed_metric_names.intersection({
        "vllm:prefix_cache_queries", "vllm:prefix_cache_queries_total",
        "vllm:prefix_cache_hits", "vllm:prefix_cache_hits_total",
    }))
    cache_evidence = {
        "declared": prefix_caching_declared,
        "counters_present": cache_counters_present,
        "queries_delta": prefix_delta["queries_delta"],
        "hits_delta": prefix_delta["hits_delta"],
        "valid": prefix_caching_declared == "off"
        and cache_counters_present
        and prefix_delta["queries_delta"] == 0
        and prefix_delta["hits_delta"] == 0,
    }
    schedule_evidence = offered_schedule_evidence(
        tenants,
        duration_s,
        traffic_seed,
        samples,
        arrival_mode,
        poisson_send_lag_p99_limit_ms,
        poisson_send_lag_max_limit_ms,
    )
    proof_checks_valid = (
        cache_evidence["valid"]
        and schedule_evidence["valid"]
        and (
            not require_flow_control
            or (
                headers_evidence["valid"]
                and active_flow_metrics["valid"]
                and engagement_evidence["valid"]
            )
        )
        and prometheus_postflight["valid"]
    )

    with (run_dir / "client_samples.csv").open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[f.name for f in fields(RequestSample)],
        )
        writer.writeheader()
        for sample in samples:
            writer.writerow(asdict(sample))

    if metric_rows:
        fieldnames = sorted({key for row in metric_rows for key in row})
        with (run_dir / "metric_samples.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(metric_rows)

    if metric_long_rows:
        fieldnames = [
            "run_id", "scenario", "elapsed_s", "sample_epoch_s", "source", "metric_generation",
            "metric", "labels_json", "value",
        ]
        with (run_dir / "metric_samples_long.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(metric_long_rows)

    if concurrency_log:
        with (run_dir / "concurrency_samples.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["elapsed_s", "tenant", "target_concurrency", "actual_inflight"])
            writer.writeheader()
            writer.writerows(concurrency_log)

    traffic_fieldnames = [
        "run_id", "scenario", "elapsed_s", "tenant", "arrival_process",
        "target_rps", "target_concurrency", "issued_requests",
        "completed_requests", "outstanding_requests", "send_delay_s",
        "safety_ceiling_state",
    ]
    with (run_dir / "traffic_samples.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=traffic_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(traffic_log)
    remove_partial_run_artifacts(run_dir)

    preconditions = {
        "run_id": run_id,
        "scenario": scenario,
        "arrival_mode": arrival_mode,
        "streaming_requested": True,
        "traffic_samples_written": True,
        "metric_samples_written": bool(metric_rows),
        "metric_samples_long_written": bool(metric_long_rows),
        "metric_preflight_valid": metric_preflight["valid"],
        "metric_preflight_missing": metric_preflight["missing_concepts"],
        "prometheus_preflight": prometheus_preflight,
        "prometheus_postflight": prometheus_postflight,
        "run_started_epoch_s": run_started_epoch_s,
        "run_ended_epoch_s": run_ended_epoch_s,
        "header_evidence": headers_evidence,
        "cache_off_evidence": cache_evidence,
        "offered_schedule": schedule_evidence,
        "flow_control_engagement": engagement_evidence,
        "active_flow_metrics": active_flow_metrics,
        "proof_checks_valid": proof_checks_valid,
        "client_samples_written": bool(samples),
        "outstanding_safety_ceiling": outstanding_safety_ceiling,
        "poisson_send_lag_p99_limit_ms": poisson_send_lag_p99_limit_ms,
        "poisson_send_lag_max_limit_ms": poisson_send_lag_max_limit_ms,
        "safety_ceiling": safety_state or {"state": "ok"},
        "request_ids": "present",
        "prompt_tokens": "present_from_prompt_pool",
        "completion_tokens": "stream_usage_when_available",
        "tpot": "computed_only_when_completion_tokens_available",
        "slo_proof_valid": compute_slo_proof_valid(
            arrival_mode, safety_state, bool(metric_rows), samples, proof_checks_valid
        ),
        "slo_proof_reason": slo_proof_reason(
            arrival_mode, safety_state, bool(metric_rows), samples, proof_checks_valid
        ),
    }
    (run_dir / "preconditions.json").write_text(json.dumps(preconditions, indent=2))

    client_summary = summarize_samples(run_id, scenario, samples, duration_s, trim_s, arrival_mode)
    window_summary = summarize_windows(
        run_id, scenario, samples, analysis_windows or [], arrival_mode
    )
    summary = {
        "run_id": run_id,
        "scenario": scenario,
        "duration_s": duration_s,
        "drain_timeout_s": drain_timeout_s,
        "arrival_mode": arrival_mode,
        "outstanding_safety_ceiling": outstanding_safety_ceiling,
        "poisson_send_lag_p99_limit_ms": poisson_send_lag_p99_limit_ms,
        "poisson_send_lag_max_limit_ms": poisson_send_lag_max_limit_ms,
        "safety_ceiling": safety_state or {"state": "ok"},
        "metric_sample_interval_s": metric_sample_interval_s,
        "metric_preflight": metric_preflight,
        "prometheus_preflight": prometheus_preflight,
        "prometheus_postflight": prometheus_postflight,
        "proof": {
            "headers": headers_evidence,
            "cache_off": cache_evidence,
            "offered_schedule": schedule_evidence,
            "flow_control_engagement": engagement_evidence,
            "active_flow_metrics": active_flow_metrics,
            "valid": proof_checks_valid,
        },
        "tenants": [asdict(t) for t in tenants],
        "client_summary": client_summary,
        "window_summary": window_summary,
        "metric_delta": metric_evidence,
        "metric_sample_summary": summarize_metric_samples(metric_rows),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if window_summary:
        fieldnames = list(window_summary[0])
        with (run_dir / "window_summary.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(window_summary)
    write_live_status(live_status_path, build_live_status(
        run_id, scenario, stage_id, "complete", "Run complete", duration_s,
        tenants, samples, traffic_log, metric_rows,
    ))
    print(json.dumps({"event": "run_complete", "run_id": run_id, "scenario": scenario, "samples": len(samples)}), flush=True)
    return summary


def summarize_metric_samples(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for concept in ("vllm_running", "vllm_waiting", "vllm_kv_cache"):
        for key in metrics_capture.METRIC_ALIASES[concept]:
            vals = [float(row[key]) for row in rows if row.get(key) not in ("", None)]
            if vals:
                result[key] = {
                    "max": max(vals),
                    "mean": statistics.mean(vals),
                    "p95": percentile(vals, 0.95),
                }
                break
    for key in ("epp:inflight_requests", "epp:inflight_tokens"):
        vals = [float(row[key]) for row in rows if row.get(key) not in ("", None)]
        if vals:
            result[key] = {
                "max": max(vals),
                "mean": statistics.mean(vals),
                "p95": percentile(vals, 0.95),
            }
    result["capture_health"] = {
        "samples": len(rows),
        "vllm_scrape_errors": sum(bool(row.get("vllm_scrape_error")) for row in rows),
        "epp_scrape_errors": sum(bool(row.get("epp_scrape_error")) for row in rows),
        "epp_plugin_state_errors": sum(
            bool(row.get("epp_plugin_state_error")) for row in rows
        ),
        "missing_inflight_request_samples": sum(
            row.get("epp:inflight_requests") in ("", None) for row in rows
        ),
        "missing_inflight_token_samples": sum(
            row.get("epp:inflight_tokens") in ("", None) for row in rows
        ),
    }
    preemption_key = next((
        key
        for key in metrics_capture.METRIC_ALIASES["vllm_preemptions"]
        if any(row.get(key) not in ("", None) for row in rows)
    ), None)
    preemptions = [float(row[preemption_key]) for row in rows
                   if preemption_key and row.get(preemption_key) not in ("", None)]
    if preemptions:
        result[preemption_key] = {
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


def sweep_defs(
    points: list[int], duration: int, ramp_s: float = 0.0
) -> list[tuple[str, list[Tenant], int]]:
    return [
        (
            f"saturation_concurrency_{point}",
            [Tenant("sweep-standard", 0, [{
                "start_s": 0,
                "duration_s": duration,
                "concurrency": point,
                "ramp_s": ramp_s,
            }])],
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
    parser.add_argument("--scenario-file", default="",
                        help="One JSON file containing complete scenarios, tenants, token shapes, traffic "
                             "phases, and analysis windows. When set, built-in scenarios and sweeps are skipped.")
    parser.add_argument("--tenant-shapes-file", default="",
                        help="JSON object keyed by fairness ID with optional input_tokens and output_tokens. "
                             "Use this for mixed realtime, agentic, and batch request shapes.")
    parser.add_argument("--analysis-windows-file", default="",
                        help="JSON mapping scenario names to named start_s/end_s windows. "
                             "Use it to report baseline, pressure, and recovery separately.")
    parser.add_argument("--poisson-phases-file", default="",
                        help="JSON mapping scenario and tenant phases to explicit Poisson rates. "
                             "Required for built-in scenarios in --arrival-mode poisson.")
    parser.add_argument("--live-status-file", default=os.environ.get("FLOW_LIVE_STATUS_FILE", ""),
                        help="Atomic JSON status file read by the Flow Control Flight Recorder monitor.")
    parser.add_argument("--stage-id", default=os.environ.get("FLOW_STAGE_ID", ""),
                        help="Campaign stage identifier shown in the monitor.")
    parser.add_argument("--prompt-pool-size", type=int, default=384)
    parser.add_argument("--prompt-pool-cache-dir", default="",
                        help="Reuse deterministic measured-token prompt pools across runs.")
    parser.add_argument("--sweep-duration", type=int, default=30)
    parser.add_argument("--sweep-points", default="8,16,24,32,48,64,96,128,160")
    parser.add_argument("--sweep-ramp-s", type=float, default=0.0,
                        help="Ramp each closed-loop sweep point from zero to target concurrency.")
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
                        help="Test 2 SLO shape: premium light so premium p95<300ms while standard floods. "
                             "Under the default closed-loop mode this is an offered-concurrency shape, NOT an "
                             "SLO proof; use --arrival-mode poisson with --poisson-phases-file "
                             "(preconditions.json slo_proof_valid) for open-loop evidence.")
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
    parser.add_argument("--allow-unknown-prefix-cache-state", action="store_true",
                        help="Allow a diagnostic run without a declared cache state. Never use for counted runs.")
    parser.add_argument("--traffic-scale", type=float, default=1.0,
                        help="Uniform multiplier on every scenario's target concurrency. 1.0 = as authored "
                             "(hot, peaks past the 128 knee). ~0.82 lands peaks at the knee for the clean spine.")
    parser.add_argument("--metric-sample-interval-s", type=float, default=0.5,
                        help="EPP, vLLM, and client-concurrency sample cadence in seconds (default 0.5; "
                             "minimum 0.1). Lower values increase metrics-endpoint and harness overhead.")
    parser.add_argument("--arrival-mode", default="closed_loop", choices=["closed_loop", "poisson"],
                        help="Traffic driver mode. closed_loop preserves existing target concurrency behavior; "
                             "poisson drives open-loop arrivals and requires an explicit rate for every phase.")
    parser.add_argument("--outstanding-safety-ceiling", type=int, default=10_000,
                        help="Per-tenant outstanding request ceiling for open-loop runs. Hitting it marks "
                             "the run invalid for SLO proof in preconditions.json.")
    parser.add_argument("--poisson-send-lag-p99-limit-ms", type=float, default=100.0,
                        help="Maximum allowed p99 gap between planned and actual open-loop sends.")
    parser.add_argument("--poisson-send-lag-max-limit-ms", type=float, default=500.0,
                        help="Maximum allowed single planned-to-actual open-loop send gap.")
    args = parser.parse_args()
    if args.metric_sample_interval_s < 0.1:
        parser.error("--metric-sample-interval-s must be at least 0.1 seconds")
    if args.outstanding_safety_ceiling < 1:
        parser.error("--outstanding-safety-ceiling must be positive")
    if args.poisson_send_lag_p99_limit_ms <= 0:
        parser.error("--poisson-send-lag-p99-limit-ms must be positive")
    if args.poisson_send_lag_max_limit_ms < args.poisson_send_lag_p99_limit_ms:
        parser.error("--poisson-send-lag-max-limit-ms must be at least the p99 limit")
    if args.vllm_prefix_caching == "unknown" and not args.allow_unknown_prefix_cache_state:
        parser.error("counted runs require --vllm-prefix-caching on|off")
    try:
        tenant_shapes = load_tenant_shapes(args.tenant_shapes_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"invalid --tenant-shapes-file: {exc}")
    try:
        analysis_windows = load_analysis_windows(args.analysis_windows_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"invalid --analysis-windows-file: {exc}")
    try:
        poisson_phases = load_poisson_phases(args.poisson_phases_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"invalid --poisson-phases-file: {exc}")
    try:
        scenario_file_defs, scenario_file_windows = load_scenario_file(args.scenario_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"invalid --scenario-file: {exc}")
    if args.scenario_file and (args.tenant_shapes_file or args.analysis_windows_file or args.poisson_phases_file):
        parser.error("--scenario-file already contains shapes, windows, and rates; do not combine override files")
    if scenario_file_defs:
        analysis_windows = scenario_file_windows
    warmup_shapes = required_warmup_shapes(
        args.input_tokens, args.output_tokens, scenario_file_defs
    )

    global TRAFFIC_SCALE
    TRAFFIC_SCALE = args.traffic_scale

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    live_status_path = Path(args.live_status_file) if args.live_status_file else None
    token = os.environ.get("METRICS_TOKEN")

    config = {
        "model_name": MODEL_NAME,
        "base_url": BASE_URL,
        "input_tokens_target": args.input_tokens,
        "output_tokens": args.output_tokens,
        "scenario_file": args.scenario_file,
        "scenario_file_scenarios": [item[0] for item in scenario_file_defs],
        "tenant_shapes_file": args.tenant_shapes_file,
        "tenant_shapes": tenant_shapes,
        "analysis_windows_file": args.analysis_windows_file,
        "analysis_windows": analysis_windows,
        "poisson_phases_file": args.poisson_phases_file,
        "poisson_phases": poisson_phases,
        "live_status_file": args.live_status_file,
        "stage_id": args.stage_id,
        "prompt_pool_size": args.prompt_pool_size,
        "prompt_pool_cache_dir": args.prompt_pool_cache_dir,
        "sweep_duration_s": args.sweep_duration,
        "sweep_ramp_s": args.sweep_ramp_s,
        "scenario_duration_s": args.scenario_duration,
        "drain_timeout_s": args.drain_timeout,
        "repeats": args.repeats,
        "warmup_duration_s": args.warmup_duration,
        "warmup_concurrency": args.warmup_concurrency,
        "warmup_shapes": [
            {"input_tokens": input_tokens, "output_tokens": output_tokens}
            for input_tokens, output_tokens in warmup_shapes
        ],
        "stabilization_repeats": args.stabilization_repeats,
        "warmup_included_in_summaries": False,
        "traffic_seed": args.traffic_seed,
        "headers": [
            "x-llm-d-inference-objective",
            "x-llm-d-inference-fairness-id",
            "x-gateway-inference-objective",
            "x-gateway-inference-fairness-id",
        ],
        "objectives": OBJECTIVES,
        "flow_control_mode": os.environ.get("FLOW_CONTROL_MODE", ""),
        "epp_plugin_state_url": EPP_PLUGIN_STATE_URL,
        "queue_depth_threshold": os.environ.get("QUEUE_DEPTH_THRESHOLD", ""),
        "kv_cache_util_threshold": os.environ.get("KV_CACHE_UTIL_THRESHOLD", ""),
        "metrics_staleness_threshold": os.environ.get("METRICS_STALENESS_THRESHOLD", ""),
        "flow_control_headroom": os.environ.get("FLOW_CONTROL_HEADROOM", ""),
        "usage_limit_threshold": os.environ.get("USAGE_LIMIT_THRESHOLD", ""),
        "concurrency_mode": os.environ.get("CONCURRENCY_MODE", ""),
        "max_concurrency": os.environ.get("MAX_CONCURRENCY", ""),
        "max_token_concurrency": os.environ.get("MAX_TOKEN_CONCURRENCY", ""),
        "add_estimated_output_tokens": os.environ.get(
            "ADD_ESTIMATED_OUTPUT_TOKENS", ""
        ),
        "steady_state_trim_s": args.steady_state_trim_s,
        "vllm_prefix_caching_declared": args.vllm_prefix_caching,
        "traffic_scale": args.traffic_scale,
        "metric_sample_interval_s": args.metric_sample_interval_s,
        "arrival_mode": args.arrival_mode,
        "outstanding_safety_ceiling": args.outstanding_safety_ceiling,
        "poisson_send_lag_p99_limit_ms": args.poisson_send_lag_p99_limit_ms,
        "poisson_send_lag_max_limit_ms": args.poisson_send_lag_max_limit_ms,
        "harness_version": "canonical",
        "peer_center": args.peer_center,
        "a_center": args.a_center,
        "a_amplitude": args.a_amplitude,
        "peer_flat": args.peer_flat,
        "matched_pair_one_band": args.test3_matched_pair_one_band,
        "matched_pair_two_band": args.test3_matched_pair_two_band,
    }
    (out_dir / "benchmark_config.json").write_text(json.dumps(config, indent=2))

    print(json.dumps({"event": "benchmark_start", **config}), flush=True)
    prompt_cache_dir = Path(args.prompt_pool_cache_dir) if args.prompt_pool_cache_dir else None
    prompts = await cached_prompt_pool(
        args.input_tokens, args.prompt_pool_size, prompt_cache_dir
    )
    prompt_pools = {args.input_tokens: prompts}
    (out_dir / "prompt_pool.json").write_text(json.dumps(prompts, indent=2))
    prompt_counts = Counter(p["tokens"] for p in prompts)
    print(json.dumps({"event": "default_prompt_pool_complete", "token_counts": dict(prompt_counts)}), flush=True)

    for token_target in sorted({shape[0] for shape in warmup_shapes} - set(prompt_pools)):
        pool = await cached_prompt_pool(
            token_target, args.prompt_pool_size, prompt_cache_dir
        )
        prompt_pools[token_target] = pool
        (out_dir / f"prompt_pool_{token_target}.json").write_text(json.dumps(pool, indent=2))
    print(json.dumps({
        "event": "warmup_prompt_pools_ready",
        "shapes": [
            {"input_tokens": input_tokens, "output_tokens": output_tokens}
            for input_tokens, output_tokens in warmup_shapes
        ],
    }), flush=True)

    if args.warmup_duration > 0:
        warmup_summaries = []
        for input_tokens, output_tokens in warmup_shapes:
            if len(warmup_shapes) == 1 and (
                input_tokens, output_tokens
            ) == (args.input_tokens, args.output_tokens):
                warmup_id = "00-warmup_standard"
                warmup_scenario = "warmup_standard"
            else:
                warmup_id = f"00-warmup_{input_tokens}in_{output_tokens}out"
                warmup_scenario = f"warmup_{input_tokens}in_{output_tokens}out"
            warmup_tenants = [Tenant(
                f"warmup-{input_tokens}in-{output_tokens}out",
                0,
                [{
                    "start_s": 0,
                    "duration_s": args.warmup_duration,
                    "concurrency": args.warmup_concurrency,
                }],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )]
            warmup_summary = await run_workload(
                warmup_id,
                warmup_scenario,
                warmup_tenants,
                args.warmup_duration,
                args.drain_timeout,
                prompt_pools,
                args.input_tokens,
                args.output_tokens,
                token,
                out_dir,
                args.traffic_seed,
                metric_sample_interval_s=args.metric_sample_interval_s,
                arrival_mode="closed_loop",
                outstanding_safety_ceiling=args.outstanding_safety_ceiling,
                prefix_caching_declared=args.vllm_prefix_caching,
                analysis_windows=[],
                live_status_path=live_status_path,
                stage_id=args.stage_id or "warmup",
                require_flow_control_proof=False,
            )
            warmup_summaries.append(warmup_summary)
            await asyncio.sleep(5)
        (out_dir / "warmup_summaries.json").write_text(
            json.dumps(warmup_summaries, indent=2)
        )
        if len(warmup_summaries) == 1:
            (out_dir / "warmup_summary.json").write_text(
                json.dumps(warmup_summaries[0], indent=2)
            )

    all_defs: list[tuple[str, list[Tenant], int]] = []
    if scenario_file_defs:
        all_defs.extend(scenario_file_defs)
    elif not args.skip_sweep:
        sweep_points = [int(point.strip()) for point in args.sweep_points.split(",") if point.strip()]
        all_defs.extend(sweep_defs(sweep_points, args.sweep_duration, args.sweep_ramp_s))
    if not scenario_file_defs and not args.skip_scenarios:
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

    try:
        apply_tenant_shapes(all_defs, tenant_shapes)
        apply_poisson_phases(all_defs, poisson_phases)
    except ValueError as exc:
        parser.error(str(exc))

    for scenario, tenants, _duration in all_defs:
        try:
            validate_arrival_configuration(tenants, args.arrival_mode)
        except ValueError as exc:
            parser.error(f"{scenario}: {exc}")

    prompt_targets = required_prompt_targets(args.input_tokens, all_defs)
    extra_prompt_targets = [target for target in prompt_targets if target not in prompt_pools]
    for token_target in extra_prompt_targets:
        pool = await cached_prompt_pool(
            token_target, args.prompt_pool_size, prompt_cache_dir
        )
        prompt_pools[token_target] = pool
        (out_dir / f"prompt_pool_{token_target}.json").write_text(json.dumps(pool, indent=2))
    print(json.dumps({
        "event": "prompt_pools_ready",
        "targets": prompt_targets,
        "pool_sizes": {str(target): len(prompt_pools[target]) for target in prompt_targets},
    }), flush=True)

    stabilization_summaries = []
    if args.stabilization_repeats > 0:
        for scenario_idx, (scenario, tenants, duration) in enumerate(all_defs, start=1):
            for repeat in range(1, args.stabilization_repeats + 1):
                run_id = f"stabilization-{scenario_idx:02d}-{scenario}-s{repeat:02d}"
                summary = await run_workload(
                    run_id, scenario, tenants, duration, args.drain_timeout, prompt_pools,
                    args.input_tokens, args.output_tokens, token, out_dir, args.traffic_seed,
                    args.steady_state_trim_s, args.metric_sample_interval_s,
                    args.arrival_mode, args.outstanding_safety_ceiling,
                    args.poisson_send_lag_p99_limit_ms,
                    args.poisson_send_lag_max_limit_ms,
                    args.vllm_prefix_caching,
                    analysis_windows.get(scenario, []),
                    live_status_path,
                    args.stage_id or None,
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
            run_id, scenario, tenants, duration, args.drain_timeout, prompt_pools,
            args.input_tokens, args.output_tokens, token, out_dir, args.traffic_seed,
            args.steady_state_trim_s, args.metric_sample_interval_s,
            args.arrival_mode, args.outstanding_safety_ceiling,
            args.poisson_send_lag_p99_limit_ms,
            args.poisson_send_lag_max_limit_ms,
            args.vllm_prefix_caching,
            analysis_windows.get(scenario, []),
            live_status_path,
            args.stage_id or None,
        )
        summary["repeat"] = repeat
        summaries.append(summary)
        await asyncio.sleep(5)

    (out_dir / "all_summaries.json").write_text(json.dumps(summaries, indent=2))

    with (out_dir / "summary.csv").open("w", newline="") as f:
        fieldnames = [
            "run_id", "repeat", "scenario", "arrival_mode", "tenant", "priority", "objective", "duration_s", "trim_s",
            "total", "n_steady_ttft", "low_n_use_p90", "http_200",
            "http_429", "http_503", "http_other", "non_200", "timeouts", "errors", "throughput_rps",
            "active_window_http_200", "drain_window_http_200", "steady_throughput_rps",
            "ttft_p50_s", "ttft_p90_s", "ttft_p95_s", "ttft_p99_s",
            "latency_p50_s", "latency_p90_s", "latency_p95_s", "latency_p99_s",
            "tpot_p50_s", "tpot_p90_s", "tpot_p95_s", "tpot_p99_s",
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

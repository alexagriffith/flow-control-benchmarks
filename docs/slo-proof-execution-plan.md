# SLO proof protocol

This protocol defines the evidence required before this benchmark can support a
customer-facing claim that llm-d flow control keeps a named workload inside a
named SLO under production-like traffic.

The current published results demonstrate priority admission and batch deferral:
when a shared inference pool saturates, higher-priority work is served ahead of
lower-priority work, and deferrable work can be queued instead of rejected. A
stronger SLO claim requires the additional gates below.

## Evidence Requirements

Every SLO proof run must publish the workload, load, objective, scope, and
provenance needed to reproduce the result.

| Field | Required evidence |
|---|---|
| Workload | Model, tokenizer, input-token distribution, output-token distribution, prompt corpus, streaming mode |
| Load | Arrival process, offered request rate by tenant, tenant mix, burst shape, warmup, counted window |
| Objective | TTFT, end-to-end latency, TPOT, and success-rate targets |
| Scope | Replica count, GPU type, tensor parallelism, prefix-caching state, detector mode |
| Runtime limits | `max-num-seqs`, `max-num-batched-tokens`, `max-model-len`, queue/deadline settings |
| Provenance | Runner git SHA, manifests or Helm values, benchmark config, raw artifacts, validator output |

Targets must be declared before the run starts. The protocol intentionally
allows different workloads to name different SLOs, but the chosen target cannot
be relaxed after seeing the result. If the run misses the target, lower the
offered load in a new run or restate the claim; do not massage the result.

The default proof target for an interactive premium tier is p95 TTFT <= 300 ms,
premium success rate >= 99%, plus named end-to-end latency and TPOT targets for
the selected output shape.

## Required Runner Signals

The runner must emit request-level, traffic-level, and system-level artifacts
with a shared `run_id`, `repeat_id`, `scenario`, and elapsed-time clock.

### Request samples

Each request row must include:

- Request ID.
- Tenant and resolved priority.
- Objective class.
- Planned arrival time and actual send time.
- Status, error class, and retry count if any.
- TTFT.
- End-to-end latency.
- Prompt token count.
- Completion token count.
- TPOT, calculated as `(latency_s - ttft_s) / max(1, completion_tokens - 1)`.
- Timeout flag.

### Traffic samples

Each traffic row must include:

- Target arrival rate or target concurrency by tenant.
- Actual issued requests per interval.
- Actual completions per interval.
- Outstanding requests.
- Open-loop safety-ceiling state.
- Driver-side queueing or send delay.

### Metric samples

Each metric row must preserve labels for EPP and vLLM series, including pod or
engine identity when exposed. Required series include:

- EPP queue size and queue bytes by priority and fairness ID.
- EPP queue duration or wait-time histogram when exposed.
- EPP pool saturation and priority resolution.
- vLLM running requests.
- vLLM waiting requests.
- vLLM KV-cache usage.
- vLLM preemption or swap counters.
- vLLM TTFT, end-to-end latency, and TPOT histograms when exposed.
- Request success, timeout, and rejection counters.

If a metric is unavailable in a build, the run must mark it unavailable rather
than silently substituting a different signal.

TTFT claims require streaming responses to be verified in the request path.
Client-measured TTFT and end-to-end latency also require the client-to-gateway
network path to be characterized, or the result must be labeled as including
that client network component.

## Test Matrix

### 1. Priority Admission Baseline

Purpose: prove the mechanism before claiming an SLO.

Protocol:

- Closed-loop concurrency is acceptable for this mechanism test.
- Use the same model shape across gate-off and gate-on arms.
- Run premium, standard, and batch tenants together.
- Drive enough load to saturate the pool.
- Run at least three counted repeats.

Pass criteria:

- Premium resolves to the intended high-priority band.
- The pool is saturated during the counted window.
- Premium p95 TTFT is below standard p95 TTFT with flow control on.
- Batch rejections drop materially with flow control on.
- Lower-priority tradeoffs are reported, not hidden.

### 2. Open-Loop SLO Attainment

Purpose: prove a named premium SLO under production-like arrivals.

Protocol:

- Use open-loop Poisson arrivals.
- Hold premium at the target offered RPS.
- Sweep standard and batch offered load until the pool reaches the stated
  saturation condition.
- Run gate-off and gate-on arms at the same offered rates.
- Use at least five counted repeats.
- Use at least 900 seconds per counted repeat for p99 claims, and at least 300
  seconds for p95-only claims.

Pass criteria:

- Every repeat meets the named premium TTFT, end-to-end latency, TPOT, and
  success-rate targets, or the miss is reported explicitly.
- 429, 503, timeout, and client errors count as SLO failures.
- Report median plus min-max range across repeats; do not pool repeats.

### 3. Queue Stability And Survivor Bias

Purpose: prove the result is not only the latency of successful survivors.

Protocol:

- Use the same open-loop mix as the SLO attainment test.
- Increase standard and batch load until the gate must defer work.
- Keep the run long enough to observe whether queues plateau or diverge.

Pass criteria:

- Premium success rate meets target.
- Rejections and timeouts are reported by tenant.
- EPP and vLLM queues plateau during the counted window.
- Any unbounded queue growth invalidates the SLO claim for that load.

### 4. Decode And Memory Pressure

Purpose: ensure fast first tokens are not hiding slow generation or KV pressure.

Protocol:

- Run the baseline 512-input / 128-output shape.
- Add at least one larger-context or longer-output shape.
- Keep premium steady while lower-priority work creates sustained pressure.
- Compare utilization-based gating with an admission-cap detector when both are
  available.

Pass criteria:

- Premium TPOT and end-to-end latency meet target.
- KV and preemption/swap metrics are present and non-dead.
- If decode or memory-pressure metrics are unavailable, the run cannot support a
  full SLO claim.

### 5. Detector And Scale-Out Reproduction

Purpose: show that the winning configuration is not a single-replica accident.

Protocol:

- Keep model, prompt pool, tenant mix, offered rates, and objective constant.
- Compare detector settings at one replica.
- Re-run the winning detector at additional replica counts.
- Preserve per-pod metrics; do not rely only on aggregate scrapes.

Pass criteria:

- Premium SLO holds at the stated replica count.
- Throughput scales in the expected direction.
- No single replica is overloaded while others are idle.
- Cross-pod behavior is visible in the published metrics.

## Replay Data Contract

The canonical run directory should contain:

- `client_samples.csv` for request-level timing, token, status, and TPOT data.
- `traffic_samples.csv` for offered load and driver state.
- `metric_samples.csv` for EPP and vLLM time series with labels preserved.
- `summary.json` for per-tenant percentiles and success rates.
- `preconditions.json` for gate, objective, saturation, and metric availability.
- `validator.json` for pass/fail results and failure reasons.
- `benchmark_config.json` for runtime and deployment provenance.

These artifacts are sufficient for both statistical validation and visual
replay. A replay tool may animate only the signals that exist in the artifacts;
exact request routing and exact vLLM iteration membership require trace IDs that
are present in client, router, and model-server events.

## Publication Rule

Use this wording only after the relevant test passes:

> Under `<load>`, on `<hardware/model/scope>`, flow control kept premium within
> `<SLO>` while preserving `<success-rate>` success.

Until then, use the narrower wording:

> Flow control provides priority admission under saturation: higher-priority work
> is served ahead of lower-priority work, and deferrable work can be queued
> instead of rejected.

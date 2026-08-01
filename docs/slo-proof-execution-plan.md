# SLO proof execution plan

This is the execution plan for proving whether llm-d flow control can keep a
named workload inside a named SLO under production-like traffic.

Current evidence proves priority admission and batch deferral. It does not yet
prove an unconditional SLO claim because the accepted runs are closed-loop,
single-replica, and TTFT-heavy.

## Current Cluster Snapshot

Checked with `kubectl` on the current test cluster before planning.

| Resource | Current state |
|---|---|
| GPU nodes | 2 x `p5.48xlarge` |
| GPU type | H100 |
| Allocatable GPUs | 16 total, 8 per GPU node |
| Requested GPUs | 8 total |
| Apparent free GPUs | 8 total |
| Existing GPU pods | Two existing benchmark pods, 4 GPUs each |

Rule: do not touch existing GPU workloads. Run flow-control tests on the
remaining allocatable GPUs, with explicit node and GPU accounting before every run.

## Phase 0: Make The Harness SLO-Capable

These changes are blocking before any customer-grade SLO run.

1. **Add open-loop Poisson arrivals.**
   - Keep current closed-loop concurrency for mechanism tests.
   - Add `--arrival closed|poisson`.
   - For Poisson, each tenant phase specifies `rate_rps`, not concurrency.
   - Inter-arrival time: `rng.expovariate(rate_rps)`.
   - Add a high safety ceiling for outstanding requests and record if it is hit.
   - If the safety ceiling is hit, mark the run invalid for SLO proof.

2. **Capture per-request TPOT.**
   - Record actual generated token count, not just requested `max_tokens`.
   - Preferred: use streaming usage metadata when available.
   - Compute `tpot_s = (latency_s - ttft_s) / max(1, completion_tokens - 1)`.
   - Add p50/p95/p99 TPOT per tenant and repeat.

3. **Add hard preconditions.**
   - Gate-on run must observe premium priority `100` in EPP flow-control metrics.
   - APC must be off or measured and reported.
   - Saturation must be real: `num_requests_running` near `max-num-seqs` and
     `num_requests_waiting > 0` for the counted window.
   - Required metrics must be present under accepted aliases.
   - Write `preconditions.json` next to every repeat.

4. **Add `pipeline/validate_slo.py`.**
   - Reads run directories, `preconditions.json`, and `summary.csv`.
   - Computes per-repeat percentiles, then reports median plus min-max range.
   - Never pools repeats.
   - Fails if any required precondition fails.
   - Fails if premium TTFT, E2E, TPOT, or success rate misses the named target.
   - Counts 429, 503, timeout, and client errors as SLO failures.

5. **Harden config provenance.**
   - Record actual GPU type, replica count, TP, max-num-seqs, max-model-len,
     max-num-batched-tokens, detector type, queue-depth threshold or
     maxConcurrency, prefix-caching state, and measured APC hit rate.
   - Fail gate-on runs if flow-control mode is empty or objectives are unresolved.

## Phase 1: Mechanism Baseline

Purpose: prove priority admission and validate the new precondition machinery.

Traffic:

- Closed-loop, because this is a mechanism test.
- Model shape: 512 input / 128 output.
- Tenants: premium steady, standard surge, batch flood.
- Gate off and gate on arms.
- 3 repeats, 300 s counted per repeat.

Pass criteria:

- Premium priority resolves to `100`.
- Pool is saturated.
- Premium p95 TTFT is lower than standard p95 TTFT with gate on.
- Batch 429s drop to zero or near zero.
- `validate_slo.py --mode mechanism` passes.

Publication wording allowed:

> Flow control provides priority admission under saturation: higher-priority work
> is served ahead of lower-priority work, and deferrable work can be queued
> instead of rejected.

## Phase 2: Open-Loop SLO Attainment

Purpose: prove a named SLO under production-like arrivals.

Traffic:

- Open-loop Poisson arrivals.
- Premium held at target RPS.
- Standard and batch load swept until saturation.
- Same offered rates for gate-off and gate-on arms.
- 5 repeats, 900 s counted per repeat for p99; 5 repeats, 300 s minimum for p95.

Default target to test:

| Axis | Premium target |
|---|---|
| TTFT | p95 <= 300 ms, unless another target is named |
| E2E | p95 <= output-shape target |
| TPOT | p95 <= target ms/token |
| Success | >= 99%; 429/503/timeout/error count as failures |

Pass criteria:

- Every repeat meets the named target, or the miss is reported explicitly.
- Premium success rate meets target.
- Standard and batch tradeoffs are reported, not hidden.

Publication wording allowed only after pass:

> Under `<load>`, on `<hardware/model/scope>`, flow control kept premium within
> `<SLO>` while preserving `<success-rate>` success.

## Phase 3: Success-Rate And Queue-Stability Test

Purpose: avoid "latency of survivors" bias.

Traffic:

- Same as Phase 2.
- Increase standard/batch offered load until the gate has to defer work.
- Keep the run long enough to see whether queues plateau or diverge.

Pass criteria:

- Premium success rate stays above target.
- Batch rejection rate improves without unbounded EPP queue growth.
- Queue size trends plateau during the counted window.

## Phase 4: Decode And KV Pressure Test

Purpose: make sure fast TTFT is not hiding slow generation or memory pressure.

Traffic:

- Premium steady, latency-sensitive traffic.
- Batch high-context traffic with longer outputs.
- Include at least 512/128 and one larger-context shape.
- Run both utilization detector and concurrency detector arms.

Required metrics:

- TTFT p95/p99.
- E2E p95/p99.
- TPOT p95/p99.
- `vllm:num_requests_running`.
- `vllm:num_requests_waiting`.
- KV cache usage metric, using the metric name emitted by the build.
- Swap/preemption counters, using the metric name emitted by the build.
- EPP queue size and queue duration by priority and fairness ID.

Pass criteria:

- Premium TPOT and E2E meet target.
- KV/preemption metrics are present and non-dead.
- If the build cannot expose KV/swap pressure, the run cannot support a full
  SLO claim.

## Phase 5: Detector Comparison

Purpose: identify whether queue-depth gating or concurrency capping is the right
tool for the SLO.

Arms:

- Utilization detector / queue-depth gating.
- Concurrency detector with `maxConcurrency` sweep: 32, 48, 64, 96, 128.

Same for all arms:

- Model.
- GPU type.
- Replica count.
- Prefix caching state.
- Prompt pool.
- Arrival rates.
- Tenant mix.

Report:

- Premium TTFT p95/p99.
- Premium E2E p95/p99.
- Premium TPOT p95/p99.
- Premium success rate.
- Standard latency.
- Batch rejection rate.
- Throughput.

Pass criteria:

- The winning detector meets the stated premium SLO without violating success
  target.
- Lower-priority cost is visible.

## Phase 6: Scale-Out Reproduction

Purpose: prove the behavior survives more than one replica.

Traffic:

- Repeat Phase 2 at 2 replicas, then 4 replicas.
- Use per-pod metrics, not a single aggregate vLLM scrape.
- Keep the same arrival mix and scale offered load with replica count.

Pass criteria:

- Premium SLO holds.
- Throughput scales with replicas.
- No single replica is overloaded while others are idle.
- EPP cross-pod behavior is visible in per-pod metrics.

## Cleanup

After every run:

1. Scale serving replicas back to baseline.
2. Restore detector config to the shipped default.
3. Delete transient benchmark jobs and scratch ConfigMaps.
4. Save raw run data under `data-v4/<test-name>/`.
5. Save `preconditions.json`, `validator.json`, and a README beside the raw data.
6. Update `data-v4/CANONICAL-RESULTS.json` only for runs that pass.
7. Archive invalid runs; do not delete them.

## Run Order

1. Implement harness changes: open-loop, TPOT, preconditions, validator.
2. Run Phase 1 to validate the mechanism and the validator.
3. Run Phase 2 at one conservative premium RPS.
4. If Phase 2 fails the 300 ms target, lower offered load or restate the target;
   do not massage the result.
5. Run Phase 3 and Phase 4.
6. Run Phase 5 detector comparison.
7. Run Phase 6 scale-out only after the single-replica proof is clean.

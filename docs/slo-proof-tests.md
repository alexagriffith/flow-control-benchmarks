# SLO proof tests

This repo separates two claims:

- **Priority admission claim:** flow control decides who waits when the pool saturates.
- **SLO claim:** a named workload stays inside a defined objective under a defined load.

The first claim is already demonstrated by the corrected service-tier and batch
runs. The second requires stricter tests. A run only supports an SLO claim when it
passes all gates below.

## SLO Definition

A publishable SLO result must name:

| Field | Required value |
|---|---|
| Workload | model, input length distribution, output length distribution |
| Load | offered request rate, arrival process, tenant mix |
| Objective | p95 or p99 TTFT, p95 or p99 end-to-end latency, TPOT, success rate |
| Duration | at least 5 repeats, at least 900 s counted per repeat for p99 |
| Scope | replica count, GPU type, tensor parallelism, prefix caching state |

Recommended default for the next proof run:

| Metric | Target |
|---|---|
| Premium TTFT | p95 <= 300 ms, or a different stated target |
| Premium E2E | p95 <= target for output length |
| Premium TPOT | p95 <= target ms/token |
| Premium success | >= 99%; 429, 503, and timeout count as failures |
| Standard / batch | reported, not hidden |

## Test 1: Priority Admission Under Saturation

Purpose: prove the mechanism works before claiming an SLO.

Protocol:

- One fixed model shape: 512 input tokens / 128 output tokens.
- Gate off and gate on arms.
- Three tenants: premium, standard, batch.
- Closed-loop concurrency is acceptable for this mechanism test.
- Pool must be saturated: `vllm:num_requests_running` pinned near `max-num-seqs`
  and `vllm:num_requests_waiting` > 0 during the counted window.

Pass criteria:

- Premium resolves to priority `100` in EPP flow-control metrics.
- APC is off or explicitly characterized.
- Premium p95 TTFT is lower than standard p95 TTFT with gate on.
- Batch 429s drop materially, ideally to zero, with gate on.

## Test 2: Open-Loop SLO Attainment

Purpose: prove a named premium SLO under realistic arrivals.

Protocol:

- Use an open-loop Poisson arrival driver.
- Hold premium at the target offered RPS.
- Sweep standard/batch load until the pool reaches saturation.
- Run gate off and gate on arms at the same offered rates.
- Use at least 5 repeats, 900 s counted per repeat for p99 claims.

Pass criteria:

- Premium meets the named TTFT and E2E objective in every repeat, or the result is
  reported as the median with a min-max range and a clear failure note.
- Premium success rate stays >= target.
- Standard and batch outcomes are reported so the tradeoff is visible.

## Test 3: Success-Rate SLO

Purpose: avoid declaring victory by excluding failed requests from latency.

Protocol:

- Count every request by tenant and status.
- Treat HTTP 429, HTTP 503, timeout, and client error as SLO failures.
- Report both latency-of-successful-requests and total success rate.

Pass criteria:

- Premium success rate meets target.
- Batch deferral reduces rejections without hiding unbounded queue growth.

## Test 4: Decode And Memory Blind-Spot

Purpose: ensure fast TTFT is not masking slow decode or KV pressure.

Protocol:

- Capture TTFT, end-to-end latency, TPOT, KV cache usage, swap/preemption counters,
  and EPP queue duration.
- Run a high-context batch surge while premium stays steady.
- Include a detector arm that caps admission, not only one that reorders queues.

Pass criteria:

- Premium TPOT and E2E stay within target.
- KV/preemption counters are present and non-dead.
- If the build cannot expose KV/swap metrics, the run cannot support a full SLO
  claim.

## Test 5: Detector Comparison

Purpose: identify which mechanism can hold an absolute latency target.

Protocol:

- Same model, hardware, prompt pool, tenant mix, and offered rates.
- Arm A: utilization detector / queue-depth gating.
- Arm B: concurrency detector with `maxConcurrency` sweep.
- Report premium p95/p99 TTFT, E2E, TPOT, success rate, standard latency, batch
  rejection rate, and throughput.

Pass criteria:

- The winning detector meets the stated premium SLO without violating the success
  target.
- The tradeoff imposed on lower-priority work is visible.

## Publication Rule

Use this wording only after the relevant test passes:

> Under `<load>`, on `<hardware/model/scope>`, flow control kept premium within
> `<SLO>` while preserving `<success-rate>` success.

Until then, use the narrower wording:

> Flow control provides priority admission under saturation: higher-priority work
> is served ahead of lower-priority work, and deferrable work can be queued instead
> of rejected.

# Flow control benchmarks for llm-d

Flow control is the Endpoint Picker's policy layer for multi-tenant inference. While the GPU has capacity, requests pass straight through. When the pool saturates, flow control activates: new work waits in policy-aware queues in front of vLLM, so priority, fairness, and traffic class decide what runs next instead of arrival order.

This repo holds the measured evidence, the charts drawn from it, the per-request data, and the runner that produced it. Everything ran on one H100 serving GPT-OSS 20B behind the llm-d inference gateway, with automatic prefix caching off so the latencies reflect scheduling rather than a warm cache.

## What flow control does, in one screen

Same traffic, same GPU, the only change is whether flow control is on. Premium is the interactive tier, standard is normal traffic, batch is deferrable background work.

<img src="assets/results-at-a-glance.svg" width="100%" alt="With flow control on under a saturated service-tier surge, premium p95 TTFT is 1117 ms versus 1406 ms for standard; batch isolation turns 48,224 rejected requests into zero">

- **A surge that isn't yours no longer gets equal priority.** Under a saturated standard surge, flow control on held premium p95 time to first token at 1117 ms versus 1406 ms for standard, with a 1056-1211 ms range across three 300 s repeats. Standard absorbed more of the wait it created.
- **Overload is queued, not rejected.** When batch traffic flooded the pool, the run without flow control returned 48,224 HTTP 429 rejections. With flow control on, zero. The platform held the deferrable work until capacity existed instead of pushing retries back to the application.
- **It costs nothing when the pool is calm.** When the GPU has headroom, gate-on and gate-off match. Flow control is the insurance that only charges under pressure.

**How to read the numbers.** The pool is one GPU, so the absolute latencies track that hardware, model shape, and offered load. The measured finding is priority admission: premium is served ahead of standard under saturation, and deferrable work waits instead of failing. A hard SLO claim needs a named objective and tests that report TTFT, end-to-end latency, TPOT, and success rate under the target load; see [SLO proof tests](docs/slo-proof-tests.md) and the [execution plan](docs/slo-proof-execution-plan.md).

## How we chose the operating point and configuration

Before any scenario, we swept a single tenant from 32 to 200 concurrent requests to find where this pool saturates. Throughput peaked at 128 concurrent requests, then fell: past that point the closed loop piled on latency without more goodput, and vLLM's running batch stayed pinned at its 128 limit with a real queue behind it. That knee is the operating point every scenario runs at, because a queueing policy only has something to do once the pool is full.

<img src="assets/operating-point-sweep.svg" width="100%" alt="Single-tenant sweep from 32 to 200 concurrent: throughput peaks at 128 requests per second and falls past it, while p95 TTFT climbs steadily; the knee at 128 is the chosen operating point">

The rest of the configuration follows from making the measurement honest rather than flattering:

| Choice | Value | Why |
|---|---|---|
| Request shape | 512 in / 128 out | A single fixed shape so every number is comparable; the output ladder (64, 128, 512) is varied separately |
| `max-num-seqs` | 128 | The GPU's running-batch limit; saturation means offered load beyond this, not that the H100 was exhausted |
| Prefix caching | off | So the latencies reflect scheduling, not a warm cache. Each prompt has a unique head and body |
| Repeats | 3 counted; 300 s for corrected SLO-sensitive runs | Repeats capture run-to-run variance; SLO-sensitive claims use per-repeat p95 with a min-max range, never pooled repeats |
| Priority | verified per run | Premium had to resolve to priority 100 in the flow-control queue metric before a run counted |

The sweep was run at 180 s per point over two passes in randomized order, and the two passes agreed. Data in [`data-v4/operating-point-sweep`](data-v4/operating-point-sweep).

<img src="assets/dispatch-path.svg" width="100%" alt="Dispatch path: the gateway tags each request, the Endpoint Picker queues by priority band with round-robin fairness and a saturation gate, then dispatches to vLLM">

## Service tiers under mixed load

**What it proves.** When the pool saturates, priority decides who waits. Premium stays ahead while standard absorbs more of the queue.

**What we saw.** Standard traffic surged past what the GPU could absorb, so vLLM ran a full batch of 128 with requests waiting behind it. The corrected 300 s gate-on rerun held premium p95 TTFT at 1117 ms, with a 1056-1211 ms range across repeats, while standard landed at 1406 ms. Priority moved more of the wait from the tier with the tighter objective to the tier that could absorb it.

<img src="assets/tiers-p95-gate.svg" width="100%" alt="Under saturation with flow control on, premium p95 TTFT is 1117 ms while standard is 1406 ms, with premium stable across three repeats">

<img src="assets/traffic-tiers.svg" width="100%" alt="Offered concurrency over the run: standard surges to about 96 in-flight requests mid-run while premium holds steady and low. The arrivals are noisy, not a flat synthetic load.">

The arrivals are noisy sinusoidal, so the load looks like production rather than a flat synthetic ramp. Standard surges past the GPU batch limit mid-run; premium holds a low steady rate throughout.

| tier | p50 on | p90 on | p95 on | p95 range |
|---|---|---|---|---|
| premium | 374 ms | 914 ms | 1117 ms | 1056-1211 ms |
| standard | 593 ms | 1240 ms | 1406 ms | 1250-1488 ms |


An earlier cut pooled all repeats into one percentile and produced a 251 ms tiers headline. That number is withdrawn. The corrected method computes p95 per repeat and reports the median with the min-max range, so run-to-run variance cannot disappear inside a pooled percentile.

<img src="assets/tiers-output-lengths.svg" width="100%" alt="Premium p95 TTFT for the corrected 128-token service-tier run is 1117 ms with flow control on; earlier output-length cells are being restated with the same per-repeat method">

Corrected 300 s gate-on data is in [`data-v4/tiers-gate-on-300s`](data-v4/tiers-gate-on-300s). The older 64- and 512-output cells remain in `data-v4/` for provenance, but their pooled output-length ratios are not used as headline SLO evidence until restated with the same per-repeat method.

**Why it matters.** A latency-sensitive product gets preferential admission through a surge it did not cause. That is a necessary part of enforcing tiered service objectives on shared capacity, but not the whole SLO proof by itself.

*Noisy priority run, 512 input / 128 output tokens, three counted 300 s repeats. Premium resolved to priority 100, verified in the flow-control queue metric before counting. Data in [`data-v4/tiers-gate-on-300s`](data-v4/tiers-gate-on-300s).*

## Batch isolation under surge

**What it proves.** Deferrable work fills the pool without displacing interactive work, and the gate moves the retry burden from the application to the platform.

**What we saw.** Batch traffic ramped past what the pool could serve. Without flow control, the saturation shed load, and 48,224 requests returned HTTP 429. With flow control on, zero requests were rejected. Batch was queued behind the interactive tiers, its p95 TTFT rose as it waited, and interactive latency was unharmed. The served throughput was nearly the same in both arms. What changed is who handled the overflow.

<img src="assets/batch-429-elimination.svg" width="100%" alt="Without flow control 48,224 batch requests are rejected with HTTP 429; with flow control on, zero rejections, and batch is queued behind the interactive tiers instead">

<img src="assets/traffic-batch.svg" width="100%" alt="Offered concurrency over the run: batch ramps in after the interactive tiers and floods the pool well past the GPU batch limit.">

<img src="assets/pct-batch.svg" width="100%" alt="p50, p90, and p95 TTFT for premium, standard, and batch, gate off versus on. Interactive tiers hold while batch is deferred.">

| tier | p50 off | p90 off | p95 off | p50 on | p90 on | p95 on |
|---|---|---|---|---|---|---|
| premium | 145 ms | 1030 ms | 1394 ms | 154 ms | 968 ms | 1196 ms |
| standard | 155 ms | 1082 ms | 1347 ms | 164 ms | 1069 ms | 1328 ms |
| batch | 559 ms | 1354 ms | 1605 ms | 1148 ms | 1975 ms | 2173 ms |

Gate off, 48,224 batch requests were rejected with HTTP 429. Gate on, zero. Batch's TTFT rises because it is queued behind the interactive tiers instead of being shed.


**Why it matters.** Without the gate, application teams build retry and backoff for the requests the platform refuses. With it, overnight document and report pipelines can fill the same GPUs that serve interactive traffic by day, and the platform holds the work until capacity exists. That is what lets a consolidated pool run hot.

*Batch isolation run, three counted repeats. Premium and batch priorities verified before counting. Data in [`data-v4/batch-gate-on`](data-v4/batch-gate-on) and [`data-v4/batch-gate-off`](data-v4/batch-gate-off).*

## Consolidation without giving up latency

**What it proves.** Two premium tenants share one GPU, and when a standard tenant floods the pool, flow control keeps the premium tenants ahead of the flood.

**What we saw.** Two premium tenants ran together while a standard tenant ramped in and pushed the pool to a full batch of 128 with requests waiting. With flow control on, the premium p95 TTFT held at 795 ms against standard at 1062 ms. The separation is the point: the shared pool ran hot, and the interactive tenants kept their place in line.

<img src="assets/consolidation-p95-gate.svg" width="100%" alt="Two premium tenants share a GPU; when a standard tenant floods the pool, premium p95 TTFT stays below standard with flow control on">

<img src="assets/traffic-consolidation.svg" width="100%" alt="Offered concurrency over the run: two premium tenants hold a steady packed load while a standard tenant floods the pool past the GPU batch limit.">

<img src="assets/pct-consolidation.svg" width="100%" alt="p50, p90, and p95 TTFT for premium and standard, gate off versus on, on the consolidated pool.">

| tier | p50 off | p90 off | p95 off | p50 on | p90 on | p95 on |
|---|---|---|---|---|---|---|
| premium | 256 ms | 750 ms | 886 ms | 256 ms | 664 ms | 795 ms |
| standard | 455 ms | 849 ms | 948 ms | 499 ms | 935 ms | 1062 ms |


**Why it matters.** Consolidation is a cost decision. Packing tenants onto one GPU only pays off if a noisy neighbor cannot take the interactive tenants down with it, and flow control is what holds that line.

*Saturated consolidation run, three counted repeats. Data in [`data-v4/consolidation-gate-on`](data-v4/consolidation-gate-on) and [`data-v4/consolidation-gate-off`](data-v4/consolidation-gate-off).*

## The boundary: priority acts across tiers, not among equals

Flow control decides which priority band is served first. That is where its leverage is, and it is also where the leverage ends. Two runs mark the edge.

**Same-band fairness.** We ran three tenants at the same priority and had one of them burst to several times the load of the other two, to see whether the greedy tenant could starve its well-behaved peers. Fairness bounds the burster's share of the pool, so it takes its turn rather than monopolizing dispatch. It does not lower a peer's tail latency, because all three sit in one band and compete for the same running batch. Fairness bounds throughput inside a band; it does not create a latency tier inside one.

<img src="assets/pct-fairness.svg" width="100%" alt="p50, p90, and p95 TTFT for the three same-priority tenants, gate off versus on. The effect is small because there is no priority gap to enforce.">

| tier | p50 off | p90 off | p95 off | p50 on | p90 on | p95 on |
|---|---|---|---|---|---|---|
| premium, all three at priority 100 | 258 ms | 740 ms | 894 ms | 290 ms | 752 ms | 882 ms |

**A calm pool.** Below saturation, gate-on and gate-off match. There is no queue to order, so the low latency comes from headroom, not from the policy.

Both results scope the strong ones. Flow control changes who waits when tiers contend for a full pool. Give it equal tenants, or a pool with room to spare, and it has nothing to arbitrate.

## The concurrency detector, and the limit of a latency SLO

Every result above uses the shipped **utilization detector**, which gates on vLLM queue depth. An alternative **concurrency detector**, from upstream llm-d, caps in-flight concurrency directly. The two answer the saturation question differently, so we swept the concurrency detector to see whether it does better on an absolute latency target. We swept its `maxConcurrency` from 32 to 128 to find whether any setting holds premium under 300 ms at a saturating load.

<img src="assets/upstream-sweep.svg" width="100%" alt="Premium p95 TTFT as a function of maxConcurrency forms a U with its minimum of 461 ms at maxConcurrency 48, while standard p95 falls as the cap loosens">

The premium p95 TTFT traces a U: it bottoms at 461 ms at `maxConcurrency` 48, then rises as the cap loosens and premium competes with more admitted standard traffic. Standard improves as the cap loosens, from 12.7 s down to 2.7 s.

What the sweep tells a platform team:

- **The cap is a tuning dial, not a switch.** `maxConcurrency` sets where the pain lands between premium and standard, and the two move in opposite directions across it.
- **The premium-optimal point is not the tightest one.** Premium is best at `maxConcurrency` 48, not 32. Below the optimum, even premium starts to queue; above it, premium's tail climbs as more standard traffic is admitted alongside it.
- **Tightening the cap trades standard's latency for premium's.** At 32, premium is protected and standard waits 12.7 s; at 128 they converge. You pick the point your SLAs demand.
- **A concurrency cap is a separation control, not a latency-SLO control.** No setting reached premium p95 under 300 ms at this offered load, because a 300 ms objective needs a lower load, not only a tighter cap. Holding an absolute SLO is a load decision, shown next.

*Upstream concurrency-detector sweep, matched load, premium resolved to priority 100. Data in [`data-v4/upstream-sweep`](data-v4/upstream-sweep).*

## SLO proof tests

The results above show priority admission and batch deferral. To claim that a deployment keeps a specific SLO, the benchmark has to define that SLO and pass it on all required axes: TTFT, end-to-end latency, TPOT, and success rate. The test design is in [`docs/slo-proof-tests.md`](docs/slo-proof-tests.md), with the public evidence protocol in [`docs/slo-proof-execution-plan.md`](docs/slo-proof-execution-plan.md). The short version:

- **Closed-loop priority admission test:** confirms the gate puts premium ahead of standard under saturation.
- **Open-loop SLO test:** drives a named request rate with Poisson arrivals and checks whether premium meets the target at p95/p99.
- **Success-rate test:** treats 429, 503, and timeout as SLO failures, not just latency exclusions.
- **Decode and memory test:** reports TPOT and KV/preemption metrics so a fast first token cannot hide slow generation.
- **Detector comparison:** compares utilization gating with concurrency caps at the same offered load, because admission caps are the mechanism for absolute latency objectives.

## Scope

Every run above is a single replica, so cross-pod scoring was not the subject; what is measured is priority admission control on one pool. A two-replica pass reproduced the tier result, with the premium p95 TTFT at 177 ms (data in [`data-v4/multi-replica-tiers`](data-v4/multi-replica-tiers)). The multi-replica behavior of the endpoint picker is a separate study.

A verification gap earlier in this campaign sent tenants to pools without priority objectives, so the gate saw every request at priority 0 and the tier results collapsed to no effect. Every counted run here was re-run with the priority resolution verified in the flow-control queue metric before the data was kept. The invalidated runs are archived, not deleted, and the correction is recorded in the run log.

## The walkthrough

**[How we got the numbers, one pass at a time](walkthrough.html)** is the longer story behind the results above: what was tested in what order, where a measurement turned out to be measuring the wrong thing, and how the campaign landed on numbers that survive scrutiny.

## Learn flow control

**[Open the interactive explainer](https://alexagriffith.github.io/flow-control-benchmarks/learn/flow-control-journey.html)**

Six stops take you from the cost problem to the dispatch path, the saturation gate, and what the policy does under pressure, ending in a playground where you drive the load yourself. It autoplays and runs in light or dark. Source is at [`learn/flow-control-journey.html`](learn/flow-control-journey.html). [The written explainer](https://alexagriffith.github.io/flow-control-benchmarks/learn/flow-control.html) is the same material as a page to read.


## Pipeline

`pipeline/benchmark_v4.py` is the runner that produced every accepted run. It drives multi-tenant traffic through the gateway with per-tenant objective and fairness headers, verifies priority resolution and gate state before counting, logs every request, scrapes vLLM and Endpoint Picker metrics, and writes one directory per repeat with `client_samples.csv`, `metric_samples.csv`, and `summary.json`. `pipeline/gen_charts.py` draws every chart in `assets/` from those CSVs. Same data in, identical charts out.

The first campaign's report and its run-level data are archived in [`archive/`](archive/) so the progression is visible. The numbers in this README supersede it.


## Other resources

- [First pressure campaign report](report/flow-control-under-pressure.html) — the 2026-07-21 run, kept for the progression. It ran with prefix caching on, so its latencies reflect a warm cache; the report carries that note at the top. The verified numbers on this page supersede it.

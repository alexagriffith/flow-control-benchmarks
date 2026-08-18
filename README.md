# Flow control benchmarks for llm-d

Flow control is the Endpoint Picker's policy layer for multi-tenant inference.
While the GPU has capacity, requests pass straight through. When the pool
saturates, flow control activates: new work waits in policy-aware queues in
front of vLLM, so priority, fairness, and traffic class decide what runs next
instead of arrival order.

This repo hosts measured evidence, generated charts, per-request data, and the
runner code that produced it. The benchmark campaigns ran GPT-OSS 20B on NVIDIA
H100 GPUs behind the llm-d inference gateway.

- **[Utilization detector](benchmark-data/rhaii-3.4-flow-control/):** established
  the operating point and tested priority, batch queuing, shared pools, and
  peer fairness.
- **[Concurrency detector](benchmark-data/upstream-flow-control-v0.9.0/):**
  tested request-count and token-count admission with production and advanced
  traffic patterns.
- **[Batch eviction](benchmark-data/batch-eviction/):** tested reserved capacity,
  eviction, and retry after batch work entered vLLM.

The takeaway figures are headline cuts. Later sections use package-level
results and evidence links.

## Takeaways

### Shared model pool

**Chat, batch, and tenant traffic can share one GPU instead of separate
underutilized pools.** The consolidation runs put two high-priority tenants and a
lower-priority burst on one model pool. Separate production scenarios test
real-time, standard, and batch traffic under the same flow-control policy.

<img src="assets/v09-tuning/consolidation-data.svg" width="100%" alt="Measured consolidation run showing two high-priority tenants and a lower-priority burst sharing one model pool, with high-priority p95 time to first token remaining much lower during the surge">

<sub>Traffic: selected repeat 2. Surge p95 TTFT: median across three request-count admission repeats, log scale.</sub>

<img src="assets/v09-tuning/consolidation.svg" width="100%" alt="Two high-priority tenants and a lower-priority burst share one GPU through the Endpoint Picker">

[Tenant consolidation runs](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/) · [Production scenario runs](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/) · [Additional consolidation evidence](benchmark-data/rhaii-3.4-flow-control/consolidation-gate-on/)

### Saturated pool

**When the pool saturates, policy decides who waits. Higher-priority traffic is
protected under load, while lower-priority traffic absorbs the queue’s latency.**
The Endpoint Picker continues to dispatch higher-priority requests and queues
lower-priority work until model capacity becomes available.

<img src="assets/v09-tuning/05-production.svg" width="100%" alt="Priority-tier results show platinum, gold, and silver traffic below one second median surge p95 time to first token while bronze batch exceeds 13 seconds">

<sub>Surge p95 TTFT: median across three request-count admission repeats, log scale.</sub>

[Priority-tier runs](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/)

### Same-priority peers

**One tenant’s surge does not starve same-priority peers.** Round-robin fairness
keeps each tenant receiving dispatch turns inside one priority band. Their tail
latency still depends on the vLLM capacity they share.

<img src="assets/v09-tuning/06-fairness.svg" width="100%" alt="Peer fairness results showing peer tenants retaining service while one tenant sends a larger burst">

<sub>Surge p95 TTFT: median across three request-count admission repeats, log scale. The lower panel shows Endpoint Picker round-robin dispatch and the resulting A/B/C sequence sent to the model pool.</sub>

[Peer fairness runs](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/same-priority-fairness/)

### Batch after dispatch

**Reserved capacity and eviction protect real-time p95 TTFT while batch
sequences are already executing in vLLM.** Reserved capacity keeps room for
real-time work before dispatch. Eviction releases eligible batch work after it
has entered vLLM, and the Async Processor retries that batch job in the tested
setup.

<img src="assets/batch-eviction-data.svg" width="100%" alt="Measured batch eviction results show reserved capacity and eviction keeping real-time p95 time to first token near the real-time-only reference while unprotected batch raises latency">

<sub>Real-time p95 TTFT: median across three matched 300-second repeats.</sub>

<img src="assets/batch-eviction-panel.svg" width="100%" alt="Eviction releases occupied batch capacity for real-time work and sends the interrupted batch request through retry to completion">

<sub>Reserved capacity acts before dispatch. Eviction acts after dispatch: the Endpoint Picker selects eligible batch work, Gateway/Envoy resets the vLLM stream and returns HTTP 429, and the Async Processor retries the batch request.</sub>

[Batch-interference runs](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/) · [Single-model-replica eviction runs](benchmark-data/batch-eviction/single-model-replica/) · [Two-model-replica eviction runs](benchmark-data/batch-eviction/two-model-replicas/)

## Flow control under load

Priority admission and shared-pool protection compare traffic classes with
flow control on. Batch overload compares flow control off and on.

<img src="assets/results-at-a-glance.svg" width="100%" alt="Three measured outcomes: priority admission keeps premium traffic ahead of standard traffic, batch overload produces zero HTTP 429 responses with flow control on, and premium traffic stays faster than standard traffic in a consolidated pool">

<sub>Compare bars within each card. The cards use the metric printed on each card.</sub>

- **Priority admission:** higher-priority traffic had lower p95 TTFT than
  standard traffic under saturated load.
- **Zero HTTP 429:** batch overload queued at the Endpoint Picker; HTTP 429
  responses dropped to zero with the gate on.
- **Shared pool protection:** higher-priority tenants kept lower p95 TTFT than
  standard traffic in one shared model pool.

[Earlier campaign evidence](benchmark-data/rhaii-3.4-flow-control/campaign-overview.md)

## Where flow control sits in llm-d

Requests carry tenant and priority metadata into the llm-d Gateway. Flow control
runs inside the Endpoint Picker, before the request is assigned to a vLLM
replica.

**Flow control runs inside the Endpoint Picker.**

<img src="assets/flow-control-in-llmd.svg" width="100%" alt="Requests carry tenant and priority metadata through the llm-d Gateway to flow control inside the Endpoint Picker, then to vLLM">

**Flow control queues by priority band and tenant.**

<img src="assets/dispatch-path.svg" width="100%" alt="Endpoint Picker flow control groups the consolidation run into a priority 100 tenant queue and a priority 0 Tenant C queue before dispatching to vLLM">

<sub>The first diagram shows where flow control runs. The second uses the consolidation run as a concrete queue example: Tenant A and Tenant B share priority 100, while Tenant C waits in priority 0.</sub>

## How we chose the operating point and configuration

The tests set GPU capacity first, then chose vLLM execution limits and the
Endpoint Picker signal that determined when new work should wait. Each sweep
changed one control while the rest of that package stayed fixed.

### Throughput stopped improving near 128 concurrent requests

<img src="assets/operating-point-sweep.svg" width="100%" alt="Two single-tenant concurrency-sweep passes show a throughput knee at 128 concurrent requests while p95 time to first token continues to rise at higher offered concurrency">

<sub>Operating-point sweep: median of two single-tenant passes. The 128-request knee became the capacity reference for the later tests.</sub>

### More active sequences added little throughput and more token delay

The middle batched-token budget produced the best tested throughput and
first-token latency. Raising the active-sequence limit added little throughput
and increased time per output token.

<img src="assets/configuration-engine.svg" width="100%" alt="vLLM engine sweeps compare throughput and latency at three maximum-sequence settings and three batched-token budgets, highlighting 128 sequences and 8,192 tokens">

<sub>Engine sweeps: three matched runs per setting. Bars use the units printed above each column.</sub>

### Request count stopped work before the vLLM queue grew

The in-flight request limit acts in the Endpoint Picker. The waiting-queue
threshold reacts after requests have accumulated inside vLLM.

<img src="assets/configuration-admission.svg" width="100%" alt="Endpoint Picker request-limit calibration and vLLM waiting-queue calibration compare throughput and p95 first-token latency at the tested settings">

<sub>Admission calibration: the highlighted row was carried forward within each package. The production detector comparison tests the two signal types directly.</sub>

### Request shape determined whether counting requests was enough

Request count remained the baseline for similarly sized, latency-sensitive
traffic. Exact input-token count was carried as the size-aware option when
prompt sizes varied materially.

| Sweep | What it established | Evidence |
|---|---|---|
| GPU operating point | 128 concurrent requests marked the throughput knee for the first campaign. | [Operating-point sweep](benchmark-data/rhaii-3.4-flow-control/operating-point-sweep/) |
| vLLM maximum sequences | 128 avoided the token-delay increase measured at higher limits. | [Engine configuration](benchmark-data/upstream-flow-control-v0.9.0/engine-configuration/) |
| vLLM batched-token budget | 8,192 produced the best tested throughput and p95 TTFT balance. | [Engine configuration](benchmark-data/upstream-flow-control-v0.9.0/engine-configuration/) |
| Endpoint Picker request limit | 128 became the request-count baseline; 160 added a small amount of throughput and higher p95 TTFT. | [Request and token admission](benchmark-data/upstream-flow-control-v0.9.0/request-and-token-admission-calibration/) |
| vLLM waiting-queue threshold | Eight waiting requests beat five in the short calibration; the later matched production comparison still favored request-count admission. | [Utilization detector calibration](benchmark-data/upstream-flow-control-v0.9.0/utilization-detector-calibration/) · [Detector comparison](benchmark-data/upstream-flow-control-v0.9.0/results.html#production) |
| KV-cache pressure | A 0.8 threshold was retained as a pressure guardrail; the sweep did not establish a latency-optimal default. | [Utilization detector calibration](benchmark-data/upstream-flow-control-v0.9.0/utilization-detector-calibration/) |
| Request size | Exact input tokens became the size-aware option; the input-plus-output estimate was not carried forward. | [Request and token admission](benchmark-data/upstream-flow-control-v0.9.0/request-and-token-admission-calibration/) |
| Earlier priority tuning | A 48-request cap minimized premium p95 TTFT in the historical two-repeat sweep; it did not set the later production baseline. | [Earlier priority tuning](benchmark-data/upstream-flow-control-v0.9.0/request-concurrency-priority-tuning/) |

## Production scenarios

The four scenarios used the same surge schedule and changed the traffic mix.
The charts below use request-count admission. Earlier utilization-detector runs
are linked when the comparison answers the same question.

### Bronze batch absorbed most of the surge delay

Platinum, Gold, and Silver stayed below one second while Bronze absorbed the
queue delay.

<img src="assets/v09-tuning/priority-tiers-section.svg" width="100%" alt="Priority-tier results show platinum, gold, and silver traffic below one second median surge p95 time to first token while bronze batch exceeds 13 seconds">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Priority-tier evidence](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/) · [Earlier priority-tier evidence](benchmark-data/rhaii-3.4-flow-control/tiers-gate-on-300s/)

### Batch queued behind real-time traffic

Real-time and standard traffic stayed below one second during the surge while
batch absorbed the wait.

<img src="assets/v09-tuning/batch-isolation-section.svg" width="100%" alt="Batch queue results show real-time and standard traffic below one second median surge p95 time to first token while batch exceeds 13 seconds">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Batch queued behind real-time traffic](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/) · [Earlier batch queue evidence](benchmark-data/rhaii-3.4-flow-control/batch-gate-on/)

### Two high-priority tenants shared one pool

Both high-priority tenants stayed below one second while the lower-priority
burst carried the queue delay.

<img src="assets/v09-tuning/consolidation-section.svg" width="100%" alt="Tenant-consolidation results show two high-priority tenants below one second median surge p95 time to first token while the lower-priority burst waits about 25.9 seconds">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Tenant-consolidation evidence](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/) · [Earlier consolidation evidence](benchmark-data/rhaii-3.4-flow-control/consolidation-gate-on/)

### Peers kept receiving dispatch turns

Peers B and C stayed below one second while Tenant A's larger burst waited
longer.

<img src="assets/v09-tuning/same-priority-fairness-section.svg" width="100%" alt="Peer fairness results show the bursting tenant waiting about 12.1 seconds while peers B and C stay near half a second">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Peers kept receiving dispatch turns](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/same-priority-fairness/)

### Request count kept real-time latency lower

Request-count admission kept real-time p95 TTFT lower in the two direct
comparisons. It limits admitted work before requests accumulate in vLLM;
queue-depth detection reacts after that queue forms.

<img src="assets/v09-tuning/detector-comparison.svg" width="100%" alt="Request-count admission keeps real-time p95 time to first token lower than queue-depth detection in matched consolidation and same-priority fairness scenarios">

<sub>Median p95 TTFT range across the selected real-time tenants. Both detector methods used the same prompts, traffic schedule, model, GPU, and repeat count.</sub>

[Detector comparison](benchmark-data/upstream-flow-control-v0.9.0/results.html#production)

## Advanced scenarios

These runs add mixed prompt shapes, batch already inside vLLM, repeated surges,
larger model pools, and prefix-aware routing.

### Request count favored real-time; input tokens favored batch

In the mixed workload, request-count admission kept real-time p95 TTFT lower.
Input-token admission kept batch p95 TTFT lower. A separate long-context
comparison showed queue activation, but its 16 ms mean latency difference was
not significant.

<img src="assets/v09-tuning/07-mixed.svg" width="100%" alt="Mixed-workload comparison shows the latency tradeoff between request-count admission and input-token admission across request shapes">

<sub>Mixed workload: median surge p95 TTFT across three repeats. Lower is better.</sub>

[Mixed-workload evidence](benchmark-data/upstream-flow-control-v0.9.0/mixed-production-workload/) · [Long-context evidence](benchmark-data/upstream-flow-control-v0.9.0/long-context-admission/)

### Longer output raised first-token latency

Agentic requests generated longer responses and held vLLM capacity longer.
Later requests waited longer for their first token even though time per output
token stayed similar.

<img src="assets/v09-tuning/07-workload-shapes.svg" width="100%" alt="Selected workload-shape results show agentic requests with higher p95 time to first token while p95 time per output token stays close to chat">

<sub>Selected workload shapes: median surge p95 across three single-tenant repeats.</sub>

[Workload-shape evidence](benchmark-data/upstream-flow-control-v0.9.0/selected-workload-shapes/)

### Running batch raised real-time latency

Batch already occupying vLLM left no immediate capacity for new real-time
requests. Priority queues cannot release work that is already running.

<img src="assets/v09-tuning/08-batch-interference.svg" width="100%" alt="Real-time p95 time to first token rises when batch work already occupies vLLM">

<sub>Batch interference test: real-time p95 TTFT with and without batch already inside vLLM. Median across three matched repeats.</sub>

[Batch-interference evidence](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/) · [Batch-eviction evidence](benchmark-data/batch-eviction/)

### The queue drained after both surges

Flow control engaged twice. The queue returned to zero after each surge, and
every request completed.

<img src="assets/v09-tuning/09-stability.svg" width="100%" alt="Real-time latency and queue pressure rise during two surges and return during both recovery windows">

<sub>Recovery test: premium p95 TTFT and queue pressure across one 30-minute run.</sub>

[Recovery evidence](benchmark-data/upstream-flow-control-v0.9.0/long-stability/)

### Per-GPU throughput held from one to four replicas

Served throughput per GPU varied by 0.6%. Sparse non-200 responses at one and
two replicas keep this result from proving rejection-free scale-out.

<img src="assets/v09-tuning/10-scale.svg" width="100%" alt="Served throughput per GPU stays stable as the model pool grows from one to four replicas">

<sub>Scale test: median served throughput per GPU across three repeats at each pool size.</sub>

[Scaling evidence](benchmark-data/upstream-flow-control-v0.9.0/multi-replica-scaling/)

### Prefix-aware routing did not justify a default change

It lowered latency for real-time chat, agentic, and batch requests but raised
latency for standard long-context requests. Cache-hit rate was unchanged, and
routing became less balanced.

<img src="assets/v09-tuning/11-routing.svg" width="100%" alt="Prefix-aware routing improves some request shapes while increasing latency and route imbalance for others">

<sub>Prefix-routing test: median p95 TTFT across three repeats. Route imbalance was 0.9% with random routing and 19.1% with prefix-aware routing.</sub>

[Prefix-routing evidence](benchmark-data/upstream-flow-control-v0.9.0/prefix-cache-routing/)

## Batch eviction tests

These tests start from the failure mode above: batch is already using vLLM
capacity when real-time demand arrives.

### Reserved capacity protected real-time traffic

Reserved capacity kept room before dispatch. Eviction released eligible batch
work that had already entered vLLM.

<img src="assets/batch-eviction-data.svg" width="100%" alt="Measured batch eviction results show reserved capacity and eviction keeping real-time p95 time to first token near the real-time-only reference while unprotected batch raises latency">

<sub>Single-replica test: real-time p95 TTFT across matched 300-second repeats.</sub>

### Evicted batch work returned through retry

The tested retry path returned each evicted batch job to normal request
processing and produced one final result.

<img src="assets/batch-eviction-panel.svg" width="100%" alt="Reserved capacity protects before dispatch; eviction selects eligible batch work after dispatch and retry returns the batch request to the normal path">

<sub>Batch eviction path: reserve before dispatch, evict eligible running batch after dispatch, then retry the same batch request.</sub>

- **Single replica:** reserved capacity and eviction kept real-time p95 TTFT near
  the real-time-only reference.
- **Retry:** evicted batch work was retried and produced one final result in the
  tested setup.
- **Two replicas:** the retry path also worked with two model replicas; the
  latency comparison remains directional.

[Single-replica eviction evidence](benchmark-data/batch-eviction/single-model-replica/) · [Two-replica eviction evidence](benchmark-data/batch-eviction/two-model-replicas/)

## Claims and evidence

<details markdown="1">
<summary><strong>Open the claim index</strong></summary>

### Shared capacity

| Claim | Evidence |
|---|---|
| Multiple tenants and workload classes shared one model pool while priority kept higher-priority traffic ahead of a lower-priority burst. | [Tenant consolidation](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/) · [Earlier consolidation](benchmark-data/rhaii-3.4-flow-control/consolidation-gate-on/) |
| Served throughput per GPU stayed stable as the pool grew. Smaller pools returned some HTTP non-200 responses. | [Model-pool scaling](benchmark-data/upstream-flow-control-v0.9.0/multi-replica-scaling/) |

### Priority and admission

| Claim | Evidence |
|---|---|
| Higher-priority requests kept faster access while lower-priority traffic absorbed more queue latency during the surge. | [Priority tiers](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/) |
| Excess batch traffic stayed queued at the Endpoint Picker while real-time traffic retained access. | [Batch queued behind real-time traffic](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/) · [Earlier batch queue evidence](benchmark-data/rhaii-3.4-flow-control/batch-gate-on/) |
| Request-count admission kept real-time p95 TTFT lower than queue-depth detection in two directly compared scenarios. | [Detector comparison](benchmark-data/upstream-flow-control-v0.9.0/results.html#production) |
| Round-robin fairness kept same-priority peers receiving dispatch turns during another tenant's burst. | [Peers kept receiving dispatch turns](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/same-priority-fairness/) |

### Batch after dispatch

| Claim | Evidence |
|---|---|
| Batch interference raised real-time latency after batch entered vLLM because admission control could not release occupied capacity. | [Batch interference](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/) |
| Reserved capacity kept room for real-time work, and eviction released capacity from eligible running batch requests. | [Single-model proof](benchmark-data/batch-eviction/single-model-replica/) · [Two-model proof](benchmark-data/batch-eviction/two-model-replicas/) |
| Evicted batch requests were retried, and each tested batch job produced one final result. | [Retry evidence](benchmark-data/batch-eviction/single-model-replica/) |

### Configuration and resilience

| Claim | Evidence |
|---|---|
| The capacity and engine sweeps selected the point where more admitted work stopped improving throughput and started adding latency. | [Operating-point sweep](benchmark-data/rhaii-3.4-flow-control/operating-point-sweep/) · [Engine configuration](benchmark-data/upstream-flow-control-v0.9.0/engine-configuration/) |
| Queue depth and KV-cache thresholds control when model-side pressure activates flow control. | [Utilization calibration](benchmark-data/upstream-flow-control-v0.9.0/utilization-detector-calibration/) |
| Token-aware admission activated queues for long-context prompts, but did not establish a general latency advantage in that package. | [Long-context admission](benchmark-data/upstream-flow-control-v0.9.0/long-context-admission/) |
| The selected agentic workload had higher first-token latency than chat while per-token latency stayed close. | [Selected workload shapes](benchmark-data/upstream-flow-control-v0.9.0/selected-workload-shapes/) |
| The queue drained after both sustained surges and every request completed. | [Long stability](benchmark-data/upstream-flow-control-v0.9.0/long-stability/) |

</details>

## Walkthrough and reproduction

The [combined evidence page](benchmark-data/results.html) links accepted runs to
their configuration, metrics, checks, and reproduction commands. The
[benchmark walkthrough](walkthrough.html) explains the test order and methods.

- [Flow control guide](learn/flow-control.html)
- [Interactive explainer](https://alexagriffith.github.io/flow-control-benchmarks/learn/flow-control-journey.html)
- [Benchmark data](benchmark-data/)
- [Runner and reproduction guide](pipeline/README.md)
- [Flow Control Flight Recorder](https://github.com/alexagriffith/flow-control-visualizer)

<details markdown="1">
<summary><strong>Scope</strong></summary>

The benchmarks establish measured behavior and comparative baselines. A
deployment service-level objective still needs a named target plus TTFT,
end-to-end latency, time per output token, and success-rate tests at the
deployment's expected load.

Three-repeat comparisons report descriptive medians and ranges. No formal
statistical significance is claimed. The recovery figure is a single
30-minute run; the production comparisons use three-repeat medians.

Most packages disable prefix caching so latency reflects scheduling behavior.
The prefix-routing package enables caching because cache reuse is the behavior
under test.

</details>

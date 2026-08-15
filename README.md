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

<img src="assets/v09-tuning/consolidation-data.svg" width="100%" alt="Measured consolidation run showing two high-priority tenants and a lower-priority burst sharing one GPU, with high-priority p95 time to first token remaining much lower during the surge">

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

<sub>Surge p95 TTFT: median across three request-count admission repeats, log scale. The lower panel shows Endpoint Picker round-robin dispatch and the resulting A/B/C sequence sent to the shared GPU.</sub>

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

Flow-control-on/off runs show the same system before and after policy queues
were active.

<img src="assets/results-at-a-glance.svg" width="100%" alt="Three measured outcomes: priority admission keeps premium traffic ahead of standard traffic, batch overload produces zero HTTP 429 responses with flow control on, and premium traffic stays faster than standard traffic in a consolidated pool">

<sub>Compare bars within each card. The cards use the metric printed on each card.</sub>

- **Priority admission:** higher-priority traffic had lower p95 TTFT than
  standard traffic under saturated load.
- **Zero HTTP 429:** batch overload queued at the Endpoint Picker; HTTP 429
  responses dropped to zero with the gate on.
- **Shared pool protection:** higher-priority tenants kept lower p95 TTFT than
  standard traffic in one shared model pool.

## Where flow control sits in llm-d

The Gateway labels each request. Flow control runs inside the Endpoint Picker,
before the request is assigned to a vLLM replica.

<img src="assets/flow-control-in-llmd.svg" width="100%" alt="Requests pass through the llm-d Gateway to flow control inside the Endpoint Picker, then to vLLM. Requests pass through while capacity is available; under saturation, priority queues decide which requests wait">

<details markdown="1">
<summary><strong>Dispatch path under pressure</strong></summary>

<img src="assets/dispatch-path.svg" width="100%" alt="Request path from gateway headers through Endpoint Picker priority queues and fairness to a vLLM model replica">

Reserved capacity limits how much lower-priority work can enter vLLM. Eviction
can release capacity from eligible batch work that has already entered vLLM.

</details>

## Configurations

The benchmark set the replica limit first, then compared admission signals, and
then tested protection for batch work that had already entered vLLM.

<img src="assets/operating-point-sweep.svg" width="100%" alt="Two single-tenant concurrency-sweep passes show served throughput peaking near 128 concurrent requests while p95 time to first token rises beyond that point">

<sub>Operating-point sweep: median of two single-tenant passes.</sub>

- **Replica capacity:** 128 concurrent requests was the operating point for
  production traffic.
- **Admission signal:** request-count admission kept real-time latency lower
  than queue-depth detection in the direct comparison.
- **Prompt shape:** token-count admission helped long-context and batch work
  when prompt sizes varied.
- **Batch already running:** reserved capacity protected room before dispatch;
  eviction released eligible batch work after dispatch.

[Operating-point sweep](benchmark-data/rhaii-3.4-flow-control/operating-point-sweep/) · [Detector comparison](benchmark-data/upstream-flow-control-v0.9.0/results.html#production) · [Mixed workload](benchmark-data/upstream-flow-control-v0.9.0/mixed-production-workload/) · [Batch eviction](benchmark-data/batch-eviction/)

## Production scenarios

The four scenarios used the same surge schedule and changed the traffic mix.
The charts below use request-count admission. Earlier utilization-detector runs
are linked when the comparison answers the same question.

### Higher-priority traffic stayed faster

**Higher-priority traffic kept faster access while bronze batch absorbed most
of the surge delay.**

<img src="assets/v09-tuning/priority-tiers-section.svg" width="100%" alt="Priority-tier results show platinum, gold, and silver traffic below one second median surge p95 time to first token while bronze batch exceeds 13 seconds">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Priority-tier evidence](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/) · [Earlier priority-tier evidence](benchmark-data/rhaii-3.4-flow-control/tiers-gate-on-300s/)

### Batch queued behind real-time traffic

**Batch used spare capacity and queued behind real-time traffic when the pool
saturated.**

<img src="assets/v09-tuning/batch-isolation-section.svg" width="100%" alt="Batch queue results show real-time and standard traffic below one second median surge p95 time to first token while batch exceeds 13 seconds">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Batch queued behind real-time traffic](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/) · [Earlier batch queue evidence](benchmark-data/rhaii-3.4-flow-control/batch-gate-on/)

### High-priority tenants shared one pool

**Two high-priority tenants shared the pool while a lower-priority burst carried
most of the queue delay.**

<img src="assets/v09-tuning/consolidation-section.svg" width="100%" alt="Tenant-consolidation results show two high-priority tenants below one second median surge p95 time to first token while the lower-priority burst waits about 25.9 seconds">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Tenant-consolidation evidence](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/) · [Earlier consolidation evidence](benchmark-data/rhaii-3.4-flow-control/consolidation-gate-on/)

### Peers kept receiving dispatch turns

**The tenant sending the larger burst waited longer while both peers continued
receiving dispatch turns.**

<img src="assets/v09-tuning/same-priority-fairness-section.svg" width="100%" alt="Peer fairness results show the bursting tenant waiting about 12.1 seconds while peers B and C stay near half a second">

<sub>Production scenario: median p95 TTFT during the surge, log scale.</sub>

[Peers kept receiving dispatch turns](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/same-priority-fairness/)

### Request count kept real-time latency lower

**Request-count admission kept real-time p95 TTFT lower than queue-depth
detection in the two direct comparisons.** Queue-depth detection activates
after requests accumulate inside vLLM. Request count limits admitted work
before that queue builds.

<img src="assets/v09-tuning/detector-comparison.svg" width="100%" alt="Request-count admission keeps real-time p95 time to first token lower than queue-depth detection in matched consolidation and same-priority fairness scenarios">

<sub>Median p95 TTFT range across the selected real-time tenants. Both detector methods used the same prompts, traffic schedule, model, GPU, and repeat count.</sub>

[Detector comparison](benchmark-data/upstream-flow-control-v0.9.0/results.html#production)

## Advanced scenarios

These runs add mixed prompt shapes, batch already inside vLLM, repeated surges,
larger model pools, and prefix-aware routing.

### Request shape changed the best admission signal

**Request-count admission favored real-time latency in the mixed workload.
Input-token admission lowered batch latency in the same run.** The long-context
package showed token-aware queue activation, but did not establish a latency
advantage.

<img src="assets/v09-tuning/07-mixed.svg" width="100%" alt="Mixed-workload comparison shows the latency tradeoff between request-count admission and input-token admission across request shapes">

<sub>Mixed workload: median surge p95 TTFT across three repeats. Lower is better.</sub>

[Mixed-workload evidence](benchmark-data/upstream-flow-control-v0.9.0/mixed-production-workload/) · [Long-context evidence](benchmark-data/upstream-flow-control-v0.9.0/long-context-admission/)

| Request-shape package | Takeaway |
|---|---|
| Mixed workload | Request-count admission was lower for real-time traffic; input-token admission was lower for batch. |
| Long context | Token-aware admission activated queues, but the paired latency difference was not significant. |

### Workload shape changed first-token latency

**The selected agentic workload had higher first-token latency than chat while
per-token latency stayed close.** This package characterizes the selected
request shapes before they are mixed with other traffic.

<img src="assets/v09-tuning/07-workload-shapes.svg" width="100%" alt="Selected workload-shape results show agentic requests with higher p95 time to first token while p95 time per output token stays close to chat">

<sub>Selected workload shapes: median surge p95 across three single-tenant repeats.</sub>

[Workload-shape evidence](benchmark-data/upstream-flow-control-v0.9.0/selected-workload-shapes/)

### Batch interference raised real-time latency

**Once batch work occupied vLLM capacity, real-time requests waited behind it.**
The batch-eviction runs above show how reserved capacity and eviction restore
room for real-time work.

<img src="assets/v09-tuning/08-batch-interference.svg" width="100%" alt="Real-time p95 time to first token rises when batch work already occupies vLLM">

<sub>Batch interference test: real-time p95 TTFT with and without batch already inside vLLM. Median across three matched repeats.</sub>

[Batch-interference evidence](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/) · [Batch-eviction evidence](benchmark-data/batch-eviction/)

### The queue drained after repeated surges

Queue pressure rose during each surge and drained during each recovery window.
The recovery package contains one 30-minute run.

<img src="assets/v09-tuning/09-stability.svg" width="100%" alt="Real-time latency and queue pressure rise during two surges and return during both recovery windows">

<sub>Recovery test: premium p95 TTFT and queue pressure across one 30-minute run.</sub>

### Per-GPU throughput stayed stable

**Served throughput per GPU stayed stable as the model pool grew.** Smaller
pools returned some HTTP non-200 responses, so only the four-replica runs
served all offered traffic.

<img src="assets/v09-tuning/10-scale.svg" width="100%" alt="Served throughput per GPU stays stable as the model pool grows from one to four replicas">

<sub>Scale test: served throughput per GPU across the tested pool sizes. Median across three repeats.</sub>

[Recovery evidence](benchmark-data/upstream-flow-control-v0.9.0/long-stability/) · [Scaling evidence](benchmark-data/upstream-flow-control-v0.9.0/multi-replica-scaling/)

### Prefix routing helped chat and added costs

**Prefix-aware routing lowered real-time chat latency in the routing workload.**
Prefix-aware routing raised latency for standard long-context requests and
increased route imbalance in the same run.

<img src="assets/v09-tuning/11-routing.svg" width="100%" alt="Prefix-aware routing improves some request shapes while increasing latency and route imbalance for others">

<sub>Prefix-routing test: real-time chat p95 TTFT and routing costs from the same run.</sub>

| Cost in routing run | Random routing | Prefix-aware routing |
|---|---:|---:|
| Standard long-context p95 TTFT | 10.3 s | 12.6 s |
| Route imbalance | 0.9% | 19.1% |
| HTTP 429 responses | 4 | 19 |

[Prefix-routing evidence](benchmark-data/upstream-flow-control-v0.9.0/prefix-cache-routing/)

## Batch eviction tests

These tests start from the failure mode above: batch is already using vLLM
capacity when real-time demand arrives.

**Reserved capacity kept room for real-time traffic before dispatch. Eviction
and retry handled eligible batch work after dispatch.**

<img src="assets/batch-eviction-data.svg" width="100%" alt="Measured batch eviction results show reserved capacity and eviction keeping real-time p95 time to first token near the real-time-only reference while unprotected batch raises latency">

<sub>Single-replica test: real-time p95 TTFT across matched 300-second repeats.</sub>

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

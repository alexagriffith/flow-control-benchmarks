# What changes when inference traffic stops being polite?

Flow control · GPT-OSS 20B · one H100 · llm-d

One model endpoint often mixes interactive chat, standard application traffic, tenant spikes, and batch jobs. When the GPU is full, something waits. Flow control decides who.

This post has two parts. First, the mechanism: what flow control is, how requests are classified, and how the endpoint picker decides what to release. Then the evidence: four benchmark scenarios that show how the mechanism behaves under pressure.

## One GPU, many kinds of traffic.

GPUs serving inference have a hard ceiling. In this deployment, vLLM could run at most 128 requests at once with `--max-num-seqs 128`. Below that ceiling, everything feels fast and scheduling policy is mostly invisible. At the ceiling, every new request has to wait.

The question is not whether waiting exists. The question is who absorbs it.

Flow control is the set of rules that answers that question. It cannot add GPU capacity, and it does not make the model faster. It makes shared capacity predictable by deciding whether the next dispatch opportunity goes to the premium request a person is waiting on, the standard internal app, or the batch job nobody will read until tomorrow.

## What flow control is, and what it is not.

Flow control is a predictability and capacity-protection feature.

It helps a platform consolidate workloads because teams no longer need to isolate every workload onto dedicated GPU capacity just to avoid noisy-neighbor risk. But it is not magic. If total demand exceeds the pool, some request still waits.

The difference is where the waiting happens.

Without flow control, requests can pile up inside vLLM's local queue. Once a request is there, the endpoint picker has already committed it to that backend. It cannot easily reorder that work by business priority or tenant fairness.

With flow control, the endpoint picker holds work in policy-aware queues before dispatch. When backend capacity opens, it releases work in the configured order.

Visual idea:

```text
Without flow control
Client → Gateway → vLLM local queue
                    [batch] [standard] [premium]  ← too late to reorder cleanly

With flow control
Client → Gateway → Endpoint Picker queues → vLLM
                    premium lane
                    standard lane
                    batch lane
```

## Follow one request through the stack.

Every request hits the same model route. The route does not change by tier. What changes is two HTTP headers:

- `x-gateway-inference-objective`: which service objective or traffic class this request belongs to.
- `x-gateway-inference-fairness-id`: which tenant or workload should get fair sharing inside its tier.

In our run, the objective resolved to one of three priority bands:

| Objective | Priority | Plain-English role |
| --- | ---: | --- |
| `gpt-oss-premium` | 100 | Interactive or SLA-backed traffic |
| `gpt-oss-standard` | 0 | Best-effort application traffic |
| `gpt-oss-batch` | -10 | Offline, batch, or sheddable work |

The endpoint picker combines priority and fairness ID into a flow key. Every unique flow key gets its own queue.

```text
Flow key = (priority, fairness ID)

(100, premium-tenant-a) → premium tenant A queue
(100, premium-tenant-b) → premium tenant B queue
(0, standard-tenant-a)  → standard tenant A queue
(-10, batch-tenant-a)   → batch tenant A queue
```

## Priority orders the lanes. Fairness shares them.

The endpoint picker does not maintain one flat queue. It builds a hierarchy.

```text
Priority 100  premium
  premium-tenant-a  [request] [request] [request]
  premium-tenant-b  [request] [request]
  premium-tenant-c  [request]

Priority 0    standard
  standard-tenant-a [request] [request] [request]
  standard-tenant-b [request] [request]

Priority -10  batch
  batch-tenant-a    [request] [request] [request] ...

highest priority with work
          ↓
round robin across tenants in that band
          ↓
first request in the selected tenant queue
          ↓
saturation gate → router → vLLM
```

The dispatch cycle follows five steps:

1. Select the highest-priority band with queued work.
2. Apply the fairness policy to choose a flow inside that band.
3. Apply the ordering policy to choose a request inside that flow.
4. Ask the saturation detector whether the inference pool can accept more work.
5. If capacity is available, send the request through the normal routing pipeline. If the pool remains saturated, leave the request queued.

In this run, fairness was round-robin across flow keys, and ordering inside a flow was first come, first served.

## The saturation gate is what makes the policy visible.

Below saturation, the endpoint picker can dispatch quickly, so priority and fairness do not have much to do. At saturation, those policies start shaping latency.

The saturation detector watched two backend pressure signals:

- vLLM queue depth, with `queueDepthThreshold: 4`.
- KV-cache utilization, with `kvCacheUtilThreshold: 0.8`.

When backend pressure crossed the configured threshold, the endpoint picker kept requests in its own queues longer. As capacity became available, the dispatcher released work by priority and fairness.

Visual idea:

```text
Backend has room?

yes → dispatch now → normal scheduler → vLLM
no  → keep queued in EPP → retry on next dispatch cycle
```

## How did we know flow control was doing anything?

We checked three layers:

| Signal | Where it came from | Why it matters |
| --- | --- | --- |
| Request status | Per-request client samples | Shows success, timeout, and error responses. |
| Time to first token | Client-side streaming timestamps | Measures the delay before a user sees output. |
| EPP queue duration | Endpoint-picker metrics | Shows which priority or tenant waited in the gateway queue. |
| Running requests | `vllm:num_requests_running` | Shows how much work vLLM actively served. |
| Waiting requests | `vllm:num_requests_waiting` | Shows when the runtime was full and overflow work waited. |

We considered flow control active only when the layers agreed: vLLM was full, queueing appeared, and client-visible latency moved with queueing.

## We made the requests boring on purpose.

The benchmark used deterministic prompts so traffic policy, not prompt randomness, was the variable.

The main pressure runs used about 512 input tokens and 128 output tokens. The SLA consolidation pass used 512 input tokens and 64 output tokens to represent a shorter interactive answer.

This matters because concurrency alone is not a workload. A 512-token answer and a 64-token answer hold runtime capacity for different amounts of time.

## Four scenarios, four questions.

### Test 1. Can we consolidate underutilized workloads?

This is the FinOps story.

The customer question is not "can every overloaded request stay under 300 ms?" The question is whether two underutilized interactive workloads can share one H100 while keeping p95 time to first token under the service objective.

The best evidence came from the output-64 consolidation pass after warm stabilization:

| Counted repeat | Tenant A p95 TTFT | Tenant B p95 TTFT |
| --- | ---: | ---: |
| Repeat 2 | 117 ms | 125 ms |
| Repeat 3 | 123 ms | 124 ms |

Takeaway: normal-load consolidation can stay inside a 300 ms p95 TTFT objective. That is the FinOps point.

### Test 2. Does premium stay ahead when standard surges?

This is the business-hours mixed workload.

Premium tenants stayed at low steady load. Standard tenants surged in the middle of the run and then recovered.

In the accepted noisy run, vLLM reached 128 running requests and waiting requests appeared. Premium traffic stayed materially ahead of standard traffic:

| Lane | p50 TTFT | p95 TTFT |
| --- | ---: | ---: |
| Premium | ~40 ms | ~555-568 ms |
| Standard | ~380 ms | ~890-899 ms |

Takeaway: when the pool is full, standard absorbs more waiting. Premium does not get a free speedup; it gets a better place in line.

### Test 3. Can one premium tenant spike without collapsing the others?

This is the same-priority fairness story.

All three tenants were premium. Tenant A sent more traffic during the middle of the run. Tenants B and C stayed in the same priority band.

The accepted saturated rerun showed real pressure: vLLM waiting p95 was 31-33 requests, and all counted requests completed with HTTP 200.

| Tenant | Role | Avg p95 TTFT | Avg EPP queue mean |
| --- | --- | ---: | ---: |
| premium-tenant-a | Greedy spike | 744 ms | 107 ms |
| premium-tenant-b | Same-priority peer | 670 ms | 80 ms |
| premium-tenant-c | Same-priority peer | 675 ms | 81 ms |

Takeaway: priority chooses the lane. Fairness shares the lane.

The gap is intentionally moderate. Fairness is not supposed to punish a busy tenant dramatically. It is supposed to keep peers bounded while the busy tenant carries more of the queue it created.

### Test 4. Can batch work starve interactive traffic?

This is priority inversion prevention.

Premium and standard traffic stayed active. Batch traffic surged later in the run. The batch lane absorbed the largest queue time:

| Lane | Priority | p95 TTFT | Mean EPP queue |
| --- | ---: | ---: | ---: |
| Premium | 100 | ~608 ms | ~64 ms |
| Standard | 0 | ~660 ms | ~66 ms |
| Batch | -10 | ~976 ms | ~202 ms |

Takeaway: batch paid for the surge. That is what priority inversion prevention should look like.

## What about dropping requests?

The main four-scenario evidence is about queueing, priority, and fairness. It is not primarily a drop test.

The original clean Test 4 did observe two client-visible 503s on batch traffic in one repeat while premium and standard remained clean. That is useful as a caveat, but it is not a clean rejection curve.

We also have a separate overload probe where TTL and queue budget were tightened to force rejection behavior. That is the right evidence for "what happens when the platform starts to drop," but it should be framed as a deliberate overload configuration, not as the main production setting.

## What this proves.

The tests prove a mechanism, not a miracle.

- Flow control can help consolidate GPU workloads because normal-load tenants can share one pool while staying inside a latency objective.
- Under saturation, priority decides which class waits first.
- Within the same priority class, fairness keeps one tenant from monopolizing every dispatch turn.
- Batch traffic can absorb queueing behind interactive traffic.
- The policy is auditable: headers map to objectives, objectives map to priorities, and metrics show where requests waited.

When the runtime fills up, someone feels the wait. Flow control makes that choice explicit.

## What this does not claim.

It does not claim that overloaded traffic always stays under 300 ms. The 300 ms story belongs to the normal-load consolidation point.

It does not claim flow control adds capacity. It lets a platform share existing capacity more safely.

It does not claim every workload shape behaves the same. Token shape matters. A longer generation can change throughput and latency at the same concurrency.

## The short version.

Flow control lets the platform say:

> We can share the GPU when there is room. When there is not room, the platform decides who waits based on policy instead of accident.

That is the difference between a shared inference pool and a noisy queue.

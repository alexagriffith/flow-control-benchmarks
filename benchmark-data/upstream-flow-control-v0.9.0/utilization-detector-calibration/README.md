# Utilization detector calibration

## Business question

When should queue depth or KV-cache pressure activate flow control?

## Queue-depth result

Queue depth 8 produced the lowest p95 TTFT in this short-request closed-loop
calibration, with 1.6% lower median throughput than queue depth 5.

| Queue depth | Repeats | Steady RPS | p95 TTFT | p99 TTFT |
|---:|---:|---:|---:|---:|
| 5 | 3 | 47.6 | 1,863 ms | 2,142 ms |
| 8 | 3 | 46.8 | 1,607 ms | 1,864 ms |

This does not make 8 the production default. A higher threshold waits for a
larger backend queue before activating policy, so the later open-loop scenarios
compare earlier thresholds 2 and 5 under bursty, mixed-priority traffic.

## KV-cache result

The KV-cache sweep intentionally used 28,672 input tokens, 256 output tokens,
and concurrency 96 to create memory pressure. Threshold 0.8 activated policy
earlier than 0.75 and had lower p95 TTFT, but neither improved TTFT over the
flow-control-off control.

| KV setting | Repeats | p95 TTFT | Peak policy queue |
|---|---:|---:|---:|
| Flow control off | 3 | 16,938 ms | 0 requests |
| Threshold 0.8 | 3 | 21,559 ms | 23-27 requests |
| Threshold 0.75 | 3 | 28,540 ms | 28-29 requests |

Threshold 0.8 is retained as the primary memory-pressure calibration point.
This sweep verifies detector activation and exposes its cost; it does not prove
a latency SLO.

## Method

- One llm-d Endpoint Picker v0.9.0 served one vLLM model replica on one NVIDIA
  H100.
- Prefix caching was disabled and verified by configuration and counters.
- Queue-depth calibration tested thresholds 1, 2, 4, 5, and 8 with short,
  fixed-size requests.
- KV-cache calibration tested thresholds 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, and
  1.0, plus a flow-control-off control.
- Selected boundaries and the control have three matched runs; intermediate
  points are labeled calibration evidence.
- Every retained run captured request rows, traffic samples, queue metrics, KV
  utilization, preemptions, route counts, and cache counters.

## Evidence

| File | Contents |
|---|---|
| [`summary.csv`](summary.csv) | One row per retained detector run with explicit units and evidence role. |
| [`request-results.csv`](request-results.csv) | One sanitized row per request. |
| [`traffic-samples.csv`](traffic-samples.csv) | Issued, completed, and outstanding requests over time. |
| [`system-metrics.csv`](system-metrics.csv) | Curated queue, saturation, vLLM, KV-cache, and preemption metrics. |
| [`run-evidence.csv`](run-evidence.csv) | Metrics, routes, headers, cache state, engagement, and proof gates. |
| [`analysis.json`](analysis.json) | Matched medians, decision, and claim boundary. |

## Scope

These closed-loop sweeps calibrate activation points. Customer-facing behavior
is established by the open-loop production scenarios, where thresholds are
tested with noisy traffic, mixed priorities, and varying request shapes.

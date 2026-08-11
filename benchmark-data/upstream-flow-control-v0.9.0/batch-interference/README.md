# Batch interference baseline

## Business question

How much can batch work already running in vLLM increase realtime latency?

## Result

Realtime median p95 TTFT increased from 133 ms to 15,378 ms when batch work was
already running in vLLM. The increase was 15,245 ms, or 115.3x the
realtime-only reference.

| Realtime result | Realtime only | Batch already running |
|---|---:|---:|
| Median p95 TTFT | 133 ms | 15,378 ms |
| Median p99 TTFT | 147 ms | 18,294 ms |
| Median p95 TPOT | 7.7 ms/token | 267.1 ms/token |
| Median p95 end-to-end latency | 989 ms | 34,189 ms |

All 3,600 requests completed without errors or rejections. Flow control held
batch work in its queue longer than realtime work, but it could not reclaim
batch requests that were already running inside vLLM. During the interference
runs, vLLM reached 128 running requests and 63 median peak waiting requests.

## Method

- One Endpoint Picker served one vLLM model replica on one NVIDIA H100 GPU.
- The realtime-only arm was configured for 4,096 input and 128 output tokens
  at a noisy sinusoidal rate centered on 3 requests/s.
- The interference arm started 20,000-input, 128-output batch traffic first at
  a rate centered on 2.75 requests/s, then replayed the same realtime trace.
- Each arm ran three matched 240-second repeats.
- The Endpoint Picker used request-concurrency detection with
  `maxConcurrency=128`, 10% headroom, and priority-aware queues.
- Reserved capacity and batch eviction were not configured.
- Prefix caching was disabled, cache counters remained zero, and vLLM recorded
  no preemptions.

The complete configuration is in [`run-config.json`](run-config.json).

## Evidence

| File | Contents |
|---|---|
| [`summary.csv`](summary.csv) | Run-level realtime latency, queue, vLLM, KV cache, and flow-control results. |
| [`request-results.csv`](request-results.csv) | One sanitized row per realtime or batch request. |
| [`traffic-samples.csv`](traffic-samples.csv) | Issued, completed, and outstanding requests by workload over time. |
| [`system-metrics.csv`](system-metrics.csv) | Curated Endpoint Picker and vLLM metrics collected during every run. |
| [`run-evidence.csv`](run-evidence.csv) | Schedule, header, route, metric, cache, flow-control, and data-quality gates. |
| [`analysis.json`](analysis.json) | Three-repeat medians, ranges, comparison, and claim boundary. |

## Scope

This is an interference baseline. It shows why priority queues alone cannot
reclaim capacity from batch work already running in vLLM. It does not test
reserved capacity or batch eviction.

The evidence includes queue, saturation, vLLM running and waiting, KV cache,
and preemption data. Exact Endpoint Picker in-flight plugin-state samples are
not part of this package.

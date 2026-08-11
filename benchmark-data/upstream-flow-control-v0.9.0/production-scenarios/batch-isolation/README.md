# Batch isolation

## Business question

Can realtime and standard work retain lower TTFT while batch traffic shares the
same model server?

## What the benchmark showed

| Workload | Median surge p95 TTFT |
|---|---:|
| Realtime | 442 ms |
| Standard | 515 ms |
| Batch | 13,077 ms |

Realtime and standard traffic retained lower TTFT while batch absorbed more of
the queue. The selected result uses three repeats. Every request succeeded,
flow control engaged during each run, and prefix caching remained off.

## Run inventory

- Selected detector: request count 128 with 15% headroom; 3 repeats
- Calibration: utilization queue depth 2; 1 run
- Traffic: open-loop Poisson arrivals with noisy sinusoidal phases, a timed
  surge, and recovery
- Request shape: 511 input tokens and 128 output tokens
- Topology: 1 Endpoint Picker, 1 vLLM model replica, 1 NVIDIA H100

## Evidence

| File | Contents |
|---|---|
| [`summary.csv`](summary.csv) | Per-run outcomes, throughput, TTFT, end-to-end latency, and TPOT. |
| [`window-summary.csv`](window-summary.csv) | Baseline, surge, and recovery metrics. |
| [`request-results.csv`](request-results.csv) | One sanitized row per request. |
| [`traffic-samples.csv`](traffic-samples.csv) | Issued, completed, and outstanding requests over time. |
| [`system-metrics.csv`](system-metrics.csv) | Queue, saturation, vLLM, KV-cache, preemption, and cache metrics. |
| [`run-evidence.csv`](run-evidence.csv) | Headers, route counts, cache state, flow-control engagement, and proof gates. |
| [`run-config.json`](run-config.json) | Images, topology, engine settings, detector settings, and traffic method. |
| [`analysis.json`](analysis.json) | Medians, ranges, run inventory, and claim boundary. |

The queue-depth-2 result is a single-run calibration. The package does not use
it as a matched detector comparison.

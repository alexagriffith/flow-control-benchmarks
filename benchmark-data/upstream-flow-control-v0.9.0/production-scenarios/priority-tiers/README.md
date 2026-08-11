# Priority tiers

## Business question

Does flow control preserve dispatch order across four priority bands during a
shared-model surge?

## What the benchmark showed

| Priority band | Median surge p95 TTFT |
|---|---:|
| Platinum realtime | 404 ms |
| Gold realtime | 511 ms |
| Silver standard | 656 ms |
| Bronze batch | 13,264 ms |

Higher-priority traffic retained lower TTFT while batch absorbed more of the
queue. The result uses three selected repeats. Every request succeeded, flow
control engaged during each run, and prefix caching remained off.

## Run inventory

- Detector: request count 128 with 10% headroom
- Repeats: 3
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
| [`run-config.json`](run-config.json) | Images, topology, engine settings, detector setting, and traffic method. |
| [`analysis.json`](analysis.json) | Medians, ranges, run inventory, and claim boundary. |

This package does not include a matched utilization-detector comparison because
the retained priority-tier controls did not pass route-count proof.

# Consolidation

## Business question

Can two realtime tenants retain lower TTFT while standard traffic surges on the
same model server?

## What the benchmark showed

With request-count admission, the two realtime tenants had median surge p95
TTFT of 509 ms and 556 ms. The standard burst had median surge p95 TTFT of
25,892 ms.

The matched utilization-detector runs produced realtime p95 TTFT between
4,567 ms and 5,117 ms. The observed ranges did not overlap across the tested
detectors. Three repeats support descriptive medians and ranges rather than a
formal statistical-significance claim.

## Run inventory

- Request count 128 with 10% headroom: 3 repeats
- Utilization queue depth 2: 3 repeats
- Utilization queue depth 5: 3 repeats
- Traffic: open-loop Poisson arrivals with noisy sinusoidal phases, a timed
  surge, and recovery
- Request shape: 511 input tokens and 128 output tokens
- Topology: 1 Endpoint Picker, 1 vLLM model replica, 1 NVIDIA H100

Every request succeeded, flow control engaged during all nine runs, and prefix
caching remained off.

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
| [`analysis.json`](analysis.json) | Matched detector medians, ranges, run inventory, and claim boundary. |

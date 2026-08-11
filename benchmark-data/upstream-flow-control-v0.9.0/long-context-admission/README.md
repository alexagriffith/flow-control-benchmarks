# Long-context admission comparison

## Business question

Does exact input-token admission protect realtime latency from a burst of large
requests better than request-count admission?

## Result

Exact-token admission made large-request pressure visible to flow control. It
formed a policy queue in all eight runs, compared with one of eight
request-count runs.

| Measure | Request-count admission | Exact-token admission |
|---|---:|---:|
| Mean realtime burst p95 TTFT | 354 ms | 337 ms |
| Runs with an active policy queue | 1 / 8 | 8 / 8 |
| Successful requests | 18,453 / 18,453 | 18,453 / 18,453 |

The 16 ms p95 TTFT difference was small relative to run variance. The paired
test returned `p=0.367`, and the 95% confidence interval ranged from -24 ms to
57 ms. This data supports the detector behavior and does not establish a
general latency advantage.

## Decision

Keep request-count admission at 128 requests with 10% headroom as the default.
Use exact-token admission when request sizes vary enough to require size-aware
queue activation.

## Method

- One Endpoint Picker served two vLLM model replicas, each on one NVIDIA H100.
- Realtime chat used 1,024 input tokens and 128 output tokens at a steady noisy
  rate.
- Standard-priority pressure used a timed burst of 20,000 input tokens and 128
  output tokens.
- Request-count admission used 128 requests per model replica and 10% headroom.
- Exact-token admission used the vLLM tokenizer, a 20,000 input-token cap per
  model replica, and 25% headroom.
- The same open-loop noisy sinusoidal trace was replayed for both methods across
  eight frozen seeds.
- Prefix caching was disabled and verified by configuration and counters.

The complete configuration is in [`run-config.json`](run-config.json).

## Evidence

| File | Contents |
|---|---|
| [`summary.csv`](summary.csv) | Per-run realtime and long-context latency, queue, KV-cache, and response counts. |
| [`request-results.csv`](request-results.csv) | One sanitized row per request across all 16 runs. |
| [`traffic-samples.csv`](traffic-samples.csv) | Issued, completed, and outstanding requests over time. |
| [`system-metrics.csv`](system-metrics.csv) | Curated Endpoint Picker and per-model vLLM metrics. |
| [`run-evidence.csv`](run-evidence.csv) | Schedule, headers, routes, request shapes, cache state, metrics, and flow-control engagement. |
| [`analysis.json`](analysis.json) | Paired seed results, confidence interval, statistical test, decision, and claim boundary. |

All 16 runs passed the data-quality, schedule, request-shape, header, route,
stream, metric, cache, preemption, and restart checks. The flow-control proof
gate records queue activation separately, so inactive request-count runs remain
usable detector-boundary evidence.

## Scope

The result applies to this model, hardware, two-replica topology, and tested
traffic shape. Exact-token admission counts input size more accurately. It does
not measure KV-memory use directly, so KV-cache and preemption metrics remain
part of the evidence.

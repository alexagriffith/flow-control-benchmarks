# Engine capacity and configuration

## Business question

Which vLLM limits provide a useful throughput and latency balance before flow
control policy is evaluated under production traffic?

## Result

The single-GPU capacity curve flattened near concurrency 128. Increasing
closed-loop concurrency from 128 to 160 raised steady throughput from 49.0 to
50.7 requests per second while adding more waiting.

The matched engine sweeps selected:

- `max-num-seqs=128`
- `max-num-batched-tokens=8192`

| Max sequences | Repeats | Steady RPS | p95 TTFT | p99 TTFT | p95 TPOT |
|---:|---:|---:|---:|---:|---:|
| 128 | 3 | 50.5 | 1,822 ms | 1,888 ms | 19.6 ms/token |
| 160 | 3 | 50.7 | 1,365 ms | 1,978 ms | 27.0 ms/token |
| 192 | 3 | 51.9 | 1,691 ms | 2,613 ms | 28.9 ms/token |

Sequence limit 128 gives up little throughput while avoiding the higher TPOT
and tail-latency cost at the larger limits.

| Max batched tokens | Repeats | Steady RPS | p95 TTFT | p95 TPOT |
|---:|---:|---:|---:|---:|
| 4,096 | 1 | 47.8 | 1,877 ms | 20.5 ms/token |
| 8,192 | 3 | 50.5 | 1,822 ms | 19.6 ms/token |
| 16,384 | 1 | 48.7 | 2,162 ms | 22.8 ms/token |

The alternatives did not improve throughput or latency enough to justify more
repeats.

## Method

- One llm-d Endpoint Picker v0.9.0 served one vLLM model replica on one NVIDIA
  H100.
- Prefix caching was disabled and verified by configuration and counters.
- The capacity curve tested closed-loop concurrency from 8 through 160.
- The sequence sweep fixed concurrency at 192 and tested limits from 64 through
  192.
- The batched-token sweep fixed `max-num-seqs=128` and tested 4,096, 8,192, and
  16,384 tokens.
- Every retained run captured request rows, traffic samples, direct metrics,
  Prometheus metrics, route counts, cache counters, and configuration.

The complete shared configuration is in [`run-config.json`](run-config.json).

## Evidence

| File | Contents |
|---|---|
| [`summary.csv`](summary.csv) | One row per retained sweep run with throughput and latency in explicit units. |
| [`request-results.csv`](request-results.csv) | One sanitized row per request. |
| [`traffic-samples.csv`](traffic-samples.csv) | Issued, completed, and outstanding requests over time. |
| [`system-metrics.csv`](system-metrics.csv) | Curated Endpoint Picker and vLLM metrics. |
| [`run-evidence.csv`](run-evidence.csv) | Metrics, routes, cache state, headers, and proof-gate results. |
| [`analysis.json`](analysis.json) | Selected settings, matched medians, and claim boundary. |

## Scope

This is closed-loop engine calibration, not an SLO proof. The selected settings
are the starting point for the open-loop production scenarios published
separately.

## Reproduce

This package used the native closed-loop runner with cache off. `max-num-seqs` was tested at 64, 96, 128, 160, and 192. `max-num-batched-tokens` was tested at 4,096, 8,192, and 16,384. The selected point and its nearest boundaries were repeated three times.

```bash
pipeline/run-in-cluster.sh <output-dir> <live-status.json> <prompt-cache-dir> "" -- --sweep-points <max-num-seqs> --sweep-duration 180 --skip-scenarios --prompt-pool-size 24 --warmup-duration 30 --warmup-concurrency 2 --steady-state-trim-s 30 --metric-sample-interval-s 0.5 --vllm-prefix-caching off --traffic-seed 42 --arrival-mode closed_loop
```

Set the corresponding vLLM `max-num-seqs` and `max-num-batched-tokens` before each point. [`run-config.json`](run-config.json) records the image, hardware, model, and metric cadence.

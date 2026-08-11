# Mixed production workload

## Business question

Which admission method better protects realtime traffic when chat, agentic,
long-context, and batch work share one model server?

## Result

Request-count admission gave realtime chat the lower surge latency. Input-token
admission spread latency more evenly across the four workloads.

| Admission method | Realtime p95 TTFT | Agentic p95 TTFT | Long-context p95 TTFT | Batch p95 TTFT | Peak vLLM waiting |
|---|---:|---:|---:|---:|---:|
| Request count: 128 with 10% headroom | 1,994 ms | 2,745 ms | 5,150 ms | 8,654 ms | 16 requests |
| Input tokens: 75,000 | 2,914 ms | 2,836 ms | 3,076 ms | 2,832 ms | 43 requests |

Request-count admission lowered realtime p95 TTFT by 919 ms and moved more of
the wait to lower-priority work. Input-token admission reduced long-context and
batch latency, while more requests waited inside vLLM.

All 9,132 requests succeeded. Flow control engaged in all six runs. Prefix
caching was disabled, cache counters remained zero, and vLLM recorded no
preemptions.

## Method

- One Endpoint Picker v0.9.0 served one GPT-OSS 20B model replica on one NVIDIA H100.
- Each admission method ran three 180-second repeats using the same deterministic traffic trace.
- Arrivals used open-loop Poisson timing with noisy sinusoidal baseline, surge, and recovery phases.
- Realtime chat used 1,024 input and 128 output tokens at priority 100.
- Agentic traffic used 4,096 input and 512 output tokens at priority 50.
- Long-context and batch traffic used 20,000 input and 128 output tokens at priorities 0 and -10.
- vLLM used `max-num-seqs=128`, `max-num-batched-tokens=8192`, and a 32,768-token model limit.

The complete configuration is in [`run-config.json`](run-config.json).

## Evidence

| File | Contents |
|---|---|
| [`summary.csv`](summary.csv) | Per-run TTFT, TPOT, queue, KV cache, and detector results. |
| [`window-summary.csv`](window-summary.csv) | Baseline, surge, and recovery results by workload. |
| [`request-results.csv`](request-results.csv) | One sanitized row per request with timing, tokens, and status. |
| [`traffic-samples.csv`](traffic-samples.csv) | Issued, completed, and outstanding requests over time. |
| [`system-metrics.csv`](system-metrics.csv) | Endpoint Picker and vLLM metrics collected during every run. |
| [`run-evidence.csv`](run-evidence.csv) | Schedule, header, route, cache, flow-control, and data-quality checks. |
| [`analysis.json`](analysis.json) | Per-run values, medians, ranges, and claim boundary. |

## Scope

The configuration choice depends on workload shape and business priority.
These latency values apply to the tested model, GPU, traffic, and single-model
setup; they do not define a general service-level objective.

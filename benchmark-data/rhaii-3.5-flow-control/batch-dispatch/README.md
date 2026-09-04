# When should Batch work enter a shared inference service?

**Takeaway:** metrics-gated queued dispatch met the 250-ms realtime p95 TTFT
target in two of three blocks, more often than either direct-dispatch method.
It drained the Batch backlog 53 seconds faster than 429-responsive direct
dispatch at the median.

## What was tested

Three counterbalanced blocks compared direct fixed-concurrency dispatch, direct
429-responsive dispatch, and metrics-gated queued dispatch. Every planned
Batch request completed under all three methods.

| Batch dispatch method | Realtime target met | Realtime surge p95 TTFT | Median Batch drain |
| --- | ---: | ---: | ---: |
| Direct, fixed concurrency | 0 of 3 blocks | 546 ms | 212 s |
| Direct, 429-responsive concurrency | 1 of 3 blocks | 305 ms | 287 s |
| Queued, metrics-gated dispatch | 2 of 3 blocks | 249 ms | 234 s |

Each method ran once in each block. A block met the target when realtime p95
TTFT remained at or below 250 ms during the declared surge window. The fixed
direct method kept 30 Batch requests active. The 429-responsive direct method
halved that limit after an HTTP 429 and increased it one request at a time after
successful responses. The metrics-gated method held Batch in a queue and
adjusted dispatch from the number of requests running in vLLM.

- [Normalized analysis](analysis.json)
- [Shared-pool starting configuration](../examples/getting-started/04-priority-standard-batch.yaml)

## Evidence

[Run summaries](summary.csv), [request outcomes](request-results.csv),
[traffic samples](traffic-samples.csv), [system metrics](system-metrics.csv),
[validation records](run-evidence.csv), [control comparison](control-summary.csv),
[run contract](run-config.json), and [scenario](scenario.json).

## Operating requirement

Restart-safe queued dispatch needs durable queue state, reconstructable worker
ownership, and startup reconciliation.

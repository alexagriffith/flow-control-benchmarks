# When should Batch work enter a shared inference service?

**Takeaway:** metrics-gated Async Batch was the only tested dispatch control to
meet the 250 ms realtime p95 TTFT target in two of three blocks. It drained the
Batch backlog 53 seconds faster than synchronous AIMD at the median.

## What was tested

Three counterbalanced blocks compared fixed synchronous dispatch, synchronous
additive-increase/multiplicative-decrease (AIMD), and metrics-gated Async
dispatch. Every planned Batch request completed under all three controls.

| Dispatch control | Target passes | Realtime surge p95 TTFT | Median Batch drain |
| --- | ---: | ---: | ---: |
| Fixed synchronous | 0/3 | 546 ms | 212 s |
| Synchronous AIMD | 1/3 | 305 ms | 287 s |
| Metrics-gated Async | 2/3 | 249 ms | 234 s |

- [Normalized analysis](analysis.json)
- [Shared-pool starting configuration](../examples/getting-started/04-priority-standard-batch.yaml)

## Operating requirement

Restart-safe Async dispatch needs durable queue state, reconstructable worker
ownership, and startup reconciliation.

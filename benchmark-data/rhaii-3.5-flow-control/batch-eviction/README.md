# Can retry-owned work release capacity after it enters vLLM?

**Takeaway:** eviction supplied a retry-safe overflow path. At both tested
reserve levels it removed the realtime non-200 outcomes observed in the
eviction-off runs, and every Batch job completed exactly once. The retry path increased
Batch completion time through cancellation and recomputation.

## What was tested

The campaign used longer 20,000-token Batch jobs, heavier overlap, matched
eviction-off/on pairs, and 25% and 50% reserved capacity.

| Reserve | Realtime non-200: eviction off / on | Batch p95 change |
| ---: | ---: | ---: |
| 25% | 15 / 0 | +18.7% |
| 50% | 11 / 0 | +9.4% |

- [Normalized analysis](analysis.json)
- [Complete public Batch eviction package](../../batch-eviction/)

## Evidence

[Run summaries](summary.csv), [paired effects](paired-effects.csv),
[matched-pair evidence](run-evidence.json), and [run contract](run-config.json).

## Scope

The result uses the experimental Endpoint Picker digest named in the campaign
README. Eviction is appropriate only for work whose owner accepts retry and
recomputation.

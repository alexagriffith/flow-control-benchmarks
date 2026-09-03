# Which priority ceiling matches the operating goal?

**Takeaway:** fixed holdback provided stronger interactive protection;
soft-reflective ceilings allowed more lower-tier progress and kept the Endpoint
Picker queue smaller. The policies serve different operating goals.

## What was tested

Each policy had three accepted runs with the same one-H100 workload.

| Policy | Platinum / Gold p95 TTFT | Bronze p95 / peak queue |
| --- | ---: | ---: |
| Fixed holdback 0.50 | 135 ms / 133 ms | 66,045 ms / 1,250 |
| Soft-reflective ceilings | 200 ms / 318 ms | 6,219 ms / 143 |

- [Normalized analysis](analysis.json)
- [Fixed-holdback benchmark configuration](../examples/benchmark-reproduction/05-fixed-priority-holdback.yaml)
- [Soft-reflective benchmark configuration](../examples/benchmark-reproduction/06-soft-reflective-ceilings.yaml)

## Evidence

[Run summaries](summary.csv), [request outcomes](request-results.csv),
[traffic samples](traffic-samples.csv), [system metrics](system-metrics.csv),
[validation records](run-evidence.csv), [run contract](run-config.json), and
[scenario](scenario.json).

## Scope

This result describes a workload-specific tradeoff. Recalibrate the ceilings
for another model, request shape, or topology.

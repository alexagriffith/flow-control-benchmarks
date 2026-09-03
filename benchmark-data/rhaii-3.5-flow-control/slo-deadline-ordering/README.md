# Can queue order favor requests with tighter latency objectives?

**Takeaway:** SLO deadline ordering increased the share of requests meeting
250 ms and 500 ms TTFT objectives while every request completed and the queue
drained. No-objective requests waited longer.

## What was tested

First-come, first-served (FCFS) and SLO deadline ordering were compared in three
accepted matched runs per policy.

| Result | FCFS | SLO ordering |
| --- | ---: | ---: |
| 250 ms objective: requests meeting target | 41.4% | 72.2% |
| 500 ms objective: requests meeting target | 70.4% | 90.6% |
| No objective: median p95 TTFT | 826 ms | 1,495 ms |

- [Normalized analysis](analysis.json)
- [Benchmark configuration](../examples/benchmark-reproduction/04-slo-deadline-ordering.yaml)
- [Reviewed evidence chart](../assets/slo-deadline-ordering.svg)

## Evidence

[Run summaries](summary.csv), [request outcomes](request-results.csv),
[traffic samples](traffic-samples.csv), [system metrics](system-metrics.csv),
[validation records](run-evidence.csv), [run contract](run-config.json), and
[scenario](scenario.json).

## Scope

Use this policy when callers provide meaningful deadlines and lower precedence
for no-objective requests is acceptable.

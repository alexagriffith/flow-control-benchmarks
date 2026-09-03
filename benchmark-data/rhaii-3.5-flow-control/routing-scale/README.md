# What changed when the service moved from one replica to two?

**Takeaway:** the two-replica routing block completed and bounded the observed
scaling behavior. Served requests per second per GPU changed by 0.39%, and
protected-burst p95 TTFT changed by 7.35%.

## What was tested

Six accepted counterbalanced runs compared one and two identical model
replicas. Random endpoint selection intentionally kept queue and cache scoring
outside the replica-count comparison.

- [Normalized analysis](analysis.json)
- [Two-replica benchmark configuration](../examples/benchmark-reproduction/03-two-replica-random-baseline.yaml)

## Scope

This result supports routing-scale completion. A latency scale-out claim
requires a dedicated scaling study with that acceptance criterion.

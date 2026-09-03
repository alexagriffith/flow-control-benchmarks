# Can token quota and priority create a soft service preference?

**Takeaway:** a trusted external quota classifier gave in-budget work higher
priority while forwarding overage at standard priority. In-budget success
improved in all three matched blocks, and every Batch backlog completed.

## What was tested

Nine accepted runs in three counterbalanced blocks compared:

- no quota: forward all work at standard priority;
- classifying quota: promote in-budget work and forward overage normally;
- blocking quota: promote in-budget work and reject overage.

Classifying quota increased in-budget success by 1.21 to 1.63 percentage points
across the blocks and lowered its p95 TTFT in two blocks. Blocking quota rejected
207 to 208 overage requests per run, so its successful-response latency excludes
that rejected work.

- [Focused analysis](analysis.json)
- [Serving-side configuration and classifier policy](../examples/benchmark-reproduction/09-soft-pt-serving-policy.yaml)
- [Request-cost metadata study](../request-cost-metadata/)

## Scope

This one-replica, fixed-shape composition creates a measurable preference on
shared capacity. A hard provisioned-throughput guarantee requires dedicated
capacity controls and a separate acceptance test.

# Did the shared service complete a 30-minute replay?

**Takeaway:** the service completed all 14,889 planned requests and drained the
replay. The two protected surges differed by 31.5%, so the supported result is
completion and drain behavior.

## What was tested

The one-replica service ran a 30-minute replay with two protected surges.
Protected p95 TTFT was 673 ms in the first surge and 885 ms in the second. The
predeclared repeatability guardrail was 20%.

- [Normalized analysis](analysis.json)
- [One-replica benchmark configuration](../examples/benchmark-reproduction/02-four-scenario-request-detector.yaml)

## Scope

This is stability boundary evidence: all work completed, and the 31.5%
surge-to-surge difference exceeded the 20% latency repeatability guardrail.

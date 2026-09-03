# Can completed token cost reach Gateway metadata exactly?

**Takeaway:** the request-attribute reporter exported prompt tokens plus
completion tokens to Gateway metadata exactly, creating a reliable measurement
signal for downstream quota, attribution, or settlement logic.

## What was tested

- All 200 usage-bearing responses matched the expected token sum.
- All 20 responses that omitted usage completed and correctly omitted the cost.
- The observer read `x-gateway-inference-request-cost` from Envoy dynamic
  metadata.

- [Normalized analysis](analysis.json)
- [Benchmark configuration](../examples/benchmark-reproduction/07-request-cost-metadata.yaml)

## Evidence

[Request outcomes](request-results.csv), [validation record](run-evidence.json),
and [run contract](run-config.json).

## Scope

This test establishes exact metadata propagation. Identity, pricing, ledger,
quota, and capacity guarantees belong to downstream services and require their
own validation.

# Where should a shared service set its operating point?

**Takeaway:** the 250 ms p95 time-to-first-token (TTFT) objective selected
40.6 requests per second as the operating point. Higher offered loads produced
rapid queue growth, so 40.6 requests per second became the safe target for this workload.

## What was tested

- GPT-OSS 20B with 512 input tokens and 128 output tokens.
- Prefix caching disabled.
- Four offered loads from 30.4 to 55.8 requests per second.
- A 250 ms p95 TTFT and 25 ms p95 time-per-output-token objective.

## Results

| Requests per second | p95 TTFT | Interpretation |
| ---: | ---: | --- |
| 30.4 | 124 ms | Inside the objective |
| 40.6 | 155 ms | Selected operating point |
| 50.7 | 6,273 ms | Queueing boundary |
| 55.8 | 28,900 ms | Deep overload |

- [Normalized analysis](analysis.json)
- [Benchmark configuration](../examples/benchmark-reproduction/01-capacity-request-concurrency.yaml)
- [Reviewed evidence chart](../assets/capacity-slo-envelope.svg)

## Scope

These values apply to the tested model, request shape, and one-H100 topology.
Repeat the sweep before choosing limits for another deployment.

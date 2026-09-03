# Which request cap protected the selected operating point?

**Takeaway:** a request cap of 128 with 10% scheduling headroom met the
250 ms p95 TTFT objective in all three accepted repeats at 40.6 requests per
second. A cap of 96 passed one of three repeats.

## What was tested

The capacity sweep selected 40.6 requests per second. The detector calibration
then compared caps of 96 and 128 while holding the workload and topology fixed.

| Request cap | Objective passes | Median p95 TTFT |
| ---: | ---: | ---: |
| 96 | 1/3 | 257 ms |
| 128 | 3/3 | 143 ms |

- [Normalized analysis](analysis.json)
- [Benchmark configuration](../examples/benchmark-reproduction/01-capacity-request-concurrency.yaml)

## Scope

The selected cap is a workload-specific calibration result. Set another
deployment's limit from its own model, request shape, and topology.

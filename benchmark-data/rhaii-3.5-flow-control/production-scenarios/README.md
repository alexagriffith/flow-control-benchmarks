# Do the established mechanisms reproduce on Red Hat AI Inference 3.5?

**Takeaway:** the OpenDataHub v0.10 Endpoint Picker build reproduced the
established priority, Batch isolation, consolidation, and same-priority
fairness behaviors on the tested Red Hat AI Inference 3.5 stack.

## What was tested

The campaign ran four scenarios with request concurrency, queue-depth
utilization QD2, and queue-depth utilization QD5 detection. Each configuration
had three accepted repeats: 36 accepted runs and 303,093 successful requests.

The reproduced behaviors were:

- higher-priority requests retained lower latency;
- deferrable Batch work absorbed more waiting during interactive demand;
- two protected flows retained differentiation during a standard-priority surge;
- peer flow IDs continued to make progress during unequal offered load.

- [Normalized analysis](analysis.json)
- [Benchmark configuration](../examples/benchmark-reproduction/02-four-scenario-request-detector.yaml)
- [Earlier Red Hat AI Inference 3.4 package](../../rhaii-3.4-flow-control/)
- [Earlier upstream v0.9 package](../../upstream-flow-control-v0.9.0/)

## Evidence

[Run summaries](summary.csv), [request outcomes](request-results.csv),
[traffic samples](traffic-samples.csv), [system metrics](system-metrics.csv),
[validation records](run-evidence.csv), [run contract](run-config.json), and
[scenario](scenario.json).

## Scope

This is mechanism-level reproduction. An image-version effect requires a
separate matched cross-version test.

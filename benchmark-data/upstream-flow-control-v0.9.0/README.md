# Stable upstream flow control benchmarks

These packages test the llm-d Endpoint Picker v0.9.0 flow-control path. Each
package contains the selected repeats, exact configuration, request-level data,
traffic samples, system metrics, evidence gates, analysis, and claim boundary.

| Package | Question |
|---|---|
| [Batch interference baseline](batch-interference/) | How much can batch work already running in vLLM increase realtime latency? |
| [Mixed production workload](mixed-production-workload/) | Which admission method better protects realtime traffic when four workload shapes share one model server? |
| [Model pool scaling](multi-replica-scaling/) | Does priority protection remain stable as the model pool scales from one to four replicas? |
| [Long stability](long-stability/) | Does the selected configuration recover after repeated production-shaped surges? |

Rejected attempts and exploratory points are not included. Repository-level
charts and conclusions are updated only after the individual packages pass
their validators.

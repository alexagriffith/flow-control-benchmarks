# Stable upstream flow control benchmarks

These packages test the [llm-d Endpoint Picker v0.9.0](https://github.com/llm-d/llm-d-router) flow-control path. Each
package contains the selected repeats, exact configuration, request-level data,
traffic samples, system metrics, evidence gates, analysis, and claim boundary.
Each capability folder also includes a tested architecture diagram and plots generated from its accepted data.

View the visual summary in [`results.html`](results.html).

## Runner and reproduction

The current campaign runner is [`pipeline/benchmark.py`](../../pipeline/benchmark.py), SHA-256 `3811ec26c46bf3a26fa643698ec54bf569bb4bc99c3ea22ca18f805cb077b8e0`. Closed-loop sweeps ran it directly. Open-loop packages used GuideLLM 0.7.0 with traffic schedules compiled from the same runner.

Every package below contains runner provenance and a README that names the detector settings, repeat count, cache state, and launch arguments. Scenario-based packages also contain the executable traffic definition. Closed-loop engine sweeps record their point matrix in `run-config.json`; the historical July package retains its per-run `benchmark_config.json` files. The common Kubernetes client and metrics setup is documented in [`pipeline/README.md`](../../pipeline/README.md). The repository reruns traffic against a compatible deployed model service; it does not install the model service itself.

The scenario, seed, and runner hash reproduce the issued GuideLLM traffic. Latency and model output are measured results, so another cluster should compare repeated distributions rather than expect identical request-by-request values.

## Configuration

| Package | Question |
|---|---|
| [Engine configuration](engine-configuration/) | Which vLLM request and scheduling limits balance latency and throughput? |
| [Utilization detector calibration](utilization-detector-calibration/) | Which queue-depth and KV-cache thresholds should advance into production traffic? |
| [Request and token admission calibration](request-and-token-admission-calibration/) | When should request size affect how much work enters vLLM? |
| [Earlier request-concurrency priority tuning](request-concurrency-priority-tuning/) | How does the request cap trade realtime latency against lower-priority latency in a two-tier short-request study? |

## Production behavior

| Package | Question |
|---|---|
| [Production scenarios](production-scenarios/) | Does the selected configuration protect priority traffic across tiering, batch isolation, consolidation, and same-priority fairness? |
| [Batch interference baseline](batch-interference/) | How much can batch work already running in vLLM increase realtime latency? |
| [Mixed production workload](mixed-production-workload/) | Which admission method better protects realtime traffic when four workload shapes share one model server? |
| [Selected workload shapes](selected-workload-shapes/) | How do chat and agentic output shapes change the selected configuration's behavior? |
| [Long-context admission](long-context-admission/) | Does exact input-token admission detect large-request pressure more reliably than request-count admission? |

## Scale and routing

| Package | Question |
|---|---|
| [Model pool scaling](multi-replica-scaling/) | Does priority protection remain stable as the model pool scales from one to four replicas? |
| [Long stability](long-stability/) | Does the selected configuration recover after repeated production-shaped surges? |
| [Prefix-cache routing](prefix-cache-routing/) | Does prefix-aware routing improve service under a saturated mixed workload when prefix caching is enabled? |

Rejected attempts and exploratory points are not included. Repository-level
charts use only packages that pass their validators.

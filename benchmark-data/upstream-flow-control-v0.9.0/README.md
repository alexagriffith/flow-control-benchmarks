# Stable upstream flow control benchmarks

These packages test the [llm-d Endpoint Picker v0.9.0](https://github.com/llm-d/llm-d-router) flow-control path. Each
package contains the selected repeats, exact configuration, request-level data,
traffic samples, system metrics, evidence gates, analysis, and claim boundary.
Each capability folder also includes a tested architecture diagram and plots generated from its accepted data.

View the visual summary in [`results.html`](results.html).

## How the tests build the answer

1. **Calibrate capacity.** Engine, request-count, token, queue-depth, and KV-cache
   sweeps establish the settings worth carrying forward.
2. **Test production traffic.** Open-loop traffic tests priority, batch
   isolation, consolidation, fairness, request shapes, and mixed workloads.
3. **Test failure boundaries.** Batch interference shows what admission control
   cannot fix after work enters vLLM.
4. **Harden the result.** Longer runs, larger model pools, and cache-aware
   routing test recovery and deployment boundaries.

## Traffic methods

- **Closed loop:** a fixed number of callers send another request only after
  the prior request finishes. We used it to find capacity and configuration
  limits.
- **Open-loop Poisson:** requests arrive on schedule even when earlier requests
  are still running. We used it to represent external production demand.
- **Noisy sinusoidal phases:** the open-loop rate rises and falls through a
  baseline, surge, and recovery. We used it to test protection during bursts
  and recovery afterward.

## Runner and reproduction

The current campaign runner is [`pipeline/benchmark.py`](../../pipeline/benchmark.py), SHA-256 `3811ec26c46bf3a26fa643698ec54bf569bb4bc99c3ea22ca18f805cb077b8e0`. Closed-loop sweeps ran it directly. Open-loop packages used GuideLLM 0.7.0 with traffic schedules compiled from the same runner.

Every package below contains runner provenance and a README that names the detector settings, repeat count, cache state, and launch arguments. Scenario-based packages also contain the executable traffic definition. Closed-loop engine sweeps record their point matrix in `run-config.json`; the historical July package retains its per-run `benchmark_config.json` files. The common Kubernetes client and metrics setup is documented in [`pipeline/README.md`](../../pipeline/README.md). The repository reruns traffic against a compatible deployed model service; it does not install the model service itself.

The scenario, seed, and runner hash reproduce the issued GuideLLM traffic. Latency and model output are measured results, so another cluster should compare repeated distributions rather than expect identical request-by-request values.

## Configuration

| Business question | One-sentence answer | Evidence |
|---|---|---|
| Which vLLM limits balance latency and throughput? | The tested balance was 128 maximum running sequences and 8,192 maximum batched tokens. | [Engine configuration](engine-configuration/) |
| When should utilization activate flow control? | Queue depth responds to requests waiting inside vLLM, while the KV-cache threshold responds to memory pressure. | [Utilization detector calibration](utilization-detector-calibration/) |
| When should request size affect admission? | Request count was the stronger starting point for this one-GPU calibration, while input-token admission was useful when request sizes varied materially. | [Request and token admission calibration](request-and-token-admission-calibration/) |
| How does the request cap trade realtime latency against lower-priority latency? | A cap of 48 produced the lowest premium p95 TTFT in the historical two-repeat study, while tighter caps delayed standard traffic. | [Earlier request-concurrency priority tuning](request-concurrency-priority-tuning/) |

## Production behavior

| Business question | One-sentence answer | Evidence |
|---|---|---|
| Does the selected configuration protect priority traffic? | Higher-priority realtime traffic stayed faster across all four traffic patterns; three scenarios met the repeat-stability gate, while batch isolation remained directional evidence. | [Production scenarios](production-scenarios/) |
| How much can running batch increase realtime latency? | Under request-count admission, running batch increased realtime p95 TTFT from 133 ms to 15,378 ms. | [Batch interference baseline](batch-interference/) |
| Which admission method better protects realtime traffic in a mixed workload? | Request-count admission produced lower realtime latency, while input-token admission lowered long-context and batch latency. | [Mixed production workload](mixed-production-workload/) |
| How do chat and agentic output shapes change latency? | Every request completed, but the longer agentic output produced higher p95 TTFT. | [Selected workload shapes](selected-workload-shapes/) |
| Did exact input-token admission improve long-context latency? | It detected large-request pressure in every paired run but did not produce a statistically significant realtime latency improvement. | [Long-context admission](long-context-admission/) |

## Scale and routing

| Business question | One-sentence answer | Evidence |
|---|---|---|
| Does per-GPU throughput hold as the model pool grows? | Served throughput per GPU stayed within 0.6% from one to four model replicas. | [Model pool scaling](multi-replica-scaling/) |
| Does the selected configuration recover after repeated surges? | The queue drained after both surges and every request completed. | [Long stability](long-stability/) |
| Did prefix-aware routing improve every workload? | No; it helped realtime and batch latency but increased standard long-context latency, route imbalance, and HTTP 429 responses. | [Prefix-cache routing](prefix-cache-routing/) |

Rejected attempts and exploratory points are not included. Repository-level
charts use only packages that pass their validators.

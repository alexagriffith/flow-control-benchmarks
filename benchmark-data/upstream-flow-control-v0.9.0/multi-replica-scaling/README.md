# Model pool scaling

## Business question

Can a model pool add replicas without losing per-GPU efficiency or worsening
premium latency under the same offered load per GPU?

## Result

Scaling from one to four model replicas kept served throughput per GPU within
0.6% and did not worsen median premium burst p95 TTFT. Four replicas served four
times the offered traffic with no HTTP 429 responses in the three tested runs.

The smaller pools exposed a capacity boundary: five realtime requests received
HTTP 429 responses at one replica, and one standard long-context request received
an HTTP 429 response at two replicas. The data supports the scale-out behavior;
it does not prove rejection-free service at every pool size.

| Model replicas | Offered requests | Success | Median premium burst p95 TTFT | Served requests/s/GPU |
|---:|---:|---:|---:|---:|
| 1 | 3,498 | 99.86% | 374 ms | 4.293 |
| 2 | 7,014 | 99.99% | 340 ms | 4.317 |
| 4 | 13,950 | 100% | 322 ms | 4.302 |

Flow control engaged in all nine runs. Prefix caching was disabled, cache
counters remained zero, and vLLM recorded no preemptions. The Endpoint Picker
did not restart.

## Method

- One Endpoint Picker served one, two, or four vLLM model replicas on the same
  number of NVIDIA H100 GPUs.
- Each topology ran three fresh 270-second repeats with the same deterministic
  seed and matched offered traffic per GPU.
- Premium chat sent 1,024 input and 128 output tokens at a noisy sinusoidal rate
  centered on 4 requests/s/GPU.
- Standard long-context traffic sent 20,000 input and 128 output tokens during a
  timed noisy sinusoidal burst centered on 0.5 requests/s/GPU.
- The Endpoint Picker used token-concurrency detection, a 20,000-token limit,
  25% headroom, random model selection, and priority-aware flow-control queues.
- vLLM used `max-num-seqs=128`, `max-num-batched-tokens=8192`, and a 32,768-token
  model limit.

The complete configuration is in [`run-config.json`](run-config.json).

## Evidence

| File | Contents |
|---|---|
| [`summary.csv`](summary.csv) | Run-level latency, throughput, status, queue, KV cache, and restart results. |
| [`request-results.csv`](request-results.csv) | One sanitized row per request with TTFT, TPOT, latency, token counts, and status. |
| [`traffic-samples.csv`](traffic-samples.csv) | Issued, completed, and outstanding requests by tenant over time. |
| [`system-metrics.csv`](system-metrics.csv) | Curated Endpoint Picker and per-model vLLM metrics collected during every run. |
| [`run-evidence.csv`](run-evidence.csv) | Schedule, header, route, metric, cache, flow-control, and data-quality gates. |
| [`analysis.json`](analysis.json) | Per-run values, topology medians, decision limits, and claim boundary. |

## Scope

This package tests one Endpoint Picker with one, two, or four model replicas. It
does not test multiple Endpoint Picker replicas, set an absolute service-level
objective, or show rejection-free service at every pool size.

## Reproduce

This package used GuideLLM 0.7.0 with one Endpoint Picker and one, two, or four model replicas. Offered load scaled with the replica count. Each topology ran three times with seed 18, exact input-token admission at 20,000 tokens per replica, 25% headroom, random routing, and cache off.

```bash
python3 pipeline/guidellm_trace.py --scenario-file benchmark-data/upstream-flow-control-v0.9.0/multi-replica-scaling/scenario.json --scenario <one_replica_scaled_load|two_replica_scaled_load|four_replica_scaled_load> --out-dir /tmp/model-pool-scale --traffic-seed 18
python3 pipeline/run_guidellm_scenario.py --manifest /tmp/model-pool-scale/manifest.json --run-dir results/model-pool-scale --prefix model-pool-scale --namespace <namespace> --runner-pod <runner-pod> --expected-detector concurrency-detector --expected-concurrency-mode tokens --expected-max-concurrency 1000000 --expected-max-token-concurrency 20000 --expected-add-estimated-output-tokens false --expected-headroom 0.25 --expected-picker random-picker --expected-prefix-cache off --expected-model-replicas <1|2|4> --http-version 1 --guidellm-worker-processes <8|16|32> --guidellm-mp-poll-interval-s 0.01 --drain-after-done --drain-timeout-s 300 --recover-multiline-sse
```

[`scenario.json`](scenario.json) defines the matched per-GPU traffic. [`run-config.json`](run-config.json) defines the topology matrix.

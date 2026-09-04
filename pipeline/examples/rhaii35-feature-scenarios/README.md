# RHAII 3.5 SLO and prefill/decode traffic

These scenarios replay the traffic shapes used to test SLO deadline ordering
and prefill/decode (P/D) flow control. They send requests to a deployed
Endpoint Picker and model service. They do not simulate the Endpoint Picker.

## What each scenario tests

| Test | Scenario | Traffic mode | Configuration |
| --- | --- | --- | --- |
| SLO ordering | [`slo-deadline-ordering.json`](slo-deadline-ordering.json) | Fixed Poisson arrivals with 250 ms, 500 ms, and absent TTFT headers in one flow | [SLO deadline ordering](../../../benchmark-data/rhaii-3.5-flow-control/examples/benchmark-reproduction/04-slo-deadline-ordering.yaml) |
| Same-priority progress under prefill pressure | [`pd-same-priority-prefill.json`](pd-same-priority-prefill.json) | 20,000-token prompts start first; a smaller peer joins later | [P/D hybrid admission](../../../benchmark-data/rhaii-3.5-flow-control/examples/benchmark-reproduction/08-prefill-decode-hybrid.yaml) |
| Same-priority progress under decode pressure | [`pd-same-priority-decode.json`](pd-same-priority-decode.json) | 4,096-token generations start first; a smaller peer joins later | [P/D hybrid admission](../../../benchmark-data/rhaii-3.5-flow-control/examples/benchmark-reproduction/08-prefill-decode-hybrid.yaml) |
| Priority protection under prefill pressure | [`pd-priority-prefill.json`](pd-priority-prefill.json) | Standard-priority large prompts start first; a priority-100 peer joins later | [P/D hybrid admission and holdback](../../../benchmark-data/rhaii-3.5-flow-control/examples/benchmark-reproduction/08-prefill-decode-hybrid.yaml) |
| Priority protection under decode pressure | [`pd-priority-decode.json`](pd-priority-decode.json) | Standard-priority long generations start first; a priority-100 peer joins later | [P/D hybrid admission and holdback](../../../benchmark-data/rhaii-3.5-flow-control/examples/benchmark-reproduction/08-prefill-decode-hybrid.yaml) |

The P/D request shapes are synthetic pressure tests. Measure the request and
token knees again before using their numerical limits with another model,
topology, or client workload.

The canonical runner sends OpenAI Completions requests. Qualify Chat
Completions, Messages, or another client API separately before using its
results for that request path.

## Set the target service

The deployment examples above create the `InferenceObjective` names referenced
by these scenarios. Change the scenario objective names when the deployed
resources use different names.

```bash
export BENCHMARK_RUNNER_NAMESPACE=YOUR_NAMESPACE
export BENCHMARK_RUNNER_POD=flow-control-benchmark-runner
export BENCHMARK_RUNNER_MANIFEST=pipeline/kubernetes/benchmark-runner.yaml
export MODEL_NAME=YOUR_SERVED_MODEL_NAME
export BASE_URL=http://YOUR_ENDPOINT_PICKER_SERVICE.YOUR_NAMESPACE.svc.cluster.local:8080
export TOKENIZE_URL=http://YOUR_MODEL_SERVICE.YOUR_NAMESPACE.svc.cluster.local:8000/tokenize
export EPP_METRICS_URL=http://YOUR_ENDPOINT_PICKER_SERVICE.YOUR_NAMESPACE.svc.cluster.local:9090/metrics
export VLLM_METRICS_URL=http://YOUR_MODEL_SERVICE.YOUR_NAMESPACE.svc.cluster.local:8000/metrics
export EPP_PLUGIN_STATE_URL=http://YOUR_ENDPOINT_PICKER_SERVICE.YOUR_NAMESPACE.svc.cluster.local:9090/debug/plugins
export EXPECT_FLOW_CONTROL=1
export EXPECT_PROMETHEUS=0

mkdir -p .cache/prompts results
```

Set `PROMETHEUS_URL`, `PROMETHEUS_NAMESPACE`, and `EXPECT_PROMETHEUS=1` when
the benchmark must also validate Prometheus ingestion.

## Run the SLO comparison

Run the equal-deadline arm first, then the mixed-deadline arm. Both arms use
the same generated arrival schedule for a given seed.

```bash
SCENARIO=pipeline/examples/rhaii35-feature-scenarios/slo-deadline-ordering.json

pipeline/run-feature-in-cluster.sh \
  slo-equal results/slo-equal results/live-status.json .cache/prompts \
  "$SCENARIO" -- \
  --arrival-mode poisson \
  --traffic-seed 4101 \
  --repeats 1 \
  --prompt-pool-size 256 \
  --steady-state-trim-s 0 \
  --metric-sample-interval-s 1 \
  --drain-timeout 300 \
  --vllm-prefix-caching off

pipeline/run-feature-in-cluster.sh \
  slo-mixed results/slo-mixed results/live-status.json .cache/prompts \
  "$SCENARIO" -- \
  --arrival-mode poisson \
  --traffic-seed 4101 \
  --repeats 1 \
  --prompt-pool-size 256 \
  --steady-state-trim-s 0 \
  --metric-sample-interval-s 1 \
  --drain-timeout 300 \
  --vllm-prefix-caching off
```

The feature runner writes the canonical benchmark artifacts plus
`slo_request_evidence.jsonl` and `slo-adapter-contract.json`. The contract
records the adapter version and the SHA-256 hashes of the canonical runner and
scenario.

## Run a P/D scenario

Set all three metric URLs to capture prefill, decode, and Endpoint Picker
metrics in one time series.

```bash
export PREFILL_METRICS_URL=http://YOUR_PREFILL_SERVICE.YOUR_NAMESPACE.svc.cluster.local:8000/metrics
export DECODE_METRICS_URL=http://YOUR_DECODE_SERVICE.YOUR_NAMESPACE.svc.cluster.local:8000/metrics
export EPP_METRICS_URL=http://YOUR_ENDPOINT_PICKER_SERVICE.YOUR_NAMESPACE.svc.cluster.local:9090/metrics
export CONCURRENCY_MODE=hybrid
export MAX_CONCURRENCY=64
export MAX_TOKEN_CONCURRENCY=80000
export FLOW_CONTROL_HEADROOM=0.1
export ADD_ESTIMATED_OUTPUT_TOKENS=false

SCENARIO=pipeline/examples/rhaii35-feature-scenarios/pd-priority-prefill.json

pipeline/run-feature-in-cluster.sh \
  canonical results/pd-priority-prefill results/live-status.json .cache/prompts \
  "$SCENARIO" -- \
  --arrival-mode closed_loop \
  --traffic-seed 201 \
  --repeats 1 \
  --prompt-pool-size 256 \
  --steady-state-trim-s 0 \
  --metric-sample-interval-s 1 \
  --drain-timeout 300 \
  --vllm-prefix-caching off
```

The P/D metric capture writes `pd-stage-metrics.csv`, an errors file, and a
contract that fails when any required metrics source disappears for more than
one consecutive sample. A valid run still requires the canonical benchmark
proof gates and the intended prefill or decode stage to reach the configured
saturation threshold.

The launcher removes its CPU benchmark pod after copying the artifacts. Set
`BENCHMARK_RUNNER_CLEANUP=false` only when another run will reuse that pod.

## Accepted result packages

- [SLO deadline-ordering data](../../../benchmark-data/rhaii-3.5-flow-control/slo-deadline-ordering/)
- [P/D flow-control data](../../../benchmark-data/rhaii-3.5-flow-control/pd-flow-control/)

The accepted packages report repeated results from the tested RHAII 3.5
deployment. A new replay must preserve its own configuration, request rows,
metrics, validation records, and runner contracts.

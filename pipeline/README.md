# Benchmark pipeline

The upstream v0.9.0 campaign used one benchmark implementation with two traffic drivers.

| Test type | Traffic driver | When it was used |
|---|---|---|
| Closed loop | [`benchmark.py`](benchmark.py) | Engine, detector, request-count, and token-admission sweeps. The runner keeps a configured number of requests in flight. |
| Open loop | GuideLLM 0.7.0 through [`run_guidellm_scenario.py`](run_guidellm_scenario.py) | Production scenarios, workload-shape comparisons, batch interference, routing, scaling, and stability. Requests follow a fixed Poisson arrival schedule, independent of completion time. |

[`guidellm_trace.py`](guidellm_trace.py) imports the same scenario and traffic logic from `benchmark.py`. Both paths use the same prompt construction, headers, metric capture, proof gates, and artifact schema.

The published `benchmark.py` is the hardened runner used for the accepted upstream campaign. Its SHA-256 is `3811ec26c46bf3a26fa643698ec54bf569bb4bc99c3ea22ca18f805cb077b8e0`.

The [request-concurrency priority-tuning package](../benchmark-data/upstream-flow-control-v0.9.0/request-concurrency-priority-tuning/) is the exception. It was run in July 2026 with the earlier [`benchmark-2026-07.py`](archive/benchmark-2026-07.py) revision. The public copy changes only deployment-specific defaults; `run-config.json` records both its public hash and the executed runner hash. Its exact settings remain in each run's `benchmark_config.json`; the current runner should be used for new work.

## What is captured

Each counted run records:

- one row per request, including priority, fairness ID, TTFT, TPOT, status, and route;
- the issued traffic schedule and the actual send time;
- Endpoint Picker, vLLM, Envoy, Kubernetes, and Prometheus metrics during traffic;
- the deployed images, engine arguments, detector state, and cache state;
- proof gates for traffic timing, headers, routing, flow-control engagement, metrics, and errors.

Prefix caching was disabled for the primary campaign. Cache behavior was enabled only in the dedicated prefix-cache routing package.

## Closed-loop command

These commands rerun the benchmark against an existing compatible model service and Endpoint Picker. They do not install the model service. Before traffic starts, the runner verifies the deployed image, model replicas, vLLM arguments, flow-control configuration, cache state, routes, and metrics.

Set the deployment-specific names and credentials through environment variables. The included benchmark-runner manifest creates only the CPU client pod. Scenario-based packages version the traffic file; every package versions its benchmark arguments.

The positional arguments are the local artifact directory, the JSON file read by the status dashboard, the deterministic prompt-pool cache, and the package's scenario JSON. An empty scenario argument runs a built-in closed-loop sweep.

```bash
export BENCHMARK_RUNNER_NAMESPACE=flow-control
export BENCHMARK_RUNNER_POD=flow-control-benchmark-runner
export BENCHMARK_RUNNER_MANIFEST=pipeline/kubernetes/benchmark-runner.yaml
export ENDPOINT_PICKER_SERVICE=flow-control-epp
export MODEL_SERVICE=flow-control-model
export BASE_URL="http://${ENDPOINT_PICKER_SERVICE}.${BENCHMARK_RUNNER_NAMESPACE}.svc.cluster.local:8080"
export TOKENIZE_URL="http://${MODEL_SERVICE}.${BENCHMARK_RUNNER_NAMESPACE}.svc.cluster.local:8000/tokenize"
export EPP_METRICS_URL="http://${ENDPOINT_PICKER_SERVICE}.${BENCHMARK_RUNNER_NAMESPACE}.svc.cluster.local:9090/metrics"
export VLLM_METRICS_URL="http://${MODEL_SERVICE}.${BENCHMARK_RUNNER_NAMESPACE}.svc.cluster.local:8000/metrics"
export PROMETHEUS_URL="${PROMETHEUS_URL:?Set the Prometheus API URL}"
export OBJECTIVE_PREFIX=flow-control-v090
export EXPECT_FLOW_CONTROL=1
export EXPECT_PROMETHEUS=1

pipeline/run-in-cluster.sh \
  results/example-run results/live-status.json .cache/prompts \
  benchmark-data/upstream-flow-control-v0.9.0/selected-workload-shapes/scenarios.json -- \
  --scenario-filter chat_short_output \
  --prompt-pool-size 24 \
  --warmup-duration 30 \
  --warmup-concurrency 2 \
  --steady-state-trim-s 30 \
  --metric-sample-interval-s 0.5 \
  --vllm-prefix-caching off \
  --traffic-seed 42 \
  --arrival-mode closed_loop
```

## Open-loop command

The first command creates the deterministic GuideLLM trace. The second starts one synchronized GuideLLM worker per tenant and records metrics for the full run.

```bash
NAMESPACE=flow-control
RUNNER_POD=flow-control-benchmark-runner
SCENARIO_FILE=benchmark-data/upstream-flow-control-v0.9.0/selected-workload-shapes/scenarios.json
SCENARIO_NAME=chat_short_output
TRACE_DIR=/tmp/flow-control-trace
OUTPUT_DIR=results/chat-short-output

kubectl apply -n "$NAMESPACE" -f pipeline/kubernetes/benchmark-runner.yaml
kubectl wait -n "$NAMESPACE" --for=condition=Ready \
  pod/"$RUNNER_POD" --timeout=600s

python3 pipeline/guidellm_trace.py \
  --scenario-file "$SCENARIO_FILE" \
  --scenario "$SCENARIO_NAME" \
  --out-dir "$TRACE_DIR" \
  --traffic-seed 42

python3 pipeline/run_guidellm_scenario.py \
  --manifest "$TRACE_DIR/manifest.json" \
  --run-dir "$OUTPUT_DIR" \
  --prefix chat-short-output \
  --namespace "$NAMESPACE" \
  --runner-pod "$RUNNER_POD" \
  --expected-prefix-cache off \
  --http-version 1 \
  --guidellm-worker-processes 4 \
  --drain-after-done \
  --recover-multiline-sse
```

Detector, headroom, token-counting, routing, and replica arguments differ by package. Each package README states the exact values. Its `run-config.json` records the deployment contract. Upstream packages also include the executable traffic scenario.

## Metrics-only capture

[`metrics_capture.py`](metrics_capture.py) validates the required Endpoint Picker and vLLM metric families before traffic starts and records the time series during the run.

```bash
RUN_ID=${RUN_ID:?Set the run identifier}
SCENARIO=${SCENARIO:?Set the scenario name}
OUTPUT_DIR=${OUTPUT_DIR:?Set the output directory}
DURATION_SECONDS=${DURATION_SECONDS:?Set the capture duration in seconds}

python3 pipeline/metrics_capture.py \
  --run-id "$RUN_ID" \
  --scenario "$SCENARIO" \
  --out-dir "$OUTPUT_DIR" \
  --duration "$DURATION_SECONDS" \
  --interval 1 \
  --require-flow-control
```

Counted flow-control runs must not use `--allow-missing`.

## Validation and publication

1. Put each accepted capability set under `benchmark-data/` with its own README, configuration, analysis, request data, traffic samples, system metrics, and proof gates. Include the executable scenario and traffic command when the public runner supports the test.
2. Generate each public-safe tested configuration with `python3 pipeline/generate_package_configs.py`.
3. Generate the data-bound architecture and result visuals with `python3 pipeline/generate_package_visuals.py`.
4. Confirm both generated asset sets are current with their `--check` commands.
5. Run `python3 pipeline/validate_upstream_packages.py` for stable upstream packages or `python3 pipeline/validate_batch_eviction_packages.py` for batch eviction.
6. Build the grouped HTML report after the packages pass.
7. Update the repository README and shared charts last.

For upstream packages, the scenario file, seed, runner hash, image, and tested configuration reproduce the issued traffic contract. GuideLLM creates the requests again; it does not feed saved model responses back into the run. GPU timing and model output are not expected to match bit-for-bit on another cluster; compare the repeated latency and throughput distributions under the same hardware and software contract.

The batch-eviction packages publish the tested configuration, accepted data, and deterministic artifact-validation commands. Their original Async Processor traffic harness is not yet part of this public pipeline.

Rejected and partial attempts remain outside `benchmark-data/`. Public packages contain no customer names, credentials, private cluster identifiers, or local paths.

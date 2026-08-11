# Benchmark pipeline

The upstream v0.9.0 campaign used one benchmark implementation with two traffic drivers.

| Test type | Traffic driver | When it was used |
|---|---|---|
| Closed loop | [`benchmark.py`](benchmark.py) | Engine, detector, request-count, and token-admission sweeps. The runner keeps a configured number of requests in flight. |
| Open loop | GuideLLM 0.7.0 through [`run_guidellm_scenario.py`](run_guidellm_scenario.py) | Production scenarios, workload-shape comparisons, batch interference, routing, scaling, and stability. Requests follow a fixed Poisson arrival schedule, independent of completion time. |

[`guidellm_trace.py`](guidellm_trace.py) imports the same scenario and traffic logic from `benchmark.py`. Both paths use the same prompt construction, headers, metric capture, proof gates, and artifact schema.

The published `benchmark.py` is the hardened runner used for the accepted upstream campaign. Its SHA-256 is `3811ec26c46bf3a26fa643698ec54bf569bb4bc99c3ea22ca18f805cb077b8e0`.

The [request-concurrency priority-tuning package](../benchmark-data/upstream-flow-control-v0.9.0/request-concurrency-priority-tuning/) is the exception. It was run in July 2026 with the earlier [`benchmark-2026-07.py`](archive/benchmark-2026-07.py) revision. Its exact settings remain in each run's `benchmark_config.json`; the current runner should be used for new work.

## What is captured

Each counted run records:

- one row per request, including priority, fairness ID, TTFT, TPOT, status, and route;
- the issued traffic schedule and the actual send time;
- Endpoint Picker, vLLM, Envoy, Kubernetes, and Prometheus metrics during traffic;
- the deployed images, engine arguments, detector state, and cache state;
- proof gates for traffic timing, headers, routing, flow-control engagement, metrics, and errors.

Prefix caching was disabled for the primary campaign. Cache behavior was enabled only in the dedicated prefix-cache routing package.

## Closed-loop command

Set the deployment-specific service names and credentials through environment variables. Scenario-based packages version the traffic file; every package versions its benchmark arguments.

The positional arguments are the local artifact directory, the JSON file read by the status dashboard, the deterministic prompt-pool cache, and the package's scenario JSON. An empty scenario argument runs a built-in closed-loop sweep.

```bash
export BENCHMARK_RUNNER_NAMESPACE=<namespace>
export BENCHMARK_RUNNER_POD=<runner-pod>
export BENCHMARK_RUNNER_MANIFEST=<runner-pod-manifest>
export BASE_URL=http://<endpoint-picker-service>:8080
export TOKENIZE_URL=http://<model-service>:8000/tokenize
export EPP_METRICS_URL=http://<endpoint-picker-service>:9090/metrics
export VLLM_METRICS_URL=http://<model-service>:8000/metrics
export PROMETHEUS_URL=https://<prometheus-host>/api/v1
export OBJECTIVE_PREFIX=flow-control-v090
export EXPECT_FLOW_CONTROL=1
export EXPECT_PROMETHEUS=1

pipeline/run-in-cluster.sh \
  <output-dir> <live-status.json> <prompt-cache-dir> <scenario.json> -- \
  --scenario-filter <scenario-name> \
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
python3 pipeline/guidellm_trace.py \
  --scenario-file <scenario.json> \
  --scenario <scenario-name> \
  --out-dir <trace-dir> \
  --traffic-seed 42

python3 pipeline/run_guidellm_scenario.py \
  --manifest <trace-dir>/manifest.json \
  --run-dir <output-dir> \
  --prefix <run-name> \
  --namespace <namespace> \
  --runner-pod <runner-pod> \
  --endpoint http://<endpoint-picker-service>:8080 \
  --expected-prefix-cache off \
  --http-version 1 \
  --guidellm-worker-processes 4 \
  --drain-after-done \
  --recover-multiline-sse
```

Detector, headroom, token-counting, routing, and replica arguments differ by package. Each package README states the exact values, and its `run-config.json` and scenario file contain the full configuration and traffic definition.

## Metrics-only capture

[`metrics_capture.py`](metrics_capture.py) validates the required Endpoint Picker and vLLM metric families before traffic starts and records the time series during the run.

```bash
python3 pipeline/metrics_capture.py \
  --run-id <run-id> \
  --scenario <scenario-name> \
  --out-dir <output-dir> \
  --duration <seconds> \
  --interval 1 \
  --require-flow-control
```

Counted flow-control runs must not use `--allow-missing`.

## Validation and publication

1. Put each accepted capability set under `benchmark-data/` with its own README, scenario, configuration, analysis, request data, traffic samples, system metrics, and proof gates.
2. Run `python3 pipeline/validate_upstream_packages.py` for stable upstream packages or `python3 pipeline/validate_batch_eviction_packages.py` for batch eviction.
3. Build the grouped HTML report after the packages pass.
4. Update the repository README and shared charts last.

Rejected and partial attempts remain outside `benchmark-data/`. Public packages contain no customer names, credentials, private cluster identifiers, or local paths.

#!/usr/bin/env bash
set -euo pipefail

usage="usage: pipeline/rhaii35/run-in-cluster.sh <canonical|slo-mixed|slo-equal> <local-output-dir> <local-live-status-file> <prompt-cache-dir> <scenario-file> -- [benchmark args]"

mode=${1:?$usage}
local_output_dir=${2:?$usage}
local_live_status=${3:?$usage}
prompt_cache_dir=${4:?$usage}
scenario_file=${5:?$usage}
shift 5
if [[ "${1:-}" != "--" ]]; then
  echo "expected -- before benchmark arguments" >&2
  exit 2
fi
shift

case "$mode" in
  canonical|slo-mixed|slo-equal) ;;
  *)
    echo "unsupported feature mode: $mode" >&2
    exit 2
    ;;
esac

script_dir=$(cd "$(dirname "$0")" && pwd)
pipeline_dir=$(cd "$script_dir/.." && pwd)
namespace=${BENCHMARK_RUNNER_NAMESPACE:-default}
pod=${BENCHMARK_RUNNER_POD:-flow-control-benchmark-runner}
manifest=${BENCHMARK_RUNNER_MANIFEST:-$pipeline_dir/kubernetes/benchmark-runner.yaml}
cleanup_runner=${BENCHMARK_RUNNER_CLEANUP:-true}
remote_root=/work/current
remote_runner=$remote_root/runner
remote_input=$remote_root/input
remote_output=$remote_root/output
remote_live_status=$remote_root/live-status.json
remote_sampler_stop=$remote_root/stop-pd-stage-sampler
remote_benchmark_pid=$remote_root/benchmark.pid
remote_sampler_pid=$remote_root/pd-stage-sampler.pid
benchmark_pid=""
sampler_pid=""
sync_pid=""
runner_applied=0

signal_remote_process() {
  local pid_file=$1
  kubectl exec -n "$namespace" "$pod" -c runner -- \
    sh -c 'if [ -f "$1" ]; then pid=$(cat "$1"); kill "$pid" 2>/dev/null || true; fi' \
    sh "$pid_file" >/dev/null 2>&1 || true
}

stop_sampler() {
  if [[ -n "$sampler_pid" ]] && kill -0 "$sampler_pid" 2>/dev/null; then
    kubectl exec -n "$namespace" "$pod" -c runner -- \
      touch "$remote_sampler_stop" >/dev/null 2>&1 || true
    wait "$sampler_pid" 2>/dev/null || true
  fi
  sampler_pid=""
}

cleanup() {
  if [[ -n "$sync_pid" ]] && kill -0 "$sync_pid" 2>/dev/null; then
    kill "$sync_pid" 2>/dev/null || true
    wait "$sync_pid" 2>/dev/null || true
  fi
  if [[ -n "$benchmark_pid" ]] && kill -0 "$benchmark_pid" 2>/dev/null; then
    signal_remote_process "$remote_benchmark_pid"
    kill "$benchmark_pid" 2>/dev/null || true
    wait "$benchmark_pid" 2>/dev/null || true
  fi
  stop_sampler
  if [[ "$runner_applied" == "1" && "$cleanup_runner" == "true" ]]; then
    kubectl delete -n "$namespace" -f "$manifest" \
      --ignore-not-found --wait=false >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for path in "$manifest" "$prompt_cache_dir" "$scenario_file"; do
  if [[ ! -e "$path" ]]; then
    echo "required path does not exist: $path" >&2
    exit 2
  fi
done

mkdir -p "$local_output_dir" "$(dirname "$local_live_status")"
benchmark_log="$local_output_dir/in-cluster-runner.log"
sampler_log="$local_output_dir/pd-stage-sampler.log"

kubectl apply -n "$namespace" -f "$manifest"
runner_applied=1
kubectl wait -n "$namespace" --for=condition=Ready pod/"$pod" --timeout=600s
kubectl exec -n "$namespace" "$pod" -c runner -- \
  sh -lc "rm -rf '$remote_root' && mkdir -p '$remote_runner' '$remote_input/prompt-pools' '$remote_output'"
kubectl cp --retries=5 "$pipeline_dir/." "$namespace/$pod:$remote_runner" -c runner
kubectl cp --retries=5 "$prompt_cache_dir/." "$namespace/$pod:$remote_input/prompt-pools" -c runner
kubectl cp --retries=5 "$scenario_file" "$namespace/$pod:$remote_input/scenario.json" -c runner

remote_env=()
for name in \
  BASE_URL TOKENIZE_URL EPP_METRICS_URL EPP_PLUGIN_STATE_URL VLLM_METRICS_URL \
  PROMETHEUS_URL PROMETHEUS_INSECURE_HTTPS PROMETHEUS_NAMESPACE \
  PROMETHEUS_POD_PREFIX PROMETHEUS_EPP_SERVICE PROMETHEUS_VLLM_SERVICE \
  PROMETHEUS_SCRAPE_SETTLE_S OBJECTIVE_PREFIX EXPECT_FLOW_CONTROL \
  EXPECT_PROMETHEUS PYTHONHASHSEED ENDPOINT_NAME MODEL_NAME FLOW_STAGE_ID \
  FLOW_CONTROL_MODE QUEUE_DEPTH_THRESHOLD KV_CACHE_UTIL_THRESHOLD \
  METRICS_STALENESS_THRESHOLD FLOW_CONTROL_HEADROOM USAGE_LIMIT_THRESHOLD \
  CONCURRENCY_MODE MAX_CONCURRENCY MAX_TOKEN_CONCURRENCY \
  ADD_ESTIMATED_OUTPUT_TOKENS METRICS_TOKEN; do
  if declare -p "$name" >/dev/null 2>&1; then
    remote_env+=("$name=${!name}")
  fi
done
remote_env+=("FLOW_LIVE_STATUS_FILE=$remote_live_status")

remote_args=(
  --output-dir "$remote_output"
  --prompt-pool-cache-dir "$remote_input/prompt-pools"
  --scenario-file "$remote_input/scenario.json"
)
remote_args+=("$@")

benchmark_command=(python3 "$remote_runner/benchmark.py" "${remote_args[@]}")
if [[ "$mode" == "slo-mixed" || "$mode" == "slo-equal" ]]; then
  slo_header_mode=${mode#slo-}
  benchmark_command=(
    python3 "$remote_runner/rhaii35/slo_scenario_runner.py"
    --canonical-runner "$remote_runner/benchmark.py"
    --slo-header-mode "$slo_header_mode"
    "${remote_args[@]}"
  )
fi

pd_stage_capture=false
if [[ -n "${PREFILL_METRICS_URL:-}" || -n "${DECODE_METRICS_URL:-}" ]]; then
  pd_stage_capture=true
fi
if [[ "$pd_stage_capture" == "true" && \
      (-z "${PREFILL_METRICS_URL:-}" || -z "${DECODE_METRICS_URL:-}" || -z "${EPP_METRICS_URL:-}") ]]; then
  echo "P/D metric capture requires PREFILL_METRICS_URL, DECODE_METRICS_URL, and EPP_METRICS_URL together" >&2
  exit 2
fi
if [[ "$pd_stage_capture" == "true" ]]; then
  sampler_env=()
  if declare -p METRICS_TOKEN >/dev/null 2>&1; then
    sampler_env+=("METRICS_TOKEN=$METRICS_TOKEN")
  fi
  sampler_tls_args=()
  if [[ "${PD_METRICS_INSECURE_HTTPS:-false}" == "true" ]]; then
    sampler_tls_args+=(--insecure-https)
  fi
  kubectl exec -n "$namespace" "$pod" -c runner -- \
    sh -c 'echo $$ > "$1"; shift; exec "$@"' \
    sh "$remote_sampler_pid" env "${sampler_env[@]}" \
    python3 "$remote_runner/rhaii35/pd_stage_sampler.py" \
      --prefill-url "$PREFILL_METRICS_URL" \
      --decode-url "$DECODE_METRICS_URL" \
      --endpoint-picker-url "$EPP_METRICS_URL" \
      --output "$remote_output/pd-stage-metrics.csv" \
      --interval "${PD_METRIC_INTERVAL_S:-1}" \
      --duration 86400 \
      --request-timeout "${PD_METRIC_REQUEST_TIMEOUT_S:-10}" \
      --stop-file "$remote_sampler_stop" \
      "${sampler_tls_args[@]}" \
      >"$sampler_log" 2>&1 &
  sampler_pid=$!
fi

kubectl exec -n "$namespace" "$pod" -c runner -- \
  sh -c 'echo $$ > "$1"; shift; exec "$@"' \
  sh "$remote_benchmark_pid" env "${remote_env[@]}" "${benchmark_command[@]}" \
  >"$benchmark_log" 2>&1 &
benchmark_pid=$!

sync_live_status() {
  local temporary="${local_live_status}.tmp"
  while kill -0 "$benchmark_pid" 2>/dev/null; do
    if kubectl exec -n "$namespace" "$pod" -c runner -- \
      cat "$remote_live_status" >"$temporary" 2>/dev/null; then
      mv "$temporary" "$local_live_status"
    else
      rm -f "$temporary"
    fi
    sleep 1
  done
}
sync_live_status &
sync_pid=$!

set +e
wait "$benchmark_pid"
benchmark_status=$?
set -e
benchmark_pid=""

kill "$sync_pid" 2>/dev/null || true
wait "$sync_pid" 2>/dev/null || true
sync_pid=""
kubectl exec -n "$namespace" "$pod" -c runner -- cat "$remote_live_status" \
  >"${local_live_status}.tmp" 2>/dev/null \
  && mv "${local_live_status}.tmp" "$local_live_status" || true

sampler_status=0
if [[ -n "$sampler_pid" ]]; then
  kubectl exec -n "$namespace" "$pod" -c runner -- \
    touch "$remote_sampler_stop" >/dev/null 2>&1 || true
  set +e
  wait "$sampler_pid"
  sampler_status=$?
  set -e
  sampler_pid=""
fi

kubectl cp --retries=5 "$namespace/$pod:$remote_output/." "$local_output_dir" -c runner

if [[ "$benchmark_status" != "0" ]]; then
  cat "$benchmark_log" >&2
  exit "$benchmark_status"
fi
if [[ "$sampler_status" != "0" ]]; then
  cat "$sampler_log" >&2
  exit "$sampler_status"
fi

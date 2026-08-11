#!/usr/bin/env bash
set -euo pipefail

local_output_dir=${1:?usage: run-in-cluster.sh <local-output-dir> <local-live-status-file> <prompt-cache-dir> [scenario-file] -- [benchmark args]}
local_live_status=${2:?usage: run-in-cluster.sh <local-output-dir> <local-live-status-file> <prompt-cache-dir> [scenario-file] -- [benchmark args]}
prompt_cache_dir=${3:?usage: run-in-cluster.sh <local-output-dir> <local-live-status-file> <prompt-cache-dir> [scenario-file] -- [benchmark args]}
scenario_file=${4:-}
shift 4
if [[ "${1:-}" != "--" ]]; then
  echo "expected -- before benchmark arguments" >&2
  exit 2
fi
shift

script_dir=$(cd "$(dirname "$0")" && pwd)
base_dir=$(cd "$script_dir/.." && pwd)
namespace=${BENCHMARK_RUNNER_NAMESPACE:-default}
pod=${BENCHMARK_RUNNER_POD:-flow-control-benchmark-runner}
manifest=${BENCHMARK_RUNNER_MANIFEST:-$script_dir/kubernetes/benchmark-runner.yaml}
remote_root=/work/current
remote_runner=$remote_root/runner
remote_input=$remote_root/input
remote_output=$remote_root/output
remote_live_status=$remote_root/live-status.json
benchmark_pid=""
sync_pid=""

cleanup() {
  if [[ -n "$sync_pid" ]] && kill -0 "$sync_pid" 2>/dev/null; then
    kill "$sync_pid" 2>/dev/null || true
    wait "$sync_pid" 2>/dev/null || true
  fi
  if [[ -n "$benchmark_pid" ]] && kill -0 "$benchmark_pid" 2>/dev/null; then
    kill "$benchmark_pid" 2>/dev/null || true
    wait "$benchmark_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

kubectl apply -n "$namespace" -f "$manifest"
kubectl wait -n "$namespace" --for=condition=Ready pod/"$pod" --timeout=600s
kubectl exec -n "$namespace" "$pod" -c runner -- \
  sh -lc "rm -rf '$remote_root' && mkdir -p '$remote_runner' '$remote_input/prompt-pools' '$remote_output'"
kubectl cp --retries=5 "$script_dir/." "$namespace/$pod:$remote_runner" -c runner
kubectl cp --retries=5 "$prompt_cache_dir/." "$namespace/$pod:$remote_input/prompt-pools" -c runner
if [[ -n "$scenario_file" ]]; then
  kubectl cp --retries=5 "$scenario_file" "$namespace/$pod:$remote_input/scenario.json" -c runner
fi

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
  ADD_ESTIMATED_OUTPUT_TOKENS; do
  if declare -p "$name" >/dev/null 2>&1; then
    remote_env+=("$name=${!name}")
  fi
done
remote_env+=("FLOW_LIVE_STATUS_FILE=$remote_live_status")

remote_args=(
  --output-dir "$remote_output"
  --prompt-pool-cache-dir "$remote_input/prompt-pools"
)
if [[ -n "$scenario_file" ]]; then
  remote_args+=(--scenario-file "$remote_input/scenario.json")
fi
remote_args+=("$@")

mkdir -p "$local_output_dir" "$(dirname "$local_live_status")"
benchmark_log="$local_output_dir/in-cluster-runner.log"
kubectl exec -n "$namespace" "$pod" -c runner -- \
  env "${remote_env[@]}" python3 "$remote_runner/benchmark.py" "${remote_args[@]}" \
  > "$benchmark_log" 2>&1 &
benchmark_pid=$!

sync_live_status() {
  local tmp_status="${local_live_status}.tmp"
  while kill -0 "$benchmark_pid" 2>/dev/null; do
    if kubectl exec -n "$namespace" "$pod" -c runner -- \
      cat "$remote_live_status" > "$tmp_status" 2>/dev/null; then
      mv "$tmp_status" "$local_live_status"
    else
      rm -f "$tmp_status"
    fi
    sleep 1
  done
}
sync_live_status &
sync_pid=$!

set +e
wait "$benchmark_pid"
status=$?
set -e
benchmark_pid=""
kill "$sync_pid" 2>/dev/null || true
wait "$sync_pid" 2>/dev/null || true
sync_pid=""
kubectl exec -n "$namespace" "$pod" -c runner -- cat "$remote_live_status" \
  > "${local_live_status}.tmp" 2>/dev/null && mv "${local_live_status}.tmp" "$local_live_status" || true
kubectl cp --retries=5 "$namespace/$pod:$remote_output/." "$local_output_dir" -c runner

if [[ "$status" != "0" ]]; then
  cat "$benchmark_log" >&2
fi
exit "$status"

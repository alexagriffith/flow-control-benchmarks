#!/usr/bin/env bash
set -euo pipefail

mode=${1:-}
shift || true
case "$mode" in
  no-quota|classifying-quota|blocking-quota) ;;
  *) echo "usage: $0 no-quota|classifying-quota|blocking-quota [GuideLLM arguments]" >&2; exit 2 ;;
esac

root=$(cd "$(dirname "$0")/../.." && pwd)
soft_pt="$root/pipeline/soft-pt"
namespace=${NAMESPACE:?Set NAMESPACE}
runner=${RUNNER_POD:-flow-control-benchmark-runner}
classifier_service=soft-pt-classifier
redis_host=${REDIS_HOST:?Set REDIS_HOST}
redis_port=${REDIS_PORT:-6379}
upstream=${UPSTREAM_URL:?Set UPSTREAM_URL}
batch_api=${BATCH_API_URL:?Set BATCH_API_URL}
model=${MODEL_NAME:-openai/gpt-oss-20b}
llmis=${LLM_INFERENCE_SERVICE:?Set LLM_INFERENCE_SERVICE}
scheduler_deployment=${EPP_DEPLOYMENT:-${llmis}-kserve-router-scheduler}
output=${OUTPUT_DIR:-results/soft-pt/$(date -u +%Y%m%dT%H%M%SZ)-$mode}
prefix=${RUN_PREFIX:-soft-pt-${mode//-/_}}
remote=/work/soft-pt
batch_input="$output/batch-input.jsonl"

for command in kubectl curl jq python3; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 2; }
done
mkdir -p "$output"

kubectl -n "$namespace" get llminferenceservice "$llmis" -o json \
  > "$output/llminferenceservice.json"
kubectl -n "$namespace" get deployment "$scheduler_deployment" -o json \
  > "$output/endpoint-picker-deployment.json"
python3 "$root/pipeline/rhaii35/runtime_preflight.py" \
  --service-json "$output/llminferenceservice.json" \
  --scheduler-json "$output/endpoint-picker-deployment.json" \
  --output "$output/runtime-preflight.json" \
  --expected-model "$model"

cleanup() {
  if kubectl -n "$namespace" exec "$runner" -- test -f "$remote/reset_bucket.py" \
    >/dev/null 2>&1; then
    kubectl -n "$namespace" exec "$runner" -- python3 "$remote/reset_bucket.py" \
      --policy "$remote/policy.json" --redis-host "$redis_host" --redis-port "$redis_port" \
      > "$output/redis-reset-cleanup.json" 2>/dev/null || true
  fi
  kubectl -n "$namespace" cp "$runner:/tmp/soft-pt-classifier.log" \
    "$output/classifier-decisions.log" >/dev/null 2>&1 || true
  kubectl -n "$namespace" exec "$runner" -- sh -lc \
    'test ! -f /tmp/soft-pt-classifier.pid || kill "$(cat /tmp/soft-pt-classifier.pid)" >/dev/null 2>&1 || true' \
    >/dev/null 2>&1 || true
  kubectl -n "$namespace" delete service "$classifier_service" --ignore-not-found \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT

kubectl -n "$namespace" wait --for=condition=Ready "pod/$runner" --timeout=600s
kubectl -n "$namespace" apply -f "$soft_pt/kubernetes/classifier-service.yaml"
kubectl -n "$namespace" exec "$runner" -- rm -rf "$remote"
kubectl -n "$namespace" exec "$runner" -- mkdir -p "$remote"
for file in classifier_proxy.py policy.json token_bucket_reserve.lua token_bucket_settle.lua reset_bucket.py preflight_token_bucket.py; do
  kubectl -n "$namespace" cp "$soft_pt/$file" "$runner:$remote/$file"
done

kubectl -n "$namespace" exec "$runner" -- python3 "$remote/preflight_token_bucket.py" \
  --policy "$remote/policy.json" --redis-host "$redis_host" --redis-port "$redis_port" \
  --reserve-lua "$remote/token_bucket_reserve.lua" --settle-lua "$remote/token_bucket_settle.lua" \
  > "$output/token-bucket-preflight.json"
kubectl -n "$namespace" exec "$runner" -- python3 "$remote/reset_bucket.py" \
  --policy "$remote/policy.json" --redis-host "$redis_host" --redis-port "$redis_port" \
  > "$output/redis-reset-before.json"

kubectl -n "$namespace" exec "$runner" -- sh -lc \
  "rm -f /tmp/soft-pt-classifier.log; nohup python3 '$remote/classifier_proxy.py' \
    --policy '$remote/policy.json' --mode '$mode' --upstream '$upstream' \
    --redis-host '$redis_host' --redis-port '$redis_port' \
    --reserve-lua '$remote/token_bucket_reserve.lua' \
    --settle-lua '$remote/token_bucket_settle.lua' \
    >/tmp/soft-pt-classifier.log 2>&1 & echo \$! >/tmp/soft-pt-classifier.pid"

for _ in $(seq 1 30); do
  if kubectl -n "$namespace" exec "$runner" -- python3 -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8090/healthz", timeout=2).read()' \
    >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
kubectl -n "$namespace" exec "$runner" -- python3 -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8090/healthz", timeout=2).read().decode())' \
  > "$output/classifier-health.json"

python3 "$soft_pt/generate_batch_input.py" --output "$batch_input" \
  > "$output/batch-input-manifest.json"
curl -fsS -X POST "$batch_api/v1/files" -F purpose=batch \
  -F "file=@$batch_input;type=application/jsonl" > "$output/file-create.json"
file_id=$(jq -er '.id' "$output/file-create.json")
jq -n --arg file "$file_id" --arg mode "$mode" \
  '{input_file_id:$file,endpoint:"/v1/completions",completion_window:"1h",metadata:{study:"soft-pt",arm:$mode}}' \
  > "$output/batch-create-request.json"
curl -fsS -X POST "$batch_api/v1/batches" -H 'content-type: application/json' \
  --data-binary @"$output/batch-create-request.json" > "$output/batch-create-response.json"
batch_id=$(jq -er '.id' "$output/batch-create-response.json")

python3 "$root/pipeline/rhaii35/launch_guidellm_replay.py" \
  --manifest "$soft_pt/traces/manifest.json" \
  --run-dir "$output/realtime" \
  --prefix "$prefix" \
  --namespace "$namespace" \
  --endpoint "http://$classifier_service.$namespace.svc.cluster.local:8090" \
  --model "$model" \
  --worker-processes 4 \
  "$@"

curl -fsS "$batch_api/v1/batches/$batch_id" > "$output/batch-status-window-end.json"
for _ in $(seq 1 90); do
  curl -fsS "$batch_api/v1/batches/$batch_id" > "$output/batch-status-final.json"
  batch_state=$(jq -r '.status' "$output/batch-status-final.json")
  case "$batch_state" in completed|failed|expired|cancelled) break ;; esac
  sleep 2
done
case "$batch_state" in
  completed) ;;
  *) echo "Batch did not complete successfully: $batch_state" >&2; exit 1 ;;
esac
kubectl -n "$namespace" cp "$runner:/tmp/soft-pt-classifier.log" \
  "$output/classifier-decisions.log"
kubectl -n "$namespace" exec "$runner" -- python3 "$remote/reset_bucket.py" \
  --policy "$remote/policy.json" --redis-host "$redis_host" --redis-port "$redis_port" \
  > "$output/redis-reset-after.json"
jq -e '.remaining == 0' "$output/redis-reset-after.json" >/dev/null

echo "Soft-PT arm complete: $output"

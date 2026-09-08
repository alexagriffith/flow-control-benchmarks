#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
base_output=${OUTPUT_ROOT:-results/soft-pt-matrix/$(date -u +%Y%m%dT%H%M%SZ)}
orders=(
  "no-quota classifying-quota blocking-quota"
  "blocking-quota no-quota classifying-quota"
  "classifying-quota blocking-quota no-quota"
)

for block_index in 0 1 2; do
  block=$((block_index + 1))
  for mode in ${orders[$block_index]}; do
    OUTPUT_DIR="$base_output/block-$block/$mode" \
    RUN_PREFIX="soft-pt-b${block}-${mode//-/_}" \
      "$root/pipeline/soft-pt/run-arm.sh" "$mode" "$@"
  done
done

echo "Soft-PT matrix complete: $base_output"

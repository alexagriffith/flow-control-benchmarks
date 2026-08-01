# tiers-128 backfill — gate ON — run 20260730T033000

The surgical rerun of the one non-defensible v4 result: **tiers gate-on, 128 output
tokens**. Its v4 premium p95 swung **3.36x** across repeats (606 / 362 / 181 ms), a
pooled-across-repeats artifact that violates BENCHMARK-STANDARD.md rules 1 & 3. This
run applies the BACKFILL-PLAN fix and re-derives a stable, honest number.

## Result (headline)

**Premium (priority=100) p95 TTFT ≈ 1117 ms (range 1056–1211 ms across 3 repeats),
spread 1.15x — STABLE (≤1.5x gate met).**

Per-repeat premium p95 (pooled priority=100, steady-state trim 40 s):

| repeat | premium p95 (ms) | premium p50 (ms) | premium steady n (pooled) |
|---|---|---|---|
| r01 | 1211 | 510 | 4108 |
| r02 | 1117 | 374 | 4420 |
| r03 | 1056 | 345 | 4537 |
| **median** | **1117** | — | — |
| **range** | **1056–1211** | — | — |
| **spread** | **1.15x** | — | — |

The old "251 ms / 7.1x" headline was a pooled-across-repeats p95 and is retired. The
true steady-state premium tail under a saturated tiers pool is ~1.1 s — premium at
~48 aggregate genuinely competes for the 128 slots, so its tail is a real scheduling
result, not the ~8 s transient that inflated the v4 number. Closed-loop (optimistic).

## Exact config

- Endpoint: `gpt-oss-20b-fc` (ns <namespace>), already serving, gate ON.
- Harness: `benchmark_v4_backfill.py` (v4 patched harness + BACKFILL tiers def).
- Scenario: `test2_tiers_tuned` (premium a/b/c @ ~16 each = ~48; standard-a ramped
  surge to ~65; standard-b steady ~35).
- 512 in / 128 out, APC OFF.
- **3 repeats, 300 s each, steady-state trim 40 s.**
- **Standard surge RAMPED** via `ramp_s: 15` on standard-tenant-a's surge phase
  (replaces the v4 hard step); standard-tenant-b holds a steady baseline so the pool
  never empties while the surge ramps in.
- Premium offered load RAISED to ~16 each so every repeat clears >1000 pooled premium
  steady samples (achieved ~4100–4500).
- `queueDepthThreshold` = 4 (deployed, from the EPP `--config-text`); recorded into
  `benchmark_config.json`.
- CLI: `--test2-tuned --input-tokens 512 --output-tokens 128 --prompt-pool-size 384
  --scenario-duration 300 --repeats 3 --drain-timeout 120 --steady-state-trim-s 40
  --vllm-prefix-caching off`.

## Verification (pre-flight + live, all PASS)

1. **Gate ON + premium=priority 100** — EPP `--config-text` shows
   `featureGates: [flowControl]`, `queueDepthThreshold: 4`. InferenceObjective
   `gpt-oss-premium` → priority 100 on `gpt-oss-20b-fc-inference-pool`. Live
   flow_control metrics carried `priority="100"` and `priority="0"` labels
   throughout the run → gate actively arbitrating premium vs standard.
2. **APC OFF** — serving pod launched with `--no-enable-prefix-caching`
   (`enable_prefix_caching=False`); `prefix_cache_queries_total` / `hits_total`
   deltas = **0.0** all three repeats (flat, no caching).
3. **Saturated with a FAT queue** — `vllm:num_requests_running` max = **128** every
   repeat (mean ~114–117); `vllm:num_requests_waiting` > 0 for ~66% of samples
   (max 70–96). Real, sustained queueing.
4. **Patched metrics populate** — `kv_cache_usage_perc` non-zero (mean ~0.034–0.035,
   max ~0.040); `inter_token_latency_seconds_{sum,count}` present → TPOT ~19–21 ms
   mean; `num_preemptions_total` present.

## Captured serving metrics (per repeat)

| repeat | running max / mean | waiting max / mean | kv max / mean | TPOT mean (ms) | preemptions (delta) |
|---|---|---|---|---|---|
| r01 | 128 / 114.4 | 96 / 10.0 | 0.040 / 0.034 | 21.4 | 0 |
| r02 | 128 / 116.7 | 73 / 9.7  | 0.041 / 0.035 | 19.9 | 0 |
| r03 | 128 / 117.2 | 70 / 8.3  | 0.040 / 0.035 | 19.4 | 0 |

Preemptions were 0 across all repeats — with 128 output tokens the KV footprint stays
low (kv usage ~3–4%), so no swap/preempt pressure. The preemption counter is captured
and reads a true 0, not a missing metric.

## Notes on the validator

`validate_run.py` lists `vllm:time_per_output_token_seconds` and
`vllm:gpu_cache_usage_perc` in REQUIRED_METRICS, but this vLLM build
(`v0.18.0+rhaiv.7`) emits the equivalents `vllm:inter_token_latency_seconds` (TPOT)
and `vllm:kv_cache_usage_perc` instead. Those two show as non-blocking WARNINGS in the
validator, not failures. The equivalent data IS captured (see table above). The two
BLOCKING metrics (`num_requests_running`, `num_requests_waiting`) are present.

## Related
**Technical**
[[epp]] · [[inference-pool]] · [[prefix-caching]] · [[ttft]] · [[vllm]]


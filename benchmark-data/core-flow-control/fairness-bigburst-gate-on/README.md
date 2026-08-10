# Fairness rerun — bigger burst, saturated (2026-07-30 05:11)

Rerun of the same-band fairness test with tenant A bursting hard (a-center 120,
amplitude 20) vs steady peers B/C (peer-center 11), to settle whether the modest
v4 fairness result was real or a thin-queue artifact.

## Config
- Scenario: `--test3-fair-sat`, 3 tenants all priority 100, A bursts / B,C steady.
- 512 in / 128 out, gate ON, 3 repeats, 300s, trim 40s, APC off.
- Verified live: gate engaged (pool saturation ~1.25, running pinned ~121/128,
  waiting >0), premium=priority 100 resolving in the EPP queue metric.
- Metrics captured: kv_cache_usage_perc, inter_token_latency (TPOT), num_preemptions.

## Result (per-tenant, median of 3 repeats)
| tenant | p50 | p95 |
|---|---|---|
| A (burster) | 347 ms | 943 ms |
| B (peer)    | 191 ms | 877 ms |
| C (peer)    | 203 ms | 893 ms |

Validator: PASS. Stable (p95 spread << 1.5x). ~11,600 premium-band samples/repeat.

## Finding
The modest fairness effect is REAL, not a load artifact. Within-band round-robin
fairness bounds the burster's SHARE (A's p50 347 ms is worse than the peers' ~200 ms,
so A waits more), but it does NOT create a large latency gap at the tail (all three
p95 ~880-940 ms) because the three tenants share one band's 128 slots. Fairness
arbitrates throughput within a band; it does not make a latency tier inside one.
Honest, reproducible, and stable — safe to use in the public writeup.

## Related
**Technical**
[[epp]]


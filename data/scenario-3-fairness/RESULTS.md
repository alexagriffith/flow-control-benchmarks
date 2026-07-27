# Test 3 Saturated Noisy Fairness — Accepted

Run date: 2026-07-24
Purpose: rerun same-priority fairness with realistic noisy traffic that actually reaches saturation.

## Verdict

Accepted for the customer evidence package, with a moderate fairness signal.

- 1 uncounted stabilization pass, then 3 counted repeats.
- All counted requests were HTTP 200; no 429s, 503s, timeouts, or client errors.
- vLLM reached saturation in every counted repeat: running p95 128, waiting p95 31-33 requests.
- The greedy same-priority tenant carried more queue time and higher TTFT, while the other premium tenants stayed bounded.

## Key Metrics

| Tenant | Traffic role | Avg p50 TTFT | Avg p95 TTFT | Avg EPP queue mean |
|---|---|---:|---:|---:|
| premium-tenant-a | Greedy spike | 382 ms | 744 ms | 107 ms |
| premium-tenant-b | Same-priority peer | 206 ms | 670 ms | 80 ms |
| premium-tenant-c | Same-priority peer | 221 ms | 675 ms | 81 ms |

## Source Files

- `summary.csv` — counted repeat client metrics.
- `all_summaries.json` — counted repeat vLLM and EPP-derived metrics.
- `stabilization_summaries.json` — uncounted stabilization pass.
- `benchmark_config.json` — exact runner settings.

## Notes

This supersedes `../test3-final-v2/` for the noisy Test 3 fairness claim. The older run was clean but did not queue (`vllm:num_requests_waiting` stayed at 0), so it should be treated as diagnostic, not as final fairness-under-saturation evidence.

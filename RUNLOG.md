# Run log

The accepted run behind each scenario. A run is accepted when it exercises the behavior it claims to show, completes its counted repeats with consistent results, and has clean error accounting. Warmup and stabilization passes are always excluded from summaries.

All campaigns ran GPT-OSS 20B on one GPU behind the llm-d inference gateway, with priority bands premium 100, standard 0, batch -10 and round-robin fairness within a band.

| Scenario | Campaign | Date | What made it the accepted run |
|---|---|---|---|
| 1 · Consolidation | SLA rerun, consolidation operating point | 2026-07-23 | Sized to the consolidation story. The p95 TTFT stayed at 114 to 125 ms across both tenants and both counted repeats, all HTTP 200 |
| 2 · Service tiers | Noisy priority run | 2026-07-24 | Noisy sinusoidal surge that genuinely saturates the pool. Premium p50 41 ms vs standard 379 ms, 53,399 requests |
| 3 · Fairness | Saturated fairness run | 2026-07-24 | The only fairness run that both saturates (vLLM waiting p95 31 to 33) and finishes clean, 65,003 requests, zero errors |
| 4 · Batch isolation | Clean pressure pass, Test 4 | 2026-07-21 | Batch at triple the interactive arrival rate, queue means 202 ms batch vs 64 and 66 ms interactive, three consistent repeats |

The scenarios come from separate campaigns rather than one unified pass, and each README section cites its run and data directory. Along the way we also ran configuration comparisons and concurrency sweeps to find the right operating points. Runs that did not exercise the claimed behavior, for example fairness runs that never queued, were excluded from the evidence.

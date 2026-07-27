# Run log

The accepted run behind each result. A run is accepted when it exercises the behavior it claims to show, completes its counted repeats with consistent results, and has clean error accounting. Warmup and stabilization passes are always excluded from summaries.

Every campaign ran GPT-OSS 20B on one GPU behind the llm-d inference gateway, with priority bands premium 100, standard 0, and batch -10, and round-robin fairness inside a band.

| Result | Campaign | Date | What made it the accepted run |
|---|---|---|---|
| Capacity envelope | Input/output variation sweep | 2026-07-21 | Five request shapes swept 16 to 160 concurrent. Same first-queue point, order of magnitude cost spread |
| Consolidation | SLA rerun at the consolidation operating point | 2026-07-23 | Sized to the consolidation question. The p95 TTFT held at 114 to 125 ms across both tenants and both counted repeats, all HTTP 200 |
| Service tiers | Noisy priority run | 2026-07-24 | Noisy sinusoidal surge that genuinely saturates the pool. Premium p50 41 ms against standard 379 ms, 53,399 requests |
| Batch isolation | Clean pressure pass, Test 4 | 2026-07-21 | Batch at triple the interactive arrival rate. Queue means 202 ms batch against 64 and 66 ms interactive, three consistent repeats |
| First pressure campaign | Doubled load on one endpoint | 2026-07-21 | The first end to end pass. Every request served at 513 to 606 ms p95 TTFT, which later tuning improved on |

Same-band fairness is still open. The runs so far either stayed under the knee or did not separate the spiking tenant from its peers cleanly enough to publish, so that result is being rerun at a hotter operating point.

Along the way we also ran configuration comparisons and concurrency sweeps to find the right operating points. Runs that did not exercise the claimed behavior were left out of the evidence.

# Run log

Every benchmark campaign behind this repo, in order, with its verdict and the reason. The four scenarios in the README each cite their accepted run. Runs marked limited stay here for completeness and are not used for headline claims.

All campaigns ran GPT-OSS 20B on one GPU behind the llm-d inference gateway, with priority bands premium 100, standard 0, batch -10 and round-robin fairness within a band.

## 2026-07-20 to 21 · Clean pressure pass

A full four-test pass at high concurrency, three repeats per test, 12 runs. All counted requests returned HTTP 200 except two batch-only 503s in Test 4.

| Test | Result | Verdict |
|---|---|---|
| 1 · One endpoint, doubled load | p95 TTFT 525 to 601 ms under pressure | Good pressure evidence. Superseded for the consolidation claim by the 07-23 rerun, which measured a properly sized operating point |
| 2 · Priority differentiation | Premium ahead of standard throughout | Superseded by the 07-24 noisy rerun |
| 3 · Fairness, three premium, one spike | Fairness signal present | Superseded by the 07-24 saturated rerun |
| 4 · Priority inversion prevention | Queue means 202 ms batch vs 64 and 66 ms interactive | **Accepted. Scenario 4 evidence** |

The Test 1 pressure result is worth reading correctly. At double load the pool was saturated, so 525 to 601 ms reflects a deliberately overloaded operating point, not a tuned consolidation target. The campaign also identified the latency knee, 175 ms p95 at concurrency 96 against 359 ms at 128.

## 2026-07-23 · Configuration comparison and sweeps

Three vLLM and Endpoint Picker configurations compared on Test 1, then concurrency sweeps at two output caps.

| Variant | p95 TTFT | Verdict |
|---|---|---|
| Control, max_num_seqs 128, queue threshold 4 | ~598 ms | Best of the three under pressure |
| Variant B, max_num_seqs 96, threshold 4 | ~1065 ms | Worse. Restricting the model created a bottleneck |
| Variant C, max_num_seqs 96, threshold 1 | ~1168 ms | Worst |

The output-128 sweep produced 13-second TTFT outliers traced to a runner streaming artifact and is not used for any claim. The finding that held: the right fix for the consolidation story was sizing the traffic to the operating point, not restricting the model server.

## 2026-07-23 · SLA consolidation rerun

Test 1 at a consolidation-sized operating point, output cap 64, one stabilization pass plus counted repeats.

**Accepted. Scenario 1 evidence.** The p95 TTFT stayed at 114 to 125 ms across both tenants and both counted repeats, all HTTP 200. The stabilization pass showed the cold-start effect, roughly 12.4 s first-pass TTFT, and is excluded by design.

## 2026-07-24 · Noisy priority and saturated fairness reruns

Tests 2 and 3 rerun with noisy sinusoidal traffic that reaches genuine saturation, run through `pipeline/benchmark_v3.py`.

| Run | Result | Verdict |
|---|---|---|
| test2, noisy priority | Premium p50 41 ms vs standard 379 ms at saturation | **Accepted. Scenario 2 evidence** |
| test3, saturated fairness | Spiker p50 382 ms vs peers 206 and 221 ms, zero errors across 65,003 requests | **Accepted. Scenario 3 evidence** |
| test3, first attempt | Clean but never queued, vLLM waiting stayed at 0 | Limited. Did not exercise the fairness path |
| test1, maxseq48 exploratory | Premium p95 316 to 341 ms, standard 407 to 415 ms | Limited. Shows differentiation with standard as a third class, kept as supporting evidence only |

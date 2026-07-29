# Run log

Every accepted run in this repo comes from the v4 campaign on one H100 serving GPT-OSS 20B behind the llm-d inference gateway. Automatic prefix caching off, 512 input tokens, priority resolution and gate state verified before each counted run.

## Accepted runs (data-v4/)

| Scenario | Gate | Premium p95 TTFT | Note |
|---|---|---|---|
| Service tiers | off | 1778 ms | premium and standard degrade together |
| Service tiers | on | 251 ms | premium held inside the 300 ms objective |
| Batch isolation | off | — | 48,224 requests rejected (HTTP 429) |
| Batch isolation | on | — | zero rejections, batch queued behind interactive |
| Consolidation (saturated) | on | 795 ms | premium ahead of standard at 1062 ms |
| Fairness (saturated) | on | — | within-band share bounded, tail not insulated |

Service tiers at 512 output tokens: premium p95 TTFT 5259 ms without flow control, 145 ms with it. Two-replica pass reproduced the tier result at 177 ms.

## Correction recorded

An earlier pass in this campaign sent tenants to inference pools that had no priority objectives bound, so the Endpoint Picker resolved every request to priority 0 and the service-tier results collapsed to no measurable effect. The gate was running; it simply had no priority gap to enforce. Every counted run above was re-run after binding the objectives and verifying, in the flow-control queue metric, that premium resolved to priority 100 before the data was kept. The invalidated runs are archived, not deleted.

## Archived

The first campaign (pre-v4) is in `archive/pre-v4-campaign/`. Its latencies were measured with automatic prefix caching on, so they reflect a warm cache rather than scheduling, and are kept only to show the progression. The numbers in the README supersede them.

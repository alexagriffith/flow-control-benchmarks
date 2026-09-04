# How should flow control protect a prefill/decode service?

**Takeaway:** the tested recipe combines stage-aware hybrid admission, separate
flow identities, round-robin fairness, and rank-based priority holdback. Hybrid
admission detected both large-prompt prefill pressure and long-generation
decode pressure in the matched screens.

## What was tested

The same-node GPT-OSS 20B topology used one TP1 prefill pod and one TP1 decode
pod, each on one H100, with NVIDIA Inference Xfer Library (NIXL) KV transfer.
Prefix caching was disabled.

The prefill-heavy workload used large prompts and short responses. The
decode-heavy workload used shorter prompts and long responses. These two
traffic shapes created pressure at different P/D stages.

The study tested four decisions under both traffic shapes:

1. Which saturation detector reports the active pressure: request count, token
   count, or both.
2. Whether round-robin scheduling keeps equal-priority workloads moving.
3. Whether priority reserve protects the TTFT target for priority `100` while
   priority `0` continues to complete requests.
4. Whether eviction reclaims capacity from retryable priority `-10` work, and
   how retries affect its completion time.

| Detector | Prefill-heavy peak | Decode-heavy peak | Qualified shape |
| --- | ---: | ---: | --- |
| Request count, cap 64 | 0.094 | 1.016 | Decode |
| Token count, cap 80,000 | 1.884 | 0.640 | Prefill |
| Hybrid | 1.885 | 1.031 | Prefill and decode |

Round-robin fairness kept both equal-priority flows making progress. Priority
holdback preserved measured capacity for priority `100`, and optional eviction
completed the cancellation, retry, and drain path for priority `-10` work in
all three matched pairs.

- [Focused analysis](analysis.json)
- [Reviewed Endpoint Picker recipe](configuration/selected-recipe.yaml)
- [Complete P/D deployment example](../examples/benchmark-reproduction/08-prefill-decode-hybrid.yaml)
- [Executable P/D scenarios and commands](../../../pipeline/rhaii35/#run-a-pd-scenario)

## Evidence

[Run summaries](summary.csv), [request outcomes](request-results.csv),
[traffic samples](traffic-samples.csv), [system metrics](system-metrics.csv),
[validation records](run-evidence.csv), [detector screens](detector-screens.csv),
[priority repeats](priority-repeats.csv), [eviction pairs](eviction-pairs.csv),
[run contract](run-config.json), and [scenario](scenario.json).

## Scope

Recalculate request, token, and priority ceilings for another model, traffic
shape, replica count, or topology.

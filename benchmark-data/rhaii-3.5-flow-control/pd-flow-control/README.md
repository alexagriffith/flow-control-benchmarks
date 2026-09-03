# How should flow control protect a prefill/decode service?

**Takeaway:** the tested recipe combines stage-aware hybrid admission, separate
flow identities, round-robin fairness, and rank-based priority holdback. Hybrid
admission detected both large-prompt prefill pressure and long-generation
decode pressure in the matched screens.

## What was tested

The same-node GPT-OSS 20B topology used one TP1 prefill pod and one TP1 decode
pod, each on one H100, with NVIDIA Inference Xfer Library (NIXL) KV transfer.

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

## Scope

Recalculate request, token, and priority ceilings for another model, traffic
shape, replica count, or topology.

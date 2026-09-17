# Website follow-up after V1

September 16, 2026. V1 covers public copy, learner correctness, replay readability, controls and narrow evidence-claim corrections. This plan covers the remaining additions.

## Keep the published decision guide

The [decision guide](../benchmark-decision-map/) already covers detector selection, token-accounting prerequisites, priority ceilings, fairness, deadline ordering, rejection, prediction, eviction, prefill/decode and conditional shared accounting. Its router reference is `30f06d9c`.

The shorter local decision-map and calibration drafts are separate proposals. They must not replace the published guide. The useful additions are:

1. A compact plugin map: what each plugin observes, which decision it controls, its dependencies, and the builds tested.
2. A question-by-question entry into the existing branches.
3. Three separate checks: was the run valid, did the mechanism engage, and did the outcome meet the target?

Keep the detailed configuration and source references behind those branches. Reduce the draft operator reference to links and steps that the guide does not already explain.

## Recorded playback

The learner replay is an illustrative priority/queue model. It does not play recorded benchmark requests or implement every router plugin.

Reuse the [Flow Control Flight Recorder](https://github.com/alexagriffith/flow-control-visualizer) for recorded telemetry. Source review at `48e1e716` found these boundaries:

| Package | Next step |
|---|---|
| Four upstream production scenarios | Validate their matching request/metric schemas as the first adapter. |
| Two-model eviction | Validate the existing per-model pressure and realtime adapter. Eviction/retry correlation needs additional work. |
| Other upstream packages | Add explicit mappings for differing metric and request columns. Keep summary-only data static. |
| RHAII 3.5 | Map `flow` identities and add explicit prefill/decode stage handling. |
| Exact request destinations or engine batch membership | Require correlated lifecycle events. Aggregate samples cannot establish these paths. |

Before linking recorded playback, remove missing-field-to-zero fallbacks and derive coverage labels from populated fields. Confirm one production scenario and one two-model package end to end. Then link supported recorded windows from their evidence pages.

## Supporting drafts and images

- **Calibration kit:** local offline intake prototype, not a runner or completed experiment. Empty template fields are unresolved inputs. A preview should say “Experiment template — unresolved inputs,” not “Recorded settings.” Keep it in operator/developer documentation until reviewed.
- **Comparison map:** reuse its valid-run / engaged-mechanism / accepted-outcome distinction in the published guide. Avoid another parallel guide.
- **Prefix-cache routing results SVG:** keep. Its package README embeds it, and it combines latency, cache hits, routing imbalance and rejections.
- **Batch-isolation results SVG:** keep. Its package README embeds the directional results and repeat-stability caveat.

No benchmark reruns, metrics changes or evidence deletion are part of this plan.

# Website follow-up after V1

September 16, 2026. V1 covers public copy, learner correctness, replay readability, controls and narrow evidence-claim corrections. This plan covers the remaining additions.

## Review the existing views first

Walk every learner step and optional mode at desktop and narrow widths. Check the rendered story, technical accuracy, wording, clutter, controls and request routes. Record findings per view and fix them before expanding the learner. Apply the same review to public navigation and the decision-guide entrypoints.

## Deeper learning

Queue a dedicated Learn entry or second tab after the current cleanup. Cover every detector plugin, then the related policies and configuration. Each lesson should explain purpose, input signals, calculation, the decision it controls, configuration, dependencies, interactions and a concrete example. Bind implementation details and evidence to their versions. Map existing coverage before adding another view.

Initial coverage map, checked against router `bb2113e4`:

| Plugin | Learning topics |
|---|---|
| `utilization-detector` | Engine waiting, KV-cache pressure, endpoint and pool scores, stale telemetry, filtering. |
| `concurrency-detector` | Request, token and hybrid modes, accounting lifetime, limits, headroom and endpoint filtering. |
| Related policies and producers | Priority ceilings, fairness, ordering, token-accounting dependencies and how they connect to dispatch. |

Request, token and hybrid are three modes of the concurrency detector. Prefill/decode aggregation is a topology topic shared by both detector lessons. Check each target build before presenting its behavior as available.

### Prepared detector lesson — September 17

Source review for the next lesson is complete at `bb2113e4`. Implementation stays behind the existing-view formatting fixes.

- Show two plugins, with Requests / Tokens / Hybrid nested under Concurrency.
- Keep a connected calculation: inputs → endpoint scores → pool score → selected priority ceiling → dispatch. Show candidate filtering as a separate branch into endpoint selection.
- Use one worked example per mode. Compare hybrid's average of endpoint maxima with the request-only and token-only averages.
- Put defaults, accounting lifetime, stale metrics and prefill/decode details behind disclosures.
- Preserve exact boundaries: dispatch requires a score below the ceiling. Concurrency filter limits are integer-truncated, utilization limits remain floating point, and both filters use strict comparisons. Their all-filtered fallback does not open a blocked dispatch gate.
- Explain that headroom changes endpoint filtering. It does not reserve GPU capacity or change the pool-score denominator.
- Label examples as calculations against this source revision, not measurements or production recommendations.

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

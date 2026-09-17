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

### Next build: Dig deeper

After publishing the existing-view composition fixes, add a separate connected learner at `learn/flow-control-plugins.html`. Link it from the introduction's final view and from a compact Overview / Dig deeper navigation. Preserve the existing introduction and decision guide.

Start with two definitions: a plugin supplies a configurable decision or measurement. A detector turns request accounting or engine measurements into a pressure score.

Keep one stable request path and highlight the decision being taught. Reuse the current queue, cursor, meter, endpoint and reduced-motion animation helpers. Main views contain the question, the connected diagram and one short answer. Plugin names, configuration, defaults, dependencies and pinned sources belong in expandable details.

| Lesson | Reader's question | Visible relationship |
|---|---|---|
| Identity | Which queue does this request join? | Objective → priority; fairness identity × priority → flow queue. Distinguish the objective resource from plugins. |
| Selection | Which waiting request goes next? | Priority band → fairness selects a flow → ordering selects its request. Show round robin versus global-strict and request ordering with the same waiting work. |
| Detection | How does the router measure pressure? | Producers/engine signals → endpoint scores → pool score. Switch requests/tokens/hybrid inside Concurrency, or choose Utilization. |
| Dispatch | Can this priority dispatch, and where? | Pool score versus ceiling → dispatch; separate endpoint filter → picker. Headroom belongs to filtering. |
| Accounting | What changes after dispatch? | Router dispatch → engine wait/run → first response → completion. Show when request/token accounting is released. |
| Rejection | Why can a request be rejected? | Separate queue budgets/expiry from optional request-control admitters after flow-control admission unblocks. Show prediction dependencies. |
| Eviction | Can running work be interrupted? | Blocked higher priority → eligible lower-priority victim → cancellation → eventual engine/accounting response. Retry belongs to the caller. |

Coverage at router `bb2113e4ecd79b049c7322164794ca9ea30b8cbb`:

- **Fairness:** `round-robin-fairness-policy`, `global-strict-fairness-policy`, `program-aware-fairness`.
- **Ordering:** `fcfs-ordering-policy`, `edf-ordering-policy`, `slo-deadline-ordering-policy`.
- **Ceilings:** `static-usage-limit-policy`, `priority-holdback-policy`, `soft-reflective-ceiling-policy`.
- **Detectors:** `concurrency-detector`, `utilization-detector`.
- **Eviction:** `sheddable-eviction-filter`, `priority-then-time-eviction-order-policy`.
- **Related pieces:** objective resources; agent identity; tokenization, output-length buckets and in-flight accounting; prefix-cache producers; latency prediction, observation and optional admitters; ordinary scheduling filters/scorers/pickers.

These are all 13 flow-control plugin types plus their relevant companions, not a claim to teach every router plugin. Check registration and implementation against the [pinned source](https://github.com/llm-d/llm-d-router/blob/bb2113e4ecd79b049c7322164794ca9ea30b8cbb/cmd/epp/runner/runner.go#L637).

Technical acceptance must preserve strict dispatch/filter boundaries, the separate all-filtered fallback, hybrid's average of endpoint maxima, per-stage P/D aggregation, stale-metric policy, accounting lifetimes and current-versus-historical defaults. Optional admitters are after flow-control admission unblocks and data production, before scheduling. Do not infer HTTP status from a plugin's internal error without the Director mapping. Source maturity flags are not deployed-product support claims.

Use calculations and illustrative animation, with measured evidence linked separately. Each lesson needs independent source review and native-size element/group/page inspection in both themes and at narrow/desktop widths before publication.

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

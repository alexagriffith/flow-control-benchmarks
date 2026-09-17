# Learner composition review — September 17, 2026

Reviewed learner SHA-256: `82574a8c202ce97355f1a31b605dcb9e4dfd7e471bced6d616153625d8f8dedb`.

## Result

The existing learner passes the bounded technical and independent visual review. Fairness and tier branches merge into a shared dispatch arrow. Request animations follow drawn routes and stop at component ports. Queue counts, actor markers and status banners have reserved space. Context labels remain readable. Diagrams align with narration, and optional eviction detail is folded into its own disclosure.

Measured values, simulation arithmetic, evidence links and benchmark artifacts are unchanged. No benchmark was rerun. The deeper plugin section is planned in [website-v2-plan.md](website-v2-plan.md) and has not been implemented.

## Validation

- 19 replay and scene unit tests passed.
- Controls and 68 evidence-card states passed.
- Full browser regression passed on `cb5aab1a`: 158 scene/detail/option views and 34 normal-motion steps. The final change affects only the batch-input animation and received a separate normal-motion review in both themes.
- `pipeline/validate_learner_composition.py` passed on the final source. It checks junctions, count ownership, label clearance, actor/status spacing, interrupted-animation cleanup and batch gateway-port motion.
- Independent review covers all 34 guided steps and 20 existing options, with targeted checks bound to each final source delta. The final receipt follows.

Publication must be verified separately against the deployed commit. The `/tmp/` paths below identify local review artifacts, not public evidence pages.

---

# Independent adversarial formatting review

## Scope and revision

Existing 34-step learner and its current detector, priority, policy and execution options. Detector expansion is outside this review. Implementation is owned by parent; this reviewer made no learner edits.

Baseline: published Git `d653311`, journey SHA256 `dbc3fd7b5c8728acdbe87bb8e4d74e9bbdb015c57f623f2c8a3bbdfe0442560e`.

Evidence inspected: all 34 full-page main views and all 20 option views in `/tmp/website-queue-20260916/overlap-release-final` (render hash `09bef116…`, differs from baseline only in green knee caption placement), plus 1844px unchanged policy/fairness/summary pages from `/tmp/learner-allviews-independent`. Current knee has separate exact-baseline native/full-page evidence in `/tmp/learner-evidence-review-20260917/final-knee`. Narrow policy, summary, ceilings and eviction views inspected as specific siblings. Final candidate needs new source-bound inspection; this is not blanket approval of the old artifact.

## Why previous acceptance was insufficient

Previous geometry/motion checks established that dots followed paths, but did not ask whether the paths correctly assigned ownership or formed a comprehensible merge. Dimmed context was treated as emphasis rather than tested for a readable end-to-end explanation. Flow label fit was tested for turn counts, leaving empty/overflow labels outside that check. Contact sheets and page overflow checks cannot close sibling collisions. These original assumptions are reopened here.

## Priority findings

| ID | Priority | Defect visible in baseline | Smallest correction / acceptance |
|---|---|---|---|
| A1 | P2 | Policy branches stop before shared pool; fairness branches have overlapping arrowheads, gap and unnamed receiver. | Deliberate merge, common stem and one arrow entering named receiver. One request at a time follows exact route. |
| A2 | P2 | Whole-component opacity .28 makes gateway/EPP/other queues/endpoints barely legible. Learner cannot trace selected dispatch to its owner/receiver. | Readable neutral context text/frame; selection uses border/color. Confirm both themes. |
| A3 | P2 | Static endpoint A arrow originates at high-band lower-right even when priority0 selected; B arrow appears from priority0 even when high selected. | Actual selected-band port or unambiguous shared dispatch spine. Check8.2–9.3,10.0–2 and policy/execution siblings. |
| A4 | P2 | 9.2 third-flow empty status outside band;13.0/.1 tenant-a+11 collides with next tenant dot. | Reserve count/status inset per flow; test empty, >7, last cell and largest existing labels. |
| A5 | P2 | Summary underline/pills/capsule have no explained encoding; tier blocks imply arbitrary fixed service shares. | Replace unsupported decorative metrics with short functional captions; preserve summary framework. |
| A6 | P2 | Replay all-to-all routing looks like an X crossing, suggesting premium→B and batch→A with ambiguous standard junction. | Explicit three-to-one merge, common stem, one-to-two fan-out with matching smooth bends; dots follow same paths. |
| A7 | P2 | Engine/eviction request dot overlays endpoint metric text; narrow eviction red dot covers KV/score line. Cancellation route passes through cycle captions. | Dedicated actor inset or external state label; control line starts from EPP boundary clear of captions. |
| A8 | P3 | Every ordinary execution phase exposes eviction conditions first; relevant phase explanation is pushed below dense unrelated paragraphs. | Eviction-only conditional prose or nested disclosure, with chosen phase explanation adjacent to selector. |

## Per-view disposition

The following rows replace broad prior “clear” verdicts where newly adversarial criteria fail. “No new finding” means only that this pass did not identify another concrete issue; candidate effects still require checking.

| View | Current review disposition |
|---|---|
| 0.0 | No new finding; capacity comparison remains illustrative. |
| 1.0 | No new finding; retain peak/queue coupling. |
| 2.0 | Final native knee fix already exact-baseline reviewed; no new finding. |
| 3.0 | A1: disconnected convergence. |
| 4.0 | A1: triple arrowhead and unnamed receiver. |
| 5.0 | A5: unsupported visual meters. |
| 6.0 | A2; gateway branch geometry shares A1 grammar. |
| 6.1 | A2: lookup result context unreadable. |
| 6.2 | A2: fallback context unreadable. |
| 7.0 | A2: queue identity context unreadable. |
| 7.1 | A2; maintain new-flow label clearance. |
| 7.2 | A2; keep empty/lease/timeout distinction. |
| 8.0 | A2: signal-to-pool owner context unreadable. |
| 8.1 | A2; gate labels themselves readable. |
| 8.2 | A2/A3: dispatch relationship must remain assigned to selected band. |
| 8.3 | A2/A3: endpoint B connector appears from wrong band. |
| 8.4 | A2/A3: selected high request needs clear source. |
| 9.0 | A2/A3: current destination nearly invisible. |
| 9.1 | A2/A3: skip scan and subsequent selected-band dispatch. |
| 9.2 | A2/A3/A4: third empty text outside band. |
| 9.3 | A2/A3/A7/A8: ordinary lifecycle versus optional cancellation. |
| 10.0 | A2/A3: active priority0 but static arrow from high edge. |
| 10.1 | A2/A3/A4: overflow reservation must apply to all tenant cells. |
| 10.2 | A2/A3: retain fitted turn labels. |
| 11.0 | A2; shared measured card formatting separately native-size reviewed. |
| 11.1 | A2; source/scale/rows now clear. |
| 11.2 | A2; measured TTFT boundary retained. |
| 12.0 | A2; no new metric-card finding. |
| 12.1 | A2; zero bar is zero and metrics unchanged. |
| 12.2 | A2; boundary card remains factual. |
| 13.0 | A2/A4: +11 touches next tenant dot. |
| 13.1 | A2/A4: same ownership collision. |
| 13.2 | A2; linked package scope retained. |
| 14.0 | A6: crossing/merge ambiguity; existing controls remain outside new defect scope. |

## Optional coverage

- All8 detector selections (four signals × combined/P-D): no new numerical/label mismatch found; A2 applies to neutral mechanism context. Keep P/D pressure relationships separate from request flow.
- Both high/low priority checks: A2/A3. Low waits while high may select B; never imply endpoint filter changes pool denominator.
- All5 policy selections: A2/A3/A4. Selected request-order labels remain meaningful; empty higher band and selected lower band must match route.
- All5 execution phases: A2/A3/A7/A8. Solid completion via gateway and dashed optional cancellation remain semantically separate.
- Expanded details: prior measured-card/native link fix remains applicable. Root must recheck progressive disclosure after A8, without removing export links.

## Candidate review

Candidate and final-delta review records follow. The cb5aab1a revision closes the route/banner findings. Final82574a8c additionally corrects the batch-evidence input-animation sibling; see final acceptance and delta below.

### First candidate review — 0b845f78

Captured90 states (34 desktop guided steps,10 narrow selected steps,3 policy timed states per width,20 option selections per width). Runtime errors: none. The initial all34 desktop load and subsequent option load began at0b845f78. The final on-disk guard observed25e0d8fb, so this is revision evidence plus final deltas, not a single frozen acceptance. Captures are `/tmp/learner-adversarial-20260917/candidate`; native-groups00–16 paste crops without resizing.

A2/A3/A4/A5/A6/A8 and A7's request-state marker were visibly corrected in inspected candidate renders. A1 still needs the policy priority timed state's receiver named. A7 control lines crossed the header chips and lookup→Admission arrow. Parent corrected those plus the following new exposed padding issue:

- **A9 (P2):** meter numeric value nearly touches cycle circle; cycle caption touches EPP right boundary. Reserve actual value/circle/caption clearance.
- **A10 (P2):** readable context reveals settled mechanism callouts overlapping Admission, priority headings and tenant cells. Remove redundant callouts6.1/6.2/7.0/9.0/9.1/9.2; retain header attachment6.0 in its separate space. Native groups03/04/08/09 are failing controls.

These are acceptance failures, not optional aesthetic preferences. Original metrics, evidence links, core component arrangement and diagram modes remain outside the requested changes.

### Final route, contrast and callout delta — 599dcdfa / 6e9ec8c7

92 source-rendered states in `final/report.json`: both themes at1844(normal motion) and375(reduced motion), including early/late animation captures for6.0/6.1/6.2/7.0/7.1, twelve affected main steps, all three policy timed states, engine/completion/eviction. No page errors. Run began599dcdfa and ended6e9ec8c7; the sole intervening change removes canceled `.fly-dot` markers in resetCanvas, with no geometry/copy change. Parent separately regression-tested leaving fairness during normal motion and observing zero stale markers.

Native/full-page inspection closes A1–A6, A8–A10 and the actor/cancellation parts of A7: named receivers, one merged dispatch arrow, neutral context readability, shared band-to-endpoint ownership, reserved queue overflow space, summary functional captions, replay merge/trunk/fan-out, folded optional eviction conditions, separated meter/cycle labels, and removal of callouts covering node labels. Lookup motion takes the outside-Admission gutter; FlowKey stops at Admission; first-flow arrival stops at band boundary before the new queue appears.

**A7 sibling still open:** solid completion return crossed the lookup→Admission arrowhead. This is visible in `final/1844-light-execution-complete-page.png` at the horizontal return/vertical lookup junction. Requested the same outside-EPP/top-gutter discipline used for the dashed control route while preserving completion direction and solid style.

### Page distribution delta — 8987d038

30 frozen states in `layout/report.json`, no page errors, start/end SHA2568987d0385c0301dc91ff15f24ef3e294dd35fb1f4b5724927995fc48e5e04f64. Width/theme combinations:1844light,1440dark,375light. Includes policy, fairness, summary, gateway, blocked ceiling, endpoint filter, measured tier, replay, completion and eviction. Full pages and narrow left/right states inspected.

Top alignment is warranted: previous1844×1265 canvas began at135 while EPP began about397 and narration at157. Top alignment brings the diagram and narration into one reading region without moving internal components. Header chips now fit beneath the gateway and use a short metadata attachment. Policy/fairness/summary/results/replay hierarchy and spacing remain coherent.

**A11 (P2) open:** top alignment exposed cancellation-gutter/banner overlap. At1844 the dashed route touches the wait-pill bottom; at1440 and375 it crosses the pill. Examples: `layout/1440-dark-execution-evict-page.png` and `layout/375-light-execution-evict-right.png`. Reserve independent top space for the wait banner and the narrow scroll hint, placing SVG content below it.

## Final acceptance — cb5aab1a

**No unresolved actionable formatting/comprehension blocker in this review scope.** This is a local reviewed revision, not evidence of publication or a claim that benchmarks were rerun.

Final exact SHA256: `cb5aab1a45739a64f5f94a52cfa507e796cc8918142bb4c8abbcd97f805b5e82`. Final captures: `/tmp/learner-adversarial-20260917/acceptance/`. `report.json` records60 targeted states across1844×1265,1440×900 and375×900 in both themes; start/end hashes match and there are no page errors. Full-page checks were supplemented by native completion/eviction group crops and narrow left/right scroll states. This targeted pass closes changes after the earlier all34-step/all20-option native review; it is not a false claim to have independently rerun every prior combination at the final hash.

A7 completion sibling closes: endpoint A returns above the EPP and down its outside gutter to gateway; gateway returns above the input routes into premium. The completed actor has an attached, separate state box. Solid completion remains distinct from dashed optional cancellation. A11 closes: stable48px desktop/72px narrow SVG inset reserves banner and scroll-hint space. Dashed cancellation lies below the wait pill at all three inspected widths, with no node-label/metadata-chip collision.

A1–A11 are closed for the existing learner. Core visual framework, measured values and evidence links are preserved. Existing detector options were reviewed; new detector expansion is excluded.

### Final per-view closure

| View | Final review decision |
|---|---|
|0.0|Pass: dedicated/shared capacity remains an illustration; no new formatting defect.|
|1.0|Pass: coupled burst/queue story retained; no new formatting defect.|
|2.0|Pass: exact-baseline knee label/path clearance retained; current changes do not alter chart geometry.|
|3.0|Pass: three branches merge into one arrow entering named shared pool; all three timed states checked.|
|4.0|Pass: one merged dispatch stem, named Dispatch receiver and selected turn; no overlapping arrowheads.|
|5.0|Pass: functional captions explain capacity, bursts and priority; fixed-share-looking tier bars removed.|
|6.0|Pass: readable gateway context and attached, contained trusted-header chips.|
|6.1|Pass: lookup route uses outside Admission gutter; selected result stays legible; no covering callout.|
|6.2|Pass: fallback branch clear and labels unobscured; no covering callout.|
|7.0|Pass: FlowKey identity remains at Admission; motion stops at boundary; no overlaid callout.|
|7.1|Pass: first request reaches band before new flow appears; queue label has its own space.|
|7.2|Pass: empty queue/lease cleanup distinction retained, neutral context readable.|
|8.0|Pass: detector components and pool-score context remain readable in both themes.|
|8.1|Pass: wait gate visibly blocks and banner has reserved space.|
|8.2|Pass: eligible release uses shared dispatch route from selected band.|
|8.3|Pass: high/low eligibility remains separate; shared route avoids false band ownership.|
|8.4|Pass: endpoint filtering and chosen B remain traceable from high band.|
|9.0|Pass: highest eligible queue and one-per-cycle caption are clear, labels uncovered.|
|9.1|Pass: empty higher band and selected lower band route agree.|
|9.2|Pass: all empty labels remain inside their flow cell; batch selection uses shared dispatch route.|
|9.3|Pass: all five execution phases reviewed; actor boxes separate from metrics, completion/control routes clear, eviction prose folded.|
|10.0|Pass: same-band turn selection and shared dispatch route agree.|
|10.1|Pass: bounded queue dots and reserved overflow space prevent tenant collisions.|
|10.2|Pass: turn labels fit, and equal turns remain distinct from completion latency.|
|11.0|Pass: premium measured card and separate illustration context remain clear.|
|11.1|Pass: standard/ratio measured-card labels and source hierarchy retained.|
|11.2|Pass: metric-boundary copy remains readable and does not convert illustration into telemetry.|
|12.0|Pass: FC-off measured rejection card retains clear axis/value/source layout.|
|12.1|Pass: FC-on zero bar remains zero width; no metric change.|
|12.2|Pass: comparison boundary remains clear and separate from illustration.|
|13.0|Pass: per-flow overflow/status reservation prevents the earlier next-tenant collision.|
|13.1|Pass: measured latency card retained; illustrated flow counts fit.|
|13.2|Pass: linked evidence scope retained; context readable.|
|14.0|Pass: explicit merge/trunk/fan-out; counters and controls stay readable; illustrative replay boundary retained.|

Optional siblings: all8 detector configurations, both band choices, all5 policy choices and all5 execution phases received native group review. The final spacing/route changes were rechecked on affected execution siblings and representative full-page overview/mechanism/result/replay pages. No export link removal, replacement evidence page, metric edit or benchmark run was performed by this reviewer.

### Final batch-input motion delta — 82574a8c

Final effective reviewed SHA256: `82574a8c202ce97355f1a31b605dcb9e4dfd7e471bced6d616153625d8f8dedb`. Only12.0 input animation changed after cb5aab1a: the old across-gateway polyline is replaced by batch-curve→gateway-left and gateway-right→EPP-boundary hops. All other cb5aab1a geometry is unchanged.

Twelve fresh normal-motion captures (250,650,1000,1450,2100 and3000ms, both themes) have matching start/end hashes and no page errors. Evidence: `/tmp/learner-adversarial-20260917/batch-delta/report.json`; native crops `light-650-native.png`, `light-1000-native.png`, `dark-1450-native.png` and `dark-2100-native.png` show dots at the gateway ports and EPP boundary, with gateway title/proxy/gRPC text uncovered. Settled captures retain only the underlying route.

**Final disposition remains pass for this bounded review; no unresolved actionable blocker.**

## Reopened arrows and narration — latest review

User feedback on published `603693c`, page 6, supersedes the earlier blanket arrow-formatting pass. Reviewed source: `83f7191fe58963acbc6867dc40254f341f1bcfc506a279566227288b46552847`, based on main `6b23628` with its recording and evidence-layout changes preserved.

### Findings and corrections

- The earlier checks traced routes but did not compare marker paint with line paint. Node highlighting recolored stems while arrowheads kept a gray fill. Marker sizes also scaled with stroke widths. Structural routes now remain neutral, and every arrow uses the same nine-unit geometry with explicit matching paint. Endpoint branches finish with a straight approach to the port.
- An initial `context-stroke` implementation passed Chromium but painted black in WebKit. It was replaced before delivery by explicitly colored markers sharing one geometry. Native rendering was rechecked in both engines.
- The narration panel now uses a fixed 280px grid with a stable button row. Evidence columns preserve the same width. On narrow screens, narration precedes evidence so differing chart heights do not move Next within a topic.
- Visible step/type metadata is replaced by progress dots. Screen-reader metadata remains available. Next keeps a consistent label. Keyboard shortcuts use a native disclosure with labeled rows.
- All 34 guided narratives and optional detector/policy narratives were reviewed for concise, complete thoughts. The objective explanation now reads: “The Endpoint Picker finds the pool-bound InferenceObjective named in the header. The request receives its configured priority: 100.”

### Verification

- Navigation/marker regression: 324 guided and optional states per engine in Chromium and WebKit, at 1844/1440/375 widths in both themes. Checks cover marker dimensions and computed paint, neutral connectors, panel/button geometry, unclipped prose, within-topic Next position and keyboard disclosure behavior.
- Full browser regression: 158 scene/detail/option views and 34 normal-motion steps passed on the final source. Local receipt: `/tmp/learner-arrow-review-20260917/delivery-regression/report.json`.
- Recording adapter: all 34 guided views, replay controls, clean-frame Escape, deep links and normal-view isolation passed on the final source. Its test now reads lesson state instead of relying on the old “Next page” label.
- Independent review: all 34 guided steps and 20 options inspected in the 228-state candidate pack. An 80-state final delta pass in Chromium/WebKit, both themes and desktop/narrow widths checked marker paint and mobile evidence order. No unresolved actionable blocker. All nine mobile evidence steps held card top 157px / Next top 384px in that review.
- Independent semantic review found no material change. Canonical evidence, pressure arithmetic and replay calculations are byte-identical to main `6b23628`. No benchmark traffic was run.

The full regression ran after the final source freeze. Earlier captures whose source changed during review are not the delivery receipt. User acceptance and production deployment remain separate from these local checks.

### References used

- [SVG marker units](https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/markerUnits): explicit user-space dimensions avoid stroke-dependent head sizes.
- [WAI disclosure pattern](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/): keyboard-accessible show/hide controls for secondary help.
- [Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md): semantic controls, visible focus and overflow checks.

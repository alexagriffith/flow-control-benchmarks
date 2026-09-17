# Independent learner comprehension and rendered UI review

## Scope and evidence

Reviewed all 34 existing guided steps and all current optional detector, policy, priority-band and execution selections. No new detector Learn tab was built. This is an independent review of the existing visual framework, not a redesign or a benchmark run.

Baseline learner SHA-256: `9e42f02c11f3d7c455ac1adcf062829aeac0616422bfc3686e883f1744c13e85`.

Baseline companion SHA-256: `c16f17f18d8f4fe7f9e08fbc8cd06bbf049eb037d0c8e484b9b19b8c1c47c361`.

Evidence used:

- All 34 baseline guided steps were rendered in one loaded browser page at 1844 × 1265, normal motion, after 3.9 seconds per step. These captures remain bound to the baseline even though the source changed later on disk.
- The exact-baseline 158-view pass supplied 1440 and 375 screenshots of all steps, 13 expanded-detail views, 20 optional selection states, and narrow right-side captures. These were visually inspected in labeled contact sheets, with full-size views for specific findings.
- Narrow layouts deliberately preserve the connected diagram in a horizontally scrollable viewport. Narrative and controls stack below it. The scroll hint is visible. This review inspected both ends for views with relevant right-side content; it does not claim the entire diagram fits simultaneously on a 375-pixel screen.
- A prior exact-baseline independent replay review checked 8 width/theme/motion combinations, actual request-path geometry, zero/max-load counters, settings keyboard operation, pause/resume, reset, and FC-mode restart.
- A final targeted review follows the six concrete findings below. Its hashes and outcomes are recorded in the final-delta section. Dark samples from the first all-view capture were loaded after source edits began and are excluded from final acceptance evidence.

Local screenshot paths are diagnostic evidence, not published links. The companion was reviewed for the changed qualitative illustration; the separate technical reviewer owns current-source policy verification.

## Guided views

Step identifiers below are zero-based `page.step`. “Clear” means the visible answer, narrative and emphasis agree; it is not a claim that the illustration proves production behavior. Findings reference the issue list below.

| View | Question answered and visible evidence | Readability / comprehension result |
|---|---|---|
| 0.0 | Why share capacity? Dedicated and shared capacity cells contrast idle allocation with usable shared capacity. | Clear side-by-side overview, stable tier colors and bounded copy. |
| 1.0 | What happens at a burst? Arrival wave crosses the capacity boundary and excess requests wait. | F1 closed: peak occupancy and waiting now agree. |
| 2.0 | Where does control start? Measured latency knee and schematic signal/threshold view distinguish evidence from mechanism. | F2 closed: labels clear plotted lines and threshold. |
| 3.0 | Why does shared traffic need policy? Premium, standard and batch sources meet at the shared pool. | Clear three-tier progression; pressure illustration is identified as such. |
| 4.0 | What is fairness within one band? Tenant queues and rotating cursor show dispatch turns. | Clear same-band scope, without promising equal tokens or latency. |
| 5.0 | What does flow control contribute? Three concise visual summaries connect waiting, policy and shared-capacity use. | Clear summary, suitable density for an overview. |
| 6.0 | Who supplies trusted identity? Caller traffic passes the inference gateway; labels appear at the gateway boundary. | Clear ownership; identity/budget details remain optional. |
| 6.1 | How does a matched objective affect routing policy? Objective lookup supplies priority 100. | Clear matched branch and band destination. |
| 6.2 | What happens without an objective match? Fallback produces priority 0 and the default fairness key. | Clear fallback; no implication that every request is premium. |
| 7.0 | What identifies a flow? Fairness key plus priority defines the queue identity. | Clear tenant × priority relationship and highlighted lookup/admission. |
| 7.1 | When is a flow created? First request creates a new tenant flow and queue entry. | F4 closed: new label and dot are unobstructed. |
| 7.2 | When is a flow collected? An empty, unreferenced flow can age out. | F3 closed: empty flow and released-lease timeout are explicit. |
| 8.0 | What signal controls dispatch? Selected utilization detector produces endpoint scores and combined mean. | Clear normalized-pressure relationship; detector details optional. |
| 8.1 | Why wait? Pool pressure reaches the selected 1.0 ceiling. | Clear gate with waiting work; no new hardware-capacity guarantee. |
| 8.2 | Why can dispatch resume? Pressure falls and remaining eligible work can dispatch. | Clear recovery transition; no stalled state in the reviewed sequence. |
| 8.3 | Is pool headroom enough for every priority? Pool 170/200 = 0.85 is compared with priority ceilings. | Clear separation of pool pressure and priority eligibility. |
| 8.4 | Which endpoint remains eligible? Endpoint A at 110 is excluded while endpoint B at 60 remains available. | Clear endpoint filter following the pool check. |
| 9.0 | How much does one cycle dispatch? Highest eligible work yields at most one request in the cycle. | Clear cursor and request movement; scope stated. |
| 9.1 | What if the highest band is empty? Scan skips the empty high band and reaches priority 0. | Clear empty/skip/selected relationship. |
| 9.2 | When does batch get a turn? Lower-priority work dispatches after higher eligible bands have no work. | Clear qualifier that sustained higher-priority demand can keep batch waiting. |
| 9.3 | What follows dispatch? Router wait → endpoint dispatch → engine waiting/processing → client completion. | Clear actors and completion return route. Optional eviction is a separate cancel/recover control path. |
| 10.0 | What is a fair turn? Three eligible same-band flows rotate one dispatch each. | Clear rotation; higher-priority work shown drained. |
| 10.1 | What changes when one tenant bursts? Its queue grows while one-turn-per-rotation behavior stays visible. | F5 closed: shorter turn labels fit inside the band. |
| 10.2 | What becomes equal? Dispatch turns level out; completion, tokens and latency can differ. | F5 closed: third turn label fits inside the band. |
| 11.0 | What did the service-tier run measure? Premium p95 TTFT median and repeat range are displayed. | Clear measured card, units, source and scope; diagram remains illustrative. |
| 11.1 | How did standard compare? Standard 1406 ms vs premium 1117 ms, about 1.26×. | Clear comparison with explicit no-sole-cause caveat. |
| 11.2 | What does TTFT include? Time to first token includes more than router waiting. | Clear metric boundary and median-of-repeat-p95 explanation. |
| 12.0 | What happened with flow control off? Historical batch run shows 48,224 HTTP 429 rejections. | Clear count and historical provenance. |
| 12.1 | What happened with flow control on? Same closed-loop concurrency schedule shows zero rejections. | Clear difference and explicit offered-request-total caveat. |
| 12.2 | What does this comparison establish? Queueing changes closed-loop demand; rejection counts do not establish equal-arrival throughput gain. | Clear comparison boundary; queue budget/expiry are separate controls. |
| 13.0 | What did the big burst compare? Three tenants in priority 100, measured p95 TTFT. | Clear same-band comparison with source. |
| 13.1 | How large was the observed effect? Tenant p95 TTFT values 943/877/893 ms. | Clear modest-effect wording; no queue-bound or SLO guarantee. |
| 13.2 | Where is later evidence? Current learner links to later package-specific exports and scope notes. | Clear version separation; export links preserved. |
| 14.0 | Can the learner explore load and waiting? Tier load controls, explicit queues/pods, request dots and counter table support replay. | Clear simplified routes and visible queue contention. See exact-baseline replay controls/geometry checks and final disclosure-label review. |

## Optional views and controls

| Views inspected | Visible meaning / result |
|---|---|
| Threshold calibration detail (2.0) | Calibration context stays attached to the measured-versus-schematic threshold lesson. |
| Identity, budgets and outcomes (6.0, 6.2, 8.2, 12.2) | Identity/trust, waiting, rejection, expiry, cancellation and eviction are distinguished. Tables fit their container; details remain collapsible. |
| Detector: requests, combined | Request counts produce endpoint scores and combined mean; names and normalization agree. |
| Detector: requests, P/D | Same detector per stage, larger stage score controls; dashed relationship is pressure, not request traffic. |
| Detector: tokens, combined | Token-based normalized endpoint scores are explicitly selected. |
| Detector: tokens, P/D | Stage aggregation and max selection remain visible; no request arrow is repurposed. |
| Detector: hybrid, combined | Selected hybrid estimate is named and formula remains optional. |
| Detector: hybrid, P/D | Per-stage hybrid score and larger-stage gate remain distinguishable. |
| Detector: utilization, combined | Waiting/KV signal normalization and mean are described consistently. |
| Detector: utilization, P/D | Same utilization detector applied to both stages; maximum is explicit. |
| Priority band: high | Pressure 0.85 below high ceiling 1.0 permits eligibility, subject to endpoint filtering. |
| Priority band: low | Pressure 0.85 exceeds low ceiling 0.8, so low-priority work waits. |
| Policy: fairness | Eligible nonempty flows in one band rotate in the selected example; current source default is distinguished. |
| Policy: FCFS | Older logical enqueue time wins in the selected compatible global-strict comparison. |
| Policy: EDF / TTL | Nearer effective queue expiry wins; queue expiry itself is separate. |
| Policy: TTFT deadline | Nearer valid metadata deadline affects ordering; no deadline or TTFT guarantee. |
| Policy: ceilings | F6 closed: low queue/meter emphasized; switching back restores fairness. |
| Execution: queued | Request remains in router queue. |
| Execution: dispatch | Request travels to selected endpoint. |
| Execution: engine | Engine waiting/processing is distinct from router waiting. |
| Execution: complete | Result returns endpoint → gateway → client. |
| Execution: evict | Cancel/recover path targets an eligible shed-able victim, with separate endpoint identity and retry/application caveat. |
| Later-evidence disclosure | Package exports remain linked; evidence scopes and versions are explicit. |
| Settings and replay | Native disclosure/keyboard operation, pause/resume, step/reset, same-seed mode changes and counter geometry were tested in the exact-baseline independent V1 pass. User-facing replay comparison label receives final-delta check. |

## Findings and requested corrections

1. **F1 — burst occupancy consistency.** At the overflowing peak, baseline showed waiting and “pool full at peak” with roughly 40 of 48 capacity tiles filled. Derive wave boundary, tile occupancy and overflow from one normalized pressure. Preserve this lesson's existing capacity model.
2. **F2 — threshold chart label placement.** Baseline “latency keeps climbing” crossed the rising line, and “967 ms” met the nearby red segment. Move labels into nearby empty chart space.
3. **F3 — idle collection preconditions.** Baseline removed a flow with one queued request, implying stalled arrivals permit work loss. Depict empty queue, released leases and timeout before removal. Distinguish flow leases from engine completion.
4. **F4 — new-flow event hidden.** Baseline “new flow appears” callout covered the very label and dot it explained. Remove the redundant callout or move it away from the event.
5. **F5 — turn-label fit.** Baseline third tenant dispatched-count label reached the band boundary. Shorten consistently while retaining the narration's dispatch-turn meaning.
6. **F6 — ceiling selection emphasis.** Baseline ceiling selection retained fairness highlights on an emptied band. Highlight low-priority waiting and pool pressure, then restore fairness state on switching back.

## Final-delta review

**Result: all six original findings closed. No unresolved blocker from this independent comprehension/UI review.**

Final learner SHA-256: `fc62d90f2676dde8449a6034caac64bc1d82d3b60474b2ec05218b5cd5d3f29f`.

Final companion SHA-256: `7c63335c37c57ebaef3ca3169a91628bcb9e507b8afe88b8b1f5c3fe0eb50e7e`.

The targeted pass captured 56 corrected/adjacent selections plus narrow right-side views at 1844 and 375 in light normal-motion and dark reduced-motion modes. It found no page errors or document-width overflow. The first revision was `a690409d…`; during that pass the parent made a small additional chart-label move. A final frozen pass of the chart, early idle state and ceilings → fairness transition used `fc62d90f…` unchanged from start to finish. Thus the broad pass is revision evidence plus explicitly rechecked final deltas, not a falsely claimed single-hash run.

| Finding | Final observed result |
|---|---|
| F1 | Overflowing burst peak now fills all 48 capacity tiles and shows waiting outside. Wave, occupied cells and overflow use one capacity boundary. |
| F2 | 967 ms and p95 TTFT labels clear the plotted segments. The right latency caption clears both the rising curve and, after the final 10-SVG-pixel move, the vertical threshold. Confirmed in final full-size light/dark renders. |
| F3 | Before collection the tenant-new flow is explicitly idle with zero request dots. Its label disappears after the timeout. Copy states empty flow, released last lease and idle timeout; leases mean references to the flow. |
| F4 | The new-flow label and request dot remain unobstructed. The redundant callout is gone. |
| F5 | The rightmost tenant label now says “tenant c · 4 turns” and fits within the priority band; burst/steady siblings retain their visible queued work. |
| F6 | Ceiling selection emphasizes the low-priority queue, current pool meter and endpoints. Switching back restores the fairness band, neutral eligible endpoint border and normal pressure. The comparison evidence card is cleared. |

A final transition check found that the old halted banner briefly faded through a false comparison after switching to eligible work. The parent corrected this as an extension of F6: eligible states immediately hide and empty the banner. Final settled checks at both widths and themes showed `halted=false`, banner opacity 0, and neutral endpoint border. The apparent red endpoint border in the earlier 100 ms screenshot was the existing 0.45-second color transition, not persistent stale filtering.

The replay disclosure now reads **“How to compare on/off”**, which describes the help it opens. Its controls/counters retain their previously verified layout. No new detector Learn section was added. The companion's changed caption says longer prompts and replies can require more compute and KV-cache capacity. It explicitly labels the bar lengths as schematic.

Local diagnostic evidence:

- `/tmp/learner-allviews-independent/final-deltas/report.json` — corrected/adjacent view inventory, errors and overflow results.
- `/tmp/learner-allviews-independent/final-deltas/transitions.json` — frozen final hash and four settled transition checks.
- `/tmp/learner-allviews-independent/final-deltas/1844-dark-chart-final.png` — final chart-label placement.
- `/tmp/learner-allviews-independent/final-deltas/1844-light-idle-before.png` — empty idle flow before collection.
- `/tmp/learner-allviews-independent/final-deltas/1844-light-scene-01-0.png` — full pool plus burst overflow.
- `/tmp/learner-allviews-independent/final-deltas/1844-light-scene-10-2.png` — all turn labels inside the band.
- `/tmp/learner-allviews-independent/final-deltas/375-light-fairness-settled.png` — narrow policy reset without stale banner.

No benchmark metrics were changed or benchmarks run by this reviewer. This receipt records local review; commit, deployment and user approval are separate actions.

## Reopened evidence-card formatting — September 17

The earlier all-view pass missed a component-level collision. On the published learner, the absolutely positioned maximum-value label overlapped the first evidence bar by 7 CSS pixels at both 1844 and 375 viewport widths. The previous full-page and overflow checks did not establish that the children inside each card were readable.

The revision gives the badge, heading, scale, labels and bars separate layout rows. Labels and values sit above full-width tracks. Sources and methods remain accessible in a readable disclosure, with the campaign identity visible when collapsed. A zero-event series now draws a zero-width bar. Recorded values, methods and source links are unchanged.

Final learner SHA-256: `dbc3fd7b5c8728acdbe87bb8e4d74e9bbdb015c57f623f2c8a3bbdfe0442560e`.

Validation:

- 19 scene/replay unit tests passed.
- `pipeline/validate_learner_evidence.py`: 68 shared-card states across 1844/375 widths and both themes, with sources closed and expanded. Checks sibling geometry, rather than only container overflow.
- Full learner browser validation: 158 scene/detail/option views and all 34 normal-motion steps passed. The full pass used `09bef1164c8bf2834e01aed30fa191675a1a687e4bdbb525ed17d0b8c1ec1edc`, recorded in `/tmp/website-queue-20260916/overlap-release-final/report.json`. The final delta only moves the green chart caption away from the threshold. Targeted final-source checks cover both chart captions and all shared cards.
- Settings, counter stability, plot-label clearance, policy state and flow cleanup checks passed.
- Independent source review confirmed unchanged metrics, methods, claims and source/export links. Visible measured badges and illustrative endpoint labels still distinguish evidence from the diagram.

The original failing layout is retained under `/tmp/learner-evidence-review-20260917/` to verify that the new overlap check rejects it. Independent visual review checks each card at native size, then its containing sidebar, then the whole page.

The chart review also found a delayed line reveal despite reduced-motion settings. All four plotted paths now appear immediately in that mode. The green “throughput flattens” caption had touched the threshold by roughly 1 SVG pixel. It now shares the red caption's padded right alignment. Regression checks cover both captions against the plotted lines and threshold. These are rendering changes only.

Independent rendered review closed the reopened finding on final hash `dbc3fd7b…2560e`. The reviewer inspected native-size cards, containing groups and full pages. All shared-card states passed in light/dark and desktop/narrow views, including expanded sources and keyboard access. Final chart captures confirm immediate reduced-motion paths and more than 26 SVG pixels between the green caption and threshold. No remaining formatting blocker was found in this scope. Independent receipt: `/tmp/learner-evidence-review-20260917/REVIEW.md`.

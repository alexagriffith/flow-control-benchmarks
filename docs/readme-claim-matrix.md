# README claim matrix

This matrix maps each root README claim to its evidence, configuration, visual,
and public boundary.

Each visual shows a measured comparison with consistent units and direct
labels. Captions state the workload, configuration, repeats, and evidence source.

## Evidence levels

| Level | Meaning |
|---|---|
| Promoted | Strong enough for the top README. Needs one clean figure and one short caption. |
| Supporting | Useful in package READMEs or HTML detail pages. Link from the top README when relevant. |
| Boundary | Important because it explains where a claim stops. Keep visible, but do not make it sound like a failure. |
| Directional | Shows the pattern, but the exact point estimate should not be a headline. |
| Provenance | Keep for history, reproducibility, or context. Do not lead with it. |

## Promoted README claims

| README section | Claim | Evidence level | Suite | Test configuration | Strongest current evidence | Front-page visual plan | Caveat |
|---|---|---|---|---|---|---|---|
| Realtime stayed ahead during saturation | Flow control protects priority traffic under saturation. | Promoted | Upstream request-count admission and RHAII 3.4 saturation detector | Upstream request-count admission with `maxConcurrency=128`. RHAII 3.4 utilization detector with queue-depth threshold 4 for the corrected tier run. | Upstream priority tiers: platinum realtime p95 TTFT 404 ms, gold realtime 511 ms, silver standard 656 ms, bronze batch 13,264 ms. RHAII corrected tier run: premium 1,117 ms, standard 1,406 ms. | Latency isolation view. Same units, direct labels, comparator visible, no standalone raw value. | This proves priority behavior for the tested load and setup. This is not a full SLO proof. |
| Batch used shared capacity | Lower-priority work absorbs the wait. | Promoted with directional note | Upstream request-count admission, supported by RHAII 3.4 saturation detector | Upstream request-count admission. RHAII 3.4 utilization detector. | Upstream batch isolation: realtime 442 ms, standard 515 ms, batch 13,077 ms during the surge. RHAII batch run shows overload moved from rejection to queued work. | Workload-class isolation view. Emphasize realtime protected ahead of batch, with queue behavior linked below. | Upstream batch-isolation medians are useful for story shape, but exact point estimates had wider repeat spread. Avoid making request counts the headline. |
| Consolidation preserved realtime priority | Consolidation preserved realtime priority. | Promoted | Upstream request-count admission, supported by RHAII 3.4 saturation detector | Upstream request-count admission. RHAII 3.4 utilization detector. | Upstream consolidation: realtime tenant A 509 ms, realtime tenant B 556 ms, standard burst 25,892 ms. RHAII consolidation: premium 795 ms, standard 1,062 ms. | Shared-capacity outcome view. Pair latency isolation with utilization/cost caveat in caption, not inferred savings. | This is shared-pool evidence. Cost savings depend on workload mix, utilization targets, and service objectives. Do not frame as an image-version comparison. |
| Admission policy tuned latency | Admission tuning changes the latency tradeoff. | Promoted as tuning evidence | Upstream request-count admission | Upstream v0.9 was used because request-count admission was not available in the RHAII 3.4 scheduler image. | Consolidation matched comparison: request-count realtime p95 TTFT stayed around 509-556 ms; queue-depth detector realtime p95 TTFT was about 4.6-5.1 s. Same-priority comparison showed the same direction for peers. | Policy comparison view. Show matched settings and ranges together so the comparison is legible. | Matched detector comparisons exist for consolidation and same-priority fairness only. Do not claim request-count admission is universally best. |
| Batch already inside vLLM raised realtime latency | Running batch exposes the boundary of admission control. | Boundary | Upstream batch-interference baseline | Request-count admission, with reserved capacity and eviction disabled. | Batch-interference baseline: realtime p95 TTFT rose from 133 ms to 15,378 ms when batch work was already running inside vLLM. | Boundary view. Show the before/after condition plainly, with no feature-win framing. | Use this as the production case that motivates after-dispatch protection. This test did not measure reserved capacity or eviction. |
| Reserved capacity protected realtime latency | Reserved capacity protected realtime latency after batch entered vLLM. | Promoted | Batch-eviction tests | Experimental llm-d-router build with request-count admission, `maxConcurrency=48`, vLLM `max-num-seqs=96`, reserved capacity, eviction, and retry. The public package records the tested image provenance. | Single-model package: realtime p95 TTFT was 561 ms with batch and no protection, 341 ms with reserved capacity, and 348 ms with reserved capacity plus eviction and retry. Two-model package proved eviction and retry across two model replicas. | Protection view. Separate latency comparison from eviction/retry correctness. | Two-model latency scaling is inconclusive. Promote latency protection from reserved capacity and correctness for eviction/retry, not a scaling claim. |
| Select operating load from the latency objective | The latency objective selected an operating load below the measured overload boundary. | Promoted | RHAII 3.5 capacity envelope | p95 TTFT at or below 250 ms, p95 time per output token at or below 25 ms, request cap 128, and 10% headroom. | The 40.6 requests-per-second point passed all three accepted repeats. At 50.7 requests per second, p95 TTFT reached 6,273 ms. | Use the approved capacity-envelope chart with the claim and interpretation in the README. | The exploratory capacity sweep and three-repeat request-cap comparison are separate evidence groups. The operating point applies to the tested model, topology, request shape, and objectives. |
| Help queued requests meet their latency objectives | SLO deadline ordering increased the share of requests meeting their latency objectives within one flow. | Promoted, conditional | RHAII 3.5 SLO deadline ordering | One shared priority and fairness identity, with 250 ms, 500 ms, and no-objective requests. FCFS is the control. | Target attainment increased from 41.4% to 72.2% at 250 ms and from 70.4% to 90.6% at 500 ms. Every request completed and each class drained. | Use the approved FCFS versus SLO-ordering chart. | Callers must provide meaningful deadlines. Requests without an objective waited longer. The larger share meeting each objective did not establish a p95 TTFT pass for either objective. |
| P/D serving resists starvation across request shapes | Stage-aware admission, fairness, and priority reserve protect P/D serving across prefill-heavy and decode-heavy traffic. | Promoted recipe | RHAII 3.5 P/D flow control | Hybrid detector with request cap 64 and token cap 80,000; separate flow identities; round-robin fairness; three configured priorities 100, 0, and -10; rank-based ceilings 1.0, 0.75, and 0.5. A two-class service leaves the configured -10 band unused. | Hybrid saturation crossed the 0.90 qualification boundary for both traffic shapes. Equal-priority runs had no 60-second starvation interval. The priority-holdback recipe passed every protected run in both traffic blocks. | Use the compact root configuration table; keep detailed detector values in the campaign README. | The numerical limits apply to the tested GPT-OSS 20B same-node P/D topology and require local calibration elsewhere. |
| Measured capacity controls Batch entry | Metrics-gated queued dispatch keeps Batch outside serving until capacity is available. | Promoted, scoped | RHAII 3.5 Batch dispatch | Three counterbalanced blocks comparing fixed direct, 429-responsive direct, and metrics-gated queued dispatch. | The metrics-gated method met the realtime target in two of three blocks, compared with zero of three for fixed direct and one of three for 429-responsive direct. Every planned Batch request completed. | A compact table is sufficient on the root README; detailed timing and durability stay in the campaign package. | This result depends on retry-safe queued ownership, durable Redis, and startup reconciliation. |
| Eviction provides retry-safe overflow recovery | Eviction recovers capacity after eligible Batch work has entered serving. | Promoted reliability result | RHAII 3.5 Batch eviction rerun | Matched eviction-off/on pairs at 25% and 50% reserve with 20,000-token Batch jobs. | Eviction removed the realtime non-200 outcomes seen without eviction at both reserve levels, and every Batch job completed exactly once. Batch p95 completion increased 18.7% at 25% reserve and 9.4% at 50% reserve. | Keep reserve and eviction as separate controls in text; use the current paired result visuals where the full evidence is explained. | Eviction is for explicitly eligible, retry-owned work. It adds completion delay and recomputation and did not establish a stable TTFT improvement in this rerun. |

## Supporting claims for deeper pages

| Claim | Evidence level | Best home | Notes |
|---|---|---|---|
| Engine settings selected a practical operating point for the later scenarios. | Supporting | Upstream v0.9 package README or HTML detail page | Keep out of the top README unless a figure needs setup. This is calibration, not the main story. |
| Request-count admission is a good default for this one-GPU setup; token-aware admission matters when request sizes vary. | Supporting | Request/token admission package | Good tuning story. Too detailed for the top README unless the visual pass adds a small tradeoff figure. |
| Same-priority fairness prevents starvation within a priority band. | Supporting | Production scenarios package | Useful boundary. Do not make it sound like a separate low-latency lane. |
| The queue drained after repeated surges. | Supporting | Long-stability package | Good resilience evidence. One 30-minute run, so it should not be a top headline. |
| Per-GPU throughput stayed close from one to four replicas. | Supporting | Multi-replica scaling package | Keep in deeper evidence. The package recorded sparse non-200 responses, so avoid a clean scale-out headline. |
| Prefix-aware routing improved some traffic and worsened other signals in this workload. | Supporting | Prefix-cache routing package | Useful tradeoff story. Keep random routing as the control configuration for this workload. |
| Long-context exact-token admission activated the queue, but did not show a clear latency win. | Supporting | Long-context package | Good detector-boundary evidence. Do not promote as a win. |
| Fixed holdback provides stronger interactive protection; soft-reflective ceilings allow more lower-tier progress. | Supporting | RHAII 3.5 priority-usage-limit package | Useful configuration tradeoff. Keep the detailed values in the campaign README. |
| Request-cost metadata exactly represented completed usage-bearing requests. | Supporting | RHAII 3.5 request-cost-metadata package | This is a measurement signal for a later quota, chargeback, or provisioned-throughput layer. It does not enforce those policies. |
| A trusted external quota classifier can demote overage traffic while forwarding it. | Supporting | RHAII 3.5 soft-PT package | Keep in the campaign README and replay package. The root README does not need this result for the flow-control recipe. |

## Provenance and historical evidence

| Evidence | Level | Use |
|---|---|---|
| RHAII 3.4 corrected service-tier run | Promoted support | Use as the first-suite mechanism proof. |
| RHAII 3.4 batch run | Promoted support | Use to show the change from rejected work to queued work, but keep request counts out of headlines. |
| RHAII 3.4 older pooled percentile results | Provenance | Keep linked only where correction history matters. |
| Upstream request-concurrency priority tuning with two 120-second repeats | Provenance / supporting | Useful for the `maxConcurrency` shape. Do not promote as final detector proof. |
| Batch-eviction two-model latency comparison | Boundary | Use for topology mechanism proof. Do not use as a latency scaling headline. |

## Visual work queue

The generated README SVG renderer was removed. It produced valid charts, but
the layout quality was not strong enough for an open-source front page.

| Visual | Root README use | Standard |
|---|---|---|
| Architecture | Existing technical SVGs | Keep request routing, flow-control internals, and Batch job management visually separate. |
| Capacity objective | Approved capacity-envelope chart | Keep the axes and measured knee in the image; keep interpretation in the README. |
| SLO deadline ordering | Approved FCFS versus SLO-ordering chart | Spell out first-come, first-served on first use. Use direct labels, bounded axes, and no explanatory title inside the image. |
| P/D anti-starvation | Compact configuration table | A new image is optional. The table must keep detector, fairness, priority, and calibration roles distinct. |
| Batch dispatch | Compact result table | Explain the meaning of each dispatch method and each target count in the campaign README. |
| Batch already in vLLM | Existing boundary comparison | Keep the baseline condition separate from the feature result. |
| Reserved capacity and eviction | Existing paired result visuals | Separate normal reserve protection from retry-safe eviction recovery. |

README figure style: keep the claim text in the README heading and one short
line above the image. Keep the SVG focused on labels, values, and axis context.
Avoid embedded chart titles or explanatory subtitles inside the image.

Top README structure: put the headline visual under the introduction, then show
promoted takeaways. Keep detailed measured values in figures, captions, and
linked source data instead of repeating a middle measured-values column.

## Open decisions

| Decision | Recommendation |
|---|---|
| Should the top README include exact request counts? | No, except when explaining rejection behavior in a caption or evidence table. Counts mainly reflect run duration and offered load. |
| Should the top README include engine calibration? | Include only the latency-selected operating point and link the full sweep. |
| Should the top README include scale and prefix-routing results? | No for the first pass. They are useful follow-up findings, not the main flow-control story. |
| Should the top README compare images? | No. Name image and detector per suite, but frame the suites by use case. |
| Should the current figures block README review? | No. Review the story first, then redraw figures from data. |

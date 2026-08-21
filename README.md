# Flow Control Benchmarks for llm-d

Dedicated GPU pools protect latency-sensitive products, but peak-sized pools
sit idle when demand drops. Shared GPU capacity reduces that waste by running
mixed-priority and batch workloads on the same GPUs. The tradeoff is latency
isolation. Realtime products still need SLOs and service guarantees when other
workloads use the same serving capacity.

Flow control manages request admission for shared GPU capacity under pressure.
When the pool is overloaded, flow control applies configured priority and
fairness policy to determine which requests wait and which requests are
dequeued next. Batch eviction extends that control after dispatch by removing
eligible lower-priority batch work already running in vLLM, protecting realtime
latency when batch work has already consumed serving capacity.

The benchmark question is whether latency-sensitive, mixed-priority, and batch
workloads can share GPU capacity without lower-priority work crowding out
higher-priority work.

Each result section states its aggregation, repeat count, units, and evidence
source. The comparisons are descriptive; no formal statistical significance
is claimed. Most packages disable prefix caching.
The prefix-routing package enables caching because cache reuse is the behavior
under test.

<sub>Evidence [claim matrix](docs/readme-claim-matrix.md) · [priority tiers](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/) · [detector comparison](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/) · [batch eviction](benchmark-data/batch-eviction/)</sub>

## Architecture

### Router Request and Scheduling Path

<img src="assets/diagrams/llm-d-router-component-architecture.svg" width="100%" alt="The inference client sends an HTTP request to Envoy, which proxies directly to the selected vLLM replica. Envoy consults the Endpoint Picker over ExtProc. Inside the EPP, RequestControl processes the request through preparation, flow-control admission, candidate and data preparation, admission plugins, and scheduling.">

<details>
<summary><strong>Router and EPP component guide</strong></summary>

| Component | Role |
|---|---|
| Gateway / Envoy | Owns the HTTP connection, calls the EPP over ExtProc, and proxies the original request to the selected endpoint. |
| ExtProc API | Carries request data to the EPP and returns destination metadata to Envoy. |
| Request preparation | Parses the request, applies model rewrite and header processors, and builds the scheduling request. |
| Flow-control admission | Calls `EnqueueAndWait` and blocks until the request is dispatched or rejected. |
| Candidate + data | Locates eligible endpoints and runs per-request data producers. |
| Admission plugins | Applies request checks before scheduling. |
| Scheduler | Filters endpoints, scores the remaining candidates, and picks a destination. |
| vLLM replica | Serves the original HTTP request after Envoy applies the selected destination. |

</details>

### Flow-Control Request Process

<img src="assets/diagrams/llm-d-flow-control-request-process.svg" width="100%" alt="Queued work enters the Async Processor, which sends and retries requests through Envoy. Envoy proxies selected requests to vLLM and consults the Endpoint Picker over ExtProc. Inside the green EPP boundary, RequestControl prepares the request, admission applies its priority, FlowController queues it until dispatch or rejection, and the single-writer Processor manages dispatch. Dashed callouts outside the EPP boundary explain request lifecycle tracking, priority queues, dispatch limits, and in-flight eviction behavior.">

<details>
<summary><strong>Flow-control and Async Processor component guide</strong></summary>

| Component | Role |
|---|---|
| Message Queue | Holds queued asynchronous work before dispatch. |
| Async Processor | Owns request dispatch, retry, and exponential backoff. |
| Request Control | The `Director` prepares request context, invokes admission, and runs request lifecycle hooks. |
| Priority Admission | `FlowControlAdmissionController` adapts request control to the flow-control API. |
| Flow Controller | `FlowController` queues the request through `EnqueueAndWait` and waits for a terminal outcome. |
| Dispatch Processor | `Processor` serializes enqueue and dispatch decisions in a single-writer run loop. |
| Priority Queues | `FlowRegistry` maintains priority bands and per-flow queues; fairness selects a flow and ordering selects the request within it. |
| Dispatch Limits | Uses the saturation detector and usage-limit policy to decide whether a priority band may dispatch. |
| In-Flight Eviction | `ReclamationController` sizes and paces eviction; `RequestEvictor` tracks eligible work; `ImmediateResponseEvictor` signals Envoy through ExtProc. |
| Request Lifecycle | Registers dispatched work during `PreRequest` and removes tracking at the end of the response. |
| Gateway / Envoy | Returns the retryable response and resets the evicted vLLM stream. |
| vLLM Replica | Runs admitted work and releases an interrupted sequence after stream reset. |

</details>

### Flow-Control Queue Internals

<img src="assets/readme/epp-flow-control-queues.svg" width="100%" alt="Requests enter with an inference objective and fairness ID. The EPP resolves the objective to a priority and combines it with the fairness ID as a flow key. Flow control stores requests in priority bands with per-tenant queues. The Processor selects the highest priority band, a tenant queue, and its head request before applying the dispatch gate and handing the request to the Scheduler.">

## Results

| Takeaway | Evidence |
|---|---|
| Consolidation preserved realtime priority. | [Tenant consolidation](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/) |
| Flow control protects priority traffic under saturation. | [Priority tiers](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/) |
| Lower-priority work absorbs the wait. | [Batch isolation](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/) |
| Same-priority fairness prevents one tenant from starving its peers. | [Peer fairness](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/same-priority-fairness/) |
| Admission tuning changes the latency tradeoff. | [Detector comparison](benchmark-data/upstream-flow-control-v0.9.0/results.html#production) |
| Running batch exposes the boundary of admission control. | [Batch interference](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/) |
| Reserved capacity protected realtime latency after dispatch. | [Batch eviction](benchmark-data/batch-eviction/) |

## Evidence Map

| Suite | Test purpose | Configuration |
|---|---|---|
| [RHAII 3.4 flow control](benchmark-data/rhaii-3.4-flow-control/) | Saturation detector behavior under priority tiers, batch isolation, consolidation, and fairness tests. | Utilization detector with queue-depth saturation. Scheduler image is pinned in the package evidence. |
| [Upstream request-count admission](benchmark-data/upstream-flow-control-v0.9.0/) | Request-count and token-aware admission tests, including `maxConcurrency` tuning and production-shaped scenarios. | Upstream v0.9 was used because request-count admission was not available in the RHAII 3.4 scheduler image. |
| [Batch eviction](benchmark-data/batch-eviction/) | Reserved capacity for realtime latency, plus eviction and retry after lower-priority batch work has entered vLLM. | Experimental PR build with request-count admission, `maxConcurrency=48`, vLLM `max-num-seqs=96`, and batch eviction enabled. |

Each suite covers one test purpose in the larger story.

## Consolidation Preserved Realtime Priority

Multiple tenants used one GPU pool while realtime tenants stayed ahead.

<img src="assets/readme/consolidation-tenants.svg" width="100%" alt="Three panels show Realtime A, Realtime B, and both tenants together. Realtime A and B use separate fairness queues inside the Realtime priority band and share one vLLM instance; the plots show observed per-second traffic.">

<sub>Realtime tenants A and B shared one vLLM replica. Separate tenant queues kept both receiving dispatch turns.</sub>

<img src="assets/readme/consolidation-standard.svg" width="100%" alt="Two panels show Realtime Queue A and Queue B with Standard Queue C sharing one vLLM instance. During the Standard spike, the observed Standard queue backlog dominates while the Realtime queues remain much smaller; the plots show observed per-second traffic.">

<sub>Standard traffic then joined the shared replica and surged. Its lower-priority queue absorbed the backlog while the Realtime queues remained small.</sub>

<img src="assets/readme/consolidation.svg" width="100%" alt="During the consolidation surge, Realtime tenants A and B remained near 500 milliseconds p95 time to first token while the low-priority burst reached about 25,900 milliseconds">

<sub>During the surge, both Realtime tenants stayed near 500 ms p95 TTFT. The lower-priority workload absorbed the delay, reaching 25,892 ms.</sub>

<sub>Upstream v0.9 tenant consolidation, one H100, one model replica, request-count admission with `maxConcurrency=128`. n=3. Median p95 TTFT during the surge window; realtime tenant ranges were 503-558 ms and 505-599 ms. The lower-priority burst range was 22,833-27,307 ms.</sub>

<sub>Evidence [analysis.json](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/analysis.json) · [summary.csv](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/summary.csv) · [source folder](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/)</sub>

## Flow Control Protected Priority Traffic Under Saturation

Realtime and Standard tiers stayed below 1,000 ms while the Batch tier absorbed the wait.

<img src="assets/readme/priority-tiers.svg" width="100%" alt="Request-level latency distributions show Platinum, Gold, and Silver traffic clustered below 1,000 milliseconds while Bronze Batch is separated at 13,264 milliseconds p95 time to first token">

<sub>Upstream v0.9 priority tiers, one H100, one model replica, request-count admission with `maxConcurrency=128`. n=3.</sub>

<sub>Evidence [analysis.json](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/analysis.json) · [source folder](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/priority-tiers/)</sub>

## Lower-Priority Work Absorbed the Wait

Realtime and Standard stayed below 1,000 ms while Batch waited 13,077 ms.

<img src="assets/v09-tuning/batch-isolation-section.svg" width="100%" alt="Batch-isolation results show Realtime and Standard traffic below 1,000 milliseconds median surge p95 TTFT while Batch exceeds 13,000 milliseconds">

<img src="assets/readme/batch-isolation.svg" width="100%" alt="A time-aligned p95 TTFT plot shows Batch absorbing latency during the highlighted surge while Realtime and Standard remain far lower">

<sub>Upstream v0.9 batch isolation, one H100, one model replica, request-count admission with `maxConcurrency=128`. n=3. The wider repeat ranges make exact point estimates directional.</sub>

<sub>Evidence [analysis.json](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/analysis.json) · [source folder](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/)</sub>

## Admission Tuning Changes the Latency Tradeoff

Request-count admission lowered realtime latency in the matched consolidation
comparison. The calibration sweeps show why admission settings must match the
workload and pressure signal.

<img src="assets/readme/admission-policy.svg" width="100%" alt="A matched production comparison shows surge-window p95 TTFT for Realtime and low-priority traffic under request-count 128, queue-depth 2, and queue-depth 5 admission settings. A pale-green vertical band highlights request-count 128.">

<sub>Matched upstream v0.9 consolidation, one H100, one model replica. Request-count admission used `maxConcurrency=128` with 10% headroom; queue-depth thresholds were 2 and 5 waiting requests. Each setting used three repeats. Realtime combines tenants A and B; low priority is tenant C. Points are median surge-window p95 TTFT.</sub>

<sub>Evidence [analysis.json](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/analysis.json) · [summary.csv](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/summary.csv) · [source folder](benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/)</sub>

<img src="assets/readme/admission-calibration-profile.svg" width="100%" alt="Two separate calibration profiles show p95 TTFT across request caps 16, 32, 48, 64, 96, 128, and 160 and queue-depth thresholds 1, 2, 4, 5, and 8. Request cap 128 is ringed in green.">

<sub>Closed-loop upstream v0.9 calibrations, one H100, one model replica, prefix cache off. The request-count and queue-depth panels used separate workloads and independent millisecond axes. Selected boundaries have three matched runs; intermediate settings are single calibration points.</sub>

<sub>Evidence [request-count analysis.json](benchmark-data/upstream-flow-control-v0.9.0/request-and-token-admission-calibration/analysis.json) · [request-count summary.csv](benchmark-data/upstream-flow-control-v0.9.0/request-and-token-admission-calibration/summary.csv) · [queue-depth analysis.json](benchmark-data/upstream-flow-control-v0.9.0/utilization-detector-calibration/analysis.json) · [queue-depth summary.csv](benchmark-data/upstream-flow-control-v0.9.0/utilization-detector-calibration/summary.csv)</sub>

<img src="assets/readme/admission-request-tradeoff.svg" width="100%" alt="A connected scatterplot shows throughput and p95 TTFT for request caps 16 through 160. Cap 128 is ringed in green; cap 160 adds little throughput as p95 TTFT rises.">

<sub>Request-count calibration: cap 160 served 3% more steady throughput than cap 128, while median p95 TTFT increased by 363 ms (17%). Cap 128 was selected for later production tests. n=3 at caps 128 and 160; intermediate caps are single calibration points.</sub>

<sub>Evidence [analysis.json](benchmark-data/upstream-flow-control-v0.9.0/request-and-token-admission-calibration/analysis.json) · [summary.csv](benchmark-data/upstream-flow-control-v0.9.0/request-and-token-admission-calibration/summary.csv) · [source folder](benchmark-data/upstream-flow-control-v0.9.0/request-and-token-admission-calibration/)</sub>

| Policy signal | Appropriate when | Tested observation |
|---|---|---|
| Request count | Request sizes are similar and realtime latency is prioritized | Lowest realtime latency in the matched production comparisons |
| Input tokens | Prompt sizes differ materially | Lower long-context and batch latency in the mixed workload |
| Queue depth | Backend waiting-queue growth is the intended signal | Reactive; responded later in the matched consolidation test |
| KV-cache pressure | Memory pressure is the limiting condition | Verified activation, but not a latency win in calibration |

## Running Batch Exposes the Boundary of Admission Control

Realtime p95 TTFT increased from 133 ms to 15,378 ms when batch work already occupied vLLM capacity.

<img src="assets/readme/batch-interference-latency.svg" width="100%" alt="Realtime p95 TTFT remains low without Batch and rises when Realtime runs with Batch already active">

<sub>Surge window: 20-100 s elapsed. The reported median p95 TTFT values use this interval.</sub>

<img src="assets/readme/batch-interference-traffic.svg" width="100%" alt="Matched traffic comparison showing Realtime alone and Realtime running with concurrent Batch traffic">

<sub>The matched Realtime arrival pattern was unchanged; Batch was already active in the second run.</sub>

<sub>Upstream v0.9 batch-interference baseline, one H100, one model replica, request-count admission with `maxConcurrency=128`, reserved capacity not configured, eviction not configured. n=3. Median p95 TTFT during the surge window; ranges were 133-137 ms for realtime only and 15,168-15,919 ms with batch already running. This test motivates after-dispatch protection; this test did not measure reserved capacity or eviction.</sub>

<sub>Evidence [analysis.json](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/analysis.json) · [summary.csv](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/summary.csv) · [source folder](benchmark-data/upstream-flow-control-v0.9.0/batch-interference/)</sub>

## Reserved Capacity Protected Realtime Latency After Batch Entered vLLM

In the PR-image single-model test, reserved capacity returned realtime p95 TTFT
to the realtime-only range. Adding eviction kept latency in that range and
retried eligible Batch work already running in vLLM.

<img src="assets/readme/batch-protection.svg" width="100%" alt="Three independent bars show Realtime p95 TTFT at 342 milliseconds without Batch, 561 milliseconds with Batch and no controls, and 348 milliseconds with Batch, reserved capacity, and eviction">

<sub>Batch-eviction PR image, one H100, one model replica, request-count admission with `maxConcurrency=48`, vLLM `max-num-seqs=96`. n=3. Median p95 TTFT ranges were 318-345 ms for realtime only, 533-641 ms with Batch and no controls, 324-344 ms with reserved capacity, and 324-472 ms with reserved capacity plus eviction and retry. The two-model package shows eviction and retry across two model replicas; the two-model latency comparison is inconclusive.</sub>

<img src="assets/readme/batch-eviction-mechanism.svg" width="100%" alt="Sequence diagram showing the request queue feeding Batch to Async Processor, EPP selecting the in-flight Batch stream for eviction, Envoy returning HTTP 429 and resetting vLLM, Realtime using the released capacity, and the retry schedule returning Batch to the original request queue for another attempt">

<sub>EPP selects the in-flight Batch request and constructs the 429. Envoy returns it to Async Processor and resets the vLLM stream. Async Processor schedules the retry, then consumes it again from the original request queue.</sub>

In the single-model proof, all 38 evicted Batch requests were retried; all
5,376 Batch jobs completed with zero duplicate results. Across two model
replicas, all 57 eligible evictions were retried once and produced one final
result.

<sub>Evidence [single-model summary.csv](benchmark-data/batch-eviction/single-model-replica/summary.csv) · [two-model analysis.json](benchmark-data/batch-eviction/two-model-replicas/analysis.json) · [source folder](benchmark-data/batch-eviction/)</sub>

## Claim Boundaries

| Claim | Boundary |
|---|---|
| Flow control protected higher-priority traffic under saturation. | The result applies to the tested model, hardware, traffic shape, detector, and settings. |
| Lower-priority work waited instead of crowding out realtime traffic. | Queue limits, retry behavior, and capacity planning still matter. |
| Consolidation preserved realtime priority. | Cost savings depend on workload mix, utilization targets, and the platform team's service objectives. |
| Admission settings changed where latency landed. | Admission policy controls request ordering. Serving capacity still comes from vLLM and the GPU pool. |
| Reserved capacity protected realtime traffic after batch work had entered vLLM. | The single-model data shows latency protection at the tested load. The two-model data shows eviction and retry across model replicas, not latency scaling. |

The `slo_proof_valid` flag in package analysis means the scenario passed the
benchmark data-quality gate. A production SLO proof remains separate. A full
SLO proof needs a named target, declared load, success-rate target, end-to-end
latency, time per output token, backend pressure metrics, and enough repeats
for the claim being made.

## Evidence Links

| Topic | Link |
|---|---|
| Combined evidence page | [benchmark-data/results.html](benchmark-data/results.html) |
| RHAII 3.4 saturation detector | [benchmark-data/rhaii-3.4-flow-control/](benchmark-data/rhaii-3.4-flow-control/) |
| Upstream v0.9 tuning and scenarios | [benchmark-data/upstream-flow-control-v0.9.0/](benchmark-data/upstream-flow-control-v0.9.0/) |
| Batch eviction | [benchmark-data/batch-eviction/](benchmark-data/batch-eviction/) |
| Claim matrix | [docs/readme-claim-matrix.md](docs/readme-claim-matrix.md) |
| Runner and reproduction | [pipeline/README.md](pipeline/README.md) |
| SLO proof protocol | [docs/slo-proof-test.md](docs/slo-proof-test.md) |
| Benchmark | [benchmark.html](benchmark.html) |
| Flow-control guide | [learn/flow-control.html](learn/flow-control.html) |
| Interactive journey | [learn/flow-control-journey.html](learn/flow-control-journey.html) |
| Flow Control Flight Recorder | [flow-control-visualizer](https://github.com/alexagriffith/flow-control-visualizer) |

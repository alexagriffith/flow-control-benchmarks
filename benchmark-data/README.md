# Benchmark data

Promoted results are grouped by the image and capability they test:

- [`rhaii-3.4-flow-control/`](rhaii-3.4-flow-control/) contains the Red Hat AI Inference 3.4 capacity curve, priority tiers, batch isolation, consolidation, fairness, and multi-replica evidence.
- [`upstream-flow-control-v0.9.0/`](upstream-flow-control-v0.9.0/) contains the stable upstream v0.9.0 calibration and production evidence.
- [`upstream-concurrency-detector-sweep/`](upstream-concurrency-detector-sweep/) contains an earlier unversioned `maxConcurrency` tuning curve.
- [`batch-eviction/`](batch-eviction/) contains separate one-model and two-model packages for reserved capacity, batch eviction, and retry.

Exact Endpoint Picker and model-server images belong in each package's run
metadata. Results from different images are compared only when traffic, model,
engine settings, detector settings, and repeats are matched.

## Comparison status

| Comparison | Status | Reason |
|---|---|---|
| Flow control on versus off within the RHAII 3.4 scenarios | Supported | Each scenario keeps the model, traffic, and engine settings fixed. |
| Admission methods within Endpoint Picker v0.9.0 | Supported where the package uses the same deterministic trace and three repeats | The detector or limit is the changed variable. |
| RHAII 3.4 versus Endpoint Picker v0.9.0 | Not established | The existing campaigns use different arrival methods and configurations. A direct image comparison still requires one matched rerun. |

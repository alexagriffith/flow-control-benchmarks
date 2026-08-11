# Benchmark data

Promoted results are grouped by the capability they test:

- [`core-flow-control/`](core-flow-control/) contains the capacity curve, priority tiers, batch isolation, consolidation, fairness, detector sweep, and multi-replica evidence.
- [`batch-eviction/`](batch-eviction/) contains separate one-model and two-model packages for reserved capacity, batch eviction, and retry.

Exact Endpoint Picker and model-server images belong in each package's run
metadata. New public data should be added to the capability it tests.

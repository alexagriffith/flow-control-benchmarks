# Core flow-control benchmarks

This package contains the published flow-control baseline, production scenarios,
capacity curve, detector sweep, and multi-replica evidence.

Most scenario runs use the shipped RHOAI scheduler flow-control image with the
utilization detector. [`upstream-sweep/`](upstream-sweep/) is the separate
concurrency-detector comparison. Each run directory contains its benchmark
configuration and measured output.

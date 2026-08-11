# Red Hat AI Inference 3.4 flow-control benchmarks

This package contains the capacity curve and production scenarios used by the
main README. The runs used the Red Hat AI Inference 3.4 Tech Preview scheduler
image from `registry.redhat.io/rhoai/odh-llm-d-inference-scheduler-rhel9` with
its utilization detector.

The primary scenarios use a queue-depth threshold of 4, GPT-OSS 20B on one
H100, and prefix caching off. Each run directory includes its benchmark
configuration and measured output.

The separately installed upstream concurrency detector is documented in
[`../upstream-concurrency-detector-sweep/`](../upstream-concurrency-detector-sweep/).
Changing both the image and detector means those results do not isolate an
image-version difference.

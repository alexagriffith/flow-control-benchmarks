# Red Hat AI Inference 3.4 flow-control benchmarks

This package contains the capacity curve and production scenarios used by the
main README. The runs used the Red Hat AI Inference 3.4 Tech Preview scheduler
image from `registry.redhat.io/rhoai/odh-llm-d-inference-scheduler-rhel9`
pinned to digest
`sha256:bddf686d6eaf1a607e1c697f58165d685944607cb4629648f27e56b2884e3de0`
with the utilization detector.

[Read the original campaign overview](campaign-overview.md). It preserves the
README narrative and generated visuals published with this benchmark package.

The primary scenarios use a queue-depth threshold of 4, GPT-OSS 20B on one
H100, and prefix caching off. Each run directory includes its benchmark
configuration and measured output.

The separately installed upstream concurrency detector is documented in
[`../upstream-flow-control-v0.9.0/request-concurrency-priority-tuning/`](../upstream-flow-control-v0.9.0/request-concurrency-priority-tuning/).
Changing both the image and detector means those results do not isolate an
image-version difference.

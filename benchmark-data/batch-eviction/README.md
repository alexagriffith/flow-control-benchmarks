# Batch eviction benchmark data

These packages test whether lower-priority batch work can share model capacity
without compromising higher-priority realtime traffic. They cover two deployment
topologies and keep each claim tied to the evidence that supports it.

## Business question

Can reserved capacity and eviction protect realtime traffic after batch work
has entered vLLM?

**Answer.** Reserved capacity protected realtime p95 TTFT, and eviction safely
released and retried eligible batch work in the tested one- and two-model-replica
topologies.

| Package | Topology | What it establishes |
|---|---|---|
| [`single-model-replica/`](single-model-replica/) | One Endpoint Picker and one model replica | Reserved capacity protected realtime p95 TTFT. Evicted batch work was retried and produced one final result. |
| [`two-model-replicas/`](two-model-replicas/) | One Endpoint Picker and two model replicas | Eviction and retry worked across both model replicas. The latency scaling comparison remains inconclusive. |

Each package includes its configuration, run summary, request-level evidence,
claim boundary, tested architecture diagram, and data-bound result plots. The repository README and shared charts are updated only
after a package passes its data and public-content reviews.

## Verify the published packages

```bash
python3 pipeline/generate_package_configs.py --check
python3 pipeline/generate_package_visuals.py --check
python3 pipeline/validate_batch_eviction_packages.py
```

These commands regenerate or validate the public artifacts from the accepted
data. The tested deployment and traffic contract is recorded in each package's
`run-config.json` and `tested-config.yaml`.

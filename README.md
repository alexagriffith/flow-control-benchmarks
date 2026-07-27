# Flow control under pressure

Flow control is the Endpoint Picker's policy layer for multi-tenant inference in llm-d. This repo holds the first benchmark campaign: one GPU running GPT-OSS 20B, deliberately pushed past saturation, to show what the policy does when the pool is full.

Four scenarios ran under pressure, three repeats each. One endpoint under doubled load held ~525 to 601 ms p95 TTFT with every request served. Priority kept premium ahead of standard. Fairness kept three same-priority tenants within a bounded spread. A batch flood at triple the interactive arrival rate absorbed the waiting, 202 ms mean queue time against 64 and 66 ms for the interactive lanes.

How to read the numbers: this campaign measures behavior past the knee, not a latency SLA. The pool is one GPU, so absolute latencies track that hardware. The pattern is the finding. The policy, not arrival order, decided who waited.

## Learn flow control

[`learn/flow-control.html`](learn/flow-control.html) explains the mechanism end to end, what breaks without it, how a request travels through it, what it guarantees, what it costs, and how to operate it. Written against the upstream llm-d Endpoint Picker documentation. Enable GitHub Pages on this repo and the page is served live, or download the file and open it locally.

## The report

[`report/flow-control-under-pressure.pdf`](report/flow-control-under-pressure.pdf) renders in the browser. [`report/flow-control-under-pressure.html`](report/flow-control-under-pressure.html) is the same report as a single HTML file, download and open locally.

## Data

[`data/tenant-summary.csv`](data/tenant-summary.csv) has per-tenant results for every repeat of all four scenarios, including TTFT percentiles, queue means, and error counts. [`data/run-summary.csv`](data/run-summary.csv) has the run-level rollup with vLLM running and waiting stats.

A fuller evidence package, with per-request samples, capacity sweeps, and consolidation runs at a properly sized operating point, is in progress on the `evidence-v2` branch.

# Pipeline

`benchmark_v3.py` is the runner that produced every accepted run. It drives multi-tenant traffic through the gateway with per-tenant objective and fairness headers, logs every request individually, scrapes vLLM metrics, and writes one directory per repeat with `client_samples.csv`, `concurrency_samples.csv`, `metric_samples.csv`, and `summary.json`.

`gen_charts.py` draws every chart in `assets/` from those CSVs. Point its path constants at a campaign root and run it. Same data in, identical charts out.

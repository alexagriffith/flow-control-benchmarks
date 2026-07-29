# Pipeline

`benchmark_v4.py` is the runner that produced every accepted run. It drives multi-tenant traffic through the gateway with per-tenant objective and fairness headers, verifies priority resolution and gate state before counting a run, logs every request individually, scrapes vLLM and Endpoint Picker metrics, and writes one directory per repeat with `client_samples.csv`, `metric_samples.csv`, and `summary.json`.

Automatic prefix caching is off for every counted run, so the latencies reflect scheduling rather than a warm cache. Each prompt draws a unique head and body from a domain word pool, so no two prompts share more than a short system preamble.

`gen_charts.py` draws every chart in `assets/` from the run CSVs in `data-v4/`. Point its path constants at the data root and run it. Same data in, identical charts out.

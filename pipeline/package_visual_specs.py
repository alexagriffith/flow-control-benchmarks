#!/usr/bin/env python3
"""Build data-bound visualization specifications for published benchmark packages."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path


UPSTREAM = Path("benchmark-data/upstream-flow-control-v0.9.0")
BATCH = Path("benchmark-data/batch-eviction")


def load_json(root: Path, relative: str) -> dict:
    return json.loads((root / relative).read_text())


def median(values: list[float]) -> float:
    return float(statistics.median(values))


def bar(title: str, unit: str, rows: list[tuple[str, float]], takeaway: str = "", *, log: bool = False) -> dict:
    return {"kind": "bar", "title": title, "unit": unit, "rows": rows, "takeaway": takeaway, "log": log}


def dot(title: str, unit: str, rows: list[tuple[str, float]], takeaway: str = "", *, log: bool = False) -> dict:
    return {"kind": "dot", "title": title, "unit": unit, "rows": rows, "takeaway": takeaway, "log": log}


def grouped(title: str, unit: str, groups: list[str], rows: list[tuple[str, list[float]]], takeaway: str = "", *, log: bool = False) -> dict:
    return {"kind": "grouped", "title": title, "unit": unit, "groups": groups, "rows": rows, "takeaway": takeaway, "log": log}


def paired(title: str, unit: str, groups: list[str], rows: list[tuple[str, list[float]]], takeaway: str = "", *, log: bool = False) -> dict:
    return {"kind": "paired", "title": title, "unit": unit, "groups": groups, "rows": rows, "takeaway": takeaway, "log": log}


def process(title: str, unit: str, stages: list[tuple[str, float]], takeaway: str = "") -> dict:
    return {"kind": "process", "title": title, "unit": unit, "stages": stages, "takeaway": takeaway}


def line(
    title: str,
    unit: str,
    x: list[str],
    series: list[tuple[str, list[float]]],
    takeaway: str = "",
    *,
    highlight_peak: bool = False,
    statuses: list[str] | None = None,
) -> dict:
    return {
        "kind": "line", "title": title, "unit": unit, "x": x, "series": series,
        "takeaway": takeaway, "highlight_peak": highlight_peak,
        "statuses": statuses,
    }


def combo(
    title: str,
    x: list[str],
    line_name: str,
    line_unit: str,
    line_values: list[float],
    bar_name: str,
    bar_unit: str,
    bar_values: list[float],
    takeaway: str = "",
) -> dict:
    unit = f"{line_unit} · {bar_name.lower()} (%)" if bar_unit == "percent" else f"{line_unit} · {bar_unit}"
    return {
        "kind": "combo",
        "title": title,
        "unit": unit,
        "x": x,
        "line_name": line_name,
        "line_unit": line_unit,
        "line_values": line_values,
        "bar_name": bar_name,
        "bar_unit": bar_unit,
        "bar_values": bar_values,
        "takeaway": takeaway,
    }


def median_metric_window_peaks(root: Path, relative: str, metric: str, methods: list[str], window_seconds: int = 20) -> tuple[list[str], list[tuple[str, list[float]]]]:
    samples: dict[tuple[str, int, int], list[float]] = {}
    maximum_bucket = 0
    with (root / relative).open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["metric"] != metric or row["admission_method"] not in methods:
                continue
            bucket = int(float(row["elapsed_seconds"]) // window_seconds)
            maximum_bucket = max(maximum_bucket, bucket)
            key = (row["admission_method"], int(row["repeat"]), bucket)
            samples.setdefault(key, []).append(float(row["value"]))
    buckets = list(range(maximum_bucket + 1))
    labels = [str(bucket * window_seconds) for bucket in buckets]
    series: list[tuple[str, list[float]]] = []
    for method in methods:
        points = []
        for bucket in buckets:
            repeat_peaks = [max(samples.get((method, repeat, bucket), [0.0])) for repeat in (1, 2, 3)]
            points.append(median(repeat_peaks))
        series.append((method, points))
    return labels, series


def package(
    path: str,
    title: str,
    takeaway: str,
    architecture: tuple[str, str, str, str],
    panels: list[dict],
    *,
    tone: str | None = None,
) -> dict:
    return {
        "path": path,
        "title": title,
        "takeaway": takeaway,
        "architecture": architecture,
        "panels": panels,
        "tone": tone,
    }


def engine(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/engine-configuration/analysis.json")
    seq = data["max_sequence_medians"]
    tok = data["batched_token_results"]
    return package(
        str(UPSTREAM / "engine-configuration"), "Engine capacity and configuration",
        "Engine sweeps selected the latency-throughput baseline for production traffic.",
        ("Closed-loop load", "Endpoint Picker passes requests", "vLLM scheduler limits", "Throughput and latency"),
        [
            line("Throughput as maximum running sequences increased", "served requests/s", list(seq), [("Served throughput", [v["steady_throughput_rps"] for v in seq.values()])], statuses=["good", "neutral", "neutral"]),
            line("Output-token latency as maximum running sequences increased", "p95 TPOT (ms/token)", list(seq), [("Output-token latency", [v["p95_tpot_ms_per_token"] for v in seq.values()])], "Higher concurrency added little throughput and increased output-token latency.", statuses=["good", "neutral", "neutral"]),
            line("Throughput as the batched-token limit increased", "served requests/s", [f"{int(k):,}" for k in tok], [("Served throughput", [v["steady_throughput_rps"] for v in tok.values()])], statuses=["neutral", "good", "neutral"]),
            line("First-token latency as the batched-token limit increased", "p95 TTFT (ms)", [f"{int(k):,}" for k in tok], [("First-token latency", [v["p95_ttft_ms"] for v in tok.values()])], "8,192 tokens balanced the measured latency and throughput.", statuses=["neutral", "good", "neutral"]),
        ],
    )


def utilization(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/utilization-detector-calibration/analysis.json")
    qd = data["queue_depth_medians"]
    kv = data["kv_pressure_medians"]
    return package(
        str(UPSTREAM / "utilization-detector-calibration"), "Utilization detector calibration",
        "Queue depth controls when policy starts; KV threshold tests memory-pressure activation.",
        ("Fixed request load", "Queue and KV signals", "Flow-control queue", "One vLLM replica"),
        [
            line("First-token latency as queue depth increased", "p95 TTFT (ms)", list(qd), [("First-token latency", [v["p95_ttft_ms"] for v in qd.values()])]),
            line("Throughput as queue depth increased", "served requests/s", list(qd), [("Served throughput", [v["steady_throughput_rps"] for v in qd.values()])]),
            line("First-token latency across KV thresholds", "p95 TTFT (ms)", [k.replace("_", " ") for k in kv], [("First-token latency", [v["p95_ttft_ms"] for v in kv.values()])], "This deliberate memory-pressure test measures activation cost."),
        ],
    )


def request_and_token(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/request-and-token-admission-calibration/analysis.json")
    request = data["request_count_medians"]
    mixed = data["mixed_size_medians"]
    methods = [("Request count 128", mixed["request_count_128"]), ("Input-token cap", mixed["input_token_1.2x"])]
    sizes = ["short request", "medium request", "long request"]
    return package(
        str(UPSTREAM / "request-and-token-admission-calibration"), "Request and token admission",
        "In this calibration, 128 requests preserved lower TTFT; token admission changed the tradeoff for mixed sizes.",
        ("Mixed request sizes", "Request or token admission", "Priority-aware queue", "One vLLM replica"),
        [
            line("First-token latency as the request cap increased", "p95 TTFT (ms)", list(request), [("First-token latency", [v["p95_ttft_ms"] for v in request.values()])]),
            line("Throughput as the request cap increased", "served requests/s", list(request), [("Served throughput", [v["steady_throughput_rps"] for v in request.values()])]),
            paired("How admission changed latency by request size", "p95 TTFT (ms)", [m[0] for m in methods], [(s.replace(" request", "").title(), [m[1]["p95_ttft_ms_by_request_size"][s] for m in methods]) for s in sizes]),
            dot("Total throughput by admission method", "served requests/s", [("Request count 128", mixed["request_count_128"]["total_steady_throughput_rps"]), ("Input-token cap", mixed["input_token_1.2x"]["total_steady_throughput_rps"]), ("Input + output estimate", mixed["input_plus_output_estimate"]["total_steady_throughput_rps"])]),
        ],
    )


def request_priority_tuning(root: Path) -> dict:
    base = root / UPSTREAM / "request-concurrency-priority-tuning"
    caps = [32, 48, 64, 96, 128]
    premium, standard = [], []
    for cap in caps:
        rows = list(csv.DictReader((base / f"us_sweep_maxc{cap}" / "summary.csv").open()))
        premium.append(median([float(r["ttft_p95_s"]) * 1000 for r in rows if "premium" in r["tenant"]]))
        standard.append(median([float(r["ttft_p95_s"]) * 1000 for r in rows if "standard" in r["tenant"]]))
    return package(
        str(UPSTREAM / "request-concurrency-priority-tuning"), "Request-concurrency priority tuning",
        "Lower admission protected premium latency while standard traffic absorbed more queue time.",
        ("Premium and standard traffic", "Request cap and priority queues", "Admitted requests", "One vLLM replica"),
        [
            line("Premium latency across request caps", "p95 TTFT (ms)", [str(c) for c in caps], [("Premium", premium)], "The lowest premium p95 TTFT occurred at 48 requests."),
            line("Standard latency across request caps", "p95 TTFT (ms)", [str(c) for c in caps], [("Standard", standard)], "Standard latency fell as the cap admitted more work."),
        ],
    )


def scenario_package(root: Path, slug: str, title: str, traffic: str) -> dict:
    rel = UPSTREAM / "production-scenarios" / slug
    data = load_json(root, f"{rel}/analysis.json")
    selected = data["selected_configuration_results"]
    selected_rows = [(name.title(), values["median_p95_ttft_ms"]) for name, values in selected.items()]
    scenario_copy = {
        "priority-tiers": (
            "Higher-priority realtime traffic retained faster access while lower-priority traffic absorbed more delay.",
            "Lower-priority traffic absorbed more delay during the surge.",
        ),
        "consolidation": (
            "Two realtime tenants retained faster access while a lower-priority burst filled the shared pool.",
            "The lower-priority burst absorbed more delay than either realtime tenant.",
        ),
        "same-priority-fairness": (
            "Peer tenants kept receiving service while one tenant sent a larger burst.",
            "The tenant sending the larger burst absorbed most of the delay.",
        ),
    }
    package_takeaway, panel_note = scenario_copy.get(
        slug,
        (
            "Higher-priority realtime traffic retained faster access during the surge.",
            "Lower-priority or overloaded work absorbed more delay.",
        ),
    )
    if slug == "batch-isolation":
        panel_note = "Directional medians: realtime ranged from 371–669 ms and standard from 436–1,017 ms, above the 1.5× repeat-stability gate."
        package_takeaway = "Realtime stayed faster than batch in every repeat; latency spread was too wide for a stable point estimate."
    panels = [dot("Who waited during the surge", "median p95 TTFT (ms)", selected_rows, panel_note, log=True)]
    matched = data.get("matched_detector_comparisons", {})
    if matched:
        comparison = matched
        methods = list(comparison)
        realtime_names = [name for name in selected if "batch" not in name and "standard" not in name and "burster" not in name]
        if realtime_names:
            rows = [(name.title(), [comparison[m][name]["median_p95_ttft_ms"] for m in methods]) for name in realtime_names]
            panels.append(paired("How detector choice changed realtime latency", "median p95 TTFT (ms)", [m.replace("request count 128, 10% headroom", "Request count") for m in methods], rows, "The same prompts, schedule, model, and GPU were used for each detector.", log=True))
    return package(
        str(rel), title, package_takeaway,
        (traffic, "Priority and fairness queues", "Request-count admission", "One vLLM replica"), panels,
    )


def production_scenarios(root: Path) -> list[dict]:
    rel = UPSTREAM / "production-scenarios"
    data = load_json(root, f"{rel}/analysis.json")
    panels = []
    for scenario, workloads in data["selected_configuration_results"].items():
        panels.append(dot(scenario.title(), "median surge p95 TTFT (ms)", [(name.title(), values["median_p95_ttft_ms"]) for name, values in workloads.items()], log=True))
    parent = package(
        str(rel), "Realtime protection under production traffic",
        "Higher-priority traffic stayed faster across four patterns; three met the repeat-stability gate.",
        ("Noisy sinusoidal traffic", "Priority and fairness queues", "Request count 128", "One shared vLLM replica"), panels,
    )
    children = [
        scenario_package(root, "priority-tiers", "Priority tiers", "Four priority bands"),
        scenario_package(root, "batch-isolation", "Batch isolation", "Realtime, standard, and batch"),
        scenario_package(root, "consolidation", "Consolidation", "Two realtime tenants and a standard burst"),
        scenario_package(root, "same-priority-fairness", "Same-priority fairness", "One burster and two peers"),
    ]
    return [parent, *children]


def selected_shapes(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/selected-workload-shapes/analysis.json")
    shapes = data["by_workload_shape"]
    rows = [(name.title(), values["median"]) for name, values in shapes.items()]
    return package(
        str(UPSTREAM / "selected-workload-shapes"), "Selected workload shapes",
        "Longer generation raised time to first token under the same admission setting.",
        ("One workload shape per run", "Request-count admission", "Priority queue", "One vLLM replica"),
        [
            dot("First-token latency by workload shape", "median surge p95 TTFT (ms)", [(n, v["surge_p95_ttft_ms"]) for n, v in rows]),
            dot("Output-token latency by workload shape", "median surge p95 TPOT (ms/token)", [(n, v["surge_p95_tpot_ms"]) for n, v in rows]),
            dot("Served rate by workload shape", "served requests/s", [(n, v["surge_throughput_rps"]) for n, v in rows]),
        ],
    )


def mixed_workload(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/mixed-production-workload/analysis.json")
    methods = ["request-count admission", "input-token admission"]
    labels = ["Request count", "Input tokens"]
    tiers = [("Premium", "premium"), ("Gold", "gold"), ("Standard", "standard"), ("Batch", "batch")]
    by = data["by_admission_method"]
    time_labels, waiting_series = median_metric_window_peaks(
        root,
        f"{UPSTREAM}/mixed-production-workload/system-metrics.csv",
        "vllm_waiting_requests",
        methods,
    )
    return package(
        str(UPSTREAM / "mixed-production-workload"), "Mixed production workload",
        "Request count protected realtime traffic; input tokens distributed latency more evenly across request sizes.",
        ("Chat, agentic, long context, and batch", "Request or token admission", "Four priority bands", "One vLLM replica"),
        [
            paired(
                "Surge p95 TTFT by workload", "Median time to first token (ms)", labels,
                [(label, [by[m][f"{key}_surge_p95_ttft_ms"]["median"] for m in methods]) for label, key in tiers],
                "Compared with input tokens, request count lowered Premium by 920 ms. Input tokens lowered Batch by 5,822 ms.",
                log=True,
            ),
            paired(
                "Where requests queued", "Median peak queue depth (requests)", labels,
                [
                    ("Endpoint Picker", [by[m]["max_epp_queue"]["median"] for m in methods]),
                    ("Inside vLLM", [by[m]["max_vllm_waiting"]["median"] for m in methods]),
                ],
                "Request count peaked at 27 requests in the Endpoint Picker; input tokens peaked at 43 requests waiting inside vLLM.",
            ),
            line(
                "vLLM waiting requests over time", "Median 20-second peak (requests); elapsed time (s)",
                time_labels, [(labels[index], points) for index, (_, points) in enumerate(waiting_series)],
                "The input-token setting reached a higher vLLM wait peak during drain after the traffic surge.",
                highlight_peak=True,
            ),
            paired(
                "Generation latency by workload", "Median surge p95 TPOT (ms/token)", labels,
                [(label, [by[m][f"{key}_surge_p95_tpot_ms"]["median"] for m in methods]) for label, key in tiers],
                "Request count favored realtime chat; input tokens favored the longer standard and batch work.",
            ),
        ],
    )


def long_context(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/long-context-admission/analysis.json")
    pairs = data["pairs"]
    with (root / UPSTREAM / "long-context-admission/summary.csv").open(newline="") as handle:
        summary_rows = list(csv.DictReader(handle))
    active_queue_runs = {
        method: sum(
            row["flow_control_engaged"].lower() == "true"
            for row in summary_rows
            if row["admission_method"] == method
        )
        for method in ("request-count admission", "exact-token admission")
    }
    return package(
        str(UPSTREAM / "long-context-admission"), "Long-context admission",
        "Exact-token admission activated policy consistently; its latency difference was inconclusive.",
        ("Realtime plus 20k-token burst", "Request or exact-token admission", "Size-aware policy queue", "Two vLLM replicas"),
        [
            paired("Realtime latency across matched seeds", "burst p95 TTFT (ms)", ["Request count", "Exact tokens"], [(f"Seed {p['seed']}", [p["request_p95_ms"], p["token_p95_ms"]]) for p in pairs]),
            dot(
                "Runs with an active policy queue", "runs",
                [
                    ("Request count", active_queue_runs["request-count admission"]),
                    ("Exact tokens", active_queue_runs["exact-token admission"]),
                ],
                "Both arms used eight matched seeds.",
            ),
        ],
    )


def batch_interference(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/batch-interference/analysis.json")
    arms = [
        ("Realtime only", data["by_arm"]["realtime only"]),
        ("Batch preloaded before realtime", data["by_arm"]["realtime with batch already running"]),
    ]
    latency_factor = data["comparison"]["realtime_p95_ttft_factor"]
    waiting_peak = arms[1][1]["max_vllm_waiting"]["median"]
    kv_peak = arms[1][1]["max_vllm_kv_cache_usage_pct"]["median"]
    return package(
        str(UPSTREAM / "batch-interference"), "Batch interference",
        "Running batch severely delayed newly arriving realtime requests.",
        ("Batch starts before realtime", "Flow-control queue", "Batch preloaded inside vLLM", "Realtime latency"),
        [
            paired(f"Realtime p95 TTFT was {latency_factor:.0f} times the reference", "median p95 TTFT (ms)", ["Realtime only", "Batch already running"], [("Realtime", [arms[0][1]["realtime_surge_p95_ttft_ms"]["median"], arms[1][1]["realtime_surge_p95_ttft_ms"]["median"]])], log=True),
            paired(f"vLLM peak waiting reached {waiting_peak:.0f} requests", "median peak requests", ["Realtime only", "Batch already running"], [("Waiting requests", [arms[0][1]["max_vllm_waiting"]["median"], arms[1][1]["max_vllm_waiting"]["median"]])]),
            paired(f"Peak KV-cache use reached {kv_peak:.1f}%", "median peak KV cache (%)", ["Realtime only", "Batch already running"], [("KV-cache use", [arms[0][1]["max_vllm_kv_cache_usage_pct"]["median"], arms[1][1]["max_vllm_kv_cache_usage_pct"]["median"]])]),
        ],
        tone="warning",
    )


def scaling(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/multi-replica-scaling/analysis.json")
    topologies = data["topologies"]
    labels = [f"{n} model replica{'s' if n != '1' else ''}" for n in ["1", "2", "4"]]
    values = [topologies[n] for n in ["1", "2", "4"]]
    return package(
        str(UPSTREAM / "multi-replica-scaling"), "Model pool scaling",
        "Per-GPU throughput held as the pool grew. Smaller pools reached the rejection boundary.",
        ("Matched load per GPU", "One Endpoint Picker", "One, two, or four vLLM replicas", "Per-GPU service"),
        [
            line("Per-GPU throughput as the model pool grew", "served requests/s/GPU", labels, [("Per-GPU throughput", [v["median_served_rps_per_gpu"] for v in values])]),
            line("Premium latency as the model pool grew", "median burst p95 TTFT (ms)", labels, [("Premium latency", [v["median_premium_burst_p95_ttft_ms"] for v in values])]),
            line("Rejected responses as the model pool grew", "responses across three repeats", labels, [("HTTP 429 responses", [v["http_non_200"] for v in values])], "Four replicas completed the tested load without 429 responses."),
        ],
    )


def stability(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/long-stability/analysis.json")
    phases = list(data["premium_p95_ttft_ms"])
    return package(
        str(UPSTREAM / "long-stability"), "Long stability",
        "The run recovered after two sustained surges.",
        ("Thirty-minute mixed traffic", "Request-count admission", "Priority-aware queue", "One vLLM replica"),
        [
            combo(
                "Premium latency and queue activity",
                [p.replace("-", " ").title() for p in phases],
                "Premium latency",
                "p95 TTFT (ms)",
                [data["premium_p95_ttft_ms"][p] for p in phases],
                "Samples with queued requests",
                "percent",
                [data["queue_by_window"][p]["active_sample_fraction"] * 100 for p in phases],
            ),
        ],
    )


def prefix_routing(root: Path) -> dict:
    data = load_json(root, f"{UPSTREAM}/prefix-cache-routing/analysis.json")
    rows = [r for r in data["overall_latency_comparison"] if r["metric"] == "p95 TTFT"]
    arms = ["random routing", "prefix-aware routing"]
    cache_hit_rates = [data["by_arm"][arm]["prefix_cache_hit_rate"]["median"] * 100 for arm in arms]
    route_imbalance = [
        median([row["route_imbalance_percent"] for row in data["route_balance"] if row["arm"] == arm])
        for arm in arms
    ]
    return package(
        str(UPSTREAM / "prefix-cache-routing"), "Prefix-cache routing",
        "Prefix-aware routing changed latency by workload while cache hit rate stayed flat.",
        ("Shared prefixes with unique suffixes", "Random or prefix-aware scoring", "Two cache-enabled vLLM replicas", "Latency and route balance"),
        [
            paired("How prefix-aware routing changed latency", "median p95 TTFT (ms)", ["Random", "Prefix aware"], [(r["workload"].replace("-", " ").title(), [r["random_routing_median_ms"], r["prefix_aware_routing_median_ms"]]) for r in rows], log=True),
            paired(
                "Routing tradeoff", "percent", ["Random", "Prefix aware"],
                [("Cache hit rate", cache_hit_rates), ("Route imbalance", route_imbalance)],
            ),
            dot("HTTP 429 responses", "responses across three repeats", [("Random", data["by_arm"]["random routing"]["http_429_total"]), ("Prefix aware", data["by_arm"]["prefix-aware routing"]["http_429_total"])]),
        ],
    )


def batch_eviction(root: Path, replicas: int) -> dict:
    slug = "single-model-replica" if replicas == 1 else "two-model-replicas"
    rel = BATCH / slug
    rows = list(csv.DictReader((root / rel / "summary.csv").open()))
    if replicas == 1:
        groups = {}
        for row in rows:
            groups.setdefault(row["scenario"], []).append(float(row["realtime_p95_ttft_ms"]))
        display_names = {
            "Realtime only": "Realtime only",
            "Realtime with batch and no protection": "Batch with no protection",
            "Realtime with reserved capacity": "Reserved capacity",
            "Realtime with reserved capacity, batch eviction, and retry": "Reserved capacity + eviction",
        }
        plot_rows = [(display_names[name], median(values)) for name, values in groups.items()]
        retry_rows = [("Evicted", sum(int(r["evicted_batch_requests"]) for r in rows)), ("Retried", sum(int(r["async_retried_requests"]) for r in rows)), ("One final result", sum(int(r["async_retried_requests"]) for r in rows))]
        panels = [dot("Realtime latency across four scenarios", "median p95 TTFT (ms)", plot_rows), process("Safe batch retry", "requests", retry_rows, "Evicted work was retried without duplicate results.")]
        takeaway = "Realtime traffic remained protected while evicted batch work was safely retried."
        arch = ("Realtime and batch", "Reserved capacity and eviction", "One vLLM replica", "Retry owner completes batch")
        results_template = "batch_eviction_single"
    else:
        production = [r for r in rows if r["evidence_role"] == "production evidence"]
        analysis = load_json(root, f"{rel}/analysis.json")
        latency = analysis["latency"]
        confidence_interval = latency["hierarchical_bootstrap_delta_95ci_ms"]
        latency_takeaway = (
            f"Median p95 TTFT was within {abs(latency['delta_ms']):.0f} ms of the one-replica reference; "
            f"the 95% interval ({confidence_interval[0]:.0f} to {confidence_interval[1]:.0f} ms) includes zero."
        )
        panels = [
            dot(
                "Realtime latency across production repeats", "p95 TTFT (ms)",
                [(f"Repeat {i + 1}", float(r["realtime_p95_ttft_ms"])) for i, r in enumerate(production)],
                latency_takeaway,
            ),
            paired("Model route share", "percent of requests", ["Model 1", "Model 2"], [(f"Repeat {i + 1}", [float(r["realtime_route_share_model_1"]) * 100, float(r["realtime_route_share_model_2"]) * 100]) for i, r in enumerate(production)]),
            process("Batch eviction and retry", "requests", [("Evicted", sum(int(r["evicted_batch_requests"]) for r in production)), ("Retried", sum(int(r["retried_batch_requests"]) for r in production)), ("Single final result", sum(int(r["single_final_result_matches"]) for r in production))]),
        ]
        takeaway = "Batch eviction and retry worked across two balanced model replicas."
        arch = ("Realtime and batch", "One Endpoint Picker", "Two vLLM replicas", "Retry owner completes batch")
    spec = package(str(rel), f"Batch eviction: {replicas} model replica{'s' if replicas > 1 else ''}", takeaway, arch, panels)
    if replicas == 1:
        spec["results_template"] = results_template
    return spec


def build_specs(root: Path) -> list[dict]:
    specs = [engine(root), utilization(root), request_and_token(root), request_priority_tuning(root)]
    specs.extend(production_scenarios(root))
    specs.extend([selected_shapes(root), mixed_workload(root), long_context(root), batch_interference(root), scaling(root), stability(root), prefix_routing(root)])
    specs.extend([batch_eviction(root, 1), batch_eviction(root, 2)])
    return specs

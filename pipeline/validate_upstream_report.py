#!/usr/bin/env python3
"""Validate the grouped Endpoint Picker v0.9.0 report."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from statistics import median


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


class StructureParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.tags: Counter[str] = Counter()
        self.classes: Counter[str] = Counter()
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.viewport = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags[tag] += 1
        for class_name in (values.get("class") or "").split():
            self.classes[class_name] += 1
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if values.get("href"):
            self.hrefs.append(values["href"] or "")
        if tag == "meta" and values.get("name") == "viewport":
            self.viewport = values.get("content") == "width=device-width, initial-scale=1"
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            self.errors.append(f"unexpected closing tag </{tag}>")
            return
        opened = self.stack.pop()
        if opened != tag:
            self.errors.append(f"closing tag </{tag}> does not match <{opened}>")

    def finish(self) -> None:
        if self.stack:
            self.errors.append(f"unclosed tags: {', '.join(self.stack)}")


def require_text(text: str, expected: str, message: str, errors: list[str]) -> None:
    require(expected in text, message, errors)


def formatted_ms(value: float) -> str:
    return f"{round(value):,}"


def derive_production_traffic(path: Path) -> dict[str, dict[str, list[float]]]:
    selected = {
        "priority tiers": "request count 128, 10% headroom",
        "batch isolation": "request count 128, 15% headroom",
        "consolidation": "request count 128, 10% headroom",
        "same-priority fairness": "request count 128, 10% headroom",
    }
    series: dict[tuple[str, str], list[tuple[float, int]]] = defaultdict(list)
    paths = sorted(path.glob("*/traffic-samples.csv")) if path.is_dir() else [path]
    for traffic_path in paths:
        with traffic_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                scenario = row["scenario"]
                if (
                    scenario in selected
                    and row["detector"] == selected[scenario]
                    and int(row["repeat"]) == 2
                ):
                    series[(scenario, row["tenant"])].append(
                        (float(row["elapsed_seconds"]), int(row["issued_requests"]))
                    )
    result: dict[str, dict[str, list[float]]] = {}
    for (scenario, tenant), points in sorted(series.items()):
        ordered = sorted(points)
        values: list[float] = []
        for end in range(10, 241, 10):
            previous = max((value for elapsed, value in ordered if elapsed <= end - 10), default=0)
            current = max((value for elapsed, value in ordered if elapsed <= end), default=previous)
            values.append(round((current - previous) / 10, 1))
        result.setdefault(scenario, {})[tenant] = values
    return result


def validate_upstream_report(errors: list[str], root: Path) -> None:
    data = root / "benchmark-data" / "upstream-flow-control-v0.9.0"
    report = data / "results.html"
    require(report.exists(), "grouped upstream report is missing", errors)
    if not report.exists():
        return
    text = report.read_text()
    parser = StructureParser()
    try:
        parser.feed(text)
        parser.close()
        parser.finish()
    except Exception as exc:
        errors.append(f"grouped upstream report markup failed: {exc}")
    errors.extend(f"grouped upstream report markup: {error}" for error in parser.errors)

    require(parser.tags["main"] == 1, "grouped report must contain exactly one main element", errors)
    require(parser.tags["h1"] == 1, "grouped report must contain exactly one h1", errors)
    require(parser.classes["scenario"] == 4, "grouped report must contain four production-scenario visuals", errors)
    require(parser.tags["figure"] == 35, "grouped report visual inventory changed", errors)
    require(parser.classes["evidence-card"] == 13, "grouped report evidence-card inventory changed", errors)
    require(parser.classes["sweep-chart"] == 7, "grouped report sweep-chart inventory changed", errors)
    require(parser.classes["range-chart"] == 2, "grouped report range-chart inventory changed", errors)
    require(parser.classes["heatmap"] == 1, "grouped report heatmap inventory changed", errors)
    require(parser.classes["phase-chart"] == 1, "grouped report phase-chart inventory changed", errors)
    require(parser.viewport, "grouped report viewport metadata changed", errors)
    require(len(parser.ids) == len(set(parser.ids)), "grouped report contains duplicate ids", errors)
    for href in parser.hrefs:
        if href.startswith("#"):
            require(href[1:] in parser.ids, f"grouped report target does not exist: {href}", errors)
        elif "://" not in href:
            require((report.parent / href).exists(), f"grouped report link does not exist: {href}", errors)
    require("@media (max-width: 520px)" in text, "grouped report mobile breakpoint changed", errors)
    require("font-size: clamp(" not in text, "grouped report restored viewport-scaled typography", errors)
    require_text(
        text,
        "Can one shared model pool protect priority traffic across different request shapes and a larger model pool?</p>\n      <p><strong>Answer:</strong> Flow control protected higher-priority realtime traffic",
        "top business question is not followed by its direct answer",
        errors,
    )
    require_text(
        text,
        "Which admission signal protected realtime latency sooner: in-flight requests or the vLLM waiting queue?",
        "detector business question is not followed by its direct answer",
        errors,
    )
    require_text(
        text,
        '<p class="comparison-answer"><strong>Answer:</strong> In both production comparisons, the in-flight request limit protected realtime latency sooner.',
        "detector comparison answer moved or changed",
        errors,
    )
    require(".bar { display: block;" in text, "grouped report bars can collapse to zero width", errors)
    require(
        ".result-grid, .chart-grid, .chart-grid.three, .scenario-grid, .packages, .section-head, .traffic-grid, .evidence-gallery, .headroom-guide { grid-template-columns: 1fr; }" in text,
        "grouped report responsive grid collapse changed",
        errors,
    )

    engine = read_json(data / "engine-configuration" / "analysis.json")
    admission = read_json(data / "request-and-token-admission-calibration" / "analysis.json")
    production = read_json(data / "production-scenarios" / "analysis.json")
    shapes = read_json(data / "selected-workload-shapes" / "analysis.json")
    mixed = read_json(data / "mixed-production-workload" / "analysis.json")
    scaling = read_json(data / "multi-replica-scaling" / "analysis.json")
    stability = read_json(data / "long-stability" / "analysis.json")
    routing = read_json(data / "prefix-cache-routing" / "analysis.json")
    long_context = read_json(data / "long-context-admission" / "analysis.json")
    utilization = read_json(data / "utilization-detector-calibration" / "analysis.json")
    batch = read_json(data / "batch-interference" / "analysis.json")

    max_sequence_values = list(engine["max_sequence_medians"].values())
    max_sequence_throughput = "|".join(f"{value['steady_throughput_rps']:.3f}" for value in max_sequence_values)
    max_sequence_latency = "|".join(str(round(value["p99_ttft_ms"])) for value in max_sequence_values)
    require_text(text, f'data-throughput="{max_sequence_throughput}"', "max-sequence throughput changed", errors)
    require_text(text, f'data-latency="{max_sequence_latency}"', "max-sequence p99 TTFT changed", errors)
    batched_token_values = list(engine["batched_token_results"].values())
    batched_token_throughput = "|".join(f"{value['steady_throughput_rps']:.3f}" for value in batched_token_values)
    batched_token_latency = "|".join(str(round(value["p95_ttft_ms"])) for value in batched_token_values)
    require_text(text, f'data-throughput="{batched_token_throughput}"', "batched-token throughput changed", errors)
    require_text(text, f'data-latency="{batched_token_latency}"', "batched-token p95 TTFT changed", errors)
    request_cap_values = list(admission["request_count_medians"].values())
    request_cap_throughput = "|".join(f"{value['steady_throughput_rps']:.3f}" for value in request_cap_values)
    request_cap_latency = "|".join(str(round(value["p95_ttft_ms"])) for value in request_cap_values)
    require_text(text, f'data-throughput="{request_cap_throughput}"', "request-cap throughput changed", errors)
    require_text(text, f'data-latency="{request_cap_latency}"', "request-cap p95 TTFT changed", errors)
    queue_depth_values = list(utilization["queue_depth_medians"].values())
    queue_depth_throughput = "|".join(f'{value["steady_throughput_rps"]:.3f}'.rstrip("0").rstrip(".") for value in queue_depth_values)
    require_text(text, f'data-throughput="{queue_depth_throughput}"', "queue-depth throughput changed", errors)
    queue_depth_latency = "|".join(str(round(value["p95_ttft_ms"])) for value in queue_depth_values)
    require_text(text, f'data-latency="{queue_depth_latency}"', "queue-depth p95 TTFT changed", errors)
    kv_pressure_values = list(utilization["kv_pressure_medians"].values())
    kv_pressure_throughput = "|".join(f"{value['steady_throughput_rps']:.3f}".rstrip("0").rstrip(".") for value in kv_pressure_values)
    kv_pressure_latency = "|".join(str(round(value["p95_ttft_ms"])) for value in kv_pressure_values)
    require_text(text, f'data-throughput="{kv_pressure_throughput}"', "KV-pressure throughput changed", errors)
    require_text(text, f'data-latency="{kv_pressure_latency}"', "KV-pressure p95 TTFT changed", errors)
    require_text(text, 'data-throughput-label="Throughput (requests/s)"', "KV-pressure throughput unit changed", errors)
    require_text(text, "The result is a safety calibration, not a latency optimum.", "KV-pressure screening boundary changed", errors)
    require_text(text, "both thresholds were slower than the flow-control-off calibration", "KV-pressure control comparison changed", errors)
    priority_tuning = {
        "labels": "32|48|64|96|128",
        "premium": "568|461|582|761|907",
        "standard": "12722|7808|5207|3714|2697",
    }
    require_text(text, f'data-labels="{priority_tuning["labels"]}"', "priority-tuning cap sweep changed", errors)
    require_text(text, f'data-throughput="{priority_tuning["premium"]}"', "priority-tuning premium TTFT changed", errors)
    require_text(text, f'data-latency="{priority_tuning["standard"]}"', "priority-tuning standard TTFT changed", errors)
    mixed_size_cells = []
    for result in admission["mixed_size_medians"].values():
        for size in ("short request", "medium request", "long request"):
            mixed_size_cells.append(round(result["p95_ttft_ms_by_request_size"][size]))
    for value in mixed_size_cells:
        require_text(text, f'data-value="{value}"', f"mixed-size calibration changed: {value} ms", errors)
    require_text(text, "128 maximum sequences; 8,192 maximum batched tokens", "selected engine settings changed", errors)
    require_text(text, "128 in-flight requests; 10% headroom", "selected admission setting changed", errors)
    require_text(text, "128 in-flight requests; 15% headroom", "batch-isolation headroom changed", errors)
    require_text(text, "Exact input-token count", "size-aware option changed", errors)
    require_text(text, 'data-shared-axis="true"', "priority-tuning latency chart lost its shared axis", errors)
    require_text(text, "Shared y-axis: p95 TTFT in milliseconds (log scale)", "priority-tuning latency scale disclosure changed", errors)
    require_text(text, '4,163 ms</span><i class="heat-gradient"></i><span>41,909 ms', "heatmap legend changed", errors)

    selected = production["selected_configuration_results"]
    for scenario, workloads in selected.items():
        for workload, result in workloads.items():
            value = round(result["median_p95_ttft_ms"])
            require_text(text, f'data-value="{value}"', f"{scenario} {workload} chart value changed", errors)
            require_text(text, f'>{value:,} ms<', f"{scenario} {workload} display value changed", errors)
    require_text(text, "Nine matched runs; HTTP 429: 5/3,498, 1/7,014, and 0/13,950", "scale evidence scope changed", errors)
    require_text(text, "Traffic sent during each surge", "production traffic title changed", errors)
    require_text(text, "requests/s", "production traffic y-axis unit changed", errors)
    for expected_range in (
        "Platinum 368–486 ms; Gold 414–594 ms",
        "Realtime ranged from 371–669 ms",
        "A 503–558 ms; B 505–599 ms",
        "B 508–619 ms; C 563–675 ms",
    ):
        require_text(text, expected_range, f"production uncertainty changed: {expected_range}", errors)

    comparisons = production["matched_detector_comparisons"]
    comparison_groups = (
        (comparisons["consolidation"]["request count 128, 10% headroom"], "realtime tenant A", "realtime tenant B"),
        (comparisons["consolidation"]["queue depth 2"], "realtime tenant A", "realtime tenant B"),
        (comparisons["consolidation"]["queue depth 5"], "realtime tenant A", "realtime tenant B"),
        (comparisons["same-priority fairness"]["request count 128, 10% headroom"], "realtime peer B", "realtime peer C"),
        (comparisons["same-priority fairness"]["queue depth 2"], "realtime peer B", "realtime peer C"),
    )
    for results, first, second in comparison_groups:
        for workload in (first, second):
            value = round(results[workload]["median_p95_ttft_ms"])
            require_text(text, f'data-value="{value}"', f"detector comparison changed: {workload} {value} ms", errors)
            require_text(text, f'>{value:,} ms<', f"detector display changed: {workload} {value} ms", errors)
            low, high = results[workload]["range_p95_ttft_ms"]
            require_text(text, f'data-low="{round(low)}"', f"detector range minimum changed: {workload}", errors)
            require_text(text, f'data-high="{round(high)}"', f"detector range maximum changed: {workload}", errors)
    calibration = production["calibration_only"]["same-priority queue depth 5"]
    for workload in ("realtime peer B", "realtime peer C"):
        value = formatted_ms(calibration[workload]["median_p95_ttft_ms"])
        require(value not in text, f"calibration-only fairness value leaked into main report: {value} ms", errors)

    traffic_match = re.search(r'<script id="traffic-data" type="application/json">(.*?)</script>', text)
    require(traffic_match is not None, "production traffic data is missing", errors)
    if traffic_match:
        embedded_traffic = json.loads(traffic_match.group(1))
        expected_traffic = derive_production_traffic(data / "production-scenarios")
        require(embedded_traffic == expected_traffic, "production traffic plot data changed", errors)

    shape_expected = round(shapes["by_workload_shape"]["chat short output"]["median"]["surge_p95_ttft_ms"])
    agentic_expected = round(shapes["by_workload_shape"]["agentic longer output"]["median"]["surge_p95_ttft_ms"])
    require(f'data-value="{shape_expected}"' in text and f'data-value="{agentic_expected}"' in text, "workload-shape TTFT values changed", errors)
    for method in mixed["by_admission_method"].values():
        for key in ("premium_surge_p95_ttft_ms", "gold_surge_p95_ttft_ms", "standard_surge_p95_ttft_ms", "batch_surge_p95_ttft_ms"):
            value = round(method[key]["median"])
            require_text(text, f'data-value="{value}"', f"mixed-workload value changed: {key} {value} ms", errors)
    require(not long_context["statistics"]["claim_advances"], "long-context result unexpectedly advances", errors)
    for pair in long_context["pairs"]:
        require_text(text, f'data-value="{pair["request_p95_ms"]:.1f}"', "long-context request-count pair changed", errors)
        require_text(text, f'data-value="{pair["token_p95_ms"]:.1f}"', "long-context token pair changed", errors)
    require_text(text, "Mean difference: 16.4 ms; 95% confidence interval: -23.9 to 56.8 ms.", "long-context statistical scope changed", errors)

    realtime_only = batch["by_arm"]["realtime only"]["realtime_surge_p95_ttft_ms"]["median"]
    batch_running = batch["by_arm"]["realtime with batch already running"]["realtime_surge_p95_ttft_ms"]["median"]
    for value in (realtime_only, batch_running):
        require_text(text, f'data-value="{round(value)}"', "batch-interference value changed", errors)
    batch_eviction_summary = data.parent / "batch-eviction" / "single-model-replica" / "summary.csv"
    batch_eviction_rows = list(csv.DictReader(batch_eviction_summary.open(newline="")))
    batch_eviction_by_scenario: dict[str, list[float]] = defaultdict(list)
    for row in batch_eviction_rows:
        batch_eviction_by_scenario[row["scenario"]].append(float(row["realtime_p95_ttft_ms"]))
    for values in batch_eviction_by_scenario.values():
        value = round(median(values))
        require(
            f'data-value="{value}"' in text or f"{value} ms" in text,
            f"batch-eviction p95 TTFT changed: {value} ms",
            errors,
        )
    require(sum(int(row["evicted_batch_requests"]) for row in batch_eviction_rows) == 38, "batch-eviction count changed", errors)
    require(sum(int(row["async_retried_requests"]) for row in batch_eviction_rows) == 38, "batch retry count changed", errors)
    require(sum(int(row["batch_duplicate_results"]) for row in batch_eviction_rows) == 0, "batch duplicate count changed", errors)
    scale_topologies = list(scaling["topologies"].values())
    scale_throughput = "|".join(f"{topology['median_served_rps_per_gpu']:.3f}" for topology in scale_topologies)
    scale_latency = "|".join(str(round(topology["median_premium_burst_p95_ttft_ms"])) for topology in scale_topologies)
    require_text(text, f'data-throughput="{scale_throughput}"', "scale throughput changed", errors)
    require_text(text, f'data-latency="{scale_latency}"', "scale latency changed", errors)
    scale_values = [topology["median_served_rps_per_gpu"] for topology in scaling["topologies"].values()]
    scale_spread_pct = (max(scale_values) - min(scale_values)) / min(scale_values) * 100
    require(f"{scale_spread_pct:.1f}%" in text, "per-GPU throughput spread changed", errors)
    require(
        scaling["topologies"]["1"]["http_non_200"] > 0 and scaling["topologies"]["2"]["http_non_200"] > 0,
        "scale-out 429 boundary changed",
        errors,
    )
    require(f'{stability["requests"]:,} completed requests' in text and stability["queue_drained"], "stability result changed", errors)
    stability_values = "|".join(str(round(value)) for value in stability["premium_p95_ttft_ms"].values())
    require_text(text, f'data-values="{stability_values}"', "stability phase values changed", errors)
    require(stability["preemptions"] == 0, "stability preemption result changed", errors)
    routing_rows = {row["workload"]: row for row in routing["overall_latency_comparison"] if row["metric"] == "p95 TTFT"}
    for row in routing_rows.values():
        for key in ("random_routing_median_ms", "prefix_aware_routing_median_ms"):
            value = round(row[key])
            require_text(text, f'data-value="{value}"', f"prefix-routing value changed: {value} ms", errors)
    package_links = (
        "engine-configuration/", "utilization-detector-calibration/",
        "request-and-token-admission-calibration/", "production-scenarios/",
        "selected-workload-shapes/", "mixed-production-workload/",
        "long-context-admission/", "batch-interference/", "multi-replica-scaling/",
        "long-stability/", "prefix-cache-routing/", "request-concurrency-priority-tuning/",
    )
    require(all(f'href="{link}"' in text for link in package_links), "grouped report package links changed", errors)
    require_text(text, 'href="../batch-eviction/single-model-replica/results-brief.html"', "batch-eviction package link changed", errors)
    require_text(text, "separate experimental batch-eviction build", "batch-eviction build boundary changed", errors)

    required_claims = (
        "Higher-priority realtime traffic stayed faster across four production-shaped scenarios.",
        "Across the three stable scenarios, retained realtime tenants recorded median p95 time to first token (TTFT) from 404 to 570 ms.",
        "uses its own labeled y-axis range",
        "every scenario used the same surge window so differences in latency reflect the traffic mix and policy behavior",
        "The plots compare request count and queue depth with the same prompts, traffic, model, GPU, and three repeats.",
        "Flow control was engaged and policy queues were active in every retained run.",
        "Reserved capacity and eviction are covered separately.",
        "HTTP 429: 5/3,498 at one replica, 1/7,014 at two, and 0/13,950 at four.",
        "sparse 429s prevent a rejection-free claim at every pool size.",
        "No fixed service-level objective is claimed.",
    )
    for claim in required_claims:
        require_text(text, claim, f"grouped report claim boundary changed: {claim}", errors)
    for rejected in ("Calibration only.", "Statistical scope.", "Gemini", "Claude", "style approval"):
        require(rejected not in text, f"removed report wording returned: {rejected}", errors)

    data_readme = (data / "README.md").read_text()
    root_readme = (root / "README.md").read_text()
    require("[`results.html`](results.html)" in data_readme, "upstream README report link changed", errors)
    for heading in ("## Configuration", "## Production behavior", "## Scale and routing"):
        require(heading in data_readme, f"upstream README section changed: {heading}", errors)
    for link in package_links:
        require(f"({link})" in data_readme, f"upstream README package link changed: {link}", errors)
    require(
        "benchmark-data/upstream-flow-control-v0.9.0/results.html" in root_readme,
        "root README report link changed",
        errors,
    )
    for link in (
        "benchmark-data/rhaii-3.4-flow-control/",
        "benchmark-data/batch-eviction/",
    ):
        require(link in root_readme, f"root README evidence link changed: {link}", errors)
    require(
        "Most packages disable prefix caching" in root_readme
        and "prefix-routing package enables caching" in root_readme,
        "root README cache boundary changed",
        errors,
    )
    require(
        "Each result states its image" not in root_readme,
        "root README restored an unsupported blanket evidence claim",
        errors,
    )


def main() -> int:
    errors: list[str] = []
    validate_upstream_report(errors, Path(__file__).resolve().parents[1])
    if errors:
        print("Grouped upstream report validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Grouped upstream report validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

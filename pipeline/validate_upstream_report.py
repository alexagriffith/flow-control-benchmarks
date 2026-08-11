#!/usr/bin/env python3
"""Validate the grouped Endpoint Picker v0.9.0 report."""

from __future__ import annotations

import json
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


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
    require(parser.viewport, "grouped report viewport metadata changed", errors)
    require(len(parser.ids) == len(set(parser.ids)), "grouped report contains duplicate ids", errors)
    for href in parser.hrefs:
        if href.startswith("#"):
            require(href[1:] in parser.ids, f"grouped report target does not exist: {href}", errors)
        elif "://" not in href:
            require((report.parent / href).exists(), f"grouped report link does not exist: {href}", errors)
    require("@media (max-width: 520px)" in text, "grouped report mobile breakpoint changed", errors)
    require("font-size: clamp(" not in text, "grouped report restored viewport-scaled typography", errors)
    require(
        ".result-grid, .settings, .scenario-grid, .compare-grid, .evidence-grid, .packages, .section-head { grid-template-columns: 1fr; }" in text,
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

    require_text(text, f'>{engine["selected"]["max_num_sequences"]}<', "selected max sequences changed", errors)
    require_text(text, f'>{engine["selected"]["max_num_batched_tokens"]:,}<', "selected batched-token limit changed", errors)
    require_text(text, f'>{min(map(int, admission["request_count_medians"]))}<', "selected request admission cap changed", errors)

    selected = production["selected_configuration_results"]
    for scenario, workloads in selected.items():
        for workload, result in workloads.items():
            value = round(result["median_p95_ttft_ms"])
            require_text(text, f'data-ms="{value}"', f"{scenario} {workload} chart value changed", errors)
            require_text(text, f'>{value:,}<', f"{scenario} {workload} display value changed", errors)
    require_text(text, "194,923 requests completed successfully", "production request total changed", errors)

    comparisons = production["matched_detector_comparisons"]
    comparison_pairs = (
        (comparisons["consolidation"]["request count 128, 10% headroom"], "realtime tenant A", "realtime tenant B"),
        (comparisons["consolidation"]["queue depth 2"], "realtime tenant A", "realtime tenant B"),
        (comparisons["consolidation"]["queue depth 5"], "realtime tenant A", "realtime tenant B"),
        (comparisons["same-priority fairness"]["request count 128, 10% headroom"], "realtime peer B", "realtime peer C"),
        (comparisons["same-priority fairness"]["queue depth 2"], "realtime peer B", "realtime peer C"),
    )
    for results, first, second in comparison_pairs:
        pair = f'{formatted_ms(results[first]["median_p95_ttft_ms"])} and {formatted_ms(results[second]["median_p95_ttft_ms"])} ms'
        require_text(text, pair, f"detector comparison values changed: {pair}", errors)
    calibration = production["calibration_only"]["same-priority queue depth 5"]
    for workload in ("realtime peer B", "realtime peer C"):
        value = formatted_ms(calibration[workload]["median_p95_ttft_ms"])
        require_text(text, value, f"fairness calibration value changed: {value} ms", errors)

    shape_expected = round(shapes["by_workload_shape"]["chat short output"]["median"]["surge_p95_ttft_ms"])
    agentic_expected = round(shapes["by_workload_shape"]["agentic longer output"]["median"]["surge_p95_ttft_ms"])
    require(f">{shape_expected} ms<" in text and f">{agentic_expected:,} ms<" in text, "workload-shape values changed", errors)
    require(
        f'{abs(round(mixed["comparison"]["premium_request_minus_token_p95_ttft_ms"]))} ms' in text,
        "mixed-workload value changed",
        errors,
    )
    require(not long_context["statistics"]["claim_advances"], "long-context result unexpectedly advances", errors)
    for value in (
        long_context["statistics"]["mean_difference_ms"],
        long_context["statistics"]["exact_token_mean_p95_ttft_ms"],
        long_context["statistics"]["request_count_mean_p95_ttft_ms"],
    ):
        require_text(text, f'{value:.1f} ms', f"long-context value changed: {value:.1f} ms", errors)
    for replicas, topology in scaling["topologies"].items():
        require(f'{topology["median_served_rps_per_gpu"]:.3f} RPS' in text, f"{replicas}-GPU scale value changed", errors)
    scale_values = [topology["median_served_rps_per_gpu"] for topology in scaling["topologies"].values()]
    scale_spread_pct = (max(scale_values) - min(scale_values)) / min(scale_values) * 100
    require(f"{scale_spread_pct:.1f}%" in text, "per-GPU throughput spread changed", errors)
    require(
        scaling["topologies"]["1"]["http_non_200"] > 0 and scaling["topologies"]["2"]["http_non_200"] > 0,
        "scale-out 429 boundary changed",
        errors,
    )
    require(f'{stability["requests"]:,} requests' in text and stability["queue_drained"], "stability result changed", errors)
    require(stability["preemptions"] == 0, "stability preemption result changed", errors)
    routing_rows = {row["workload"]: row for row in routing["overall_latency_comparison"] if row["metric"] == "p95 TTFT"}
    realtime = routing_rows["realtime-chat"]
    require(
        f'{round(realtime["random_routing_median_ms"]):,} ms' in text
        and f'{round(realtime["prefix_aware_routing_median_ms"]):,} ms' in text,
        "prefix-routing values changed",
        errors,
    )
    package_links = (
        "engine-configuration/", "utilization-detector-calibration/",
        "request-and-token-admission-calibration/", "production-scenarios/",
        "selected-workload-shapes/", "mixed-production-workload/",
        "long-context-admission/", "batch-interference/", "multi-replica-scaling/",
        "long-stability/", "prefix-cache-routing/", "request-concurrency-priority-tuning/",
    )
    require(all(f'href="{link}"' in text for link in package_links), "grouped report package links changed", errors)

    required_claims = (
        "Median realtime and standard p95 TTFT",
        "Median realtime p95 time to first token (TTFT)",
        "429 responses occurred at one and two GPUs",
        "It is not part of the matched comparison.",
        "does not claim formal statistical significance",
        "No fixed service-level objective is claimed.",
        "Median surge p95 TTFT in milliseconds. Log scale.",
        "requests per second (RPS) per GPU",
    )
    for claim in required_claims:
        require_text(text, claim, f"grouped report claim boundary changed: {claim}", errors)

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
        "Most control tests disable prefix caching" in root_readme
        and "prefix-routing package enables it explicitly" in root_readme,
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

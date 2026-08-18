#!/usr/bin/env python3
"""Generate compact single-panel SVGs for the v0.9 tuning guide in html-suggest."""

from __future__ import annotations

import html
import json
import math
import statistics
import sys
import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from package_visual_specs import build_specs

WIDTH = 880
HEIGHT = 268
PANEL_W = WIDTH - 40
INK = "#15202b"
MUTED = "#5f6c7b"
LINE = "#dde3ea"
SURFACE = "#ffffff"
PAGE = "#f7f8fa"
COLORS = ["#2d6cdf", "#087f72", "#c56a00", "#6550a5", "#b83232"]

# Public visual contract:
# - one claim per image;
# - metric names include units;
# - prose stays outside the image;
# - plot, labels, and values occupy separate gutters;
# - every SVG has an accessible description and a stable viewBox.

# Compact takeaway charts: keep category labels above the footer band.
TAKEAWAY_INNER_TOP = 72
TAKEAWAY_CHART_TOP = 110
TAKEAWAY_BASELINE = 194
TAKEAWAY_INNER_H = TAKEAWAY_BASELINE - TAKEAWAY_INNER_TOP + 8
TAKEAWAY_LABEL_Y = TAKEAWAY_BASELINE + 14


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt(value: float, unit: str) -> str:
    if "requests/s" in unit:
        return f"{value:.3f}" if value < 10 else f"{value:.1f}"
    if "requests" in unit:
        return f"{value:,.0f}"
    if "percent" in unit.lower() or unit.endswith("(%)"):
        return f"{value:.1f}%"
    if "ms/token" in unit:
        return f"{value:.1f}"
    if "ms" in unit:
        return f"{value:.1f} ms" if abs(value) < 10 else f"{value:,.0f} ms"
    if abs(value - round(value)) < 0.001:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def text(x: float, y: float, value: object, size: int = 12, weight: int = 500, color: str = INK, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{esc(value)}</text>'
    )


def wrap_text(x: float, y: float, value: str, width: int, size: int = 11, color: str = MUTED, max_lines: int = 2) -> str:
    limit = max(12, int(width / (size * 0.55)))
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    lines = lines[:max_lines]
    if len(lines) == max_lines and sum(len(line.split()) for line in lines) < len(words):
        lines[-1] = lines[-1].rstrip(".") + "..."
    parts = [f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}">']
    for index, line_value in enumerate(lines):
        dy = 0 if index == 0 else size * 1.25
        parts.append(f'<tspan x="{x:.1f}" dy="{dy:.1f}">{esc(line_value)}</tspan>')
    parts.append("</text>")
    return "".join(parts)


def write_svg(target: Path, svg: str) -> None:
    """Validate the shared SVG contract before publishing a generated asset."""
    root = ET.fromstring(svg)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError(f"{target}: root element must be svg")
    if root.attrib.get("viewBox") is None:
        raise ValueError(f"{target}: missing viewBox")
    if root.attrib.get("role") != "img" or not root.attrib.get("aria-label"):
        raise ValueError(f"{target}: missing accessible image description")
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "text" or "x" not in element.attrib:
            continue
        x = float(element.attrib["x"])
        if not 0 <= x <= WIDTH:
            raise ValueError(f"{target}: text x={x} falls outside the viewBox")
    target.write_text(svg)


def scale(value: float, maximum: float, log: bool = False) -> float:
    if maximum <= 0:
        return 0
    if log:
        return math.log10(1 + max(0, value)) / math.log10(1 + maximum)
    return max(0, value) / maximum


def scale_domain(value: float, minimum: float, maximum: float, log: bool) -> float:
    if maximum <= minimum:
        return 0.5
    if log:
        return (math.log10(value) - math.log10(minimum)) / (math.log10(maximum) - math.log10(minimum))
    return (value - minimum) / (maximum - minimum)


def fmt_latency(value_ms: float) -> str:
    return f"{value_ms / 1000:.1f} s" if value_ms >= 1000 else f"{value_ms:.0f} ms"


def log_latency_x(value_ms: float, left: float, right: float, low_seconds: float = 0.3, high_seconds: float = 30.0) -> float:
    seconds = min(high_seconds, max(low_seconds, value_ms / 1000.0))
    low, high = math.log10(low_seconds), math.log10(high_seconds)
    return left + (math.log10(seconds) - low) / (high - low) * (right - left)


def log_latency_axis(
    parts: list[str],
    left: float,
    right: float,
    top: float,
    bottom: float,
    *,
    ticks: tuple[tuple[float, str], ...] = ((0.3, "0.3 s"), (1.0, "1 s"), (3.0, "3 s"), (10.0, "10 s"), (30.0, "30 s")),
    font_size: int = 8,
) -> None:
    for seconds, label in ticks:
        x = log_latency_x(seconds * 1000, left, right)
        parts.append(f'<line x1="{x:.1f}" y1="{top:.1f}" x2="{x:.1f}" y2="{bottom:.1f}" stroke="#e7ebef"/>')
        parts.append(text(x, top - 4, label, font_size, 600, MUTED, "middle"))


def log_marker_row(
    parts: list[str],
    label: str,
    value_ms: float,
    color: str,
    y: float,
    *,
    label_x: float,
    left: float,
    right: float,
    value_x: float,
    marker: str = "square",
    font_size: int = 10,
) -> None:
    x = log_latency_x(value_ms, left, right)
    bar_end = max(x, left + 48)
    parts.append(text(label_x, y + 4, label, font_size, 720, INK))
    parts.append(f'<rect x="{left:.1f}" y="{y - 5:.1f}" width="{right - left:.1f}" height="10" rx="5" fill="#edf1f4"/>')
    parts.append(f'<rect x="{left:.1f}" y="{y - 5:.1f}" width="{bar_end - left:.1f}" height="10" rx="5" fill="{color}"/>')
    parts.append(text(value_x, y + 4, fmt_latency(value_ms), font_size, 800, color, "end"))


def panel_header(title: str, unit: str, takeaway: str, color: str) -> str:
    parts = [
        f'<rect x="20" y="10" width="{PANEL_W}" height="{HEIGHT - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="4" fill="{color}" rx="6"/>',
        text(38, 36, title, 15, 750),
        text(38, 52, unit, 10, 650, MUTED),
    ]
    if takeaway:
        parts.append(wrap_text(38, 68, takeaway, PANEL_W - 36, 10, MUTED))
    return "".join(parts)


def chart_top(takeaway: bool) -> int:
    return 88 if takeaway else 72


def chart_bottom() -> int:
    return HEIGHT - 28


def render_line(panel: dict, color: str) -> str:
    takeaway = panel.get("takeaway", "")
    unit = panel["unit"]
    parts: list[str] = [panel_header(panel["title"], unit, takeaway, color)]
    x_labels = panel["x"]
    series = panel["series"]
    values = [float(v) for _, points in series for v in points]
    maximum = max(values + [1]) * 1.12
    top = chart_top(bool(takeaway))
    bottom = chart_bottom()
    left, right = 78, WIDTH - 44
    statuses = panel.get("statuses") or []
    status_colors = {"neutral": "#7c8996", "warning": "#b83232", "good": "#087f72"}
    x_positions = [left + i * (right - left) / max(1, len(x_labels) - 1) for i in range(len(x_labels))]
    if len(statuses) == len(x_labels):
        step = (right - left) / max(1, len(x_labels) - 1)
        for index, status in enumerate(statuses):
            if status == "neutral":
                continue
            band_left = max(left, x_positions[index] - step / 2)
            band_right = min(right, x_positions[index] + step / 2)
            parts.append(
                f'<rect x="{band_left:.1f}" y="{top:.1f}" width="{band_right - band_left:.1f}" '
                f'height="{bottom - top:.1f}" fill="{status_colors[status]}" opacity="0.16"/>'
            )
    for fraction in [0, 0.5, 1]:
        gy = bottom - (bottom - top) * fraction
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{right}" y2="{gy:.1f}" stroke="#e8ecf0"/>')
        parts.append(text(left - 6, gy + 3, fmt(maximum * fraction, unit), 8, 550, MUTED, "end"))
    tick_step = max(1, math.ceil(len(x_labels) / 6))
    for index, label in enumerate(x_labels):
        if index % tick_step == 0 or index == len(x_labels) - 1:
            label_color = status_colors.get(statuses[index], MUTED) if len(statuses) == len(x_labels) else MUTED
            parts.append(text(x_positions[index], bottom + 16, label, 8, 700, label_color, "middle"))
    for series_index, (name, points) in enumerate(series):
        series_color = COLORS[series_index % len(COLORS)]
        coords = [(x_positions[i], bottom - (bottom - top) * float(value) / maximum) for i, value in enumerate(points)]
        parts.append(
            f'<polyline points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in coords)}" '
            f'fill="none" stroke="{series_color}" stroke-width="2.5"/>'
        )
        for point_index, (px, py) in enumerate(coords):
            status = statuses[point_index] if len(statuses) == len(coords) else "neutral"
            point_color = status_colors[status] if status != "neutral" else series_color
            radius = 5 if status != "neutral" else 3.5
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{radius}" fill="{point_color}"/>')
        parts.append(f'<rect x="{left + series_index * 140}" y="{top - 16}" width="10" height="3" fill="{series_color}"/>')
        parts.append(text(left + 14 + series_index * 140, top - 12, name, 9, 650, MUTED))
    return "".join(parts)


def render_paired(panel: dict, color: str) -> str:
    takeaway = panel.get("takeaway", "")
    unit = panel["unit"] + (", log scale" if panel.get("log") else "")
    parts: list[str] = [panel_header(panel["title"], unit, takeaway, color)]
    groups = panel["groups"]
    rows = panel["rows"]
    all_values = [float(value) for _, values in rows for value in values]
    use_log = bool(panel.get("log"))
    minimum = min(all_values) * 0.8 if use_log else 0
    maximum = max(all_values + [1]) * 1.05
    legend_y = chart_top(bool(takeaway)) - 6
    for index, group in enumerate(groups):
        gx = 38 + index * (PANEL_W / len(groups))
        parts.append(f'<circle cx="{gx + 5}" cy="{legend_y - 3}" r="4.5" fill="{COLORS[index]}"/>')
        parts.append(text(gx + 14, legend_y, group, 9, 650, MUTED))
    chart_top_y = legend_y + 22
    chart_left, chart_right = 170, WIDTH - 56
    row_height = max(34, (chart_bottom() - chart_top_y - 8) / max(1, len(rows)))
    for row_index, (label, values) in enumerate(rows):
        cy = chart_top_y + row_index * row_height + row_height / 2
        positions = [
            chart_left + (chart_right - chart_left) * scale_domain(float(value), minimum, maximum, use_log)
            for value in values
        ]
        parts.append(text(38, cy + 3, label, 10, 650, INK))
        parts.append(f'<line x1="{chart_left}" y1="{cy:.1f}" x2="{chart_right}" y2="{cy:.1f}" stroke="#e8ecf0"/>')
        parts.append(
            f'<line x1="{min(positions):.1f}" y1="{cy:.1f}" x2="{max(positions):.1f}" y2="{cy:.1f}" '
            f'stroke="{LINE}" stroke-width="2.5"/>'
        )
        for group_index, (point_x, value) in enumerate(zip(positions, values)):
            point_color = COLORS[group_index % len(COLORS)]
            parts.append(
                f'<circle cx="{point_x:.1f}" cy="{cy:.1f}" r="6" fill="{point_color}" '
                f'stroke="{SURFACE}" stroke-width="2"/>'
            )
            offset_y = cy - 10 if group_index % 2 == 0 else cy + 14
            parts.append(text(point_x, offset_y, fmt(float(value), panel["unit"]), 8, 750, point_color, "middle"))
    return "".join(parts)


def render_dot(panel: dict, color: str) -> str:
    takeaway = panel.get("takeaway", "")
    unit = panel["unit"] + (", log scale" if panel.get("log") else "")
    parts: list[str] = [panel_header(panel["title"], unit, takeaway, color)]
    rows = panel["rows"]
    values = [float(value) for _, value in rows]
    use_log = bool(panel.get("log"))
    minimum = min(values) * 0.8 if use_log else 0
    maximum = max(values + [1]) * 1.05
    chart_top_y = chart_top(bool(takeaway))
    chart_left, chart_right = 190, WIDTH - 52
    row_height = max(30, (chart_bottom() - chart_top_y) / max(1, len(rows)))
    parts.append(f'<line x1="{chart_left}" y1="{chart_top_y - 10}" x2="{chart_right}" y2="{chart_top_y - 10}" stroke="{LINE}"/>')
    parts.append(text(chart_left, chart_top_y - 16, fmt(minimum if use_log else 0, panel["unit"]), 8, 550, MUTED, "middle"))
    parts.append(text(chart_right, chart_top_y - 16, fmt(max(values), panel["unit"]), 8, 550, MUTED, "middle"))
    for index, (label, value) in enumerate(rows):
        cy = chart_top_y + index * row_height + row_height / 2
        point_x = chart_left + (chart_right - chart_left) * scale_domain(float(value), minimum, maximum, use_log)
        parts.append(text(38, cy + 3, label, 10, 650, INK))
        parts.append(f'<line x1="{chart_left}" y1="{cy:.1f}" x2="{chart_right}" y2="{cy:.1f}" stroke="#e8ecf0"/>')
        parts.append(
            f'<circle cx="{point_x:.1f}" cy="{cy:.1f}" r="6" fill="{color}" stroke="{SURFACE}" stroke-width="2"/>'
        )
        parts.append(text(WIDTH - 38, cy + 3, fmt(float(value), panel["unit"]), 9, 750, INK, "end"))
    return "".join(parts)


def combo_as_line(panel: dict, color: str) -> str:
    line_panel = {
        "kind": "line",
        "title": panel["title"],
        "unit": panel["line_unit"],
        "x": panel["x"],
        "series": [(panel["line_name"], panel["line_values"])],
        "takeaway": panel.get("takeaway", ""),
    }
    return render_line(line_panel, color)


RENDERERS = {
    "line": render_line,
    "paired": render_paired,
    "dot": render_dot,
    "combo": combo_as_line,
}


def render_panel(panel: dict, color: str) -> str:
    renderer = RENDERERS.get(panel["kind"])
    if renderer is None:
        raise ValueError(f"unsupported panel kind for tuning chart: {panel['kind']}")
    body = renderer(panel, color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{esc(panel["title"])}">'
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>'
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAGE}"/>'
        f"{body}</svg>\n"
    )


def render_consolidation_takeaway_svg() -> str:
    """Show three traffic classes entering visible queues before one shared model."""
    teal, orange, blue = "#087f72", "#c56a00", "#2d6cdf"
    height = 300
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="Two high-priority tenants and a lower-priority burst enter separate queues inside the Endpoint Picker before sharing one vLLM model pool.">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        '<defs><marker id="queue-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#697684"/></marker></defs>',
        f'<rect x="32" y="82" width="186" height="174" rx="6" fill="#fbfcfd" stroke="{LINE}"/>',
        text(52, 108, "Traffic", 12, 760, INK),
        f'<rect x="258" y="70" width="384" height="198" rx="7" fill="#f8fcfb" stroke="{teal}" stroke-width="2"/>',
        text(282, 98, "Endpoint Picker", 14, 800, INK),
        text(618, 98, "Policy queues", 10, 700, teal, "end"),
        f'<rect x="696" y="98" width="152" height="140" rx="7" fill="#fbfcfd" stroke="{LINE}"/>',
        text(772, 126, "vLLM", 14, 800, INK, "middle"),
        text(772, 144, "Shared GPU", 10, 650, MUTED, "middle"),
    ]
    rows = [
        (132, "High priority A", teal, 2),
        (182, "High priority B", blue, 2),
        (232, "Lower-priority burst", orange, 8),
    ]
    for y, label, color, count in rows:
        parts.extend([
            f'<rect x="50" y="{y - 16}" width="148" height="32" rx="5" fill="#ffffff" stroke="{color}" stroke-width="1.5"/>',
            f'<circle cx="64" cy="{y}" r="5" fill="{color}"/>',
            text(76, y + 4, label, 10, 720, INK),
            f'<path d="M218 {y} H258" stroke="#9aa5b1" stroke-width="2" marker-end="url(#queue-arrow)"/>',
            f'<rect x="282" y="{y - 18}" width="336" height="36" rx="5" fill="#ffffff" stroke="{color}" stroke-width="1.5"/>',
            text(296, y + 4, label, 10, 720, INK),
        ])
        for index in range(count):
            parts.append(f'<rect x="{426 + index * 20}" y="{y - 7}" width="14" height="14" rx="2" fill="{color}" opacity="0.76"/>')
    parts.append(f'<path d="M642 157 H681" fill="none" stroke="#697684" stroke-width="2.5" marker-end="url(#queue-arrow)"/>')
    for index in range(24):
        row, col = divmod(index, 6)
        slot_color = teal if row == 0 else blue if row == 1 else orange
        parts.append(f'<rect x="{714 + col * 20}" y="{164 + row * 16}" width="14" height="11" rx="2" fill="{slot_color}" opacity="0.62"/>')
    parts.append("</svg>\n")
    return "".join(parts)


def render_consolidation_data_svg(root: Path) -> str:
    """Plot measured traffic and surge p95 TTFT for the consolidation scenario."""
    source = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/traffic-samples.csv"
    tenants = ["realtime tenant A", "realtime tenant B", "standard burst"]
    colors = {"realtime tenant A": "#087f72", "realtime tenant B": "#2d6cdf", "standard burst": "#c56a00"}
    rows: dict[str, list[tuple[int, int]]] = {tenant: [] for tenant in tenants}
    with source.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["detector"] != "request count 128, 10% headroom" or row["repeat"] != "2":
                continue
            tenant = row["tenant"]
            second = int(row["elapsed_seconds"])
            if tenant in rows and 50 <= second <= 220:
                rows[tenant].append((second, int(row["issued_requests"])))

    rates: dict[str, list[tuple[float, float]]] = {}
    for tenant, samples in rows.items():
        samples.sort()
        deltas = [(second, max(0, value - samples[index - 1][1])) for index, (second, value) in enumerate(samples) if index]
        buckets: list[tuple[float, float]] = []
        for start in range(60, 211, 10):
            values = [value for second, value in deltas if start <= second < start + 10]
            buckets.append((start + 5, sum(values) / len(values) if values else 0))
        rates[tenant] = buckets

    analysis = json.loads((source.parent / "analysis.json").read_text())
    selected = analysis["selected_configuration_results"]

    left, right, top, bottom = 78.0, 842.0, 78.0, 218.0
    max_rate = 60.0
    def tx(second: float) -> float:
        return left + (second - 60) / 150 * (right - left)
    def ty(value: float) -> float:
        return bottom - value / max_rate * (bottom - top)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="410" viewBox="0 0 {WIDTH} 410" role="img" aria-label="One model pool served two high-priority tenants during a lower-priority traffic surge. Median surge p95 time to first token was 509 milliseconds for tenant A, 556 milliseconds for tenant B, and 25.9 seconds for the lower-priority burst.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        '<rect width="880" height="410" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="860" height="390" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        text(78, 60, "Traffic rate (requests/s)", 10, 700, MUTED),
        f'<rect x="{tx(100):.1f}" y="{top}" width="{tx(175)-tx(100):.1f}" height="{bottom-top:.1f}" fill="#c56a00" opacity="0.08"/>',
        text((tx(100)+tx(175))/2, 94, "lower-priority surge", 9, 700, orange := "#c56a00", "middle"),
    ]
    for value in (0, 30, 60):
        y = ty(value)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#e7ebef"/>')
        parts.append(text(left - 10, y + 3, value, 8, 600, MUTED, "end"))
    for second in (60, 135, 210):
        parts.append(text(tx(second), 236, f"{second} s", 9, 600, MUTED, "middle"))
    labels = {
        "realtime tenant A": "High-priority tenant A",
        "realtime tenant B": "High-priority tenant B",
        "standard burst": "Lower-priority burst",
    }
    for index, tenant in enumerate(tenants):
        points = " ".join(f"{tx(second):.1f},{ty(value):.1f}" for second, value in rates[tenant])
        color = colors[tenant]
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="3"/>')
        lx = 82 + index * 245
        parts.append(f'<line x1="{lx}" y1="258" x2="{lx+18}" y2="258" stroke="{color}" stroke-width="3"/>')
        parts.append(text(lx + 26, 262, labels[tenant], 10, 650, MUTED))

    chart_left, chart_right, value_x = 242.0, 770.0, 838.0
    parts.append(f'<line x1="32" y1="280" x2="848" y2="280" stroke="{LINE}"/>')
    parts.append(text(32, 304, "Median surge p95 TTFT", 10, 700, MUTED))
    log_latency_axis(parts, chart_left, chart_right, 318, 389)
    values = [
        ("High priority A", float(selected["realtime tenant A"]["median_p95_ttft_ms"]), "#087f72"),
        ("High priority B", float(selected["realtime tenant B"]["median_p95_ttft_ms"]), "#2d6cdf"),
        ("Lower-priority burst", float(selected["standard burst"]["median_p95_ttft_ms"]), "#c56a00"),
    ]
    for index, (label, value, color) in enumerate(values):
        y = 336 + index * 25
        log_marker_row(parts, label, value, color, y, label_x=32, left=chart_left, right=chart_right, value_x=value_x)
    parts.append('</svg>\n')
    return "".join(parts)


def render_engine_takeaway_svg() -> str:
    """Overlay throughput and output-token latency across vLLM sequence limits."""
    settings = [128, 160, 192]
    throughput = [50.53, 50.71, 51.93]
    tpot = [19.57, 27.05, 28.86]
    left, right, top, bottom = 126.0, 810.0, 88.0, 214.0
    green, orange = "#087f72", "#c56a00"

    def x(index: int) -> float:
        return left + index * (right - left) / 2

    def y_throughput(value: float) -> float:
        return bottom - (value - 50.0) / 2.5 * (bottom - top)

    def y_tpot(value: float) -> float:
        return bottom - (value - 18.0) / 12.0 * (bottom - top)

    points_throughput = " ".join(f"{x(i):.1f},{y_throughput(v):.1f}" for i, v in enumerate(throughput))
    points_tpot = " ".join(f"{x(i):.1f},{y_tpot(v):.1f}" for i, v in enumerate(tpot))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="Raising the vLLM sequence limit from 128 to 192 added 1.4 requests per second while p95 time per output token increased from 19.6 to 28.9 milliseconds per token">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAGE}"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="{HEIGHT - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="4" fill="#2d6cdf" rx="6"/>',
        text(38, 36, "More running sequences added little throughput and more token delay", 15, 750),
        text(38, 52, "Three matched runs per setting", 10, 650, MUTED),
        text(left, 73, "Served throughput (requests/s)", 9, 750, green),
        text(right, 73, "p95 TPOT (ms/token)", 9, 750, orange, "end"),
        f'<rect x="{left - 54}" y="{top}" width="108" height="{bottom - top}" fill="{green}" opacity="0.10"/>',
    ]
    for value, label in [(50.0, "50"), (51.0, "51"), (52.0, "52")]:
        y = y_throughput(value)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#e7ebef"/>')
        parts.append(text(left - 12, y + 3, label, 8, 600, green, "end"))
    for value, label in [(20.0, "20"), (25.0, "25"), (30.0, "30")]:
        y = y_tpot(value)
        parts.append(text(right + 12, y + 3, label, 8, 600, orange))
    parts.extend([
        f'<polyline points="{points_throughput}" fill="none" stroke="{green}" stroke-width="4"/>',
        f'<polyline points="{points_tpot}" fill="none" stroke="{orange}" stroke-width="4"/>',
    ])
    for i, setting in enumerate(settings):
        xt = x(i)
        yt = y_throughput(throughput[i])
        yo = y_tpot(tpot[i])
        throughput_x = xt - 10 if i == len(settings) - 1 else xt
        throughput_anchor = "end" if i == len(settings) - 1 else "middle"
        tpot_x = xt + 10 if i == len(settings) - 1 else xt
        tpot_anchor = "start" if i == len(settings) - 1 else "middle"
        parts.extend([
            f'<circle cx="{xt:.1f}" cy="{yt:.1f}" r="6" fill="{green}"/>',
            f'<circle cx="{xt:.1f}" cy="{yo:.1f}" r="6" fill="{orange}"/>',
            text(throughput_x, yt - 10, f"{throughput[i]:.1f}", 9, 800, green, throughput_anchor),
            text(tpot_x, yo + 18, f"{tpot[i]:.1f}", 9, 800, orange, tpot_anchor),
            text(xt, bottom + 18, str(setting), 10, 700, INK, "middle"),
        ])
    parts.extend([
        text(left, 242, "selected", 9, 800, green, "middle"),
        text(468, 242, "+1.4 requests/s · +9.3 ms/token from 128 to 192", 10, 750, INK, "middle"),
        text(468, 254, "Maximum running sequences", 9, 600, MUTED, "middle"),
        "</svg>\n",
    ])
    return "".join(parts)


def render_configuration_engine_svg() -> str:
    """Show each vLLM execution sweep as aligned throughput and latency plots."""
    width, height = 720, 516
    teal, orange = "#087f72", "#c56a00"

    def sweep_panel(
        y: float,
        setting_label: str,
        settings: list[str],
        throughput: list[float],
        latency: list[float],
        latency_unit: str,
        selected_index: int,
    ) -> str:
        panel_x, panel_w, panel_h = 14, 692, 238
        plot_left, plot_right = 150.0, 650.0
        x_positions = [plot_left + index * (plot_right - plot_left) / (len(settings) - 1) for index in range(len(settings))]

        def scaled_y(value: float, values: list[float], top: float, bottom: float) -> float:
            low, high = min(values), max(values)
            padding = max((high - low) * 0.28, max(abs(high), 1.0) * 0.015)
            low -= padding
            high += padding
            return bottom - (value - low) / (high - low) * (bottom - top)

        throughput_top, throughput_bottom = y + 62, y + 116
        latency_top, latency_bottom = y + 144, y + 198
        selected_x = x_positions[selected_index]
        body = [
            f'<rect x="{panel_x}" y="{y}" width="{panel_w}" height="{panel_h}" rx="7" fill="#fbfcfd" stroke="{LINE}"/>',
            f'<rect x="{selected_x - 42:.1f}" y="{y + 28}" width="84" height="190" rx="6" fill="#e8f6f3"/>',
            text(selected_x, y + 43, "selected", 9, 800, teal, "middle"),
            text(34, y + 58, "Throughput", 10, 800, teal),
            text(34, y + 71, "requests/s", 8.5, 650, MUTED),
            text(34, y + 140, "Latency", 10, 800, orange),
            text(34, y + 153, latency_unit, 8.5, 650, MUTED),
            f'<line x1="{plot_left}" y1="{throughput_bottom}" x2="{plot_right}" y2="{throughput_bottom}" stroke="#dfe5ea"/>',
            f'<line x1="{plot_left}" y1="{latency_bottom}" x2="{plot_right}" y2="{latency_bottom}" stroke="#dfe5ea"/>',
        ]

        throughput_points = []
        latency_points = []
        for index, x_pos in enumerate(x_positions):
            throughput_points.append(f"{x_pos:.1f},{scaled_y(throughput[index], throughput, throughput_top, throughput_bottom):.1f}")
            latency_points.append(f"{x_pos:.1f},{scaled_y(latency[index], latency, latency_top, latency_bottom):.1f}")
        body.extend([
            f'<polyline points="{" ".join(throughput_points)}" fill="none" stroke="{teal}" stroke-width="3"/>',
            f'<polyline points="{" ".join(latency_points)}" fill="none" stroke="{orange}" stroke-width="3"/>',
        ])
        for index, (setting, x_pos) in enumerate(zip(settings, x_positions)):
            throughput_y = scaled_y(throughput[index], throughput, throughput_top, throughput_bottom)
            latency_y = scaled_y(latency[index], latency, latency_top, latency_bottom)
            latency_label = f"{latency[index]:,.1f}" if latency[index] < 100 else f"{latency[index]:,.0f}"
            body.extend([
                f'<circle cx="{x_pos:.1f}" cy="{throughput_y:.1f}" r="5" fill="{teal}" stroke="#ffffff" stroke-width="2"/>',
                text(x_pos, throughput_y - 9, f"{throughput[index]:.1f}", 9.5, 800, teal, "middle"),
                f'<circle cx="{x_pos:.1f}" cy="{latency_y:.1f}" r="5" fill="{orange}" stroke="#ffffff" stroke-width="2"/>',
                text(x_pos, latency_y - 9, latency_label, 9.5, 800, orange, "middle"),
                text(x_pos, y + 220, setting, 10, 750, INK, "middle"),
            ])
        body.append(text((plot_left + plot_right) / 2, y + 234, setting_label, 8.5, 650, MUTED, "middle"))
        return "".join(body)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="vLLM execution sweeps show 128 maximum active sequences and 8,192 maximum batched tokens as the selected settings.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        sweep_panel(14, "Maximum active sequences", ["128", "160", "192"], [50.5, 50.7, 51.9], [19.6, 27.1, 28.9], "p95 TPOT (ms/token)", 0),
        sweep_panel(264, "Maximum batched tokens", ["4,096", "8,192", "16,384"], [47.8, 50.5, 48.7], [1877, 1822, 2162], "p95 TTFT (ms)", 1),
        '</svg>\n',
    ]
    return "".join(parts)


def render_configuration_admission_svg() -> str:
    """Locate request-count admission and reactive pressure signals spatially."""
    width, height = 880, 330
    teal, blue, orange, gray = "#087f72", "#2d6cdf", "#c56a00", "#667180"

    def component(x: float, y: float, w: float, h: float, label: str, border: str, fill: str) -> str:
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.5"/>'
            + text(x + w / 2, y + h / 2 + 4, label, 9, 780, INK, "middle")
        )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Request count is measured in the Endpoint Picker before dispatch. Queue depth is measured in the vLLM waiting queue and KV pressure in the vLLM KV cache after dispatch. Both pressure signals return to the Endpoint Picker admission gate.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{width - 20}" height="{height - 20}" rx="7" fill="#ffffff" stroke="{LINE}"/>',
        '<defs>'
        '<marker id="admit-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#667180"/></marker>'
        '<marker id="queue-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#2d6cdf"/></marker>'
        '<marker id="kv-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#c56a00"/></marker>'
        '</defs>',
        component(28, 108, 112, 50, "New requests", LINE, "#fbfcfd"),
        f'<rect x="176" y="38" width="382" height="254" rx="8" fill="#f4fbf9" stroke="{teal}" stroke-width="2"/>',
        text(367, 64, "Endpoint Picker", 12, 800, INK, "middle"),
        f'<rect x="204" y="108" width="146" height="50" rx="6" fill="#ffffff" stroke="{teal}" stroke-width="1.5"/>',
        text(277, 129, "Request count", 9, 780, INK, "middle"),
        text(277, 146, "128 in-flight", 8, 700, MUTED, "middle"),
        component(384, 108, 146, 50, "Admission gate", teal, "#ffffff"),
        f'<rect x="204" y="220" width="146" height="44" rx="6" fill="#fffaf5" stroke="{orange}" stroke-width="1.5"/>',
        text(277, 238, "Policy queue", 9, 780, INK, "middle"),
        text(277, 253, "new work waits", 8, 700, MUTED, "middle"),
        f'<rect x="384" y="220" width="146" height="44" rx="6" fill="#ffffff" stroke="{gray}" stroke-width="1.5"/>',
        text(457, 238, "Reactive pressure", 9, 780, INK, "middle"),
        text(457, 253, "queue or KV signal", 8, 700, MUTED, "middle"),
        f'<path d="M140 133 H204" fill="none" stroke="{gray}" stroke-width="2" marker-end="url(#admit-arrow)"/>',
        f'<path d="M350 133 H384" fill="none" stroke="{teal}" stroke-width="2" marker-end="url(#admit-arrow)"/>',
        f'<rect x="588" y="38" width="256" height="254" rx="8" fill="#f7faff" stroke="{blue}" stroke-width="2"/>',
        text(716, 64, "vLLM", 12, 800, INK, "middle"),
        component(616, 108, 200, 50, "Running requests", LINE, "#ffffff"),
        component(616, 188, 200, 40, "Waiting queue · depth", blue, "#ffffff"),
        component(616, 244, 200, 40, "KV cache · pressure", orange, "#ffffff"),
        f'<path d="M530 133 H616" fill="none" stroke="{gray}" stroke-width="2" marker-end="url(#admit-arrow)"/>',
        f'<path d="M420 158 V196 H277 V220" fill="none" stroke="{orange}" stroke-width="2" marker-end="url(#kv-arrow)"/>',
        f'<path d="M616 208 H566 V232 H530" fill="none" stroke="{blue}" stroke-width="2" marker-end="url(#queue-arrow)"/>',
        f'<path d="M616 264 H566 V252 H530" fill="none" stroke="{orange}" stroke-width="2" marker-end="url(#kv-arrow)"/>',
        f'<path d="M457 220 V158" fill="none" stroke="{gray}" stroke-width="2" marker-end="url(#admit-arrow)"/>',
    ]
    parts.append('</svg>\n')
    return "".join(parts)


def render_configuration_request_shape_svg() -> str:
    """Compare retained request-count and input-token methods with grouped bars."""
    width, height = 880, 330
    neutral, blue, emphasis = "#667180", "#2d6cdf", "#182331"
    groups = [
        ("Short", 8419, 6621),
        ("Medium", 5659, 5213),
        ("Long", 4163, 6352),
    ]
    left, right, top, bottom = 94.0, 832.0, 58.0, 270.0
    maximum = 9000.0

    def y(value: float) -> float:
        return bottom - value / maximum * (bottom - top)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Grouped bars compare request-count and input-token admission p95 first-token latency for short, medium, and long prompts. Input-token admission was lower for short and medium prompts; request-count admission was lower for long prompts.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{width - 20}" height="{height - 20}" rx="7" fill="#ffffff" stroke="{LINE}"/>',
        text(34, 38, "p95 TTFT (milliseconds)", 10, 700, MUTED),
        f'<rect x="560" y="26" width="12" height="12" rx="2" fill="{neutral}"/>',
        text(580, 36, "Request count", 9, 700, INK),
        f'<rect x="700" y="26" width="12" height="12" rx="2" fill="{blue}"/>',
        text(720, 36, "Input tokens", 9, 700, INK),
    ]
    for tick in (0, 3000, 6000, 9000):
        tick_y = y(tick)
        parts.append(f'<line x1="{left}" y1="{tick_y:.1f}" x2="{right}" y2="{tick_y:.1f}" stroke="#e4e9ee"/>')
        parts.append(text(left - 12, tick_y + 3, f"{tick:,}", 8.5, 650, MUTED, "end"))
    centers = [220.0, 456.0, 692.0]
    bar_width = 60.0
    for center, (label, request_value, token_value) in zip(centers, groups):
        lower_value = min(request_value, token_value)
        for offset, value, color in [(-36, request_value, neutral), (36, token_value, blue)]:
            x_pos = center + offset - bar_width / 2
            top_y = y(value)
            stroke = emphasis if value == lower_value else "none"
            stroke_width = 3 if value == lower_value else 0
            parts.append(f'<rect x="{x_pos:.1f}" y="{top_y:.1f}" width="{bar_width}" height="{bottom - top_y:.1f}" rx="5" fill="{color}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            parts.append(text(x_pos + bar_width / 2, top_y - 8, f"{value:,}", 10, 800, color, "middle"))
            if value == lower_value:
                cx, cy = x_pos + bar_width - 3, top_y + 7
                parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8" fill="#087f72" stroke="#ffffff" stroke-width="2"/>')
                parts.append(f'<path d="M{cx-3:.1f} {cy:.1f} L{cx-0.5:.1f} {cy+2.5:.1f} L{cx+4:.1f} {cy-3:.1f}" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
        parts.append(text(center, 296, label, 11, 800, INK, "middle"))
    parts.append('</svg>\n')
    return "".join(parts)


def render_priority_tiers_takeaway_svg() -> str:
    """Show the priority-tier result for the saturated-pool takeaway."""
    teal, gold, blue, bronze = "#087f72", "#c69200", "#2d6cdf", "#b85c00"
    root = Path(__file__).resolve().parents[1]
    production = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios"
    priority = json.loads((production / "priority-tiers/analysis.json").read_text())["selected_configuration_results"]
    rows = [
        ("Platinum", priority["platinum realtime"]["median_p95_ttft_ms"], teal),
        ("Gold", priority["gold realtime"]["median_p95_ttft_ms"], gold),
        ("Silver", priority["silver standard"]["median_p95_ttft_ms"], blue),
        ("Bronze batch", priority["bronze batch"]["median_p95_ttft_ms"], bronze),
    ]
    height = 264
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="In the priority-tier scenario, platinum, gold, and silver traffic had median surge p95 time to first token below one second while bronze batch exceeded 13 seconds.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        text(32, 38, "Median surge p95 TTFT (seconds, log scale)", 10, 700, MUTED),
    ]
    chart_left, chart_right = 180.0, 776.0

    parts.append(f'<rect x="32" y="78" width="816" height="142" rx="6" fill="#fbfcfd" stroke="{LINE}"/>')
    one_second_x = log_latency_x(1000, chart_left, chart_right)
    parts.append(f'<rect x="{chart_left:.1f}" y="104" width="{one_second_x - chart_left:.1f}" height="90" fill="#eef8f6" opacity="0.75"/>')
    log_latency_axis(parts, chart_left, chart_right, 104, 194)
    for row_index, (label, value, color) in enumerate(rows):
        log_marker_row(parts, label, float(value), color, 126 + row_index * 22, label_x=56, left=chart_left, right=chart_right, value_x=824)
    parts.append("</svg>\n")
    return "".join(parts)


def render_batch_isolation_takeaway_svg() -> str:
    """Show the batch-isolation result in the same visual language as priority tiers."""
    teal, blue, orange = "#087f72", "#2d6cdf", "#c56a00"
    root = Path(__file__).resolve().parents[1]
    package = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/analysis.json"
    results = json.loads(package.read_text())["selected_configuration_results"]
    rows = [
        ("Realtime", results["realtime"]["median_p95_ttft_ms"], teal),
        ("Standard", results["standard"]["median_p95_ttft_ms"], blue),
        ("Batch", results["batch"]["median_p95_ttft_ms"], orange),
    ]
    height = 248
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="In the batch-isolation scenario, realtime and standard traffic stayed below one second while batch absorbed more than 13 seconds of surge p95 time to first token.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        text(32, 38, "Median surge p95 TTFT (seconds, log scale)", 10, 700, MUTED),
    ]
    chart_left, chart_right = 170.0, 776.0

    parts.append(f'<rect x="32" y="78" width="816" height="134" rx="6" fill="#fbfcfd" stroke="{LINE}"/>')
    one_second_x = log_latency_x(1000, chart_left, chart_right)
    parts.append(f'<rect x="{chart_left:.1f}" y="104" width="{one_second_x - chart_left:.1f}" height="84" fill="#eef8f6" opacity="0.75"/>')
    log_latency_axis(parts, chart_left, chart_right, 104, 188)
    for row_index, (label, value, color) in enumerate(rows):
        log_marker_row(parts, label, float(value), color, 132 + row_index * 28, label_x=56, left=chart_left, right=chart_right, value_x=824)
    parts.append("</svg>\n")
    return "".join(parts)


def render_scenario_latency_svg(
    rows: list[tuple[str, float, str]],
    aria_label: str,
    *,
    chart_left: float = 180.0,
) -> str:
    """Render one outcome-only production scenario with a shared scale."""
    width = WIDTH
    row_gap = 34
    height = 100 + len(rows) * row_gap
    chart_right = 758.0
    axis_top = 72.0
    axis_bottom = height - 28.0
    first_row_y = 94.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{aria_label}">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{width - 20}" height="{height - 20}" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        text(34, 38, "Median surge p95 TTFT (seconds, log scale)", 12, 700, MUTED),
        f'<rect x="28" y="52" width="{width - 56}" height="{height - 76}" rx="6" fill="#fbfcfd" stroke="{LINE}"/>',
    ]
    one_second_x = log_latency_x(1000, chart_left, chart_right)
    parts.append(
        f'<rect x="{chart_left:.1f}" y="{axis_top:.1f}" width="{one_second_x - chart_left:.1f}" '
        f'height="{axis_bottom - axis_top:.1f}" fill="#eef8f6" opacity="0.75"/>'
    )
    log_latency_axis(parts, chart_left, chart_right, axis_top, axis_bottom, font_size=11)
    for row_index, (label, value, color) in enumerate(rows):
        log_marker_row(
            parts,
            label,
            float(value),
            color,
            first_row_y + row_index * row_gap,
            label_x=54,
            left=chart_left,
            right=chart_right,
            value_x=840,
            font_size=11,
        )
    parts.append("</svg>\n")
    return "".join(parts)


def render_priority_tiers_section_svg() -> str:
    """Show the priority-tier measurements without repeating the README heading."""
    teal, gold, blue, bronze = "#087f72", "#c69200", "#2d6cdf", "#b85c00"
    root = Path(__file__).resolve().parents[1]
    production = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios"
    results = json.loads((production / "priority-tiers/analysis.json").read_text())["selected_configuration_results"]
    rows = [
        ("Platinum", results["platinum realtime"]["median_p95_ttft_ms"], teal),
        ("Gold", results["gold realtime"]["median_p95_ttft_ms"], gold),
        ("Silver", results["silver standard"]["median_p95_ttft_ms"], blue),
        ("Bronze batch", results["bronze batch"]["median_p95_ttft_ms"], bronze),
    ]
    return render_scenario_latency_svg(
        rows,
        "Platinum, gold, and silver traffic stayed below one second median surge p95 TTFT while bronze batch exceeded 13 seconds.",
    )


def render_batch_isolation_section_svg() -> str:
    """Show the batch-isolation measurements without repeating the README heading."""
    teal, blue, orange = "#087f72", "#2d6cdf", "#c56a00"
    root = Path(__file__).resolve().parents[1]
    package = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/analysis.json"
    results = json.loads(package.read_text())["selected_configuration_results"]
    rows = [
        ("Real-time", results["realtime"]["median_p95_ttft_ms"], teal),
        ("Standard", results["standard"]["median_p95_ttft_ms"], blue),
        ("Batch", results["batch"]["median_p95_ttft_ms"], orange),
    ]
    return render_scenario_latency_svg(
        rows,
        "Real-time and standard traffic stayed below one second median surge p95 TTFT while batch exceeded 13 seconds.",
        chart_left=170.0,
    )


def render_batch_isolation_traffic_svg() -> str:
    """Show the measured traffic rate that produced the batch-isolation result."""
    root = Path(__file__).resolve().parents[1]
    source = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/batch-isolation/traffic-samples.csv"
    tenants = ["realtime", "standard", "batch"]
    colors = {"realtime": "#087f72", "standard": "#2d6cdf", "batch": "#b85c00"}
    samples: dict[str, list[tuple[int, int]]] = {tenant: [] for tenant in tenants}
    with source.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["detector"] != "request count 128, 15% headroom" or row["repeat"] != "2":
                continue
            tenant = row["tenant"]
            second = int(row["elapsed_seconds"])
            if tenant in samples and 50 <= second <= 220:
                samples[tenant].append((second, int(row["issued_requests"])))

    rates: dict[str, list[tuple[float, float]]] = {}
    for tenant, tenant_samples in samples.items():
        tenant_samples.sort()
        deltas = [
            (second, max(0, value - tenant_samples[index - 1][1]))
            for index, (second, value) in enumerate(tenant_samples)
            if index
        ]
        buckets: list[tuple[float, float]] = []
        for start in range(60, 211, 10):
            values = [value for second, value in deltas if start <= second < start + 10]
            buckets.append((start + 5, sum(values) / len(values) if values else 0))
        rates[tenant] = buckets

    width, height = WIDTH, 300
    left, right, top, bottom = 82.0, 850.0, 44.0, 226.0
    maximum = max(value for series in rates.values() for _, value in series)
    ceiling = max(10.0, math.ceil(maximum / 10.0) * 10.0)

    def x(second: float) -> float:
        return left + (second - 60.0) / 150.0 * (right - left)

    def y(value: float) -> float:
        return bottom - value / ceiling * (bottom - top)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Measured request rate during the selected batch-isolation repeat shows batch traffic filling the shared pool while real-time and standard traffic remain steady">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        text(left, 26, "Requests per second", 10, 700, MUTED),
    ]
    for tick in (0, ceiling / 2, ceiling):
        ty = y(tick)
        parts.append(f'<line x1="{left}" y1="{ty:.1f}" x2="{right}" y2="{ty:.1f}" stroke="#e5eaee"/>')
        parts.append(text(left - 14, ty + 4, f"{tick:.0f}", 9, 650, MUTED, "end"))
    for second in (60, 135, 210):
        tx = x(second)
        parts.append(text(tx, bottom + 24, f"{second} s", 9, 650, MUTED, "middle"))
    parts.append(f'<rect x="{x(90):.1f}" y="{top}" width="{x(190)-x(90):.1f}" height="{bottom-top}" fill="#fbf2e8" opacity="0.75"/>')
    for tenant in tenants:
        points = " ".join(f"{x(second):.1f},{y(value):.1f}" for second, value in rates[tenant])
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colors[tenant]}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
    legend = [("Real-time", "realtime"), ("Standard", "standard"), ("Batch", "batch")]
    for index, (label, tenant) in enumerate(legend):
        lx = 250 + index * 190
        parts.append(f'<line x1="{lx}" y1="270" x2="{lx+28}" y2="270" stroke="{colors[tenant]}" stroke-width="4"/>')
        parts.append(text(lx + 38, 274, label, 10, 700, INK))
    parts.append("</svg>\n")
    return "".join(parts)


def render_consolidation_section_svg() -> str:
    """Show consolidation as a single claim: peers stay fast while the burst waits."""
    teal, blue, orange = "#087f72", "#2d6cdf", "#c56a00"
    root = Path(__file__).resolve().parents[1]
    package = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/consolidation/analysis.json"
    results = json.loads(package.read_text())["selected_configuration_results"]
    rows = [
        ("High priority A", results["realtime tenant A"]["median_p95_ttft_ms"], teal),
        ("High priority B", results["realtime tenant B"]["median_p95_ttft_ms"], blue),
        ("Lower-priority burst", results["standard burst"]["median_p95_ttft_ms"], orange),
    ]
    return render_scenario_latency_svg(
        rows,
        "Two high-priority tenants stayed below one second median surge p95 TTFT while the lower-priority burst waited about 25.9 seconds.",
        chart_left=190.0,
    )


def render_same_priority_fairness_section_svg() -> str:
    """Show the same-priority result as one concise data figure."""
    orange, teal, blue = "#c56a00", "#087f72", "#2d6cdf"
    root = Path(__file__).resolve().parents[1]
    package = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/same-priority-fairness/analysis.json"
    results = json.loads(package.read_text())["selected_configuration_results"]
    rows = [
        ("Tenant A burst", results["realtime burster A"]["median_p95_ttft_ms"], orange),
        ("Peer B", results["realtime peer B"]["median_p95_ttft_ms"], teal),
        ("Peer C", results["realtime peer C"]["median_p95_ttft_ms"], blue),
    ]
    return render_scenario_latency_svg(
        rows,
        "Tenant A's burst reached about 12.1 seconds median surge p95 TTFT while peers B and C stayed near half a second.",
        chart_left=172.0,
    )


def render_priority_dispatch_explainer_svg() -> str:
    """Show priority queues inside the Endpoint Picker before one shared model."""
    teal, gold, blue, bronze = "#087f72", "#c69200", "#2d6cdf", "#b85c00"
    height = 300
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="The Endpoint Picker dispatches high-priority work first while lower-priority batch waits for shared model capacity">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        '<defs><marker id="dispatch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#697684"/></marker></defs>',
        text(32, 38, "Higher-priority queues dispatch before bronze batch", 17, 780, INK),
        f'<rect x="32" y="58" width="610" height="220" rx="7" fill="#f8fcfb" stroke="{teal}" stroke-width="2"/>',
        text(56, 84, "Endpoint Picker", 14, 800, INK),
        text(618, 84, "Priority queues", 10, 700, teal, "end"),
        f'<rect x="700" y="96" width="148" height="150" rx="7" fill="#fbfcfd" stroke="{LINE}"/>',
        text(774, 124, "vLLM", 14, 800, INK, "middle"),
        text(774, 142, "Shared GPU", 10, 650, MUTED, "middle"),
    ]
    rows = [
        (116, "Platinum", teal, 3),
        (160, "Gold", gold, 4),
        (204, "Silver", blue, 5),
        (248, "Bronze batch", bronze, 10),
    ]
    for y, label, color, count in rows:
        parts.append(f'<rect x="54" y="{y - 16}" width="560" height="32" rx="5" fill="#ffffff" stroke="{color}" stroke-width="1.5"/>')
        parts.append(text(70, y + 4, label, 10, 740, INK))
        for index in range(count):
            parts.append(f'<rect x="{236 + index * 20}" y="{y-7}" width="14" height="14" rx="2" fill="{color}" opacity="0.80"/>')
    parts.extend([
        f'<path d="M642 168 H682" stroke="#697684" stroke-width="2.5" marker-end="url(#dispatch-arrow)"/>',
    ])
    for index in range(24):
        row, col = divmod(index, 6)
        color = teal if index < 12 else blue
        parts.append(f'<rect x="{716+col*19}" y="{158+row*17}" width="13" height="11" rx="2" fill="{color}" opacity="0.62"/>')
    parts.append("</svg>\n")
    return "".join(parts)


def render_configuration_map_svg() -> str:
    """Summarize the configuration sequence as a five-step decision ladder."""
    teal, blue, orange, gray = "#087f72", "#2d6cdf", "#c56a00", "#697684"
    height = 214
    steps = [
        ("1", "GPU capacity", ("128 requests",), teal),
        ("2", "vLLM limits", ("128 sequences", "8,192 tokens"), blue),
        ("3", "Wait signal", ("request count",), teal),
        ("4", "Request shape", ("input-token count",), orange),
        ("5", "Evidence", ("link every sweep",), gray),
    ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="The configuration process first finds GPU capacity, then sets vLLM limits, chooses when requests wait, accounts for request shape, and links every sweep to its evidence.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="7" fill="#ffffff" stroke="{LINE}"/>',
        '<defs><marker id="ladder-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="3.5" markerHeight="3.5" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#8b96a3"/></marker></defs>',
    ]
    box_w, box_h, gap, start_x, top = 152, 116, 20, 20, 48
    for index, (number, label, values, color) in enumerate(steps):
        x = start_x + index * (box_w + gap)
        parts.extend([
            f'<rect x="{x}" y="{top}" width="{box_w}" height="{box_h}" rx="7" fill="#fbfcfd" stroke="{color}" stroke-width="1.5"/>',
            f'<circle cx="{x + 22}" cy="{top + 22}" r="12" fill="{color}"/>',
            text(x + 22, top + 26, number, 9, 800, "#ffffff", "middle"),
            text(x + box_w / 2, top + 58, label, 11, 800, INK, "middle"),
        ])
        value_start = top + 84 - (len(values) - 1) * 6
        for line_index, value in enumerate(values):
            parts.append(text(x + box_w / 2, value_start + line_index * 15, value, 8.5, 700, color, "middle"))
        if index < len(steps) - 1:
            parts.append(f'<path d="M{x + box_w + 3} {top + box_h / 2} H{x + box_w + gap - 4}" stroke="#8b96a3" stroke-width="1.4" marker-end="url(#ladder-arrow)"/>')
    parts.append("</svg>\n")
    return "".join(parts)


def render_flow_control_architecture_svg() -> str:
    """Place flow control in the llm-d request path as a component diagram."""
    teal, blue, orange, gold = "#087f72", "#2d6cdf", "#c56a00", "#d79a00"
    height = 214
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="Requests carry tenant and priority metadata through the llm-d Gateway, then flow control queues inside the Endpoint Picker, then to a vLLM model pool.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="6" fill="none" stroke="{LINE}"/>',
        '<defs>',
        '<marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#8b96a3"/></marker>',
        '</defs>',
    ]
    components = [
        (30, 42, 150, 136, "Requests", "", "#fbfcfd", LINE),
        (208, 52, 126, 112, "llm-d Gateway / Envoy", "routes request", "#fbfcfd", LINE),
        (374, 30, 302, 164, "Endpoint Picker", "flow control", "#f8fcfb", teal),
        (730, 42, 120, 136, "vLLM", "model pool", "#fbfcfd", LINE),
    ]
    for x, y, width, box_height, title_value, subtitle, fill, stroke in components:
        parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{box_height}" rx="7" fill="{fill}" stroke="{stroke}" stroke-width="{2 if stroke == teal else 1}"/>')
        parts.append(text(x + width / 2, y + 28, title_value, 11, 780, INK, "middle"))
        if subtitle:
            parts.append(text(x + width / 2, y + 46, subtitle, 8.5, 650, MUTED, "middle"))
    for start, end in [(180, 208), (334, 374), (676, 730)]:
        parts.append(f'<path d="M{start} 110 H{end - 10}" stroke="#8b96a3" stroke-width="2.5" marker-end="url(#arch-arrow)"/>')
    traffic = [(92, "Platinum", teal), (116, "Gold", gold), (140, "Silver", blue), (164, "Bronze", orange)]
    for y, label, color in traffic:
        parts.append(f'<circle cx="52" cy="{y - 4}" r="5" fill="{color}"/>')
        parts.append(text(66, y, label, 8.5, 700, INK))
    for index, label in enumerate(("tenant", "priority")):
        y = 104 + index * 28
        parts.append(f'<rect x="224" y="{y}" width="94" height="20" rx="4" fill="#ffffff" stroke="{LINE}"/>')
        parts.append(text(271, y + 14, label, 8.5, 760, MUTED, "middle"))
    queues = [(94, "Platinum", teal, 2), (120, "Gold", gold, 3), (146, "Silver", blue, 4), (172, "Bronze", orange, 6)]
    for y, label, color, count in queues:
        parts.append(f'<rect x="392" y="{y - 14}" width="264" height="22" rx="4" fill="#ffffff" stroke="{color}"/>')
        parts.append(text(404, y + 1, label, 8, 700, INK))
        for index in range(count):
            parts.append(f'<rect x="{500 + index * 18}" y="{y - 9}" width="12" height="12" rx="2" fill="{color}" opacity="0.78"/>')
    for index in range(20):
        row, col = divmod(index, 5)
        color = teal if index < 10 else blue
        parts.append(f'<rect x="{752 + col * 15}" y="{104 + row * 16}" width="10" height="10" rx="2" fill="{color}" opacity="0.62"/>')
    parts.append("</svg>\n")
    return "".join(parts)


def render_dispatch_path_svg() -> str:
    """Zoom into the tested tenant queues inside the Endpoint Picker."""
    teal, orange = "#087f72", "#c56a00"
    height = 286
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="Inside the Endpoint Picker, the consolidation example groups Platinum Tenants A and B in priority 100 and Bronze Tenant C in priority 0 before dispatch to the shared vLLM model pool.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="6" fill="none" stroke="{LINE}"/>',
        '<defs><marker id="dispatch-active-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#087f72"/></marker></defs>',
        f'<rect x="34" y="26" width="646" height="234" rx="8" fill="#f8fcfb" stroke="{teal}" stroke-width="2"/>',
        text(357, 51, "Endpoint Picker", 12, 800, INK, "middle"),
        f'<rect x="724" y="70" width="124" height="146" rx="7" fill="#fbfcfd" stroke="{LINE}"/>',
        text(786, 96, "vLLM", 11, 800, INK, "middle"),
        text(786, 114, "model pool", 8.5, 650, MUTED, "middle"),
    ]
    parts.extend([
        text(58, 78, "Priority 100 · Platinum", 10, 800, teal),
        f'<rect x="58" y="90" width="594" height="86" rx="6" fill="#ffffff" stroke="{teal}" stroke-width="1.5"/>',
        text(78, 118, "Tenant A", 9, 760, INK),
        f'<rect x="162" y="101" width="466" height="26" rx="5" fill="#fbfcfd" stroke="{LINE}"/>',
        text(78, 156, "Tenant B", 9, 760, INK),
        f'<rect x="162" y="139" width="466" height="26" rx="5" fill="#fbfcfd" stroke="{LINE}"/>',
        text(58, 204, "Priority 0 · Bronze", 10, 800, orange),
        f'<rect x="58" y="216" width="594" height="34" rx="6" fill="#ffffff" stroke="{orange}" stroke-width="1.5"/>',
        text(78, 238, "Tenant C", 9, 760, INK),
        f'<rect x="162" y="221" width="466" height="24" rx="5" fill="#fbfcfd" stroke="{LINE}"/>',
        f'<path d="M652 132 H712" stroke="{teal}" stroke-width="2.6" marker-end="url(#dispatch-active-arrow)"/>',
    ])
    for index in range(3):
        parts.append(f'<rect x="{184 + index * 18}" y="108" width="12" height="12" rx="2" fill="{teal}" opacity="0.76"/>')
    for index in range(2):
        parts.append(f'<rect x="{184 + index * 18}" y="146" width="12" height="12" rx="2" fill="{teal}" opacity="0.76"/>')
    for index in range(10):
        parts.append(f'<rect x="{184 + index * 18}" y="227" width="12" height="12" rx="2" fill="{orange}" opacity="0.76"/>')
    for index in range(20):
        row, col = divmod(index, 5)
        color = teal if index < 10 else orange
        parts.append(f'<rect x="{752 + col * 15}" y="{132 + row * 16}" width="10" height="10" rx="2" fill="{color}" opacity="0.68"/>')
    parts.append("</svg>\n")
    return "".join(parts)


def render_batch_interference_takeaway_svg() -> str:
    """Compare real-time latency with vertical log-scale columns."""
    orange = "#c56a00"
    values = [("Real-time only", 0.133, "133 ms", "#667180"), ("Batch running", 15.378, "15.4 s", orange)]
    height = 300
    top, bottom = 62.0, 236.0
    log_min, log_max = math.log10(0.1), math.log10(30.0)

    def y(seconds: float) -> float:
        return bottom - (math.log10(seconds) - log_min) / (log_max - log_min) * (bottom - top)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="Batch in vLLM raised real-time p95 TTFT from 133 ms to 15.4 seconds">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="{PAGE}"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="{height - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="4" fill="{orange}" rx="6"/>',
        text(38, 40, "Real-time p95 TTFT (seconds, log scale)", 12, 700, MUTED),
    ]
    for seconds in (0.1, 0.3, 1, 3, 10, 30):
        yp = y(seconds)
        parts.append(f'<line x1="104" y1="{yp:.1f}" x2="826" y2="{yp:.1f}" stroke="#e7ebef"/>')
        parts.append(text(92, yp + 3, f"{seconds:g}", 8, 650, MUTED, "end"))
    for center, (label, seconds, value_label, color) in zip((300, 600), values):
        top_y = y(seconds)
        parts.append(f'<rect x="{center - 62}" y="{top_y:.1f}" width="124" height="{bottom - top_y:.1f}" rx="6" fill="{color}"/>')
        parts.append(text(center, top_y - 10, value_label, 12, 800, color, "middle"))
        parts.append(text(center, 262, label, 11, 760, INK, "middle"))

    parts.append("</svg>\n")
    return "".join(parts)


def render_scale_takeaway_svg() -> str:
    """Pair total throughput growth with stable per-GPU throughput."""
    replicas = [1, 2, 4]
    throughput = [4.293, 4.317, 4.302]
    rejections = [5, 1, 0]
    total = [replica * value for replica, value in zip(replicas, throughput)]
    blue, purple, orange = "#2d6cdf", "#6550a5", "#c56a00"
    height = 336

    def per_gpu_y(value: float) -> float:
        return 238 - (value - 4.25) / 0.10 * 126
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="Total served throughput rose with replica count while served throughput per GPU stayed within 0.6 percent from one to four replicas">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAGE}"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="{height - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        f'<rect x="38" y="56" width="386" height="244" fill="#fbfcfd" stroke="{LINE}" rx="6"/>',
        f'<rect x="456" y="56" width="386" height="244" fill="#fbfcfd" stroke="{LINE}" rx="6"/>',
        text(58, 82, "Total served throughput (requests/s)", 11, 750, MUTED),
        text(476, 82, "Served throughput per GPU (requests/s)", 11, 750, MUTED),
    ]
    for tick in (0, 6, 12, 18):
        yp = 252 - tick / 18 * 144
        parts.append(f'<line x1="74" y1="{yp:.1f}" x2="404" y2="{yp:.1f}" stroke="#e7ebef"/>')
        parts.append(text(66, yp + 3, tick, 8, 650, MUTED, "end"))
    centers = (126, 232, 338)
    for center, replica, value in zip(centers, replicas, total):
        top_y = 252 - value / 18 * 144
        parts.append(f'<rect x="{center - 34}" y="{top_y:.1f}" width="68" height="{252 - top_y:.1f}" rx="5" fill="{blue}"/>')
        parts.append(text(center, top_y - 8, f"{value:.2f}", 10, 800, blue, "middle"))
        parts.append(text(center, 276, f"{replica} replica" + ("s" if replica > 1 else ""), 9, 700, INK, "middle"))
    for value in (4.25, 4.30, 4.35):
        yp = per_gpu_y(value)
        parts.append(f'<line x1="492" y1="{yp:.1f}" x2="816" y2="{yp:.1f}" stroke="#e7ebef"/>')
        parts.append(text(484, yp + 3, f"{value:.2f}", 8, 650, MUTED, "end"))
    points = " ".join(f"{540 + i * 110:.1f},{per_gpu_y(v):.1f}" for i, v in enumerate(throughput))
    parts.append(f'<polyline points="{points}" fill="none" stroke="{purple}" stroke-width="3"/>')
    for index, (replica, value, non200) in enumerate(zip(replicas, throughput, rejections)):
        xp, yp = 540 + index * 110, per_gpu_y(value)
        parts.extend([
            f'<circle cx="{xp:.1f}" cy="{yp:.1f}" r="6" fill="{purple}"/>',
            text(xp, yp - 11, f"{value:.2f}", 10, 800, purple, "middle"),
            text(xp, 276, f"{replica} replica" + ("s" if replica > 1 else ""), 9, 700, INK, "middle"),
            f'<circle cx="{xp - 24:.1f}" cy="292" r="3.5" fill="{orange if non200 else blue}"/>',
            text(xp - 16, 295, f"non-200: {non200}", 7.5, 650, MUTED),
        ])
    parts.append(text(816, 100, "0.6% spread", 9, 800, purple, "end"))
    parts.append("</svg>\n")
    return "".join(parts)


def render_mixed_takeaway_svg() -> str:
    """Admission tradeoff: request count protects realtime; input tokens help batch."""
    req_premium, tok_premium = 1994, 2914
    req_batch, tok_batch = 8654, 2832
    neutral, blue, emphasis = "#667180", "#2d6cdf", "#182331"
    chart_top, baseline = 122, 204
    chart_h = baseline - chart_top
    height = 292
    inner_h = 156
    label_y = 218
    bar_w = 88
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="Request-count admission lowered real-time p95 time to first token by 920 milliseconds; input-token admission made batch p95 time to first token about 3 times lower.">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="{PAGE}"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="{height - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="4" fill="{blue}" rx="6"/>',
        text(38, 42, "Median surge p95 TTFT", 12, 740, MUTED),
        f'<rect x="38" y="{TAKEAWAY_INNER_TOP}" width="382" height="{inner_h}" fill="#f7f8fa" stroke="{LINE}" rx="6"/>',
        text(56, 91, "Real-time", 12, 760, INK),
    ]
    prem_max = tok_premium
    for index, (label, value, color) in enumerate(
        [("Request count", req_premium, neutral), ("Input tokens", tok_premium, blue)]
    ):
        bx = 102 + index * (bar_w + 64)
        bh = chart_h * value / prem_max
        by = baseline - bh
        stroke = emphasis if value == min(req_premium, tok_premium) else "none"
        parts.append(f'<rect x="{bx:.0f}" y="{by:.1f}" width="{bar_w}" height="{bh:.1f}" rx="5" fill="{color}" stroke="{stroke}" stroke-width="{3 if stroke != "none" else 0}"/>')
        parts.append(text(bx + bar_w / 2, by - 6, f"{value:,} ms", 12, 800, INK, "middle"))
        if value == min(req_premium, tok_premium):
            cx, cy = bx + bar_w - 4, by + 8
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8" fill="#087f72" stroke="#ffffff" stroke-width="2"/>')
            parts.append(f'<path d="M{cx-3:.1f} {cy:.1f} L{cx-0.5:.1f} {cy+2.5:.1f} L{cx+4:.1f} {cy-3:.1f}" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
        parts.append(text(bx + bar_w / 2, label_y, label, 10, 650, MUTED, "middle"))
    batch_max = req_batch
    parts.extend([
        f'<rect x="434" y="{TAKEAWAY_INNER_TOP}" width="408" height="{inner_h}" fill="#fffaf5" stroke="{LINE}" rx="6"/>',
        text(452, 91, "Batch", 12, 760, INK),
    ])
    for index, (label, value, color) in enumerate(
        [("Request count", req_batch, neutral), ("Input tokens", tok_batch, blue)]
    ):
        bx = 492 + index * (bar_w + 64)
        bh = chart_h * value / batch_max
        by = baseline - bh
        stroke = emphasis if value == min(req_batch, tok_batch) else "none"
        parts.append(f'<rect x="{bx:.0f}" y="{by:.1f}" width="{bar_w}" height="{bh:.1f}" rx="5" fill="{color}" stroke="{stroke}" stroke-width="{3 if stroke != "none" else 0}"/>')
        if value >= 1000:
            parts.append(text(bx + bar_w / 2, by - 6, f"{value / 1000:.1f} s", 12, 800, INK, "middle"))
        else:
            parts.append(text(bx + bar_w / 2, by - 6, f"{value:,} ms", 12, 800, INK, "middle"))
        if value == min(req_batch, tok_batch):
            cx, cy = bx + bar_w - 4, by + 8
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8" fill="#087f72" stroke="#ffffff" stroke-width="2"/>')
            parts.append(f'<path d="M{cx-3:.1f} {cy:.1f} L{cx-0.5:.1f} {cy+2.5:.1f} L{cx+4:.1f} {cy-3:.1f}" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
        parts.append(text(bx + bar_w / 2, label_y, label, 10, 650, MUTED, "middle"))
    parts.append("</svg>\n")
    return "".join(parts)


def render_workload_shape_takeaway_svg() -> str:
    """Compare selected chat and agentic workload shapes."""
    root = Path(__file__).resolve().parents[1]
    source = root / "benchmark-data/upstream-flow-control-v0.9.0/selected-workload-shapes/analysis.json"
    by_shape = json.loads(source.read_text())["by_workload_shape"]
    chat = by_shape["chat short output"]["median"]
    agentic = by_shape["agentic longer output"]["median"]
    teal, orange = "#087f72", "#c56a00"
    height = 260

    def bar_pair(x: float, y: float, w: float, h: float, title: str, unit: str, rows: list[tuple[str, float, str]], maximum: float) -> str:
        body = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#fbfcfd" stroke="{LINE}"/>'
        body += text(x + 18, y + 30, title, 13, 760, INK)
        track_x = x + 116
        # Reserve a fixed value column so units never overlap the filled bar.
        track_w = w - 246
        for index, (label, value, color) in enumerate(rows):
            row_y = y + 66 + index * 48
            body += text(x + 18, row_y + 4, label, 10, 720, INK)
            body += f'<line x1="{track_x}" y1="{row_y}" x2="{track_x + track_w}" y2="{row_y}" stroke="#edf1f4" stroke-width="16" stroke-linecap="round"/>'
            bw = max(8, track_w * value / maximum)
            body += f'<line x1="{track_x}" y1="{row_y}" x2="{track_x + bw:.1f}" y2="{row_y}" stroke="{color}" stroke-width="16" stroke-linecap="round"/>'
            if unit == "ms":
                value_label = f"{value:,.0f} ms"
            elif unit == "ms/token":
                value_label = f"{value:.1f} ms/token"
            else:
                value_label = f"{value:.1f}"
            body += text(x + w - 20, row_y + 4, value_label, 10, 800, color, "end")
        return body

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="The selected agentic workload had higher p95 time to first token than chat while p95 time per output token stayed similar.">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="{PAGE}"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="{height - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="4" fill="{teal}" rx="6"/>',
        text(38, 42, "Median surge p95", 12, 740, MUTED),
    ]
    ttft_rows = [
        ("Chat", float(chat["surge_p95_ttft_ms"]), teal),
        ("Agentic", float(agentic["surge_p95_ttft_ms"]), orange),
    ]
    tpot_rows = [
        ("Chat", float(chat["surge_p95_tpot_ms"]), teal),
        ("Agentic", float(agentic["surge_p95_tpot_ms"]), orange),
    ]
    parts.append(bar_pair(54, 70, 360, 150, "Time to first token (ms)", "ms", ttft_rows, 1500.0))
    parts.append(bar_pair(466, 70, 360, 150, "Time per output token (ms/token)", "ms/token", tpot_rows, 40.0))
    parts.append("</svg>\n")
    return "".join(parts)


def render_stability_takeaway_svg() -> str:
    """Align separate latency and queue plots across surge and recovery phases."""
    labels = ["Baseline", "Surge 1", "Recovery 1", "Surge 2", "Recovery 2", "Final"]
    latency = [299, 1760, 127, 1227, 279, 290]
    queue = [0, 39, 0, 27, 0, 0]
    blue, orange, green = "#2d6cdf", "#c56a00", "#087f72"
    height = 372
    left, right = 108.0, 816.0

    def x(index: int) -> float:
        return left + index * (right - left) / (len(labels) - 1)

    def y_latency(value: float) -> float:
        return 184 - value / 2000.0 * 100

    def y_queue(value: float) -> float:
        return 318 - value / 40.0 * 88

    latency_points = " ".join(f"{x(i):.1f},{y_latency(v):.1f}" for i, v in enumerate(latency))
    queue_points = " ".join(f"{x(i):.1f},{y_queue(v):.1f}" for i, v in enumerate(queue))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="Premium latency and queue depth rise during two surges and return during both recovery windows">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAGE}"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="{height - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        f'<rect x="20" y="10" width="{PANEL_W}" height="4" fill="{green}" rx="6"/>',
        text(38, 40, "One 30-minute run", 12, 700, MUTED),
        text(38, 70, "Premium p95 TTFT (milliseconds)", 9, 750, orange),
        text(38, 218, "Peak queue depth (requests)", 9, 750, blue),
    ]
    for index in (2, 4):
        xp = x(index)
        parts.append(f'<rect x="{xp - 52:.1f}" y="76" width="104" height="252" rx="5" fill="#eaf6f3" opacity="0.72"/>')
    for value in [0, 1000, 2000]:
        yp = y_latency(value)
        parts.append(f'<line x1="{left}" y1="{yp:.1f}" x2="{right}" y2="{yp:.1f}" stroke="#e7ebef"/>')
        parts.append(text(left - 10, yp + 3, f"{value:,}", 8, 600, MUTED, "end"))
    for value in [0, 20, 40]:
        yp = y_queue(value)
        parts.append(f'<line x1="{left}" y1="{yp:.1f}" x2="{right}" y2="{yp:.1f}" stroke="#e7ebef"/>')
        parts.append(text(left - 10, yp + 3, f"{value}", 8, 600, MUTED, "end"))
    parts.extend([
        f'<polyline points="{latency_points}" fill="none" stroke="{orange}" stroke-width="4"/>',
        f'<polyline points="{queue_points}" fill="none" stroke="{blue}" stroke-width="3"/>',
    ])
    for index, label in enumerate(labels):
        xp = x(index)
        label_y = y_latency(latency[index]) - 10
        label_anchor = "middle"
        label_x = xp
        if index == 1:
            label_y -= 8
            label_x -= 12
            label_anchor = "end"
        elif index == 3:
            label_y -= 8
            label_x += 12
            label_anchor = "start"
        elif index in (2, 4):
            label_y -= 4
        latency_y = y_latency(latency[index])
        queue_y = y_queue(queue[index])
        if index in (2, 4):
            parts.append(f'<circle cx="{xp:.1f}" cy="{latency_y:.1f}" r="10" fill="none" stroke="{green}" stroke-width="3"/>')
        parts.extend([
            f'<circle cx="{xp:.1f}" cy="{latency_y:.1f}" r="6" fill="{orange}"/>',
            f'<circle cx="{xp:.1f}" cy="{queue_y:.1f}" r="5" fill="{blue}"/>',
            text(label_x, label_y, f"{latency[index]:,} ms", 8, 800, orange, label_anchor),
            text(xp, 346, label, 8, 650, INK, "middle"),
        ])
        if index in (2, 4):
            parts.append(f'<circle cx="{xp:.1f}" cy="{queue_y:.1f}" r="9" fill="none" stroke="{green}" stroke-width="2.5"/>')
        if queue[index] > 0:
            parts.append(text(xp, queue_y - 10, queue[index], 8, 800, blue, "middle"))
    parts.append("</svg>\n")
    return "".join(parts)


def render_batch_eviction_takeaway_svg() -> str:
    """Show reserve, eviction, and retry with one shared Gateway."""
    teal, blue, orange, red, gray = "#087f72", "#2d6cdf", "#c56a00", "#b83232", "#667180"
    height = 410

    def box(x: float, y: float, w: float, h: float, title: str, subtitle: str = "", stroke: str = LINE, fill: str = "#ffffff") -> str:
        title_y = y + h / 2 - (5 if subtitle else -3)
        body = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}"/>'
        body += text(x + w / 2, title_y, title, 9, 800, INK, "middle")
        if subtitle:
            body += text(x + w / 2, title_y + 17, subtitle, 7.4, 650, MUTED, "middle")
        return body

    def arrow(path: str, color: str, marker: str, width: float = 2.0) -> str:
        return f'<path d="{path}" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" fill="none" marker-end="url(#{marker})"/>'

    def slots(x: float, y: float, colors: list[str]) -> str:
        body = ""
        for index, color in enumerate(colors):
            row, col = divmod(index, 5)
            body += f'<rect x="{x + col * 15:.1f}" y="{y + row * 14:.1f}" width="10" height="10" rx="2.4" fill="{color}"/>'
        return body

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="Reserved capacity keeps batch in the Endpoint Picker before vLLM. After dispatch, the Endpoint Picker can select eligible running batch, Envoy resets the vLLM stream, and the batch client retries the request.">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="8" fill="#ffffff" stroke="{LINE}"/>',
        '<defs>'
        '<marker id="reserve-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#087f72"/></marker>'
        '<marker id="batch-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#c56a00"/></marker>'
        '<marker id="retry-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#2d6cdf"/></marker>'
        '<marker id="reset-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#b83232"/></marker>'
        '<marker id="gray-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="#667180"/></marker>'
        '</defs>',
        f'<rect x="30" y="24" width="820" height="158" rx="8" fill="#fbfcfd" stroke="{LINE}"/>',
        text(50, 48, "Reserved capacity", 11, 800, teal),
        box(54, 76, 124, 58, "Gateway / Envoy", "request path"),
        f'<rect x="230" y="54" width="356" height="112" rx="8" fill="#f1fbf8" stroke="{teal}" stroke-width="1.6"/>',
        text(408, 72, "Endpoint Picker", 9, 800, INK, "middle"),
        f'<rect x="250" y="82" width="316" height="28" rx="5" fill="#ffffff" stroke="{teal}"/>',
        text(264, 100, "real-time", 8, 750, teal),
        slots(396, 90, [teal, teal, teal]),
        f'<rect x="250" y="122" width="316" height="28" rx="5" fill="#fff8f0" stroke="{orange}"/>',
        text(264, 140, "batch", 8, 750, orange),
        slots(396, 130, [orange, orange, orange, orange, orange]),
        f'<rect x="692" y="64" width="112" height="86" rx="7" fill="#ffffff" stroke="{LINE}"/>',
        text(748, 85, "vLLM", 10, 800, INK, "middle"),
        slots(718, 98, [teal, teal, teal, teal, "#dbe9e6", teal, teal, teal, teal, "#dbe9e6"]),
        text(748, 142, "running slots", 7.2, 650, MUTED, "middle"),
        arrow("M178 105 H230", gray, "gray-arrow"),
        arrow("M566 96 H692", teal, "reserve-arrow"),
        f'<rect x="616" y="90" width="12" height="12" rx="2" fill="{teal}" stroke="#ffffff" stroke-width="1.5"/>',

        f'<rect x="30" y="194" width="820" height="190" rx="8" fill="#fbfcfd" stroke="{LINE}"/>',
        text(50, 218, "Eviction and retry", 11, 800, orange),
        box(50, 250, 124, 54, "Batch client", "Async Processor", blue, "#f7f9ff"),
        box(224, 250, 132, 54, "Gateway / Envoy", "response + reset"),
        box(406, 250, 132, 54, "Endpoint Picker", "selects batch", orange, "#fff8f0"),
        box(686, 250, 116, 54, "vLLM", "releases sequence", red, "#fff8f8"),

        arrow("M406 266 H356", orange, "batch-arrow"),
        text(381, 257, "429", 7.5, 800, orange, "middle"),
        arrow("M356 288 H686", red, "reset-arrow"),
        text(521, 280, "reset stream", 7.5, 800, red, "middle"),
        arrow("M224 288 H174", blue, "retry-arrow"),
        text(199, 280, "HTTP 429", 7.5, 800, blue, "middle"),

        arrow("M112 304 V344 H290 V304", blue, "retry-arrow"),
        text(200, 338, "retry", 7.5, 800, blue, "middle"),
        arrow("M290 304 V344 H472 V304", blue, "retry-arrow"),
        text(381, 338, "route", 7.5, 800, blue, "middle"),
        arrow("M472 304 V344 H744 V304", blue, "retry-arrow"),
        text(608, 338, "dispatch", 7.5, 800, blue, "middle"),
    ]
    parts.append("</svg>\n")
    return "".join(parts)


def render_fairness_takeaway_svg() -> str:
    """Combine measured same-priority latency with the round-robin queue behavior."""
    orange, teal, blue = "#c56a00", "#087f72", "#2d6cdf"
    values = [("Tenant A", 12097, orange), ("Tenant B", 527, teal), ("Tenant C", 570, blue)]
    height = 356
    bar_left, bar_right = 176.0, 770.0

    def marker_x(value: float) -> float:
        low, high = math.log10(300), math.log10(15000)
        return bar_left + (math.log10(value) - low) / (high - low) * (bar_right - bar_left)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="Tenant A absorbed most of the surge delay while peer Tenants B and C stayed near half a second and continued receiving round-robin dispatch turns">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<rect x="10" y="10" width="{WIDTH - 20}" height="{height - 20}" rx="6" fill="none" stroke="{LINE}"/>',
        text(30, 34, "p95 TTFT (seconds, log scale)", 9, 700, MUTED),
    ]
    for tick, label in [(300, "0.3 s"), (1000, "1 s"), (3000, "3 s"), (10000, "10 s")]:
        x = marker_x(tick)
        parts.append(f'<line x1="{x:.1f}" y1="68" x2="{x:.1f}" y2="160" stroke="#e4e9ee"/>')
        parts.append(text(x, 70, label, 8, 650, MUTED, "middle"))
    for index, (label, value, color) in enumerate(values):
        y = 90 + index * 32
        parts.append(text(30, y + 4, label, 10, 700, INK))
        x = marker_x(value)
        fill_width = max(12.0, x - bar_left)
        parts.append(f'<rect x="{bar_left:.1f}" y="{y - 6}" width="{bar_right - bar_left:.1f}" height="12" rx="6" fill="#edf1f4"/>')
        parts.append(f'<rect x="{bar_left:.1f}" y="{y - 6}" width="{fill_width:.1f}" height="12" rx="6" fill="{color}"/>')
        value_label = f"{value/1000:.1f} s" if value >= 1000 else f"{value} ms"
        parts.append(text(826, y + 4, value_label, 10, 800, color, "end"))
    parts.extend([
        f'<line x1="30" y1="174" x2="850" y2="174" stroke="{LINE}"/>',
        f'<rect x="30" y="190" width="244" height="132" rx="7" fill="#fbfcfd" stroke="{LINE}"/>',
        text(48, 212, "Requests", 10, 800, INK),
        f'<rect x="302" y="190" width="352" height="132" rx="7" fill="#f8fcfb" stroke="{teal}"/>',
        text(320, 212, "Endpoint Picker", 10, 800, INK),
        text(636, 212, "one priority band", 8.5, 700, teal, "end"),
        f'<rect x="682" y="190" width="168" height="132" rx="7" fill="#fbfcfd" stroke="{LINE}"/>',
        text(766, 212, "vLLM", 10, 800, INK, "middle"),
        text(766, 228, "model pool", 8.5, 650, MUTED, "middle"),
    ])
    queues = [(238, "Tenant A", orange, 10), (270, "Tenant B", teal, 4), (302, "Tenant C", blue, 4)]
    for y, label, color, count in queues:
        parts.append(text(48, y + 4, label, 9, 700, INK))
        for index in range(count):
            parts.append(f'<rect x="{116 + index * 14}" y="{y-8}" width="10" height="14" rx="3" fill="{color}" opacity="0.78"/>')
        parts.append(f'<path d="M258 {y} H294" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<polygon points="294,{y-5} 302,{y} 294,{y+5}" fill="{color}"/>')
    parts.append(f'<rect x="320" y="226" width="316" height="76" rx="5" fill="#ffffff" stroke="{LINE}"/>')
    parts.append(text(338, 246, "Round-robin dispatch", 8.5, 750, MUTED))
    sequence = [(orange, "A"), (teal, "B"), (blue, "C")] * 3
    for index, (color, label) in enumerate(sequence):
        x = 356 + index * 29
        parts.append(f'<circle cx="{x}" cy="270" r="10" fill="{color}" opacity="0.86"/>')
        parts.append(text(x, 274, label, 7.5, 800, "#ffffff", "middle"))
    parts.append(f'<path d="M654 254 H674" stroke="{teal}" stroke-width="2.5"/>')
    parts.append(f'<polygon points="674,248 682,254 674,260" fill="{teal}"/>')
    admitted = [(orange, "A"), (teal, "B"), (blue, "C")] * 4
    for index, (color, label) in enumerate(admitted):
        row, col = divmod(index, 4)
        x, y = 724 + col * 21, 250 + row * 18
        parts.append(f'<rect x="{x}" y="{y}" width="14" height="12" rx="2" fill="{color}" opacity="0.82"/>')
        parts.append(text(x + 7, y + 9, label, 6.5, 800, "#ffffff", "middle"))
    parts.append("</svg>\n")
    return "".join(parts)


def render_batch_retry_evidence_svg() -> str:
    """Show that every observed eviction returned once in both proof packages."""
    teal, blue, orange, purple = "#087f72", "#2d6cdf", "#c56a00", "#6550a5"
    width, height = 880, 312

    def marker(marker_id: str, color: str) -> str:
        return (
            f'<marker id="{marker_id}" viewBox="0 0 10 10" refX="8" refY="5" '
            f'markerWidth="4" markerHeight="4" orient="auto"><path d="M0 1L8 5L0 9Z" fill="{color}"/></marker>'
        )

    def proof_panel(x: float, label: str, count: int) -> str:
        stage_y = 112
        stage_w = 92
        stage_h = 78
        centers = [x + 72, x + 205, x + 338]
        colors = [orange, blue, purple]
        names = ["Evicted", "Retried", "Final result"]
        body = f'<rect x="{x}" y="50" width="398" height="226" rx="7" fill="#fbfcfd" stroke="{LINE}"/>'
        body += text(x + 20, 80, label, 12, 800, INK)
        for index, (center, color, name) in enumerate(zip(centers, colors, names)):
            body += f'<rect x="{center-stage_w/2:.1f}" y="{stage_y}" width="{stage_w}" height="{stage_h}" rx="7" fill="#ffffff" stroke="{color}" stroke-width="1.6"/>'
            body += text(center, stage_y + 31, f"{count}", 19, 850, color, "middle")
            body += text(center, stage_y + 56, name, 8.5, 740, MUTED, "middle")
            if index < 2:
                body += f'<path d="M{center+stage_w/2+8:.1f} {stage_y+stage_h/2:.1f} H{centers[index+1]-stage_w/2-8:.1f}" stroke="{blue}" stroke-width="2" marker-end="url(#proof-arrow)"/>'
        check_x = centers[-1] + 34
        check_y = stage_y + 17
        body += f'<circle cx="{check_x}" cy="{check_y}" r="9" fill="{teal}" stroke="#ffffff" stroke-width="2"/>'
        body += f'<path d="M{check_x-3} {check_y} L{check_x-0.5} {check_y+2.5} L{check_x+4} {check_y-3}" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        body += text(x + 20, 244, "0 duplicate results", 9, 750, teal)
        return body

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="All 38 single-replica evictions and all 57 two-replica evictions were retried, and each produced one final result with no duplicates">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{width}" height="{height}" fill="{PAGE}"/>',
        f'<rect x="10" y="10" width="{width-20}" height="{height-20}" rx="7" fill="{SURFACE}" stroke="{LINE}"/>',
        '<defs>', marker("proof-arrow", blue), '</defs>',
        proof_panel(30, "Single replica", 38),
        proof_panel(452, "Two replicas", 57),
        '</svg>\n',
    ]
    return "".join(parts)


def render_batch_eviction_data_svg(root: Path) -> str:
    """Compare matched real-time p95 TTFT medians across the four eviction scenarios."""
    teal = "#087f72"
    source = root / "benchmark-data/batch-eviction/single-model-replica/summary.csv"
    grouped: dict[str, list[float]] = {}
    with source.open(newline="") as handle:
        for row in csv.DictReader(handle):
            grouped.setdefault(row["scenario"], []).append(float(row["realtime_p95_ttft_ms"]))
    reference = statistics.median(grouped["Realtime only"])
    scenarios = [
        ("Batch, no protection", "Realtime with batch and no protection", "#b83232"),
        ("Reserved capacity", "Realtime with reserved capacity", "#087f72"),
        ("Reserved + eviction", "Realtime with reserved capacity, batch eviction, and retry", "#087f72"),
    ]
    values = [(label, statistics.median(grouped[key]), color) for label, key, color in scenarios]
    height = 260
    left, right, top, bottom = 92.0, 830.0, 72.0, 200.0
    minimum, maximum = 0.0, 600.0

    def y(value: float) -> float:
        return bottom - (value - minimum) / (maximum - minimum) * (bottom - top)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-label="Reserved capacity and reserved capacity with eviction kept real-time p95 time to first token near the real-time-only reference while unprotected batch raised latency">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect x="1" y="1" width="{WIDTH - 2}" height="{height - 2}" rx="6" fill="#ffffff" stroke="{LINE}"/>',
        text(28, 34, "Real-time p95 TTFT (milliseconds)", 10, 700, MUTED),
    ]
    for tick in (0, 200, 400, 600):
        ty = y(tick)
        parts.append(f'<line x1="{left}" y1="{ty:.1f}" x2="{right}" y2="{ty:.1f}" stroke="#e4e9ee"/>')
        parts.append(text(left - 14, ty + 3, tick, 8, 650, MUTED, "end"))
    reference_y = y(reference)
    column_w = 94
    for index, (label, value, color) in enumerate(values):
        x = 170 + index * 238
        top_y = y(value)
        base_y = y(minimum)
        parts.append(f'<rect x="{x}" y="{top_y:.1f}" width="{column_w}" height="{base_y-top_y:.1f}" rx="5" fill="{color}"/>')
        label_y = top_y - 8 if abs(value - reference) > 40 else top_y + 17
        label_color = color if abs(value - reference) > 40 else "#ffffff"
        parts.append(text(x + column_w / 2, label_y, f"{value:.0f} ms", 10, 800, label_color, "middle"))
        parts.append(text(x + column_w / 2, 232, label, 9, 700, INK, "middle"))
    parts.extend([
        f'<line x1="{left}" y1="{reference_y:.1f}" x2="{right}" y2="{reference_y:.1f}" stroke="#22313f" stroke-width="3"/>',
        f'<rect x="{right - 202}" y="{reference_y - 19:.1f}" width="202" height="16" rx="3" fill="#ffffff"/>',
        text(right - 4, reference_y - 8, f"Real-time-only reference · {reference:.0f} ms", 9, 800, "#22313f", "end"),
    ])
    parts.append("</svg>\n")
    return "".join(parts)


def render_routing_takeaway_svg() -> str:
    """Use neutral paired columns for the mixed prefix-routing result."""
    root = Path(__file__).resolve().parents[1]
    source = root / "benchmark-data/upstream-flow-control-v0.9.0/prefix-cache-routing/analysis.json"
    comparisons = json.loads(source.read_text())["overall_latency_comparison"]
    rows = [row for row in comparisons if row["metric"] == "p95 TTFT"]
    labels = {
        "realtime-chat": "Real-time chat",
        "agentic": "Agentic",
        "standard-long-context": "Standard long context",
        "batch-long-context": "Batch long context",
    }
    width, height = 880, 432
    low_seconds, high_seconds = 0.5, 60.0
    gray, purple = "#667180", "#6550a5"

    def bar_height(value_ms: float) -> float:
        seconds = min(high_seconds, max(low_seconds, value_ms / 1000.0))
        return 116 * (math.log10(seconds) - math.log10(low_seconds)) / (
            math.log10(high_seconds) - math.log10(low_seconds)
        )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Prefix-aware routing lowered p95 TTFT for real-time chat, agentic, and batch requests, while standard long-context p95 TTFT increased.">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect width="{width}" height="{height}" fill="{PAGE}"/>',
        f'<rect x="10" y="10" width="{width - 20}" height="{height - 20}" fill="{SURFACE}" stroke="{LINE}" rx="6"/>',
        text(28, 38, "Median surge p95 TTFT (seconds, log scale)", 12, 700, MUTED),
        f'<rect x="600" y="25" width="12" height="10" rx="2" fill="{gray}"/>',
        text(620, 34, "Random", 9, 700, MUTED),
        f'<rect x="702" y="25" width="12" height="10" rx="2" fill="{purple}"/>',
        text(722, 34, "Prefix aware", 9, 700, MUTED),
    ]
    panels = [(28, 58), (448, 58), (28, 238), (448, 238)]
    for row, (panel_x, panel_y) in zip(rows, panels):
        parts.append(f'<rect x="{panel_x}" y="{panel_y}" width="404" height="164" fill="#fbfcfd" stroke="{LINE}" rx="6"/>')
        parts.append(text(panel_x + 18, panel_y + 27, labels[row["workload"]], 11, 780, INK))
        baseline = panel_y + 136
        for tick in (1, 10):
            tick_h = 116 * (math.log10(tick) - math.log10(low_seconds)) / (math.log10(high_seconds) - math.log10(low_seconds))
            ty = baseline - tick_h
            parts.append(f'<line x1="{panel_x + 54}" y1="{ty:.1f}" x2="{panel_x + 376}" y2="{ty:.1f}" stroke="#e7ebef"/>')
            parts.append(text(panel_x + 46, ty + 3, f"{tick:g} s", 7.5, 650, MUTED, "end"))
        values = [float(row["random_routing_median_ms"]), float(row["prefix_aware_routing_median_ms"])]
        for center, label, value, color in zip((panel_x + 166, panel_x + 274), ("Random", "Prefix"), values, (gray, purple)):
            bh = bar_height(value)
            parts.append(f'<rect x="{center - 34}" y="{baseline - bh:.1f}" width="68" height="{bh:.1f}" rx="5" fill="{color}"/>')
            parts.append(text(center, baseline - bh - 8, fmt_latency(value), 9.5, 800, color, "middle"))
            parts.append(text(center, baseline + 18, label, 8.5, 700, MUTED, "middle"))
    parts.append("</svg>\n")
    return "".join(parts)


def render_detector_comparison_takeaway_svg(root: Path) -> str:
    """Compare detector results for the two directly matched scenarios."""
    source = root / "benchmark-data/upstream-flow-control-v0.9.0/production-scenarios/analysis.json"
    matched = json.loads(source.read_text())["matched_detector_comparisons"]
    green, blue, orange = "#087f72", "#2d6cdf", "#c56a00"
    height = 292
    scale_max = 6000.0

    def scenario_range(values: dict, tenants: tuple[str, str], method: str) -> tuple[float, float]:
        medians = [float(values[method][tenant]["median_p95_ttft_ms"]) for tenant in tenants]
        return min(medians), max(medians)

    def fmt_range(lo: float, hi: float) -> str:
        return f"{lo:.0f}-{hi:.0f} ms"

    scenarios = [
        (
            "Consolidation",
            "Tenants A and B",
            matched["consolidation"],
            ("realtime tenant A", "realtime tenant B"),
        ),
        (
            "Same priority",
            "Peers B and C",
            matched["same-priority fairness"],
            ("realtime peer B", "realtime peer C"),
        ),
    ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="Request-count admission kept real-time p95 TTFT lower than queue-depth detection in two directly compared scenarios">',
        f'<style>text{{font-family:system-ui,-apple-system,sans-serif}}</style>',
        f'<rect x="1" y="1" width="{WIDTH - 2}" height="{height - 2}" fill="{SURFACE}" stroke="{LINE}"/>',
    ]
    panel_specs = [(30, 34, 390), (460, 34, 390)]
    for (panel_x, panel_y, panel_w), (title, subtitle, values, tenants) in zip(panel_specs, scenarios):
        plot_left = panel_x + 122
        plot_right = panel_x + panel_w - 104

        def local_x(value: float) -> float:
            return plot_left + value / scale_max * (plot_right - plot_left)

        parts.extend([
            f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="214" rx="7" fill="#fbfcfd" stroke="{LINE}"/>',
            text(panel_x + 18, panel_y + 28, title, 12, 800, INK),
            text(panel_x + 18, panel_y + 46, subtitle, 9, 650, MUTED),
        ])
        axis_y = panel_y + 78
        for tick, label in [(0, "0 ms"), (3000, "3,000 ms"), (6000, "6,000 ms")]:
            tx = local_x(tick)
            parts.append(f'<line x1="{tx:.1f}" y1="{axis_y}" x2="{tx:.1f}" y2="{panel_y + 172}" stroke="#e4e9ee"/>')
            parts.append(text(tx, axis_y - 8, label, 8, 650, MUTED, "middle"))

        rows = [
            ("Request count", "request count 128, 10% headroom", green, panel_y + 112),
            ("Queue depth", "queue depth 2", orange, panel_y + 154),
        ]
        for label, method, color, y in rows:
            lo, hi = scenario_range(values, tenants, method)
            end = max(local_x(hi), plot_left + 18)
            parts.append(text(panel_x + 18, y + 5, label, 9, 750, color))
            parts.append(f'<rect x="{plot_left:.1f}" y="{y - 7}" width="{plot_right - plot_left:.1f}" height="14" rx="7" fill="#edf2f6"/>')
            parts.append(f'<rect x="{plot_left:.1f}" y="{y - 7}" width="{end - plot_left:.1f}" height="14" rx="7" fill="{color}" opacity="0.88"/>')
            parts.append(text(panel_x + panel_w - 18, y + 5, fmt_range(lo, hi), 8.5, 800, color, "end"))
    parts.append("</svg>\n")
    return "".join(parts)


def render_batch_recovery_flow_svg() -> str:
    """Show the request path, abort signal, retryable response, and resubmission."""
    width, height = 1000, 470
    blue, teal, orange, purple = "#2d6cdf", "#087f72", "#c56a00", "#6650a5"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Batch recovery sequence: the Async Processor sends batch through the llm-d Gateway and Endpoint Picker to vLLM; the Endpoint Picker issues ImmediateResponse 429, Envoy resets the upstream connection, and the Async Processor submits the request again">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<defs>',
        f'<marker id="blue-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="{blue}"/></marker>',
        f'<marker id="orange-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="{orange}"/></marker>',
        f'<marker id="purple-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="{purple}"/></marker>',
        f'<marker id="teal-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="{teal}"/></marker>',
        '</defs>',
        text(32, 38, "Batch eviction releases capacity and returns work to the request path", 20, 780, INK),
        text(32, 60, "The Endpoint Picker issues ImmediateResponse(429); Envoy resets the upstream connection.", 12, 600, MUTED),
    ]
    actors = [
        (126, "Async Processor", purple),
        (368, "llm-d Gateway / Envoy", blue),
        (610, "Endpoint Picker", teal),
        (852, "vLLM", blue),
    ]
    for x, label, color in actors:
        parts.extend([
            f'<rect x="{x - 86}" y="84" width="172" height="48" rx="6" fill="#f7f8fa" stroke="{color}" stroke-width="2"/>',
            text(x, 114, label, 13, 780, INK, "middle"),
            f'<line x1="{x}" y1="132" x2="{x}" y2="438" stroke="#d7dde5" stroke-width="2" stroke-dasharray="5 6"/>',
        ])
    parts.extend([
        text(32, 158, "1  BATCH REQUEST", 9, 800, blue),
        f'<path d="M126 174 H844" stroke="{blue}" stroke-width="3" marker-end="url(#blue-arrow)"/>',
        text(489, 164, "Gateway routes · Endpoint Picker selects · vLLM runs", 10, 650, MUTED, "middle"),

        text(32, 222, "2  CAPACITY NEEDED", 9, 800, orange),
        f'<path d="M610 240 H376" stroke="{orange}" stroke-width="3" marker-end="url(#orange-arrow)"/>',
        text(493, 231, "ImmediateResponse(429)", 11, 750, orange, "middle"),
        f'<path d="M368 276 H844" stroke="{orange}" stroke-width="3" marker-end="url(#orange-arrow)"/>',
        text(610, 267, "Envoy resets upstream stream", 11, 750, orange, "middle"),
        f'<rect x="802" y="292" width="100" height="24" rx="4" fill="{orange}" opacity="0.12"/>',
        text(852, 308, "capacity released", 9, 750, orange, "middle"),
        f'<path d="M360 326 H134" stroke="{purple}" stroke-width="3" marker-end="url(#purple-arrow)"/>',
        text(247, 317, "HTTP 429", 11, 750, purple, "middle"),

        text(32, 370, "3  RETRY", 9, 800, teal),
        f'<path d="M126 388 H844" stroke="{teal}" stroke-width="3" marker-end="url(#teal-arrow)"/>',
        text(489, 379, "Async Processor submits the request again", 11, 750, teal, "middle"),
        f'<path d="M852 420 H134" stroke="{teal}" stroke-width="3" marker-end="url(#teal-arrow)"/>',
        text(489, 441, "One final result returns to the batch job", 11, 750, teal, "middle"),
        '</svg>\n',
    ])
    return "".join(parts)


def render_two_model_followup_svg() -> str:
    """Summarize the two-model follow-up without implying a latency scaling result."""
    width, height = 1000, 350
    blue, teal, orange, purple = "#2d6cdf", "#087f72", "#c56a00", "#6650a5"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="One Endpoint Picker routed traffic across two model replicas; both replicas received balanced traffic, eviction and retry worked on both, and the latency difference from one model was inconclusive">',
        '<style>text{font-family:system-ui,-apple-system,sans-serif}</style>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        text(32, 38, "Eviction and retry continued across two model replicas", 20, 780, INK),
        text(32, 60, "One Endpoint Picker served both models at the same per-GPU load as the single-model proof.", 12, 600, MUTED),
        f'<circle cx="218" cy="172" r="66" fill="#f7f8fa" stroke="{teal}" stroke-width="3"/>',
        text(218, 166, "Endpoint", 15, 780, INK, "middle"),
        text(218, 187, "Picker", 15, 780, INK, "middle"),
        f'<path d="M284 148 C350 148 350 112 420 112" fill="none" stroke="{blue}" stroke-width="4"/>',
        f'<path d="M284 196 C350 196 350 232 420 232" fill="none" stroke="{teal}" stroke-width="4"/>',
    ]
    for y, label, share, color in [(80, "Model replica 1", "48–52% of traffic", blue), (200, "Model replica 2", "48–52% of traffic", teal)]:
        parts.extend([
            f'<rect x="420" y="{y}" width="246" height="64" rx="6" fill="#f7f8fa" stroke="{color}" stroke-width="2"/>',
            text(442, y + 27, label, 13, 780, INK),
            text(442, y + 47, share, 11, 650, color),
            f'<circle cx="638" cy="{y + 32}" r="10" fill="#e7f4f1" stroke="{teal}"/>',
            text(638, y + 36, "✓", 10, 800, teal, "middle"),
        ])
    parts.extend([
        f'<path d="M666 112 H750" stroke="{purple}" stroke-width="4"/>',
        f'<path d="M666 232 H750" stroke="{purple}" stroke-width="4"/>',
        f'<path d="M750 112 V232" stroke="{purple}" stroke-width="4"/>',
        f'<circle cx="750" cy="172" r="18" fill="{purple}"/>',
        text(750, 177, "↻", 14, 800, "#ffffff", "middle"),
        text(784, 151, "Batch eviction and retry", 13, 780, INK),
        text(784, 172, "worked on both replicas", 12, 650, purple),
        text(784, 198, "One final result per evicted job", 11, 650, MUTED),
        f'<line x1="32" y1="296" x2="968" y2="296" stroke="{LINE}"/>',
        text(32, 326, "Latency comparison", 11, 800, INK),
        text(160, 326, "The observed difference was small; three repeats were insufficient to establish a scaling change.", 11, 650, MUTED),
        '</svg>\n',
    ])
    return "".join(parts)


TAKEAWAY_OVERRIDES: dict[str, callable] = {
    "01-engine.svg": render_engine_takeaway_svg,
    "05-production.svg": render_priority_tiers_takeaway_svg,
    "06-fairness.svg": render_fairness_takeaway_svg,
    "07-mixed.svg": render_mixed_takeaway_svg,
    "07-workload-shapes.svg": render_workload_shape_takeaway_svg,
    "08-batch-interference.svg": render_batch_interference_takeaway_svg,
    "09-stability.svg": render_stability_takeaway_svg,
    "10-scale.svg": render_scale_takeaway_svg,
    "11-routing.svg": render_routing_takeaway_svg,
}


def find_spec(specs: list[dict], path_suffix: str) -> dict:
    for spec in specs:
        if spec["path"].endswith(path_suffix):
            return spec
    raise KeyError(path_suffix)


TUNING_STEPS: list[tuple[str, str, int, str]] = [
    ("01-engine.svg", "engine-configuration", 0, "Engine capacity sweep"),
    ("02-utilization.svg", "utilization-detector-calibration", 0, "Queue depth calibration"),
    ("03-admission.svg", "request-and-token-admission-calibration", 0, "Request cap calibration"),
    ("04-cap-tradeoff.svg", "request-concurrency-priority-tuning", 0, "Premium latency across caps"),
    ("05-production.svg", "production-scenarios/priority-tiers", 0, "Priority tiers surge latency"),
    ("06-fairness.svg", "production-scenarios/same-priority-fairness", 0, "Same-priority fairness"),
    ("07-mixed.svg", "mixed-production-workload", 0, "Mixed workload admission"),
    ("08-batch-interference.svg", "batch-interference", 0, "Batch interference baseline"),
    ("09-stability.svg", "long-stability", 0, "Long stability premium latency"),
    ("10-scale.svg", "multi-replica-scaling", 0, "Per-GPU throughput scaling"),
    ("11-routing.svg", "prefix-cache-routing", 0, "Prefix routing latency"),
]

# Story takeaway panels (not numbered tuning-rail steps).
EXTRA_PANELS: list[tuple[str, str, int, str]] = []


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "assets" / "v09-tuning"
    out.mkdir(parents=True, exist_ok=True)
    specs = build_specs(root)
    for filename, path_suffix, panel_index, label in TUNING_STEPS + EXTRA_PANELS:
        spec = find_spec(specs, path_suffix)
        panel = spec["panels"][panel_index]
        color = COLORS[panel_index % len(COLORS)]
        if spec.get("tone") == "warning" and path_suffix == "batch-interference":
            color = COLORS[2]
        svg = render_panel(panel, color)
        target = out / filename
        write_svg(target, svg)
        print(f"wrote {target} ({label})")
    for filename, renderer in TAKEAWAY_OVERRIDES.items():
        target = out / filename
        write_svg(target, renderer())
        print(f"wrote {target} (scannable takeaway)")
    takeaway = out / "consolidation.svg"
    write_svg(takeaway, render_consolidation_takeaway_svg())
    print(f"wrote {takeaway} (consolidation architecture)")
    consolidation_data = out / "consolidation-data.svg"
    write_svg(consolidation_data, render_consolidation_data_svg(root))
    print(f"wrote {consolidation_data} (measured consolidation data)")
    priority_explainer = out / "priority-dispatch.svg"
    write_svg(priority_explainer, render_priority_dispatch_explainer_svg())
    print(f"wrote {priority_explainer} (priority dispatch explainer)")
    priority_section = out / "priority-tiers-section.svg"
    write_svg(priority_section, render_priority_tiers_section_svg())
    print(f"wrote {priority_section} (priority tiers README section)")
    batch_isolation = out / "batch-isolation.svg"
    write_svg(batch_isolation, render_batch_isolation_takeaway_svg())
    print(f"wrote {batch_isolation} (batch isolation takeaway)")
    batch_isolation_section = out / "batch-isolation-section.svg"
    write_svg(batch_isolation_section, render_batch_isolation_section_svg())
    print(f"wrote {batch_isolation_section} (batch isolation README section)")
    batch_isolation_traffic = out / "batch-isolation-traffic.svg"
    write_svg(batch_isolation_traffic, render_batch_isolation_traffic_svg())
    print(f"wrote {batch_isolation_traffic} (batch isolation traffic)")
    consolidation_section = out / "consolidation-section.svg"
    write_svg(consolidation_section, render_consolidation_section_svg())
    print(f"wrote {consolidation_section} (consolidation README section)")
    fairness_section = out / "same-priority-fairness-section.svg"
    write_svg(fairness_section, render_same_priority_fairness_section_svg())
    print(f"wrote {fairness_section} (same-priority fairness README section)")
    architecture = root / "assets" / "flow-control-in-llmd.svg"
    write_svg(architecture, render_flow_control_architecture_svg())
    print(f"wrote {architecture} (flow control architecture)")
    dispatch_path = root / "assets" / "dispatch-path.svg"
    write_svg(dispatch_path, render_dispatch_path_svg())
    print(f"wrote {dispatch_path} (dispatch path under pressure)")
    configuration_map = root / "assets" / "configuration-map.svg"
    write_svg(configuration_map, render_configuration_map_svg())
    print(f"wrote {configuration_map} (configuration map)")
    configuration_engine = root / "assets" / "configuration-engine.svg"
    write_svg(configuration_engine, render_configuration_engine_svg())
    print(f"wrote {configuration_engine} (vLLM execution sweeps)")
    configuration_admission = root / "assets" / "configuration-admission.svg"
    write_svg(configuration_admission, render_configuration_admission_svg())
    print(f"wrote {configuration_admission} (Endpoint Picker admission sweeps)")
    configuration_request_shape = root / "assets" / "configuration-request-shape.svg"
    write_svg(configuration_request_shape, render_configuration_request_shape_svg())
    print(f"wrote {configuration_request_shape} (request-size admission calibration)")
    batch_panel = root / "assets" / "batch-eviction-panel.svg"
    write_svg(batch_panel, render_batch_eviction_takeaway_svg())
    print(f"wrote {batch_panel} (compact batch eviction panel)")
    batch_data = root / "assets" / "batch-eviction-data.svg"
    write_svg(batch_data, render_batch_eviction_data_svg(root))
    print(f"wrote {batch_data} (measured batch eviction data)")
    batch_retry_evidence = root / "assets" / "batch-retry-evidence.svg"
    write_svg(batch_retry_evidence, render_batch_retry_evidence_svg())
    print(f"wrote {batch_retry_evidence} (measured batch retry evidence)")
    detector_panel = out / "detector-comparison.svg"
    write_svg(detector_panel, render_detector_comparison_takeaway_svg(root))
    print(f"wrote {detector_panel} (detector comparison takeaway)")
    single_model = root / "benchmark-data" / "batch-eviction" / "single-model-replica"
    recovery_flow = single_model / "batch-recovery-flow.svg"
    write_svg(recovery_flow, render_batch_recovery_flow_svg())
    print(f"wrote {recovery_flow} (batch recovery flow)")
    two_model_panel = single_model / "two-model-followup.svg"
    write_svg(two_model_panel, render_two_model_followup_svg())
    print(f"wrote {two_model_panel} (two-model follow-up)")
    html_assets = Path("/Users/algriffi/github/html-suggest/docs/assets")
    if html_assets.is_dir():
        for name in ("batch-eviction-panel.svg", "v09-tuning"):
            if name == "v09-tuning":
                src_dir = out
                dst_dir = html_assets / "v09-tuning"
                dst_dir.mkdir(parents=True, exist_ok=True)
                for svg in src_dir.glob("*.svg"):
                    (dst_dir / svg.name).write_text(svg.read_text())
            else:
                (html_assets / name).write_text(batch_panel.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate self-contained architecture diagrams and result plots for benchmark packages."""

from __future__ import annotations

import argparse
import html
import math
import sys
from pathlib import Path

from package_visual_specs import build_specs


WIDTH = 1200
PANEL_WIDTH = 550
COLORS = ["#2d6cdf", "#087f72", "#c56a00", "#6550a5", "#b83232"]
INK = "#15202b"
MUTED = "#5f6c7b"
LINE = "#cdd5df"
SURFACE = "#ffffff"
PAGE = "#f5f7f9"


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


def text(x: float, y: float, value: object, size: int = 14, weight: int = 500, color: str = INK, anchor: str = "start") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{esc(value)}</text>'


def wrapped_text(x: float, y: float, value: str, width: int, size: int = 14, weight: int = 500, color: str = INK, max_lines: int = 3) -> str:
    words = value.split()
    limit = max(10, int(width / (size * 0.57)))
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
    parts = [f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}" font-weight="{weight}">']
    for index, line_value in enumerate(lines):
        dy = 0 if index == 0 else size * 1.3
        parts.append(f'<tspan x="{x:.1f}" dy="{dy:.1f}">{esc(line_value)}</tspan>')
    parts.append("</text>")
    return "".join(parts)


def scale(value: float, maximum: float, log: bool) -> float:
    if maximum <= 0:
        return 0
    if log:
        return math.log10(1 + max(0, value)) / math.log10(1 + maximum)
    return max(0, value) / maximum


def scale_domain(value: float, minimum: float, maximum: float, log: bool) -> float:
    if maximum <= minimum:
        return 0.5
    if log:
        if minimum <= 0 or value <= 0:
            raise ValueError("log scales require positive values")
        return (math.log10(value) - math.log10(minimum)) / (math.log10(maximum) - math.log10(minimum))
    return (value - minimum) / (maximum - minimum)


def panel_height(panel: dict) -> int:
    if panel["kind"] == "combo":
        return 470
    if panel["kind"] == "process":
        return 330
    if panel["kind"] == "line":
        return 360
    count = len(panel.get("rows", panel.get("stages", panel.get("x", []))))
    if panel["kind"] == "paired":
        return max(340, 170 + count * 48)
    if panel["kind"] == "grouped":
        return max(330, 150 + count * (28 + 18 * len(panel["groups"])))
    return max(320, 150 + count * 44)


def panel_frame(x: int, y: int, height: int, title_value: str, unit: str, takeaway: str, color: str) -> list[str]:
    parts = [f'<rect x="{x}" y="{y}" width="{PANEL_WIDTH}" height="{height}" fill="{SURFACE}" stroke="{LINE}"/>',
             f'<rect x="{x}" y="{y}" width="{PANEL_WIDTH}" height="4" fill="{color}"/>',
             wrapped_text(x + 22, y + 34, title_value, 490, 18, 750)]
    parts.append(text(x + 22, y + 61, unit, 11, 650, MUTED))
    if takeaway:
        parts.append(wrapped_text(x + 22, y + 85, takeaway, 490, 11, 500, MUTED, 2))
    return parts


def render_bar(panel: dict, x: int, y: int, height: int, color: str) -> str:
    unit = panel["unit"] + (", log scale" if panel.get("log") else "")
    parts = panel_frame(x, y, height, panel["title"], unit, panel.get("takeaway", ""), color)
    rows = panel["rows"]
    values = [float(value) for _, value in rows]
    maximum = max(values + [1])
    chart_top = y + (122 if panel.get("takeaway") else 96)
    chart_left, chart_right = x + 180, x + PANEL_WIDTH - 86
    row_height = max(32, min(48, (height - (chart_top - y) - 38) / max(1, len(rows))))
    for index, (label, value) in enumerate(rows):
        cy = chart_top + index * row_height
        parts.append(wrapped_text(x + 22, cy + 14, str(label), 148, 11, 650, INK, 2))
        parts.append(f'<rect x="{chart_left}" y="{cy + 3:.1f}" width="{chart_right - chart_left}" height="14" fill="#e1e7ed"/>')
        bar_width = max(3, (chart_right - chart_left) * scale(float(value), maximum, panel.get("log", False)))
        parts.append(f'<rect x="{chart_left}" y="{cy + 3:.1f}" width="{bar_width:.1f}" height="14" fill="{color}"/>')
        parts.append(text(x + PANEL_WIDTH - 18, cy + 15, fmt(float(value), panel["unit"]), 11, 750, INK, "end"))
    return "".join(parts)


def render_dot(panel: dict, x: int, y: int, height: int, color: str) -> str:
    unit = panel["unit"] + (", log scale" if panel.get("log") else "")
    parts = panel_frame(x, y, height, panel["title"], unit, panel.get("takeaway", ""), color)
    rows = panel["rows"]
    values = [float(value) for _, value in rows]
    use_log = bool(panel.get("log"))
    minimum = min(values) * 0.8 if use_log else 0
    maximum = max(values + [1]) * 1.05
    chart_top = y + (132 if panel.get("takeaway") else 106)
    chart_left, chart_right = x + 190, x + PANEL_WIDTH - 92
    row_height = max(34, min(52, (height - (chart_top - y) - 40) / max(1, len(rows))))
    parts.append(f'<line x1="{chart_left}" y1="{chart_top - 18}" x2="{chart_right}" y2="{chart_top - 18}" stroke="{LINE}"/>')
    parts.append(text(chart_left, chart_top - 25, fmt(minimum, panel["unit"]), 9, 550, MUTED, "middle"))
    parts.append(text(chart_right, chart_top - 25, fmt(max(values + [0]), panel["unit"]), 9, 550, MUTED, "middle"))
    for index, (label, value) in enumerate(rows):
        cy = chart_top + index * row_height
        point_x = chart_left + (chart_right - chart_left) * scale_domain(float(value), minimum, maximum, use_log)
        parts.append(wrapped_text(x + 22, cy + 5, str(label), 150, 11, 650, INK, 2))
        parts.append(f'<line x1="{chart_left}" y1="{cy:.1f}" x2="{chart_right}" y2="{cy:.1f}" stroke="#e1e7ed"/>')
        parts.append(f'<circle cx="{point_x:.1f}" cy="{cy:.1f}" r="7" fill="{color}" stroke="{SURFACE}" stroke-width="2"/>')
        parts.append(text(x + PANEL_WIDTH - 18, cy + 4, fmt(float(value), panel["unit"]), 11, 750, INK, "end"))
    return "".join(parts)


def render_paired(panel: dict, x: int, y: int, height: int, color: str) -> str:
    unit = panel["unit"] + (", log scale" if panel.get("log") else "")
    parts = panel_frame(x, y, height, panel["title"], unit, panel.get("takeaway", ""), color)
    groups = panel["groups"]
    rows = panel["rows"]
    if len(groups) < 2:
        raise ValueError(f'comparison panel requires at least two groups: {panel["title"]}')
    all_values = [float(value) for _, values in rows for value in values]
    use_log = bool(panel.get("log"))
    minimum = min(all_values) * 0.8 if use_log else 0
    maximum = max(all_values + [1]) * 1.05
    legend_y = y + (128 if panel.get("takeaway") else 102)
    legend_x = x + 22
    for index, group in enumerate(groups):
        gx = legend_x + index * (480 / len(groups))
        parts.append(f'<circle cx="{gx + 6}" cy="{legend_y - 4}" r="6" fill="{COLORS[index]}"/>')
        parts.append(text(gx + 18, legend_y, group, 10, 650, MUTED))
    chart_top = legend_y + 34
    chart_left, chart_right = x + 180, x + PANEL_WIDTH - 90
    row_height = max(42, min(58, (height - (chart_top - y) - 32) / max(1, len(rows))))
    for row_index, (label, values) in enumerate(rows):
        cy = chart_top + row_index * row_height
        positions = [chart_left + (chart_right - chart_left) * scale_domain(float(value), minimum, maximum, use_log) for value in values]
        parts.append(wrapped_text(x + 22, cy + 4, str(label), 140, 11, 650, INK, 2))
        parts.append(f'<line x1="{chart_left}" y1="{cy:.1f}" x2="{chart_right}" y2="{cy:.1f}" stroke="#e1e7ed"/>')
        parts.append(f'<line x1="{min(positions):.1f}" y1="{cy:.1f}" x2="{max(positions):.1f}" y2="{cy:.1f}" stroke="{LINE}" stroke-width="3"/>')
        for group_index, (point_x, value) in enumerate(zip(positions, values)):
            point_color = COLORS[group_index % len(COLORS)]
            parts.append(f'<circle cx="{point_x:.1f}" cy="{cy:.1f}" r="7" fill="{point_color}" stroke="{SURFACE}" stroke-width="2"/>')
            value_offsets = [-12, 20, -28, 36]
            value_y = cy + value_offsets[group_index % len(value_offsets)]
            parts.append(text(point_x, value_y, fmt(float(value), panel["unit"]), 9, 750, point_color, "middle"))
    return "".join(parts)


def render_process(panel: dict, x: int, y: int, height: int, color: str) -> str:
    parts = panel_frame(x, y, height, panel["title"], panel["unit"], panel.get("takeaway", ""), color)
    stages = panel["stages"]
    top = y + (145 if panel.get("takeaway") else 118)
    left, right = x + 30, x + PANEL_WIDTH - 30
    gap = 24
    node_width = (right - left - gap * (len(stages) - 1)) / max(1, len(stages))
    node_height = 112
    for index, (label, value) in enumerate(stages):
        node_x = left + index * (node_width + gap)
        if index:
            arrow_y = top + node_height / 2
            parts.append(f'<line x1="{node_x - gap + 5:.1f}" y1="{arrow_y:.1f}" x2="{node_x - 5:.1f}" y2="{arrow_y:.1f}" stroke="{color}" stroke-width="3"/>')
            parts.append(f'<path d="M {node_x - 9:.1f} {arrow_y - 5:.1f} L {node_x - 3:.1f} {arrow_y:.1f} L {node_x - 9:.1f} {arrow_y + 5:.1f}" fill="none" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<rect x="{node_x:.1f}" y="{top:.1f}" width="{node_width:.1f}" height="{node_height}" fill="#f7f5fb" stroke="#d7cfea"/>')
        parts.append(text(node_x + node_width / 2, top + 43, fmt(float(value), panel["unit"]), 23, 800, color, "middle"))
        parts.append(wrapped_text(node_x + 12, top + 72, str(label), int(node_width - 24), 11, 650, MUTED, 2))
    return "".join(parts)


def render_grouped(panel: dict, x: int, y: int, height: int, color: str) -> str:
    unit = panel["unit"] + (", log scale" if panel.get("log") else "")
    parts = panel_frame(x, y, height, panel["title"], unit, panel.get("takeaway", ""), color)
    groups = panel["groups"]
    rows = panel["rows"]
    maximum = max([float(v) for _, values in rows for v in values] + [1])
    legend_y = y + (122 if panel.get("takeaway") else 96)
    legend_x = x + 22
    for index, group in enumerate(groups):
        gx = legend_x + index * (480 / max(1, len(groups)))
        parts.append(f'<rect x="{gx:.1f}" y="{legend_y - 9}" width="12" height="12" fill="{COLORS[index % len(COLORS)]}"/>')
        parts.append(text(gx + 18, legend_y + 1, group, 10, 650, MUTED))
    chart_top = legend_y + 22
    chart_left, chart_right = x + 170, x + PANEL_WIDTH - 86
    group_height = max(48, (height - (chart_top - y) - 30) / max(1, len(rows)))
    bar_height = max(7, min(12, (group_height - 10) / max(1, len(groups))))
    for row_index, (label, values) in enumerate(rows):
        base_y = chart_top + row_index * group_height
        parts.append(wrapped_text(x + 22, base_y + 14, str(label), 138, 11, 650, INK, 2))
        for group_index, value in enumerate(values):
            by = base_y + group_index * (bar_height + 5)
            parts.append(f'<rect x="{chart_left}" y="{by:.1f}" width="{chart_right - chart_left}" height="{bar_height:.1f}" fill="#e1e7ed"/>')
            bar_width = max(3, (chart_right - chart_left) * scale(float(value), maximum, panel.get("log", False)))
            parts.append(f'<rect x="{chart_left}" y="{by:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{COLORS[group_index % len(COLORS)]}"/>')
            parts.append(text(x + PANEL_WIDTH - 18, by + bar_height, fmt(float(value), panel["unit"]), 9, 750, INK, "end"))
    return "".join(parts)


def render_line(panel: dict, x: int, y: int, height: int, color: str) -> str:
    parts = panel_frame(x, y, height, panel["title"], panel["unit"], panel.get("takeaway", ""), color)
    x_labels = panel["x"]
    series = panel["series"]
    values = [float(v) for _, points in series for v in points]
    maximum = max(values + [1]) * 1.12
    top = y + (132 if panel.get("takeaway") else 106)
    left, right, bottom = x + 66, x + PANEL_WIDTH - 34, y + height - 55
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
                f'height="{bottom - top:.1f}" fill="{status_colors[status]}" opacity="0.18"/>'
            )
    for fraction in [0, 0.5, 1]:
        gy = bottom - (bottom - top) * fraction
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{right}" y2="{gy:.1f}" stroke="#e1e7ed"/>')
        parts.append(text(left - 8, gy + 4, fmt(maximum * fraction, panel["unit"]), 9, 550, MUTED, "end"))
    tick_step = max(1, math.ceil(len(x_labels) / 7))
    for index, label in enumerate(x_labels):
        if index % tick_step == 0 or index == len(x_labels) - 1:
            label_color = status_colors.get(statuses[index], MUTED) if len(statuses) == len(x_labels) else MUTED
            parts.append(text(x_positions[index], bottom + 22, label, 9, 700, label_color, "middle"))
    for series_index, (name, points) in enumerate(series):
        series_color = COLORS[series_index % len(COLORS)]
        coords = [(x_positions[i], bottom - (bottom - top) * float(value) / maximum) for i, value in enumerate(points)]
        parts.append(f'<polyline points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in coords)}" fill="none" stroke="{series_color}" stroke-width="3"/>')
        for point_index, (px, py) in enumerate(coords):
            status = statuses[point_index] if len(statuses) == len(coords) else "neutral"
            point_color = status_colors[status] if status != "neutral" else series_color
            point_radius = 6 if status != "neutral" else 4
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{point_radius}" fill="{point_color}"/>')
        parts.append(f'<rect x="{left + series_index * 150}" y="{top - 23}" width="12" height="4" fill="{series_color}"/>')
        parts.append(text(left + 18 + series_index * 150, top - 17, name, 10, 650, MUTED))
        if panel.get("highlight_peak") and points:
            peak_index = max(range(len(points)), key=lambda index: float(points[index]))
            peak_x, peak_y = coords[peak_index]
            label_x = peak_x - 10 if peak_x > (left + right) / 2 else peak_x + 10
            anchor = "end" if peak_x > (left + right) / 2 else "start"
            parts.append(f'<circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="7" fill="{SURFACE}" stroke="{series_color}" stroke-width="3"/>')
            parts.append(text(label_x, max(top + 12, peak_y - 12), f'Peak {fmt(float(points[peak_index]), "requests")} requests', 10, 750, series_color, anchor))
    return "".join(parts)


def render_combo(panel: dict, x: int, y: int, height: int, color: str) -> str:
    parts = panel_frame(x, y, height, panel["title"], panel["unit"], panel.get("takeaway", ""), color)
    labels = panel["x"]
    line_values = [float(value) for value in panel["line_values"]]
    bar_values = [float(value) for value in panel["bar_values"]]
    line_max = max(line_values + [1]) * 1.12
    bar_max = max(bar_values + [1]) * 1.12
    line_top, line_bottom = y + 148, y + 304
    queue_top, queue_bottom = y + 350, y + height - 62
    left, right = x + 78, x + PANEL_WIDTH - 44
    x_positions = [left + index * (right - left) / max(1, len(labels) - 1) for index in range(len(labels))]
    step = (right - left) / max(1, len(labels) - 1)

    final_left = max(left, x_positions[-1] - step / 2)
    parts.append(
        f'<rect x="{final_left:.1f}" y="{line_top:.1f}" width="{right - final_left:.1f}" '
        f'height="{queue_bottom - line_top:.1f}" fill="#087f72" opacity="0.18"/>'
    )

    for fraction in [0, 0.5, 1]:
        gy = line_bottom - (line_bottom - line_top) * fraction
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{right}" y2="{gy:.1f}" stroke="#e1e7ed"/>')
        parts.append(text(left - 9, gy + 4, fmt(line_max * fraction, panel["line_unit"]), 9, 550, MUTED, "end"))

    parts.append(f'<line x1="{left}" y1="{line_top - 20}" x2="{left + 18}" y2="{line_top - 20}" stroke="{COLORS[0]}" stroke-width="3"/>')
    parts.append(text(left + 24, line_top - 16, f'{panel["line_name"]} ({panel["line_unit"]})', 10, 650, MUTED))
    parts.append(f'<rect x="{left}" y="{queue_top - 24}" width="13" height="13" fill="#cdd5df"/>')
    parts.append(text(left + 20, queue_top - 14, f'{panel["bar_name"]} (%)', 10, 650, MUTED))

    bar_width = min(38, step * 0.42)
    for index, value in enumerate(bar_values):
        bar_height = (queue_bottom - queue_top) * value / bar_max
        bx = x_positions[index] - bar_width / 2
        by = queue_bottom - bar_height
        if index == len(bar_values) - 1:
            parts.append(f'<rect x="{bx:.1f}" y="{queue_bottom - 4:.1f}" width="{bar_width:.1f}" height="4" fill="#087f72"/>')
        else:
            parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="#cdd5df"/>')
        label_color = "#087f72" if index == len(bar_values) - 1 else MUTED
        parts.append(text(x_positions[index], max(queue_top + 9, by - 5), fmt(value, panel["bar_unit"]), 9, 700, label_color, "middle"))

    line_coords = [
        (x_positions[index], line_bottom - (line_bottom - line_top) * value / line_max)
        for index, value in enumerate(line_values)
    ]
    parts.append(
        f'<polyline points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in line_coords)}" '
        f'fill="none" stroke="{COLORS[0]}" stroke-width="3"/>'
    )
    for index, (px, py) in enumerate(line_coords):
        point_color = "#087f72" if index == len(line_coords) - 1 else COLORS[0]
        radius = 6 if index == len(line_coords) - 1 else 4
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{radius}" fill="{point_color}"/>')

    baseline_y = line_coords[0][1]
    parts.append(f'<line x1="{left}" y1="{baseline_y:.1f}" x2="{right}" y2="{baseline_y:.1f}" stroke="#7c8996" stroke-width="1" stroke-dasharray="4 4"/>')
    parts.append(text(left + 5, baseline_y - 8, f'Pre-surge {fmt(line_values[0], panel["line_unit"])}', 9, 650, MUTED))
    parts.append(text(right - 8, line_coords[-1][1] - 12, fmt(line_values[-1], panel["line_unit"]), 10, 750, "#087f72", "end"))

    for index, label in enumerate(labels):
        label_color = "#087f72" if index == len(labels) - 1 else MUTED
        parts.append(text(x_positions[index], queue_bottom + 23, label, 9, 700, label_color, "middle"))
    return "".join(parts)


def render_batch_eviction_single_results(spec: dict) -> str:
  """Wide bar-chart results view for the single-replica batch eviction package."""
  latency_panel = spec["panels"][0]
  retry_panel = spec["panels"][1]
  values_by_label = {str(label): float(value) for label, value in latency_panel["rows"]}
  order = [
      ("Realtime only", "Realtime only", "#2f6fed"),
      ("Batch with no protection", "Realtime + batch, no protection", "#b83232"),
      ("Reserved capacity", "Realtime + batch, reserved capacity", "#087f73"),
      ("Reserved capacity + eviction", "Realtime + batch, eviction and retry", "#6650a4"),
  ]
  reference = values_by_label.get("Realtime only", 342.0)
  maximum = max(values_by_label.values() or [reference, 561.0])
  chart_max = max(maximum * 1.08, reference * 1.2)
  chart_left, chart_width = 220, 660
  width, height = 960, 568

  def bar_width(value: float) -> float:
      return max(4.0, chart_width * value / chart_max)

  def delta_text(value: float) -> str:
      delta = round(value - reference)
      if delta == 0:
          return "Matches realtime-only reference"
      sign = "+" if delta > 0 else ""
      return f"{sign}{delta} ms versus realtime only"

  evicted = int(float(retry_panel["stages"][0][1]))
  retried = int(float(retry_panel["stages"][1][1]))
  unprotected = values_by_label.get("Batch with no protection", 561.0)
  protected = values_by_label.get("Reserved capacity", 341.0)

  parts = [
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
      f'<title id="title">{esc(spec["title"])} benchmark results</title>',
      f'<desc id="desc">{esc(spec["takeaway"])} Median realtime p95 TTFT across four matched scenarios with reliable batch recovery.</desc>',
      '<style>text{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;letter-spacing:0}</style>',
      f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
      text(40, 42, "BATCH EVICTION · 1 MODEL REPLICA", 11, 800, "#087f73"),
      text(40, 84, "One pool.", 34, 800, INK),
      text(40, 122, "Protected latency.", 34, 800, INK),
      text(40, 160, "Completed batch.", 34, 800, "#087f73"),
      wrapped_text(40, 186, "Consolidate chat and batch on shared GPUs without choosing cost or user experience.", 880, 14, 500, MUTED, 2),
      text(40, 232, "Median realtime p95 TTFT across three matched 300-second repeats", 13, 650, MUTED),
  ]

  y = 258
  for key, label, color in order:
      value = values_by_label.get(key, reference)
      parts.append(text(40, y, label, 15, 750, INK))
      parts.append(text(width - 40, y, fmt(value, "ms"), 16, 800, INK, "end"))
      parts.append(f'<rect x="{chart_left}" y="{y + 8}" width="{chart_width}" height="26" rx="4" fill="#eef1f4"/>')
      parts.append(f'<rect x="{chart_left}" y="{y + 8}" width="{bar_width(value):.1f}" height="26" rx="4" fill="{color}"/>')
      if key == "Realtime only":
          ref_x = chart_left + bar_width(reference)
          parts.append(f'<line x1="{ref_x:.1f}" y1="{y + 8}" x2="{ref_x:.1f}" y2="{y + 34}" stroke="{color}" stroke-width="2" opacity="0.65"/>')
      parts.append(text(40, y + 48, delta_text(value), 12, 500, MUTED))
      y += 62

  parts.append(f'<rect x="40" y="{height - 56}" width="{width - 80}" height="40" rx="6" fill="{INK}"/>')
  parts.append(text(60, height - 30, f"{fmt(unprotected, 'ms')} → {fmt(protected, 'ms')} with reserved capacity · {evicted} evicted → {retried} retried → {retried} final results · zero duplicates", 13, 700, "#ffffff"))
  parts.append(text(40, height - 16, "Values are generated from the package summary data. Median p95 TTFT in milliseconds.", 11, 500, MUTED))
  parts.append("</svg>\n")
  return "".join(parts)


def render_results(spec: dict) -> str:
    panels = spec["panels"]
    single_column = len(panels) <= 3
    columns = 1 if single_column else 2
    canvas_width = 650 if single_column else WIDTH
    row_heights = []
    for index in range(0, len(panels), columns):
        row_heights.append(max(panel_height(panel) for panel in panels[index:index + columns]))
    height = 126 + sum(row_heights) + max(0, len(row_heights) - 1) * 20 + 52
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width}" height="{height}" viewBox="0 0 {canvas_width} {height}" role="img" aria-labelledby="title desc">',
             f'<title id="title">{esc(spec["title"])} benchmark results</title>',
             f'<desc id="desc">{esc(spec["takeaway"])} Each panel names its metric and unit.</desc>',
             '<style>text{font-family:Inter,Arial,sans-serif;letter-spacing:0}</style>',
             f'<rect width="{canvas_width}" height="{height}" fill="{PAGE}"/>',
             text(40, 50, spec["title"], 30, 800),
             wrapped_text(40, 82, spec["takeaway"], canvas_width - 80, 15, 550, MUTED, 2)]
    if spec.get("tone") == "warning":
        parts.append(f'<rect x="40" y="101" width="{canvas_width - 80}" height="4" fill="{COLORS[2]}"/>')
    y = 112
    panel_index = 0
    for row_height in row_heights:
        for column in range(columns):
            if panel_index >= len(panels):
                break
            panel = panels[panel_index]
            x = 50 if single_column else 40 + column * 570
            renderer = {
                "bar": render_bar,
                "dot": render_dot,
                "grouped": render_grouped,
                "paired": render_paired,
                "process": render_process,
                "line": render_line,
                "combo": render_combo,
            }[panel["kind"]]
            frame_color = MUTED if panel.get("statuses") else COLORS[panel_index % len(COLORS)]
            parts.append(renderer(panel, x, y, row_height, frame_color))
            panel_index += 1
        y += row_height + 20
    parts.append(text(40, height - 24, "Values are generated from the package analysis or summary data. Units are shown in each panel.", 11, 500, MUTED))
    parts.append("</svg>\n")
    return "".join(parts)


def render_architecture(spec: dict) -> str:
    labels = spec["architecture"]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="350" viewBox="0 0 1200 350" role="img" aria-labelledby="title desc">',
             f'<title id="title">{esc(spec["title"])} serving path</title>',
             f'<desc id="desc">The tested path moves from {esc(labels[0])} through {esc(labels[1])} and {esc(labels[2])} to {esc(labels[3])}.</desc>',
             '<style>text{font-family:Inter,Arial,sans-serif;letter-spacing:0}</style>',
             f'<rect width="1200" height="350" fill="{PAGE}"/>',
             text(40, 48, "Tested serving path", 28, 800),
             wrapped_text(40, 78, spec["takeaway"], 1080, 14, 500, MUTED, 2),
             '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#7c8996"/></marker></defs>']
    xs = [40, 325, 610, 895]
    node_colors = ["#2d6cdf", "#087f72", "#6550a5", "#c56a00"]
    for index, (node_x, label) in enumerate(zip(xs, labels)):
        if index < len(xs) - 1:
            parts.append(f'<line x1="{node_x + 245}" y1="210" x2="{xs[index + 1] - 18}" y2="210" stroke="#7c8996" stroke-width="2" marker-end="url(#arrow)"/>')
        parts.append(f'<rect x="{node_x}" y="125" width="245" height="170" fill="{SURFACE}" stroke="{LINE}"/>')
        parts.append(f'<rect x="{node_x}" y="125" width="245" height="5" fill="{node_colors[index]}"/>')
        parts.append(f'<circle cx="{node_x + 30}" cy="160" r="16" fill="{node_colors[index]}"/>')
        parts.append(text(node_x + 30, 166, index + 1, 13, 800, "#ffffff", "middle"))
        parts.append(wrapped_text(node_x + 20, 202, label, 205, 17, 750, INK, 4))
    parts.append(text(40, 327, "Architecture reflects the topology and control point recorded in this package's run configuration.", 11, 500, MUTED))
    parts.append("</svg>\n")
    return "".join(parts)


def readme_with_visuals(path: Path, title_value: str) -> str:
    original = path.read_text()
    start = "<!-- generated:package-visuals -->"
    end = "<!-- /generated:package-visuals -->"
    folder = path.parent
    replayable = ((folder / "request-results.csv").is_file() and (folder / "system-metrics.csv").is_file()) or ((folder / "realtime-requests.csv").is_file() and (folder / "traffic-samples.csv").is_file())
    replay_link = ("\n\n[Replay this package with Flow Control Flight Recorder]"
                   "(https://github.com/alexagriffith/flow-control-visualizer#replay-a-published-benchmark-package)" if replayable else "")
    block = (f"{start}\n\n## Visual summary\n\n"
             f"![{title_value} tested serving path](architecture.svg)\n\n"
             f"![{title_value} benchmark results](results.svg)\n\n"
             f"[Tested configuration](tested-config.yaml){replay_link}\n\n{end}\n")
    if start in original:
        before, rest = original.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + block + "\n" + after.lstrip("\n")
    business = "## Business question"
    if business in original:
        section_start = original.index(business)
        next_heading = original.find("\n## ", section_start + len(business))
        if next_heading != -1:
            return original[:next_heading].rstrip() + "\n\n" + block + "\n" + original[next_heading + 1:]
    first_heading = original.find("\n## ")
    if first_heading != -1:
        return original[:first_heading].rstrip() + "\n\n" + block + "\n" + original[first_heading + 1:]
    return original.rstrip() + "\n\n" + block


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if committed visuals differ from generated output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    mismatches: list[str] = []
    for spec in build_specs(root):
        folder = root / spec["path"]
        outputs = {
            folder / "architecture.svg": render_architecture(spec),
            folder / "results.svg": (
                render_batch_eviction_single_results(spec)
                if spec.get("results_template") == "batch_eviction_single"
                else render_results(spec)
            ),
            folder / "README.md": readme_with_visuals(folder / "README.md", spec["title"]),
        }
        for path, expected in outputs.items():
            if args.check:
                if not path.exists() or path.read_text() != expected:
                    mismatches.append(str(path.relative_to(root)))
            else:
                path.write_text(expected)
    if mismatches:
        print("Generated package visuals are stale:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"- {mismatch}", file=sys.stderr)
        return 1
    if not args.check:
        print(f"Generated visuals for {len(build_specs(root))} benchmark packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

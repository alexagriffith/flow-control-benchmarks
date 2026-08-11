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
    if unit == "percent" or unit.endswith("(%)"):
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


def panel_height(panel: dict) -> int:
    if panel["kind"] == "line":
        return 360
    count = len(panel.get("rows", panel.get("x", [])))
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
    for fraction in [0, 0.5, 1]:
        gy = bottom - (bottom - top) * fraction
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{right}" y2="{gy:.1f}" stroke="#e1e7ed"/>')
        parts.append(text(left - 8, gy + 4, fmt(maximum * fraction, panel["unit"]), 9, 550, MUTED, "end"))
    x_positions = [left + i * (right - left) / max(1, len(x_labels) - 1) for i in range(len(x_labels))]
    tick_step = max(1, math.ceil(len(x_labels) / 7))
    for index, label in enumerate(x_labels):
        if index % tick_step == 0 or index == len(x_labels) - 1:
            parts.append(text(x_positions[index], bottom + 22, label, 9, 600, MUTED, "middle"))
    for series_index, (name, points) in enumerate(series):
        series_color = COLORS[series_index % len(COLORS)]
        coords = [(x_positions[i], bottom - (bottom - top) * float(value) / maximum) for i, value in enumerate(points)]
        parts.append(f'<polyline points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in coords)}" fill="none" stroke="{series_color}" stroke-width="3"/>')
        for px, py in coords:
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{series_color}" stroke="{SURFACE}" stroke-width="2"/>')
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


def render_results(spec: dict) -> str:
    panels = spec["panels"]
    row_heights = []
    for index in range(0, len(panels), 2):
        row_heights.append(max(panel_height(panel) for panel in panels[index:index + 2]))
    height = 126 + sum(row_heights) + max(0, len(row_heights) - 1) * 20 + 52
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">',
             f'<title id="title">{esc(spec["title"])} benchmark results</title>',
             f'<desc id="desc">{esc(spec["takeaway"])} Each panel names its metric and unit.</desc>',
             '<style>text{font-family:Inter,Arial,sans-serif;letter-spacing:0}</style>',
             f'<rect width="1200" height="{height}" fill="{PAGE}"/>',
             text(40, 50, spec["title"], 30, 800),
             wrapped_text(40, 82, spec["takeaway"], 1080, 15, 550, MUTED, 2)]
    y = 112
    panel_index = 0
    for row_height in row_heights:
        for column in range(2):
            if panel_index >= len(panels):
                break
            panel = panels[panel_index]
            x = 40 + column * 570
            renderer = {"bar": render_bar, "grouped": render_grouped, "line": render_line}[panel["kind"]]
            parts.append(renderer(panel, x, y, row_height, COLORS[panel_index % len(COLORS)]))
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
            folder / "results.svg": render_results(spec),
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

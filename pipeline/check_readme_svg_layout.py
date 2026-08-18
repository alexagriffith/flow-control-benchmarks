#!/usr/bin/env python3
"""Basic layout checks for SVGs embedded in README.md.

This does not replace visual review. It catches mechanical failures that should
never reach review: missing assets, malformed SVGs, out-of-bounds marks, text too
close to the canvas edge, and stale labels that have already been rejected.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
EDGE_PAD = 8.0

REJECTED_TEXT = (
    "adds headers",
    "labels requests",
    "shared GPU",
    "Illustrated request path",
    "selected repeat",
    "Flow control runs inside the Endpoint Picker",
    "Flow control inside the Endpoint Picker",
)


@dataclass(frozen=True)
class Box:
    width: float
    height: float


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def num(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else default


def embedded_svgs() -> list[Path]:
    text = README.read_text()
    sources = re.findall(r'<img\s+[^>]*src="([^"]+\.svg)"', text)
    return [ROOT / source for source in sources]


def svg_box(svg: ET.Element) -> Box:
    view_box = svg.attrib.get("viewBox")
    if view_box:
        parts = [float(part) for part in re.split(r"\s+", view_box.strip())]
        if len(parts) == 4:
            return Box(parts[2], parts[3])
    width = num(svg.attrib.get("width"))
    height = num(svg.attrib.get("height"))
    return Box(width, height)


def text_bounds(text: ET.Element) -> tuple[float, float, float, float]:
    x = num(text.attrib.get("x"))
    y = num(text.attrib.get("y"))
    size = num(text.attrib.get("font-size"), 10.0)
    label = "".join(text.itertext()).strip()
    estimated_width = len(label) * size * 0.55
    anchor = text.attrib.get("text-anchor", "start")
    if anchor == "middle":
        left = x - estimated_width / 2
        right = x + estimated_width / 2
    elif anchor == "end":
        left = x - estimated_width
        right = x
    else:
        left = x
        right = x + estimated_width
    top = y - size
    bottom = y + size * 0.35
    return left, top, right, bottom


def element_bounds(element: ET.Element) -> tuple[float, float, float, float] | None:
    name = local_name(element.tag)
    if name == "rect":
        x = num(element.attrib.get("x"))
        y = num(element.attrib.get("y"))
        width = num(element.attrib.get("width"))
        height = num(element.attrib.get("height"))
        return x, y, x + width, y + height
    if name == "circle":
        cx = num(element.attrib.get("cx"))
        cy = num(element.attrib.get("cy"))
        r = num(element.attrib.get("r"))
        return cx - r, cy - r, cx + r, cy + r
    if name == "line":
        x1 = num(element.attrib.get("x1"))
        y1 = num(element.attrib.get("y1"))
        x2 = num(element.attrib.get("x2"))
        y2 = num(element.attrib.get("y2"))
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    if name == "text":
        return text_bounds(element)
    return None


def inside_canvas(bounds: tuple[float, float, float, float], box: Box) -> bool:
    left, top, right, bottom = bounds
    return (
        left >= -EDGE_PAD
        and top >= -EDGE_PAD
        and right <= box.width + EDGE_PAD
        and bottom <= box.height + EDGE_PAD
    )


def validate_svg(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"{path.relative_to(ROOT)} is missing"]
    raw = path.read_text()
    for rejected in REJECTED_TEXT:
        if rejected in raw:
            errors.append(f"{path.relative_to(ROOT)} contains rejected label: {rejected!r}")
    try:
        svg = ET.fromstring(raw)
    except ET.ParseError as exc:
        return [f"{path.relative_to(ROOT)} is not valid XML: {exc}"]
    if local_name(svg.tag) != "svg":
        errors.append(f"{path.relative_to(ROOT)} root element is not svg")
    box = svg_box(svg)
    if box.width <= 0 or box.height <= 0:
        errors.append(f"{path.relative_to(ROOT)} has no usable width/height or viewBox")
        return errors
    for element in svg.iter():
        if any(parent in element.tag for parent in ("defs", "marker")):
            continue
        bounds = element_bounds(element)
        if bounds and not inside_canvas(bounds, box):
            name = local_name(element.tag)
            label = "".join(element.itertext()).strip()
            suffix = f" ({label[:48]})" if label else ""
            errors.append(
                f"{path.relative_to(ROOT)} has out-of-bounds {name}{suffix}: {bounds}"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    paths = embedded_svgs()
    if not paths:
        errors.append("README.md does not embed any SVG images")
    for path in paths:
        errors.extend(validate_svg(path))
    if errors:
        print("README SVG layout check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"README SVG layout check passed for {len(paths)} embedded SVGs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

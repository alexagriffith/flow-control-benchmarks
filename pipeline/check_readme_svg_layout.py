#!/usr/bin/env python3
"""Basic layout checks for SVGs embedded in README.md.

This does not replace visual review. It catches mechanical failures that should
never reach review: missing assets, malformed SVGs, out-of-bounds marks, text too
close to the canvas edge, and stale labels that have already been rejected.
"""

from __future__ import annotations

import math
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
    min_x: float
    min_y: float
    width: float
    height: float


Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


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
            return Box(parts[0], parts[1], parts[2], parts[3])
    width = num(svg.attrib.get("width"))
    height = num(svg.attrib.get("height"))
    return Box(0.0, 0.0, width, height)


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


def multiply(left: Matrix, right: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def transform_matrix(value: str | None) -> Matrix:
    result = IDENTITY
    if not value:
        return result
    for name, raw_args in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", value):
        args = [
            float(part)
            for part in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", raw_args)
        ]
        op = IDENTITY
        if name == "matrix" and len(args) == 6:
            op = tuple(args)  # type: ignore[assignment]
        elif name == "translate" and args:
            op = (1.0, 0.0, 0.0, 1.0, args[0], args[1] if len(args) > 1 else 0.0)
        elif name == "scale" and args:
            sy = args[1] if len(args) > 1 else args[0]
            op = (args[0], 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and args:
            angle = math.radians(args[0])
            rotate = (math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle), 0.0, 0.0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                op = multiply(
                    multiply((1.0, 0.0, 0.0, 1.0, cx, cy), rotate),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                )
            else:
                op = rotate
        result = multiply(result, op)
    return result


def transformed_bounds(
    bounds: tuple[float, float, float, float], matrix: Matrix
) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds
    a, b, c, d, e, f = matrix
    points = [
        (a * x + c * y + e, b * x + d * y + f)
        for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


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
        left >= box.min_x - EDGE_PAD
        and top >= box.min_y - EDGE_PAD
        and right <= box.min_x + box.width + EDGE_PAD
        and bottom <= box.min_y + box.height + EDGE_PAD
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
    def visit(element: ET.Element, parent_matrix: Matrix, ignored: bool = False) -> None:
        name = local_name(element.tag)
        ignored = ignored or name in ("defs", "marker")
        matrix = multiply(parent_matrix, transform_matrix(element.attrib.get("transform")))
        if not ignored:
            bounds = element_bounds(element)
            if bounds:
                bounds = transformed_bounds(bounds, matrix)
                if not inside_canvas(bounds, box):
                    label = "".join(element.itertext()).strip()
                    suffix = f" ({label[:48]})" if label else ""
                    errors.append(
                        f"{path.relative_to(ROOT)} has out-of-bounds {name}{suffix}: {bounds}"
                    )
        for child in element:
            visit(child, matrix, ignored)

    visit(svg, IDENTITY)
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

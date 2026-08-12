#!/usr/bin/env python3
"""Render the grouped upstream report and fail on visual regressions."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "benchmark-data" / "upstream-flow-control-v0.9.0" / "results.html"
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 375, "height": 812},
}


def inspect_page(page) -> dict[str, object]:
    return page.evaluate(
        """
        () => {
          const bars = [...document.querySelectorAll('.bar')];
          const heatCells = [...document.querySelectorAll('.heat-cell')];
          const figures = [...document.querySelectorAll('figure')];
          const color = (element) => getComputedStyle(element).backgroundColor;
          const overflowing = figures.filter((figure) =>
            !figure.classList.contains('architecture-diagram') &&
            figure.scrollWidth > figure.clientWidth + 1
          );
          return {
            horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
            figureCount: figures.length,
            sweepSvgCount: document.querySelectorAll('.sweep-svg').length,
            phaseSvgCount: document.querySelectorAll('.phase-svg').length,
            trafficSvgCount: document.querySelectorAll('.traffic-svg').length,
            rangeRowCount: document.querySelectorAll('.range-row').length,
            heatmapCount: document.querySelectorAll('.heatmap').length,
            zeroWidthBars: bars.filter((bar) => bar.getBoundingClientRect().width <= 0).length,
            barColors: [...new Set(bars.map(color))],
            heatColors: [...new Set(heatCells.map(color))],
            overflowingFigures: overflowing.length,
            overflowingFigureLabels: overflowing.map((figure) =>
              figure.querySelector('h3')?.textContent?.trim() || figure.className || 'unnamed figure'
            ),
          };
        }
        """
    )


def main() -> int:
    failures: list[str] = []
    reports: dict[str, object] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, viewport in VIEWPORTS.items():
            page = browser.new_page(viewport=viewport)
            runtime_errors: list[str] = []
            page.on("console", lambda message: runtime_errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: runtime_errors.append(str(error)))
            page.goto(REPORT.as_uri(), wait_until="load")
            page.wait_for_function("document.querySelectorAll('.sweep-svg').length === 7")
            result = inspect_page(page)
            result["runtimeErrors"] = runtime_errors
            reports[name] = result

            expected = {
                "figureCount": 35,
                "sweepSvgCount": 7,
                "phaseSvgCount": 1,
                "trafficSvgCount": 4,
                "rangeRowCount": 10,
                "heatmapCount": 1,
                "zeroWidthBars": 0,
                "overflowingFigures": 0,
                "horizontalOverflow": False,
            }
            for key, value in expected.items():
                if result[key] != value:
                    failures.append(f"{name}: {key} expected {value!r}, got {result[key]!r}")
            if len(result["barColors"]) < 3:
                failures.append(f"{name}: data bars lost their distinct colors")
            if len(result["heatColors"]) < 3:
                failures.append(f"{name}: heatmap lost its value encoding")
            if runtime_errors:
                failures.append(f"{name}: browser errors: {runtime_errors}")
            page.close()
        browser.close()

    print(json.dumps(reports, indent=2))
    if failures:
        print("\nVisual validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nUpstream report visual validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

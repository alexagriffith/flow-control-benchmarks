#!/usr/bin/env python3
"""Render public entry pages at desktop/mobile sizes, locally or after deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from validate_website import PAGES, ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="HTTP origin including the repository path")
    parser.add_argument("--screenshots", type=Path, help="Optional output directory")
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    failures = []
    reports = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name in PAGES:
            for width in (1440, 390):
                page = browser.new_page(viewport={"width": width, "height": 960})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                url = args.base_url.rstrip("/") + "/" + name if args.base_url else (ROOT / name).as_uri()
                response = page.goto(url, wait_until="networkidle")
                if response and response.status != 200:
                    errors.append(f"HTTP {response.status}")
                page.evaluate("document.fonts.ready")
                result = page.evaluate("""() => ({
                    overflow: document.documentElement.scrollWidth > innerWidth + 1,
                    brokenImages: [...document.images].filter(i => !i.complete || !i.naturalWidth).map(i => i.src),
                    h1: document.querySelector('h1')?.textContent.trim(),
                    retiredMap: document.body.innerText.toLowerCase().includes('three-campaign evidence map'),
                    clippedOverviewTables: location.pathname.endsWith('/benchmark.html') && [...document.querySelectorAll('.tblwrap')].some(t => t.scrollWidth > t.clientWidth + 1),
                })""")
                if result["overflow"] or result["brokenImages"] or result["retiredMap"] or result["clippedOverviewTables"] or not result["h1"]:
                    errors.append(str(result))
                if name == "index.html":
                    assert page.get_by_role("link", name="RHAII 3.5 benchmark takeaways", exact=False).count() == 1
                if name == "sections.html":
                    assert page.get_by_text("All 12 evidence groups", exact=True).count() == 1
                if name == "benchmark.html":
                    assert page.locator("#prefill-decode").count() == 1
                    assert page.locator("#deadlines h2").inner_text() == "Prioritize requests with earlier latency deadlines"
                if args.screenshots:
                    page.screenshot(path=str(args.screenshots / f"{name.replace('/', '-')}-{width}.png"), full_page=True)
                reports.append({"page": name, "width": width, **result, "errors": errors})
                failures.extend(f"{name} @ {width}: {e}" for e in errors)
                page.close()
        browser.close()
    print(json.dumps(reports, indent=2))
    if failures:
        raise SystemExit("\n".join(failures))
    print("Website browser validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

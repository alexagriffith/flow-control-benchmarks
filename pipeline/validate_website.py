#!/usr/bin/env python3
"""Check the published navigation, local assets, and campaign boundaries."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "index.html",
    "sections.html",
    "benchmark.html",
    "walkthrough.html",
    "learn/flow-control-journey.html",
    "learn/flow-control.html",
    "benchmark-walkthrough.html",
    "learn/flow-control-written.html",
    "learn/flow-control-interactive.html",
    "benchmark-data/upstream-flow-control-v0.9.0/results.html",
    "benchmark-data/batch-eviction/results.html",
    "benchmark-data/batch-eviction/single-model-replica/results.html",
)
RETIRED = ("benchmark-data/results.html",)
REPO = "https://github.com/alexagriffith/flow-control-benchmarks/"


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__()
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.headings = 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.headings += tag == "h1"
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])


def validate() -> list[str]:
    errors = []
    for name in RETIRED:
        if (ROOT / name).exists():
            errors.append(f"Retired page returned: {name}")
    for name in PAGES:
        file = ROOT / name
        text = file.read_text()
        page = Page(text)
        if page.headings != 1:
            errors.append(f"{name}: expected one h1, got {page.headings}")
        if "three-campaign evidence map" in text.lower():
            errors.append(f"{name}: retired evidence map returned")
        for link in page.links:
            parsed = urlsplit(link)
            target = None
            if link.startswith(REPO + "tree/main/") or link.startswith(REPO + "blob/main/"):
                target = ROOT / unquote(parsed.path.split("/main/", 1)[1])
            elif not parsed.scheme and not parsed.netloc:
                target = (file.parent / unquote(parsed.path)).resolve() if parsed.path else file
            if target is None:
                continue
            if not target.exists():
                errors.append(f"{name}: missing target {link}")
            elif target.is_file() and target.suffix == ".html" and parsed.fragment:
                # Static navigation anchors must exist; ignore SVG element fragments.
                if unquote(parsed.fragment) not in Page(target.read_text()).ids:
                    errors.append(f"{name}: missing HTML anchor {link}")
    index = (ROOT / "index.html").read_text()
    if 'href="benchmark.html"' in index or 'href="sections.html"' not in index:
        errors.append("Landing page must route campaign reports through the evidence index")
    if 'href="benchmark-walkthrough.html"' not in index:
        errors.append("Landing page must retain the benchmark walkthrough")
    if 'href="exports/' in index:
        errors.append("Landing page offers outdated downloadable snapshots")
    if index.count('class="card"') != 3 or 'href="benchmark-decision-map/"' in index:
        errors.append("Homepage must have three learning entries; planning belongs in resources")
    sections = (ROOT / "sections.html").read_text()
    if sections.count('class="card"') != 4:
        errors.append("Resource index must contain three report cards and one decision map")
    if "Earlier campaigns" in sections or 'class="resources"' not in sections:
        errors.append("Evidence index must use version headers and subordinate resource links")
    if 'href="benchmark-data/batch-eviction/results.html"' not in sections:
        errors.append("Evidence index must use the parent Batch report")
    reports = {ROOT / "benchmark-walkthrough.html"}
    reports.update(
        file for file in (ROOT / "benchmark-data").rglob("*.html")
        if 'http-equiv="refresh"' not in file.read_text()
    )
    index_targets = {
        (ROOT / urlsplit(link).path).resolve()
        for link in Page(sections).links
        if not urlsplit(link).scheme and not urlsplit(link).netloc
    }
    for report in reports:
        if report.resolve() not in index_targets:
            errors.append(f"Evidence index is missing report: {report.relative_to(ROOT)}")
    for campaign in ("rhaii-3.5-flow-control", "rhaii-3.4-flow-control",
                     "upstream-flow-control-v0.9.0", "batch-eviction"):
        if campaign not in sections:
            errors.append(f"Evidence index is missing {campaign}")
    if 'href="benchmark.html"' in sections:
        errors.append("Retired 3.5 overview must not appear in navigation")
    for name, target in {
        "benchmark.html": "sections.html#rhaii35",
        "walkthrough.html": "benchmark-walkthrough.html",
        "learn/flow-control-journey.html": "flow-control-interactive.html",
        "learn/flow-control.html": "flow-control-written.html",
    }.items():
        redirect = (ROOT / name).read_text()
        if 'http-equiv="refresh"' not in redirect or f"url={target}" not in redirect:
            errors.append(f"Missing compatibility redirect: {name}")
    batch = (ROOT / "benchmark-data/batch-eviction/results.html").read_text()
    if "local review" in batch or ".local-review/" in batch or "review draft" in batch:
        errors.append("Published Batch report contains review-only navigation")
    legacy = (ROOT / "benchmark-data/batch-eviction/single-model-replica/results.html").read_text()
    if 'url=../results.html' not in legacy:
        errors.append("Old single-model URL must redirect to the parent Batch report")
    return errors


if __name__ == "__main__":
    failures = validate()
    for failure in failures:
        print(f"- {failure}")
    if failures:
        raise SystemExit(1)
    print(f"Website validation passed: {len(PAGES)} pages, links, assets, and campaign boundaries.")

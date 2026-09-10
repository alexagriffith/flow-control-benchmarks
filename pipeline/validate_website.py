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
    "learn/flow-control.html",
    "learn/flow-control-journey.html",
    "benchmark-data/upstream-flow-control-v0.9.0/results.html",
    "benchmark-data/batch-eviction/single-model-replica/results.html",
)
RETIRED = ("walkthrough.html", "benchmark-data/results.html")
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
    if 'href="benchmark.html"' not in index or 'href="sections.html"' not in index:
        errors.append("Landing page must link the takeaways and evidence index")
    if 'href="exports/' in index:
        errors.append("Landing page offers outdated downloadable snapshots")
    sections = (ROOT / "sections.html").read_text()
    for campaign in ("rhaii-3.5-flow-control", "rhaii-3.4-flow-control",
                     "upstream-flow-control-v0.9.0", "batch-eviction"):
        if campaign not in sections:
            errors.append(f"Evidence index is missing {campaign}")
    overview = (ROOT / "benchmark.html").read_text()
    for anchor in ("capacity", "deadlines", "prefill-decode", "batch-dispatch", "batch-eviction", "reproduce"):
        if anchor not in Page(overview).ids:
            errors.append(f"Overview is missing {anchor}")
    pd = overview.split('id="prefill-decode"', 1)[-1].split('id="batch-dispatch"', 1)[0]
    if "headroom" in pd:
        errors.append("P/D overview contains an untested headroom setting")
    return errors


if __name__ == "__main__":
    failures = validate()
    for failure in failures:
        print(f"- {failure}")
    if failures:
        raise SystemExit(1)
    print(f"Website validation passed: {len(PAGES)} pages, links, assets, and campaign boundaries.")

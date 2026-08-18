#!/usr/bin/env python3
"""Render README.md into the stable local review page served on port 4193."""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("/private/tmp/flow-control-readme-preview/index.html")


STYLE = """
body{margin:0;background:#fff;color:#1f2933;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:18px;line-height:1.55}
.wrap{max-width:760px;margin:64px auto;padding:0 28px 80px}
h1{font-size:36px;line-height:1.1;margin:0 0 22px}
h2{font-size:30px;line-height:1.15;margin:56px 0 22px;padding-bottom:10px;border-bottom:1px solid #cfd8e3}
h3{font-size:21px;line-height:1.25;margin:34px 0 10px}
p + h3{margin-top:46px}
p{margin:16px 0}
img{display:block;max-width:100%;height:auto;margin:22px 0 12px;border:1px solid #d8e1eb;border-radius:6px}
sub{display:block;color:#526170;font-size:15px;line-height:1.45;margin:8px 0 18px}
a{color:#0969da}
table{border-collapse:collapse;width:100%;margin:24px 0;font-size:15px}
th,td{border:1px solid #cfd8e3;padding:12px;vertical-align:top}
th{background:#f6f8fa;text-align:left;font-size:13px;text-transform:uppercase;letter-spacing:.04em}
li{margin:8px 0}
details{border:1px solid #cfd8e3;border-radius:6px;padding:14px 16px;margin:20px 0}
summary{cursor:pointer;font-weight:700}
code{background:#f6f8fa;padding:1px 4px;border-radius:4px}
@media(max-width:640px){body{font-size:17px}.wrap{margin:28px auto;padding:0 20px 56px}h1{font-size:32px}h2{font-size:27px;margin-top:44px}table{font-size:13px}th,td{padding:9px}}
"""


def version_svg_sources(body: str) -> str:
    """Bust browser caches when a generated README SVG changes."""

    def add_version(match: re.Match[str]) -> str:
        source = match.group(1)
        asset = ROOT / source
        if not asset.is_file():
            return match.group(0)
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
        return f'src="{source}?v={digest}"'

    return re.sub(r'src="([^"?]+\.svg)"', add_version, body)


def render(output: Path) -> None:
    sys.path.insert(0, "/tmp/flow-control-preview-lib")
    try:
        import markdown
    except ImportError as exc:
        raise SystemExit(
            "Install the preview dependency with: "
            "python3 -m pip install --target /tmp/flow-control-preview-lib Markdown==3.8.2"
        ) from exc

    body = markdown.markdown(
        (ROOT / "README.md").read_text(),
        extensions=["extra", "toc"],
        output_format="html5",
    )
    body = version_svg_sources(body)
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape('README preview')}</title><style>{STYLE}</style>"
        f'</head><body><main class="wrap">{body}</main></body></html>\n'
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document)
    print(f"wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    render(args.output)


if __name__ == "__main__":
    main()

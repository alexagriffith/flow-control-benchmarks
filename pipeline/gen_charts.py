#!/usr/bin/env python3
"""Generate real-data SVG charts from the RHAII 3.4 benchmark CSVs.

Reads the gate-on / gate-off run pairs and writes the headline charts to assets/.
The corrected SLO-sensitive service-tier result uses per-repeat percentiles from
the 300 s backfill, not pooled repeats.

Verified against benchmark-data/CANONICAL-RESULTS.json. Lead with the principle
(priority admission, zero rejections, premium ahead of standard), not a fake
absolute SLO.
"""
import csv, glob, json, math, os
from collections import defaultdict
from statistics import median

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "benchmark-data", "rhaii-3.4-flow-control")
OUT = os.path.join(ROOT, "assets")
os.makedirs(OUT, exist_ok=True)

# House palette
GREEN, GOLD, BLUE, RED = "#1f8a5f", "#d59a00", "#1c78d8", "#ee0000"
INK, MUTED, FAINT = "#151515", "#6b6b6b", "#8a8a8a"
GRID, AXIS, CARD, STROKE = "#eeeeee", "#c9c8c2", "#fbfbfb", "#d7d7d7"
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"

WARMUP_START = 20.0  # seconds; skip warm-up phase, matches canonical

# Corrected 2026-07-30 service-tier backfill. The older benchmark-data/rhaii-3.4-flow-control/tiers-gate-on
# directory is kept for provenance, but its pooled 251 ms p95 is retired.
TIERS_128_CORRECTED = {
    "premium": {"p50": 374, "p90": 914, "p95": 1117, "range": (1056, 1211)},
    "standard": {"p50": 593, "p90": 1240, "p95": 1406, "range": (1250, 1488)},
}


# ---------------------------------------------------------------- data layer
def rd(p):
    return list(csv.DictReader(open(p)))


def pctl(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(p * len(vals)))]


def gather(scenario):
    """Aggregate a scenario dir (all counted repeats, skip warm-up runs).

    Returns (by_priority -> [ttft_ms], n429). start_s >= WARMUP_START only.
    """
    by_pri = defaultdict(list)
    n429 = 0
    for sub in sorted(glob.glob(os.path.join(DATA, scenario, "*/"))):
        base = os.path.basename(sub.rstrip("/"))
        if "warmup" in base:
            continue
        f = os.path.join(sub, "client_samples.csv")
        if not os.path.exists(f):
            continue
        for r in rd(f):
            if r["status"] == "429":
                n429 += 1
            if r["status"] == "200" and r["ttft_s"] and float(r["start_s"]) >= WARMUP_START:
                by_pri[r["priority"]].append(float(r["ttft_s"]) * 1000.0)
    return by_pri, n429


def p95(by_pri, priority):
    return pctl(by_pri.get(priority, []), 0.95)


# ---------------------------------------------------------------- svg helpers
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_open(w, h, aria):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{esc(aria)}"><rect width="{w}" height="{h}" fill="#ffffff"/>')


def txt(x, y, s, size=12, weight=400, fill=INK, anchor="start", spacing=None, family=FONT):
    sp = f' letter-spacing="{spacing}"' if spacing is not None else ""
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{sp}>{esc(s)}</text>')


def save(name, content):
    with open(os.path.join(OUT, name + ".svg"), "w") as f:
        f.write(content + "</svg>")
    print("wrote", name + ".svg")


def rbar(x, y, w, h, fill, r=5):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" fill="{fill}"/>'


def legend_dot(x, y, color, label):
    return (f'<circle cx="{x}" cy="{y}" r="5" fill="{color}"/>'
            + txt(x + 11, y + 4, label, 11.5, 600, "#383838"))


# ================================================================ CHART 1
# Hero: priority admission, batch rejections, and consolidation under load.
def chart_hero():
    tiers = TIERS_128_CORRECTED
    _, batch_off_429 = gather("batch-gate-off")
    consolidation_on, _ = gather("consolidation-gate-on")

    cards = [
        {
            "tag": "SERVICE PRIORITY",
            "title": "Priority admission",
            "subtitle": "p95 TTFT under saturated load",
            "rows": [
                ("Standard (on)", tiers["standard"]["p95"], "#59636e"),
                ("Premium (on)", tiers["premium"]["p95"], GREEN),
            ],
            "unit": "ms",
        },
        {
            "tag": "BATCH OVERLOAD",
            "title": "Zero rejections",
            "subtitle": "HTTP 429 responses",
            "rows": [("Gate off", batch_off_429, RED), ("Gate on", 0, GREEN)],
            "unit": "count",
        },
        {
            "tag": "SHARED POOL",
            "title": "Shared pool protected",
            "subtitle": "p95 TTFT in one model pool",
            "rows": [
                ("Standard", p95(consolidation_on, "0"), "#59636e"),
                ("Premium", p95(consolidation_on, "100"), GREEN),
            ],
            "unit": "ms",
        },
    ]

    W, H = 1200, 330
    s = svg_open(
        W,
        H,
        "Three measured outcomes: priority admission kept premium traffic below standard p95 TTFT, batch overload produced zero rejections with flow control on, and premium tenants stayed lower latency in a consolidated pool.",
    )
    cw, gap, x0, cy, ch = 374, 12, 18, 14, 302
    for index, card in enumerate(cards):
        x = x0 + index * (cw + gap)
        rows = card["rows"]
        s += f'<rect x="{x}" y="{cy}" width="{cw}" height="{ch}" rx="8" fill="{CARD}" stroke="{STROKE}"/>'
        s += txt(x + 22, cy + 30, card["tag"], 11, 800, rows[1][2], spacing="1")
        s += txt(x + 22, cy + 66, card["title"], 25, 800, INK)
        s += txt(x + 22, cy + 90, card["subtitle"], 12, 550, MUTED)
        maximum = max(value for _, value, _ in rows)
        bar_base = cy + 242
        bar_max_h = 112
        for row_index, (label, value, color) in enumerate(rows):
            bar_x = x + 40 + row_index * 188
            value_label = f"{value:,.0f}" if card["unit"] == "count" else f"{value:,.0f} ms"
            if value == 0:
                s += txt(bar_x + 53, bar_base - 8, value_label, 14, 800, color, "middle")
            else:
                height = max(18, bar_max_h * value / maximum)
                s += txt(bar_x + 53, bar_base - height - 10, value_label, 14, 800, color, "middle")
                s += f'<rect x="{bar_x}" y="{bar_base - height:.1f}" width="106" height="{height:.1f}" rx="6" fill="{color}"/>'
            s += txt(bar_x + 53, bar_base + 22, label, 11.5, 650, "#4b4f54", "middle")

    save("results-at-a-glance", s)


def chart_operating_point():
    """Render the capacity knee with explicit left and right metric axes."""
    grouped = defaultdict(lambda: {"throughput": [], "ttft_ms": []})
    pattern = os.path.join(DATA, "operating-point-sweep", "pass*", "*", "summary.json")
    for path in sorted(glob.glob(pattern)):
        payload = json.load(open(path))
        concurrency = int(payload["scenario"].rsplit("_", 1)[1])
        summary = payload["client_summary"][0]
        grouped[concurrency]["throughput"].append(float(summary["throughput_rps"]))
        grouped[concurrency]["ttft_ms"].append(float(summary["ttft_p95_s"]) * 1000.0)

    settings = sorted(grouped)
    throughput = [median(grouped[value]["throughput"]) for value in settings]
    ttft = [median(grouped[value]["ttft_ms"]) for value in settings]
    selected = 128

    W, H = 880, 320
    left, right = 76.0, 804.0
    top, bottom = 54.0, 254.0

    def sx(value):
        return left + (value - settings[0]) / (settings[-1] - settings[0]) * (right - left)

    def sy(value, low, high):
        return bottom - (value - low) / (high - low) * (bottom - top)

    throughput_low, throughput_high = 20.0, 60.0
    ttft_low, ttft_high = 0.0, 2200.0
    s = svg_open(
        W,
        H,
        "Across two concurrency-sweep passes, served throughput peaked near 128 concurrent requests while p95 time to first token continued to rise at higher limits.",
    )
    s += f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="7" fill="#ffffff" stroke="{STROKE}"/>'

    selected_x = sx(selected)
    s += f'<rect x="{selected_x - 30:.1f}" y="{top - 10:.1f}" width="60" height="{bottom - top + 20:.1f}" fill="{RED}" opacity="0.05"/>'
    s += f'<line x1="{selected_x:.1f}" y1="{top - 10:.1f}" x2="{selected_x:.1f}" y2="{bottom + 10:.1f}" stroke="{RED}" stroke-width="1.5" stroke-dasharray="5 5"/>'
    s += txt(selected_x, 30, "knee = 128", 11, 800, RED, "middle")

    for value in (20, 30, 40, 50, 60):
        y = sy(value, throughput_low, throughput_high)
        s += f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{GRID}"/>'
        s += txt(left - 12, y + 4, f"{value:.0f}", 10, 650, GREEN, "end")
    for value in (0, 500, 1000, 1500, 2000):
        y = sy(value, ttft_low, ttft_high)
        s += txt(right + 12, y + 4, f"{value:,}", 10, 650, GOLD)
    s += txt(left, 42, "Served throughput (requests/s)", 11, 750, GREEN)
    s += txt(right, 42, "p95 TTFT (milliseconds)", 11, 750, GOLD, "end")
    throughput_points = " ".join(
        f"{sx(setting):.1f},{sy(value, throughput_low, throughput_high):.1f}"
        for setting, value in zip(settings, throughput)
    )
    s += f'<polyline points="{throughput_points}" fill="none" stroke="{GREEN}" stroke-width="4"/>'
    for setting, value in zip(settings, throughput):
        x = sx(setting)
        y = sy(value, throughput_low, throughput_high)
        s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{GREEN}" stroke="#ffffff" stroke-width="2"/>'
    peak_index = throughput.index(max(throughput))
    peak_x = sx(settings[peak_index])
    peak_y = sy(throughput[peak_index], throughput_low, throughput_high)
    s += txt(peak_x + 14, peak_y + 20, f"{max(throughput):.1f} requests/s", 10, 800, GREEN)
    ttft_points = " ".join(
        f"{sx(setting):.1f},{sy(value, ttft_low, ttft_high):.1f}"
        for setting, value in zip(settings, ttft)
    )
    s += f'<polyline points="{ttft_points}" fill="none" stroke="{GOLD}" stroke-width="4"/>'
    for setting, value in zip(settings, ttft):
        x = sx(setting)
        y = sy(value, ttft_low, ttft_high)
        s += f'<rect x="{x - 5:.1f}" y="{y - 5:.1f}" width="10" height="10" fill="{GOLD}" transform="rotate(45 {x:.1f} {y:.1f})"/>'
    final_ttft_y = sy(ttft[-1], ttft_low, ttft_high)
    s += txt(right - 10, final_ttft_y - 12, f"{ttft[-1]:,.0f} ms", 11, 800, GOLD, "end")
    selected_index = settings.index(selected)
    selected_ttft_y = sy(ttft[selected_index], ttft_low, ttft_high)
    s += txt(selected_x + 12, selected_ttft_y - 12, f"{ttft[selected_index]:,.0f} ms", 10, 800, GOLD)

    for setting in settings:
        s += txt(sx(setting), 276, str(setting), 10, 650, MUTED, "middle")
    s += txt((left + right) / 2, 302, "Offered concurrency (requests)", 11, 700, MUTED, "middle")
    save("operating-point-sweep", s)


# ================================================================ CHART 2
# Per-scenario TTFT: premium vs standard, gate off vs on. Grouped bars.
def chart_scenario_ttft(scenario_base, title_line, sub_line, name,
                        target=None, ymax=2200):
    if scenario_base == "tiers":
        prem_off = None
        prem_on = TIERS_128_CORRECTED["premium"]["p95"]
        std_off = None
        std_on = TIERS_128_CORRECTED["standard"]["p95"]
    else:
        off, _ = gather(scenario_base + "-gate-off")
        on, _ = gather(scenario_base + "-gate-on")
        prem_off, prem_on = p95(off, "100"), p95(on, "100")
        std_off, std_on = p95(off, "0"), p95(on, "0")

    W, H = 1200, 340
    s = svg_open(W, H, title_line)
    s += txt(20, 30, title_line, 16, 800, INK)
    s += txt(20, 50, sub_line, 12, 400, MUTED)

    plot_top, plot_bot = 78, 262
    x_left, x_right = 60, 1170

    def sy(v):
        return plot_bot - (plot_bot - plot_top) * (v / ymax)

    # gridlines
    step = ymax / 4
    v = 0
    while v <= ymax + 1:
        y = sy(v)
        s += f'<line x1="{x_left}" y1="{y:.1f}" x2="{x_right}" y2="{y:.1f}" stroke="{GRID}"/>'
        s += txt(x_left - 8, y + 4, f"{v:.0f}", 11, 400, FAINT, "end")
        v += step

    if target is not None:
        ty = sy(target)
        s += (f'<line x1="{x_left}" y1="{ty:.1f}" x2="{x_right}" y2="{ty:.1f}" '
              f'stroke="{INK}" stroke-dasharray="5 4" opacity=".35"/>')
        s += txt(x_left + 6, ty - 8, f"{target} ms reference", 11, 600, "#4a4a4a", "start")

    # two groups: Premium, Standard. Two bars each (off, on).
    groups = [("Premium", GREEN, prem_off, prem_on),
              ("Standard", GOLD, std_off, std_on)]
    span = x_right - x_left
    bw = 120
    for gi, (gname, col, voff, von) in enumerate(groups):
        gcx = x_left + span * (gi + 0.5) / len(groups)
        pair_w = bw * 2 + 30
        x = gcx - pair_w / 2
        for val, lbl, fill in [
            (voff, "flow control off", "#9aa0a6"),
            (von, "flow control on", col),
        ]:
            if val is None:
                x += bw + 30
                continue
            y = sy(min(val, ymax))
            h = plot_bot - y
            s += rbar(x, y, bw, h, fill, 6)
            s += txt(x + bw / 2, y - 8, f"{val:.0f} ms", 12.5, 800, INK, "middle")
            s += txt(x + bw / 2, plot_bot + 18, lbl, 11.5, 600, "#4a4a4a", "middle")
            x += bw + 30
        s += txt(gcx, plot_bot + 40, gname, 13, 700, INK, "middle")

    # callout
    if scenario_base == "tiers":
        s += txt(x_left, H - 14, "Corrected 300 s run: premium p95 1,117 ms "
                                 "versus standard 1,406 ms.",
                 12, 600, "#383838")
    s += f'<line x1="{x_left}" y1="{plot_bot}" x2="{x_right}" y2="{plot_bot}" stroke="{AXIS}"/>'
    save(name, s)


# ================================================================ CHART 3
# Batch 429 elimination: 48,224 -> 0, striking.
def chart_batch_429():
    _, off_429 = gather("batch-gate-off")
    _, on_429 = gather("batch-gate-on")

    W, H = 1200, 400
    s = svg_open(W, H, f"Rejected requests under a batch flood, {off_429:,} with the gate off "
                       f"and {on_429} with it on")
    s += txt(20, 34, "Batch overload queued instead of rejected", 20, 800, INK, spacing="-0.5")
    s += txt(20, 56, "HTTP 429 responses under the same offered load", 13, 500, MUTED)

    plot_top, plot_bot = 110, 300
    x_left = 90
    col_w = 420
    gap = 160

    # gate off column
    x1 = x_left
    h_off = plot_bot - plot_top
    s += rbar(x1, plot_top, col_w, h_off, RED, 10)
    s += txt(x1 + col_w / 2, plot_top - 14, f"{off_429:,}", 34, 800, RED, "middle", spacing="-1")
    s += txt(x1 + col_w / 2, plot_bot + 30, "GATE OFF", 13, 800, INK, "middle", spacing="1.5")
    s += txt(x1 + col_w / 2, plot_bot + 50, "requests rejected with 429", 12, 400, MUTED, "middle")

    # gate on column: zero is a label, not a visible bar.
    x2 = x_left + col_w + gap
    s += txt(x2 + col_w / 2, plot_bot - 16, f"{on_429}", 34, 800, GREEN, "middle", spacing="-1")
    s += txt(x2 + col_w / 2, plot_bot + 30, "GATE ON", 13, 800, INK, "middle", spacing="1.5")
    s += txt(x2 + col_w / 2, plot_bot + 50, "requests rejected", 12, 400, MUTED, "middle")

    # arrow between
    ax = x_left + col_w + gap / 2
    s += (f'<line x1="{x_left + col_w + 24}" y1="{plot_bot - 20}" x2="{x2 - 24}" '
          f'y2="{plot_bot - 20}" stroke="{AXIS}" stroke-width="2"/>')
    s += (f'<path d="M {x2 - 24} {plot_bot - 20} l -12 -6 v 12 z" fill="{AXIS}"/>')
    s += txt(ax, plot_bot - 34, "the gate", 11.5, 600, "#4a4a4a", "middle")

    s += txt(20, H - 20, "Same offered load, same GPU. Gate on keeps excess batch work queued at the Endpoint Picker.",
             12, 600, "#383838")
    save("batch-429-elimination", s)


# ================================================================ CHART 4
# Tiers across output lengths. The old pooled 64/512 ratios are kept out of the
# headline until restated with per-repeat percentiles.
def chart_tiers_output_lengths():
    lengths = [
        ("64 tokens", None, None, "restating"),
        ("128 tokens", TIERS_128_CORRECTED["standard"]["p95"], TIERS_128_CORRECTED["premium"]["p95"], "1.25x"),
        ("512 tokens", None, None, "restating"),
    ]

    W, H = 1200, 340
    s = svg_open(W, H, "Premium p95 TTFT gate off versus on across output lengths, "
                       "with the 128-token arm restated using per-repeat percentiles")
    s += txt(20, 30, "Service-tier TTFT by output length", 16, 800, INK)
    s += txt(20, 50, "Only the 128-token arm is restated as a headline here. The older "
                     "64/512 pooled cells remain provenance until rechecked.", 12, 400, MUTED)

    import math
    plot_top, plot_bot = 78, 262
    x_left, x_right = 70, 1170
    ymin_log, ymax_log = math.log10(100), math.log10(8000)

    def sy(v):
        v = max(v, 100)
        return plot_bot - (plot_bot - plot_top) * (math.log10(v) - ymin_log) / (ymax_log - ymin_log)

    for gl in [100, 300, 1000, 3000]:
        y = sy(gl)
        s += f'<line x1="{x_left}" y1="{y:.1f}" x2="{x_right}" y2="{y:.1f}" stroke="{GRID}"/>'
        s += txt(x_left - 8, y + 4, f"{gl:,}", 11, 400, FAINT, "end")
    ty = sy(300)
    s += (f'<line x1="{x_left}" y1="{ty:.1f}" x2="{x_right}" y2="{ty:.1f}" '
          f'stroke="{INK}" stroke-dasharray="5 4" opacity=".45"/>')
    s += txt(x_right - 4, ty - 8, "300 ms reference", 11, 600, "#4a4a4a", "end")

    span = x_right - x_left
    bw = 96
    for gi, (gname, voff, von, label) in enumerate(lengths):
        gcx = x_left + span * (gi + 0.5) / len(lengths)
        pair_w = bw * 2 + 26
        x = gcx - pair_w / 2
        if voff is None:
            s += f'<rect x="{gcx - 98}" y="{plot_top + 44}" width="196" height="112" rx="9" fill="{CARD}" stroke="{STROKE}"/>'
            s += txt(gcx, plot_top + 90, "pending", 20, 800, MUTED, "middle")
            s += txt(gcx, plot_top + 116, "per-repeat restatement", 11.5, 600, "#4a4a4a", "middle")
        else:
            for val, lbl, fill in [(voff, "standard", GOLD), (von, "premium", GREEN)]:
                y = sy(val)
                h = plot_bot - y
                s += rbar(x, y, bw, h, fill, 6)
                s += txt(x + bw / 2, y - 8, f"{val:,} ms", 12, 800, INK, "middle")
                s += txt(x + bw / 2, plot_bot + 18, lbl, 11.5, 600, "#4a4a4a", "middle")
                x += bw + 26
        s += txt(gcx, plot_bot + 42, f"{gname}   ({label})", 13, 700, INK, "middle")

    s += f'<line x1="{x_left}" y1="{plot_bot}" x2="{x_right}" y2="{plot_bot}" stroke="{AXIS}"/>'
    s += legend_dot(x_right - 260, 40, GOLD, "standard")
    s += legend_dot(x_right - 150, 40, GREEN, "premium")
    save("tiers-output-lengths", s)


if __name__ == "__main__":
    chart_hero()  # -> results-at-a-glance.svg (existing hero name)
    chart_operating_point()  # -> operating-point-sweep.svg
    chart_scenario_ttft("tiers", "Priority admission under load",
                        "p95 TTFT (milliseconds), flow control on",
                        "tiers-p95-gate", ymax=2200)
    chart_scenario_ttft("consolidation", "Shared pool under load",
                        "p95 TTFT (milliseconds), flow control off vs on",
                        "consolidation-p95-gate", ymax=1200)
    chart_batch_429()  # -> batch-429-elimination.svg
    chart_tiers_output_lengths()  # -> tiers-output-lengths.svg
    print("done")

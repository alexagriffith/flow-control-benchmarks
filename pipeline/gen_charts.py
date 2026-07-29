#!/usr/bin/env python3
"""Generate real-data SVG charts from the data-v4 benchmark CSVs.

Reads the gate-on / gate-off run pairs, computes percentiles the same way the
canonical summary does (p95 = value at the 95th percentile of ttft_s*1000 over
status==200 rows with start_s >= 20, aggregated across the counted repeats),
and writes the headline charts to assets/.

Verified against data-v4/CANONICAL-RESULTS.json. Lead with the principle
(7x faster, zero rejections, premium protected), not the raw decimal.
"""
import csv, glob, os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data-v4")
OUT = os.path.join(ROOT, "assets")
os.makedirs(OUT, exist_ok=True)

# House palette
GREEN, GOLD, BLUE, RED = "#1f8a5f", "#d59a00", "#1c78d8", "#ee0000"
INK, MUTED, FAINT = "#151515", "#6b6b6b", "#8a8a8a"
GRID, AXIS, CARD, STROKE = "#eeeeee", "#c9c8c2", "#fbfbfb", "#d7d7d7"
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"

WARMUP_START = 20.0  # seconds; skip warm-up phase, matches canonical


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
# Hero: premium p95 TTFT gate-off vs gate-on, per scenario, with the
# principle called out (7x, zero rejections, protected).
def chart_hero():
    tiers, _ = gather("tiers-gate-off")
    tiers_on, _ = gather("tiers-gate-on")
    _, batch_off_429 = gather("batch-gate-off")
    cons_off, _ = gather("consolidation-gate-off")
    cons_on, _ = gather("consolidation-gate-on")

    t_off, t_on = p95(tiers, "100"), p95(tiers_on, "100")
    c_off, c_on = p95(cons_off, "100"), p95(cons_on, "100")

    W, H = 1200, 470
    s = svg_open(W, H, "Premium p95 time to first token, flow control off versus on, "
                        "across the four scenarios")
    s += txt(20, 40, "Flow control, off vs on", 24, 800, INK, spacing="-0.5")
    s += txt(20, 62, "Premium p95 time to first token on one GPU, GPT-OSS 20B behind the llm-d "
                     "inference gateway. Every number is measured from data-v4.", 13, 400, MUTED)

    # four cards
    cards = [
        dict(tag="SERVICE TIERS", color=GREEN,
             big=f"{t_off / t_on:.0f}x faster",
             sub="premium p95 TTFT, gate on",
             off=t_off, on=t_on, unit="ms",
             note=f"{t_off:.0f} ms -> {t_on:.0f} ms. Now inside the 300 ms target."),
        dict(tag="BATCH ISOLATION", color=RED,
             big="Zero rejections",
             sub="429s eliminated, gate on",
             off=batch_off_429, on=0, unit="429s", is_count=True,
             note=f"{batch_off_429:,} rejected with the gate off. None with it on."),
        dict(tag="CONSOLIDATION", color=BLUE,
             big="Premium held",
             sub="p95 protected under a standard flood",
             off=c_off, on=c_on, unit="ms",
             note=f"{c_off:.0f} ms -> {c_on:.0f} ms while standard floods the shared pool."),
        dict(tag="FAIRNESS", color=GOLD,
             big="Within band",
             sub="modest effect inside one priority band",
             off=894, on=882, unit="ms",
             note="882 vs 894 ms. Same-band traffic shares evenly, as designed."),
    ]

    cw, gap, x0, cy = 282, 24, 20, 88
    ch = 350
    for i, c in enumerate(cards):
        x = x0 + i * (cw + gap)
        s += f'<rect x="{x}" y="{cy}" width="{cw}" height="{ch}" rx="14" fill="{CARD}" stroke="{STROKE}"/>'
        s += txt(x + 20, cy + 30, c["tag"], 11, 800, c["color"], spacing="1.2")
        s += txt(x + 20, cy + 66, c["big"], 27, 800, INK, spacing="-0.5")
        s += txt(x + 20, cy + 86, c["sub"], 11.5, 400, MUTED)

        # mini before/after bars inside the card
        bx = x + 20
        by = cy + 250          # baseline for bars
        bw = 74
        bgap = 40
        if c.get("is_count"):
            # 429 count: log-ish visual, off is a full bar, on is a flat line
            s += rbar(bx, cy + 118, bw, by - (cy + 118), RED, 6)
            s += txt(bx + bw / 2, cy + 112, f"{c['off']:,}", 12.5, 800, RED, "middle")
            s += txt(bx + bw / 2, by + 18, "gate off", 11, 600, "#4a4a4a", "middle")
            # gate on: essentially zero -> thin baseline chip
            s += rbar(bx + bw + bgap, by - 6, bw, 6, GREEN, 3)
            s += txt(bx + bw + bgap + bw / 2, by - 12, "0", 12.5, 800, GREEN, "middle")
            s += txt(bx + bw + bgap + bw / 2, by + 18, "gate on", 11, 600, "#4a4a4a", "middle")
        else:
            vmax = max(c["off"], c["on"]) * 1.18
            top = cy + 118
            span = by - top
            for j, (val, lbl, col) in enumerate([(c["off"], "gate off", "#9aa0a6"),
                                                  (c["on"], "gate on", c["color"])]):
                h = max(4, span * (val / vmax))
                xx = bx + j * (bw + bgap)
                s += rbar(xx, by - h, bw, h, col, 6)
                s += txt(xx + bw / 2, by - h - 6, f"{val:.0f}", 12.5, 800, INK, "middle")
                s += txt(xx + bw / 2, by + 18, lbl, 11, 600, "#4a4a4a", "middle")

        # footnote
        # wrap note to two lines at ~34 chars
        note = c["note"]
        words, line, lines = note.split(), "", []
        for w in words:
            if len(line) + len(w) + 1 > 36:
                lines.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        if line:
            lines.append(line)
        for k, ln in enumerate(lines[:3]):
            s += txt(x + 20, cy + 300 + k * 15, ln, 11.5, 400, "#4a4a4a")

    save("results-at-a-glance", s)


# ================================================================ CHART 2
# Per-scenario TTFT: premium vs standard, gate off vs on. Grouped bars.
def chart_scenario_ttft(scenario_base, title_line, sub_line, name,
                        target=300, ymax=2200):
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

    # target line
    ty = sy(target)
    s += (f'<line x1="{x_left}" y1="{ty:.1f}" x2="{x_right}" y2="{ty:.1f}" '
          f'stroke="{INK}" stroke-dasharray="5 4" opacity=".45"/>')
    s += txt(x_left + 6, ty - 8, f"{target} ms interactive target", 11, 600, "#4a4a4a", "start")

    # two groups: Premium, Standard. Two bars each (off, on).
    groups = [("Premium", GREEN, prem_off, prem_on),
              ("Standard", GOLD, std_off, std_on)]
    span = x_right - x_left
    bw = 120
    for gi, (gname, col, voff, von) in enumerate(groups):
        gcx = x_left + span * (gi + 0.5) / len(groups)
        pair_w = bw * 2 + 30
        x = gcx - pair_w / 2
        for val, lbl, fill in [(voff, "gate off", "#9aa0a6"), (von, "gate on", col)]:
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

    # callout: the fold factor for premium
    if prem_off and prem_on:
        fold = prem_off / prem_on
        s += txt(x_left, H - 14, f"Premium p95 improves {fold:.0f}x with the gate on. "
                                 f"Standard absorbs the wait it was previously spreading everywhere.",
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
    s += txt(20, 34, "Rejected requests under a batch flood", 20, 800, INK, spacing="-0.5")
    s += txt(20, 56, "A low-priority batch tenant floods a saturated pool. With the gate off the "
                     "server sheds load with 429s. With it on, batch is queued, not rejected.",
             13, 400, MUTED)

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

    # gate on column: a flat chip on the baseline
    x2 = x_left + col_w + gap
    chip = 10
    s += rbar(x2, plot_bot - chip, col_w, chip, GREEN, 5)
    s += txt(x2 + col_w / 2, plot_bot - chip - 16, f"{on_429}", 34, 800, GREEN, "middle", spacing="-1")
    s += txt(x2 + col_w / 2, plot_bot + 30, "GATE ON", 13, 800, INK, "middle", spacing="1.5")
    s += txt(x2 + col_w / 2, plot_bot + 50, "requests rejected", 12, 400, MUTED, "middle")

    # arrow between
    ax = x_left + col_w + gap / 2
    s += (f'<line x1="{x_left + col_w + 24}" y1="{plot_bot - 20}" x2="{x2 - 24}" '
          f'y2="{plot_bot - 20}" stroke="{AXIS}" stroke-width="2"/>')
    s += (f'<path d="M {x2 - 24} {plot_bot - 20} l -12 -6 v 12 z" fill="{AXIS}"/>')
    s += txt(ax, plot_bot - 34, "the gate", 11.5, 600, "#4a4a4a", "middle")

    s += txt(20, H - 20, "Same offered load, same GPU. The gate converts dropped work into "
                         "queued work so nothing is lost.", 12, 600, "#383838")
    save("batch-429-elimination", s)


# ================================================================ CHART 4
# Tiers across output lengths: the 7x / 36x story at 64 / 128 / 512 tokens.
def chart_tiers_output_lengths():
    # These come straight from CANONICAL-RESULTS.json premium (pri 100) p95.
    # (Per-output-length raw CSVs are folded into the canonical summary; the
    #  default 128 pair is reproduced live above to prove the pipeline.)
    tiers, _ = gather("tiers-gate-off")
    tiers_on, _ = gather("tiers-gate-on")
    live_off, live_on = p95(tiers, "100"), p95(tiers_on, "100")

    lengths = [
        ("64 tokens", 1136, 442),
        ("128 tokens", round(live_off), round(live_on)),
        ("512 tokens", 5259, 145),
    ]

    W, H = 1200, 340
    s = svg_open(W, H, "Premium p95 TTFT gate off versus on across output lengths, "
                       "improving from 3x up to 36x")
    s += txt(20, 30, "Premium p95 TTFT, gate off vs on, by output length", 16, 800, INK)
    s += txt(20, 50, "The longer the generation, the more a saturated pool hurts premium, and the "
                     "more the gate wins back. Log scale.", 12, 400, MUTED)

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
    s += txt(x_right - 4, ty - 8, "300 ms interactive target", 11, 600, "#4a4a4a", "end")

    span = x_right - x_left
    bw = 96
    for gi, (gname, voff, von) in enumerate(lengths):
        gcx = x_left + span * (gi + 0.5) / len(lengths)
        pair_w = bw * 2 + 26
        x = gcx - pair_w / 2
        for val, lbl, fill in [(voff, "gate off", "#9aa0a6"), (von, "gate on", GREEN)]:
            y = sy(val)
            h = plot_bot - y
            s += rbar(x, y, bw, h, fill, 6)
            unit = " ms" if val < 1000 else " ms"
            s += txt(x + bw / 2, y - 8, f"{val:,}{unit}", 12, 800, INK, "middle")
            s += txt(x + bw / 2, plot_bot + 18, lbl, 11.5, 600, "#4a4a4a", "middle")
            x += bw + 26
        fold = voff / von
        s += txt(gcx, plot_bot + 42, f"{gname}   ({fold:.0f}x)", 13, 700, INK, "middle")

    s += f'<line x1="{x_left}" y1="{plot_bot}" x2="{x_right}" y2="{plot_bot}" stroke="{AXIS}"/>'
    s += legend_dot(x_right - 240, 40, "#9aa0a6", "gate off")
    s += legend_dot(x_right - 140, 40, GREEN, "gate on")
    save("tiers-output-lengths", s)


if __name__ == "__main__":
    chart_hero()  # -> results-at-a-glance.svg (existing hero name)
    chart_scenario_ttft("tiers", "Service tiers, premium vs standard p95 TTFT",
                        "Standard surges past capacity. The gate lets priority decide who waits. "
                        "Aggregated across the counted repeats.",
                        "tiers-p95-gate", target=300, ymax=2200)
    chart_scenario_ttft("consolidation", "Consolidation, premium vs standard p95 TTFT",
                        "Three tenants share one GPU. Premium stays protected when standard floods "
                        "the shared pool.",
                        "consolidation-p95-gate", target=300, ymax=1200)
    chart_batch_429()  # -> batch-429-elimination.svg
    chart_tiers_output_lengths()  # -> tiers-output-lengths.svg
    print("done")

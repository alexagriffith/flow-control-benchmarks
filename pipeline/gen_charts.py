#!/usr/bin/env python3
"""Generate real-data SVG charts from benchmark run CSVs."""
import csv, os, math
from collections import defaultdict

# Point these at the campaign roots described in RUNLOG.md before running.
U = "."
B = "."
OUT = "assets"
os.makedirs(OUT, exist_ok=True)

GREEN, GOLD, BLUE, RED = "#1f8a5f", "#d59a00", "#1c78d8", "#ee0000"
INK, MUTED, GRID, AXIS = "#151515", "#898781", "#ececea", "#c9c8c2"

W, H = 560, 300
ML, MR, MT, MB = 56, 16, 66, 40  # margins: top leaves room for title

def rd(p): return list(csv.DictReader(open(p)))

def ok(r): return r.get("status") == "200" and r.get("ttft_s")

def sx(t, t0, t1): return ML + (t - t0) / (t1 - t0) * (W - ML - MR)
def sy(v, v0, v1): return MT + (1 - (v - v0) / (v1 - v0)) * (H - MT - MB)

def rolling_pct(samples, pct, win=30.0, step=5.0, t0=0, t1=300):
    """samples: list of (t, v). Returns list of (t_mid, pct value) for windows with >=8 samples."""
    samples = sorted(samples)
    out = []
    t = t0 + win
    while t <= t1 + 1e-9:
        w = [v for (ts, v) in samples if t - win <= ts <= t]
        if len(w) >= 8:
            w.sort()
            out.append((t - win / 2, w[min(len(w) - 1, int(pct * len(w)))]))
        t += step
    return out

def polyline(pts, t0, t1, v0, v1, color, width=2, dash=None):
    p = " ".join(f"{sx(t,t0,t1):.1f},{sy(v,v0,v1):.1f}" for t, v in pts)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<polyline fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"{d} points="{p}"/>'

def area(pts, t0, t1, v0, v1, color, opacity=0.10):
    p = " ".join(f"{sx(t,t0,t1):.1f},{sy(v,v0,v1):.1f}" for t, v in pts)
    x_first = sx(pts[0][0], t0, t1); x_last = sx(pts[-1][0], t0, t1); ybase = sy(v0, v0, v1)
    return f'<polygon fill="{color}" fill-opacity="{opacity}" points="{x_first:.1f},{ybase:.1f} {p} {x_last:.1f},{ybase:.1f}"/>'

def grid_y(vals, v0, v1, t0, t1, fmt=lambda v: f"{v:g}"):
    s = ""
    for v in vals:
        y = sy(v, v0, v1)
        s += f'<line x1="{ML}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        s += f'<text x="{ML-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">{fmt(v)}</text>'
    return s

def axis_x(t0, t1, ticks, label="elapsed seconds"):
    ybase = H - MB
    s = f'<line x1="{ML}" y1="{ybase}" x2="{W-MR}" y2="{ybase}" stroke="{AXIS}" stroke-width="1"/>'
    for t in ticks:
        x = sx(t, t0, t1)
        s += f'<text x="{x:.1f}" y="{ybase+18}" text-anchor="middle" font-size="11" fill="{MUTED}">{t:g}</text>'
    s += f'<text x="{(ML+W-MR)/2:.0f}" y="{H-6}" text-anchor="middle" font-size="11" fill="{MUTED}">{label}</text>'
    return s

def title(t, sub=None):
    s = f'<text x="{ML}" y="20" font-size="14" font-weight="700" fill="{INK}">{t}</text>'
    if sub:
        s += f'<text x="{ML}" y="36" font-size="11" fill="{MUTED}">{sub}</text>'
    return s

def legend(items, x=None, y=None):
    x = x if x is not None else W - MR - 10
    y = y if y is not None else 56
    # right-aligned horizontal legend
    parts, cx = [], x
    for name, color in reversed(items):
        tw = len(name) * 6.3 + 16
        cx -= tw
        parts.append(f'<circle cx="{cx:.0f}" cy="{y-4}" r="4.5" fill="{color}" stroke="#ffffff" stroke-width="2"/>'
                     f'<text x="{cx+9:.0f}" y="{y}" font-size="11" font-weight="600" fill="#383838">{name}</text>')
        cx -= 14
    return "".join(parts)

def svg(body, aria):
    return (f'<svg class="line-chart" viewBox="0 0 {W} {H}" role="img" aria-label="{aria}" '
            f'xmlns="http://www.w3.org/2000/svg">{body}</svg>')

def save(name, content):
    with open(f"{OUT}/{name}.svg", "w") as f:
        f.write(content)
    print("wrote", name)

# ---------------- Scenario 1 ----------------
r02 = rd(f"{U}/tmp-stage/s1-r02-client.csv")
t0, t1, v0, v1 = 0, 300, 0, 350
body = title("Rolling p95 TTFT while the pool consolidates", "30 s window. 300 ms is a general interactive target, not a customer-set SLA.")
body += grid_y([0, 100, 200, 300], v0, v1, t0, t1, lambda v: f"{v:g} ms")
# tenant B join marker
xb = sx(155, t0, t1)
body += f'<line x1="{xb:.1f}" y1="{MT+4}" x2="{xb:.1f}" y2="{H-MB}" stroke="{AXIS}" stroke-width="1"/>'
body += f'<text x="{xb+6:.1f}" y="{H-MB-10}" font-size="11" fill="{MUTED}">tenant B joins</text>'
for tn, col, lbl in [("premium-tenant-a", GREEN, "Tenant A"), ("premium-tenant-b", BLUE, "Tenant B")]:
    pts = [(float(r["start_s"]), float(r["ttft_s"]) * 1e3) for r in r02 if ok(r) and r["tenant"] == tn]
    roll = rolling_pct(pts, 0.95, win=30, step=5, t0=(155 if tn.endswith("b") else 0), t1=300)
    if roll:
        body += area(roll, t0, t1, v0, v1, col, 0.08)
        body += polyline(roll, t0, t1, v0, v1, col, 2.5)
        ex, ev = roll[-1]
        body += f'<circle cx="{sx(ex,t0,t1):.1f}" cy="{sy(ev,v0,v1):.1f}" r="4.5" fill="{col}" stroke="#ffffff" stroke-width="2"/>'
body += f'<text x="{W-MR}" y="{sy(300,v0,v1)-6:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">300 ms interactive target</text>'
body += axis_x(t0, t1, [0, 60, 120, 180, 240, 300])
body += legend([("Tenant A", GREEN), ("Tenant B", BLUE)])
save("s1_ttft", svg(body, "Scenario 1: rolling p95 TTFT for both tenants stays near 120 ms while they consolidate onto one GPU"))

# S1 chart B: p95 bars per tenant per repeat
vals = [("Repeat 2", [("A", 114.2, GREEN), ("B", 124.9, BLUE)]),
        ("Repeat 3", [("A", 118.4, GREEN), ("B", 124.2, BLUE)])]
v0, v1 = 0, 350
body = title("p95 TTFT by repeat, counted repeats", "Repeat 1 excluded as cold-start stabilization.")
body += grid_y([0, 100, 200, 300], v0, v1, 0, 1, lambda v: f"{v:g} ms")
body += f'<text x="{W-MR}" y="{sy(300,v0,v1)-6:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">300 ms interactive target</text>'
groups = len(vals); bw = 56; gap = 8
span = W - ML - MR
for gi, (gname, bars) in enumerate(vals):
    gcx = ML + span * (gi + 0.5) / groups
    total_w = len(bars) * bw + (len(bars) - 1) * gap
    x = gcx - total_w / 2
    for name, v, col in bars:
        y = sy(v, v0, v1); hgt = (H - MB) - y
        body += f'<path d="M {x:.1f} {y+4:.1f} q 0 -4 4 -4 h {bw-8} q 4 0 4 4 v {hgt-4:.1f} h -{bw} z" fill="{col}"/>'
        body += f'<text x="{x+bw/2:.1f}" y="{y-8:.1f}" text-anchor="middle" font-size="12" font-weight="700" fill="{INK}">{v:.0f} ms</text>'
        body += f'<text x="{x+bw/2:.1f}" y="{H-MB+18}" text-anchor="middle" font-size="11" fill="{MUTED}">Tenant {name}</text>'
        x += bw + gap
    body += f'<text x="{gcx:.1f}" y="{H-6}" text-anchor="middle" font-size="11" font-weight="600" fill="#383838">{gname}</text>'
body += f'<line x1="{ML}" y1="{H-MB}" x2="{W-MR}" y2="{H-MB}" stroke="{AXIS}" stroke-width="1"/>'
save("s1_bars", svg(body, "Scenario 1: p95 TTFT stays between 114 and 125 ms in both counted repeats, far under 300 ms"))

# ---------------- Scenario 2 ----------------
conc = rd(f"{B}/cursor-experiments/test2-final-v2/02-test2_priority_noisy-r02/concurrency_samples.csv")
cls_of = lambda t: "premium" if t.startswith("premium") else "standard"
per = defaultdict(lambda: defaultdict(float))
for r in conc:
    per[round(float(r["elapsed_s"]))][cls_of(r["tenant"])] += float(r["actual_inflight"])
ts = sorted(per)
prem = [(t, per[t]["premium"]) for t in ts if t <= 300]
stan = [(t, per[t]["standard"]) for t in ts if t <= 300]
t0, t1, v0, v1 = 0, 300, 0, 180
body = title("In-flight requests by class", "Repeat 2 of 3. Standard surges mid-window. Premium holds steady.")
body += grid_y([0, 60, 120, 180], v0, v1, t0, t1)
body += area(stan, t0, t1, v0, v1, GOLD, 0.12) + polyline(stan, t0, t1, v0, v1, GOLD, 2)
body += area(prem, t0, t1, v0, v1, GREEN, 0.12) + polyline(prem, t0, t1, v0, v1, GREEN, 2)
body += axis_x(t0, t1, [0, 60, 120, 180, 240, 300])
body += legend([("Premium", GREEN), ("Standard", GOLD)])
save("s2_traffic", svg(body, "Scenario 2 traffic: standard class surges to about 160 in-flight requests while premium stays near 40"))

cli = rd(f"{B}/cursor-experiments/test2-final-v2/02-test2_priority_noisy-r02/client_samples.csv")
met = rd(f"{B}/cursor-experiments/test2-final-v2/02-test2_priority_noisy-r02/metric_samples.csv")
t0, t1, v0, v1 = 0, 300, 0, 1200
body = title("Rolling p95 TTFT by class", "30 s window. Shaded band marks vLLM local queue depth over 0.")
body += grid_y([0, 300, 600, 900, 1200], v0, v1, t0, t1, lambda v: f"{v:g}" if v else "0 ms")
# saturation band from metric samples
sat = [(float(r["elapsed_s"]), float(r["vllm:num_requests_waiting"])) for r in met]
in_band = [t for t, w in sat if w > 0]
if in_band:
    x1b, x2b = sx(min(in_band), t0, t1), sx(max(in_band), t0, t1)
    body = body.replace(f'<text x="{ML}" y="20"', f'<rect x="{x1b:.1f}" y="{MT+4}" width="{x2b-x1b:.1f}" height="{H-MB-MT-4}" fill="#f4ede0" fill-opacity="0.7"/><text x="{ML}" y="20"', 1)
    body += f'<text x="{(x1b+x2b)/2:.0f}" y="{MT+16}" text-anchor="middle" font-size="11" fill="{MUTED}">GPU saturated, requests queueing</text>'
for cls, col in [("standard", GOLD), ("premium", GREEN)]:
    pts = [(float(r["start_s"]), float(r["ttft_s"]) * 1e3) for r in cli if ok(r) and cls_of(r["tenant"]) == cls]
    roll = rolling_pct(pts, 0.95, win=30, step=5)
    body += polyline(roll, t0, t1, v0, v1, col, 2.5)
    last = roll[-1]
body += axis_x(t0, t1, [0, 60, 120, 180, 240, 300])
body += legend([("Premium", GREEN), ("Standard", GOLD)])
save("s2_ttft", svg(body, "Scenario 2: during saturation the standard class p95 TTFT rises toward 900 ms while premium stays near 550 ms"))

# ---------------- Scenario 3 ----------------
conc3 = rd(f"{B}/cursor-experiments/test3-saturated-v1/02-test3_fairness_noisy_saturated-r02/concurrency_samples.csv")
per3 = defaultdict(lambda: defaultdict(float))
for r in conc3:
    per3[round(float(r["elapsed_s"]))][r["tenant"]] += float(r["actual_inflight"])
ts3 = sorted(t for t in per3 if t <= 300)
TEN3 = [("premium-tenant-a", GOLD, "Tenant A, spiky"), ("premium-tenant-b", GREEN, "Tenant B"), ("premium-tenant-c", BLUE, "Tenant C")]
t0, t1 = 0, 300
vmax = max(per3[t][tn] for t in ts3 for tn, _, _ in TEN3)
v0, v1 = 0, math.ceil(vmax / 20) * 20
body = title("In-flight requests per tenant", "Repeat 2 of 3. All three tenants are premium. Tenant A spikes.")
body += grid_y([0, 40, 80, 120], v0, v1, t0, t1)
for tn, col, _ in TEN3:
    pts = [(t, per3[t][tn]) for t in ts3]
    body += polyline(pts, t0, t1, v0, v1, col, 2)
body += axis_x(t0, t1, [0, 60, 120, 180, 240, 300])
body += legend([(lbl, col) for _, col, lbl in TEN3])
save("s3_traffic", svg(body, "Scenario 3 traffic: tenant A repeatedly spikes while tenants B and C stay moderate"))

cli3 = rd(f"{B}/cursor-experiments/test3-saturated-v1/02-test3_fairness_noisy_saturated-r02/client_samples.csv")
t0, t1, v0, v1 = 0, 300, 0, 800
body = title("Rolling p50 TTFT per tenant", "30 s window. The spiking tenant pays for its own queue. Peers stay bounded.")
body += grid_y([0, 200, 400, 600, 800], v0, v1, t0, t1, lambda v: f"{v:g}" if v else "0 ms")
for tn, col, _ in TEN3:
    pts = [(float(r["start_s"]), float(r["ttft_s"]) * 1e3) for r in cli3 if ok(r) and r["tenant"] == tn]
    roll = rolling_pct(pts, 0.50, win=30, step=5)
    body += polyline(roll, t0, t1, v0, v1, col, 2.5)
body += axis_x(t0, t1, [0, 60, 120, 180, 240, 300])
body += legend([(lbl, col) for _, col, lbl in TEN3])
save("s3_ttft", svg(body, "Scenario 3: tenant A median TTFT rises during its spikes while tenants B and C stay lower and stable"))

# ---------------- Scenario 4 ----------------
cli4 = rd(f"{U}/tmp-stage/t4-r01-client.csv")
cli4 = [r for r in cli4 if ok(r) and r["tenant"] != "tenant"]
CLS4 = [("batch-tenant-a", BLUE, "Batch"), ("standard-tenant-a", GOLD, "Standard"), ("premium-tenant-a", GREEN, "Premium")]
# arrival rate per 10s bin
t0, t1 = 0, 300
bins = defaultdict(lambda: defaultdict(int))
for r in cli4:
    s = float(r["start_s"])
    if s <= 300:
        bins[int(s // 10) * 10 + 5][r["tenant"]] += 1
ts4 = sorted(bins)
v1 = 0
series4 = {}
for tn, col, lbl in CLS4:
    pts = [(t, bins[t][tn] / 10.0) for t in ts4]
    series4[tn] = pts
    v1 = max(v1, max(v for _, v in pts))
v0, v1 = 0, math.ceil(v1 / 10) * 10
body = title("Arrival rate by class, requests per second", "Repeat 1 of 3. Batch ramps in at about 150 s and stays high.")
body += grid_y([0, 20, 40, 60], v0, v1, t0, t1)
for tn, col, lbl in CLS4:
    body += area(series4[tn], t0, t1, v0, v1, col, 0.10) + polyline(series4[tn], t0, t1, v0, v1, col, 2)
body += axis_x(t0, t1, [0, 60, 120, 180, 240, 300])
body += legend([(lbl, col) for _, col, lbl in reversed(CLS4)])
save("s4_traffic", svg(body, "Scenario 4 traffic: batch arrival rate ramps to roughly triple the interactive classes"))

t0, t1, v0, v1 = 0, 300, 0, 1200
body = title("Rolling p95 TTFT by traffic class", "30 s window. Batch absorbs the waiting. Interactive lanes stay ahead.")
body += grid_y([0, 300, 600, 900, 1200], v0, v1, t0, t1, lambda v: f"{v:g}" if v else "0 ms")
for tn, col, lbl in CLS4:
    pts = [(float(r["start_s"]), float(r["ttft_s"]) * 1e3) for r in cli4 if ok(r) and r["tenant"] == tn]
    roll = rolling_pct(pts, 0.95, win=30, step=5)
    body += polyline(roll, t0, t1, v0, v1, col, 2.5)
body += axis_x(t0, t1, [0, 60, 120, 180, 240, 300])
body += legend([(lbl, col) for _, col, lbl in reversed(CLS4)])
save("s4_ttft", svg(body, "Scenario 4: batch p95 TTFT runs roughly 300 ms above premium and standard throughout the surge"))

print("done")

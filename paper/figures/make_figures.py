#!/usr/bin/env python3
"""Figures for the TPAMI manuscript, all from one saved profiling document.

    "C:\\Program Files\\Python312\\python.exe" paper/figures/make_figures.py [profile.sliprof]

Writes PDF figures next to this script and prints every number the text quotes, so the
manuscript can be checked against the data. Default document: profile_2026-10-16.sliprof at
the repo root (ML750ST, 800x600 @ 120 Hz, offline mode).

Row layouts (host/sli_ui.py):
  sweep rows   [T, expo_us, delay_us, frame_k, t_us, mean, std, n, nhi, active_us]
  runway rows  [window_start, expo_us, prev_level, this_level, mean, std, n, nhi, T]
"""
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
from matplotlib.colors import LogNorm                 # noqa: E402
from matplotlib.ticker import FormatStrFormatter, NullFormatter  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DOC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "profile_2026-10-16.sliprof")

plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.labelsize": 8,
                     "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
                     "axes.linewidth": 0.6, "lines.linewidth": 0.8, "pdf.fonttype": 42})
COL1, COL2 = 3.5, 7.1667                              # IEEE column / page width, inches
C = {"w": "#1f5fd0", "k": "#c0392b", "r": "#d02a2a", "g": "#1e9e3a", "b": "#1f5fd0",
     "lead": "#2e9e4f", "trail": "#7b3fb5", "rw": "#0891b2"}

d = json.load(open(DOC))
T = d["T_us"]
E = d["edges"]
lead, trail, rw = E["lead_us"], E["trail_us"], E["runway_us"]


def sweep(key):
    r = np.array([row[:6] for row in d["sweeps"][key]["rows"]], float)
    o = np.argsort(r[:, 4])
    return r[o, 4], r[o, 5]


tw, w = sweep("wwwww")
tk, k = sweep("wkkkk")
dark = float(np.median(k[tk >= 2 * T]))
print("T %.2f us, dark %.3f ADU, max expo units %d = %.2f us"
      % (T, dark, d["max_expo_units"], d["max_expo_units"] * 0.375))


# ---- light captured against exposure length (circular best window) -----------------
def best_share(t, v, width):
    """Largest share of v (dark-subtracted, >=0) inside any circular window of `width`."""
    L = 5 * T
    step = np.median(np.diff(t))
    x = np.clip(v - dark, 0, None)
    n = int(round(width / step))
    c = np.concatenate([x, x[:n]])
    s = np.convolve(c, np.ones(n), "valid")[:len(x)]
    i = int(np.argmax(s))
    return s[i] / x.sum(), t[i]


def share(t, v, a, b):
    x = np.clip(v - dark, 0, None)
    L = 5 * T
    m = ((t - a) % L) < (b - a)
    return x[m].sum() / x.sum()


print("WKKKK share [lead, trail] %.2f %%  (exposure %.2f us)"
      % (100 * share(tk, k, lead, trail), trail - lead))
print("runway [rw, lead] %.2f %%; overlap [rw, trail-T] %.2f %%; between [trail-T, lead] %.2f %%"
      % tuple(100 * share(tk, k, a, b) for a, b in
              ((rw, lead), (rw, trail - T), (trail - T, lead))))
mx = d["max_expo_units"] * 0.375
for wd in (2000, 4000, 6000, 6256.83, mx):
    s_, t0 = best_share(tk, k, wd)
    print("best window %.2f us: %.2f %% from %.1f us" % (wd, 100 * s_, t0))

# ---- the five-frame timeline: WWWWW and WKKKK with the edges ------------------------
def timeline(fname, xlim, frame3=False, size=(COL2, 2.5), legend=True, traces=None):
    """WWWWW and WKKKK profiles with the runway, lead and trail marked, over `xlim` (us).
    `frame3` overlays WKKKK three frames later, so frames 3 and 4 (black, among black frames)
    lie over frames 0 and 1: the profile an all-black KKKKK sequence would give. `traces`,
    a list of (t, v, color), replaces the WWWWW and WKKKK profiles."""
    fig, ax = plt.subplots(figsize=size)
    ax.axvspan(rw / 1000, lead / 1000, color=C["rw"], alpha=0.12, lw=0)
    ax.axvspan(lead / 1000, trail / 1000, color=C["lead"], alpha=0.08, lw=0)
    ax.axvspan(rw / 1000, (trail - T) / 1000, color="none", hatch="////", ec=C["rw"], lw=0,
               alpha=0.5)
    if traces is None:
        ax.plot(tw / 1000, w, color=C["w"], label="WWWWW (every frame white)")
        ax.plot(tk / 1000, k, color=C["k"], label="WKKKK (frame 0 white, frames 1--4 black)")
    for t_, v_, c_ in traces or ():
        ax.plot(t_ / 1000, v_, color=c_)
    if frame3:
        m = tk >= 3 * T
        ax.plot((tk[m] - 3 * T) / 1000, k[m], color=C["k"], ls="--",
                label="KKKKK (WKKKK frames 3--4, shifted by $-3T$)")
    for x_, c_, n_ in ((rw, C["rw"], "runway"), (lead, C["lead"], "lead"),
                       (trail, C["trail"], "trail")):
        ax.axvline(x_ / 1000, color=c_, lw=0.9)
        # the trail's label sits left of its line, inside the plot
        dx = (-1 if n_ == "trail" else 1) * 0.002 * (xlim[1] - xlim[0])
        ax.text((x_ + dx) / 1000, 850, n_, color=c_, ha="right" if n_ == "trail" else "left",
                fontsize=7)
    for j in range(6):
        ax.axvline(j * T / 1000, color="0.6", lw=0.4, ls=":")
    for j in range(5):
        xc = (j + 0.5) * T
        if xlim[0] < xc < xlim[1]:
            ax.text(xc / 1000, -95, "frame %d" % j, ha="center", fontsize=7, color="0.35")
    ax.set_xlim(xlim[0] / 1000, xlim[1] / 1000)
    ax.set_ylim(-130, 900)
    ax.set_xlabel("time after the vsync of frame 0 (ms)")
    ax.set_ylabel("ROI mean (ADU)")
    if legend:
        ax.legend(loc="lower center", frameon=False, ncol=2, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(pad=0.3)
    fig.savefig(os.path.join(HERE, fname))


timeline("timeline.pdf", (0, 5 * T), legend=False)
# close-up of the runway, the lead and the trail
pad = 0.05 * (trail - rw)
timeline("timeline_zoom.pdf", (rw - pad, trail + pad), frame3=True, size=(COL1, 2.3),
         legend=False)

# ---- RGB decomposition of the white sweep -------------------------------------------
X = []
for key in ("wwwww_r", "wwwww_g", "wwwww_b"):
    t_, v_ = sweep(key)
    X.append(np.interp(tw, t_, v_ - dark))
X = np.column_stack(X)
y = w - dark
A = np.column_stack([X, np.ones_like(y)])
c = np.linalg.lstsq(A, y, rcond=None)[0]
f = A @ c
res = y - f
r2 = 1 - np.sum(res ** 2) / np.sum((y - y.mean()) ** 2)
print("fit: R %.4f G %.4f B %.4f offset %.3f; rms %.2f ADU, R2 %.5f, max |res| %.1f at %.1f us"
      % (*c, np.sqrt(np.mean(res ** 2)), r2, np.abs(res).max(), tw[np.argmax(np.abs(res))]))
parts = [c[j] * X[:, j] for j in range(3)]
tot = sum(np.clip(p, 0, None).sum() for p in parts)
print("shares of fitted light: " + ", ".join(
    "%s %.1f %%" % (n_, 100 * np.clip(p, 0, None).sum() / tot) for n_, p in zip("RGB", parts)))

m = (tw >= T) & (tw < 2 * T)
fig, (a1, a2) = plt.subplots(2, 1, figsize=(COL2, 2.9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
tt = (tw[m] - T) / 1000
a1.plot(tt, y[m], color="0.75", lw=2.6, label="WWWWW $-$ dark")
for p, n_, cc in zip(parts, ("red", "green", "blue"), (C["r"], C["g"], C["b"])):
    a1.plot(tt, p[m], color=cc, lw=0.8, label="%s filter $\\times$ weight" % n_)
a1.plot(tt, f[m], color="k", ls="--", lw=0.7, label="sum + offset")
a1.set_ylabel("ADU above dark")
a1.legend(frameon=False, ncol=5, loc="upper center", bbox_to_anchor=(0.5, 1.2))
a2.plot(tt, res[m], color="k", lw=0.6)
a2.axhline(0, color="0.6", lw=0.4)
a2.set_ylabel("residual")
a2.set_xlabel("time into frame 1 (ms)")
a2.set_xlim(0, T / 1000)
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(HERE, "rgb_fit.pdf"))

# the close-up of Fig. timeline_zoom for the red, green and blue profiles, on the same
# ROI-mean axis (the dark level added back)
timeline("timeline_zoom_rgb.pdf", (rw - pad, trail + pad), size=(COL1, 2.3), legend=False,
         traces=[(tw, p + dark, cc) for p, cc in zip(parts, (C["r"], C["g"], C["b"]))])

# ---- runway maps --------------------------------------------------------------------
rm = d["runway_maps"]
L = rm["settings"]["levels"]
ix = {lv: i for i, lv in enumerate(L)}
# one single-column figure per part of the runway, laid out in inches at exactly the
# column width so LaTeX does not rescale it and its text matches the other figures. The map
# inside the previous exposure is split at y = YZ into two stacked panels, log color above
# and linear below, because below YZ the reading spans only a few ADU; the panels and the
# gap between them fill the same square box as the single map of the other part.
YZ = 88
LEFT, BOTTOM, TOP, RIGHT = 0.46, 0.36, 0.04, 0.53    # margins, inches
GAP, CPAD, CW = 0.05, 0.07, 0.12                     # panel gap, colorbar pad and width
BOX = COL1 - LEFT - RIGHT                            # side of the square box, inches
FIGH = BOTTOM + BOX + TOP
XT = [0, 64, 128, 192, 255]


def map_panel(fig, data, y0, y1, bottom, height, norm, yticks, cticks, xlabels):
    """One map panel spanning rows y0..y1 at `bottom` (inches), with its colorbar."""
    ax = fig.add_axes([LEFT / COL1, bottom / FIGH, BOX / COL1, height / FIGH])
    im = ax.imshow(data, origin="lower", cmap="magma", aspect="auto", interpolation="nearest",
                   norm=norm, extent=(-0.5, 255.5, y0 - 0.5, y1 - 0.5))
    ax.set_xticks(XT)
    ax.set_yticks(yticks)
    if xlabels:
        ax.set_xlabel("level of the runway's frame")
    else:
        ax.tick_params(axis="x", labelbottom=False, length=0)
    cax = fig.add_axes([(LEFT + BOX + CPAD) / COL1, bottom / FIGH, CW / COL1, height / FIGH])
    cb = fig.colorbar(im, cax=cax, ticks=cticks)
    cb.ax.yaxis.set_major_formatter(FormatStrFormatter("%g"))
    cb.ax.yaxis.set_minor_formatter(NullFormatter())
    return ax


for (s_, e_, name), fname in zip(rm["settings"]["windows"],
                                 ("runway_map_prev.pdf", "runway_map_between.pdf")):
    g = np.full((len(L), len(L)), np.nan)
    for r in rm["rows"]:
        if r[0] == s_:
            g[ix[int(r[2])], ix[int(r[3])]] = r[4]
    v = g[np.isfinite(g)]
    print("%s: %.2f..%.2f us, %d cells, %.1f..%.1f ADU, median %.1f"
          % (name, s_, e_, v.size, v.min(), v.max(), np.median(v)))
    fig = plt.figure(figsize=(COL1, FIGH))
    if fname == "runway_map_prev.pdf":
        hi, lo = g[YZ:], g[:YZ]
        vh, vl = hi[np.isfinite(hi)], lo[np.isfinite(lo)]
        print("%s, y >= %d: %.1f..%.1f ADU; y < %d: %.1f..%.1f ADU"
              % (name, YZ, vh.min(), vh.max(), YZ, vl.min(), vl.max()))
        hz = (BOX - GAP) * YZ / 256
        hh = BOX - GAP - hz
        map_panel(fig, lo, 0, YZ, BOTTOM, hz, plt.Normalize(vl.min(), vl.max()),
                  [0, 32, 64], [24, 26, 28, 30, 32], True)
        ax = map_panel(fig, hi, YZ, 256, BOTTOM + hz + GAP, hh,
                       LogNorm(vmin=vh.min(), vmax=vh.max()), [128, 192, 255],
                       [30, 100, 300, 1000], False)
    else:
        ax = map_panel(fig, g, 0, 256, BOTTOM, BOX, LogNorm(vmin=v.min(), vmax=v.max()),
                       XT, [20, 30, 40, 60], True)
    # the y label sits at the same place in both figures, centered on the whole box
    ax.set_ylabel("level of the frames before and after")
    ax.yaxis.set_label_coords(0.15 / COL1, (BOTTOM + BOX / 2) / FIGH, transform=fig.transFigure)
    fig.savefig(os.path.join(HERE, fname))
print("figures written to", HERE)

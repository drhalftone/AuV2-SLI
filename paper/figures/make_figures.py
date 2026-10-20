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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DOC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "profile_2026-10-16.sliprof")

plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.labelsize": 8,
                     "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
                     "axes.linewidth": 0.6, "lines.linewidth": 0.8, "pdf.fonttype": 42})
COL1, COL2 = 3.5, 7.16                                # IEEE column / page width, inches
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
widths = np.arange(50, 8326, 25.0)
shares = np.array([best_share(tk, k, wd)[0] for wd in widths])
for wd in (2000, 4000, 6000, 6256.83, mx):
    s_, t0 = best_share(tk, k, wd)
    print("best window %.2f us: %.2f %% from %.1f us" % (wd, 100 * s_, t0))

fig, ax = plt.subplots(figsize=(COL1, 1.9))
ax.plot(widths / 1000, 100 * shares, color="k")
ax.axvline(mx / 1000, color=C["trail"], ls="--", lw=0.7)
ax.text(mx / 1000 - 0.1, 40, "sensor limit\n%.2f ms" % (mx / 1000), ha="right", fontsize=7,
        color=C["trail"])
ax.axvline(T / 1000, color="0.5", ls=":", lw=0.7)
ax.set_xlabel("exposure length (ms)")
ax.set_ylabel("light captured (%)")
ax.set_xlim(0, T / 1000 * 1.02)
ax.set_ylim(0, 102)
ax.grid(alpha=0.25, lw=0.4)
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(HERE, "captured_vs_exposure.pdf"))

# ---- the five-frame timeline: WWWWW and WKKKK with the edges ------------------------
fig, ax = plt.subplots(figsize=(COL2, 2.5))
ax.axvspan(rw / 1000, lead / 1000, color=C["rw"], alpha=0.12, lw=0)
ax.axvspan(lead / 1000, trail / 1000, color=C["lead"], alpha=0.08, lw=0)
ax.axvspan(rw / 1000, (trail - T) / 1000, color="none", hatch="////", ec=C["rw"], lw=0,
           alpha=0.5)
ax.plot(tw / 1000, w, color=C["w"], label="WWWWW (every frame white)")
ax.plot(tk / 1000, k, color=C["k"], label="WKKKK (frame 0 white, frames 1--4 black)")
for x_, c_, n_ in ((rw, C["rw"], "runway"), (lead, C["lead"], "lead"),
                   (trail, C["trail"], "trail")):
    ax.axvline(x_ / 1000, color=c_, lw=0.9)
    ax.text(x_ / 1000 + 0.08, 850, n_, color=c_, ha="left", fontsize=7)
for j in range(6):
    ax.axvline(j * T / 1000, color="0.6", lw=0.4, ls=":")
for j in range(5):
    ax.text((j + 0.5) * T / 1000, -95, "frame %d" % j, ha="center", fontsize=7, color="0.35")
ax.set_xlim(0, 5 * T / 1000)
ax.set_ylim(-130, 900)
ax.set_xlabel("time after the vsync of frame 0 (ms)")
ax.set_ylabel("ROI mean (ADU)")
ax.legend(loc="lower center", frameon=False, ncol=2, bbox_to_anchor=(0.5, 1.0))
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(HERE, "timeline.pdf"))

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

# ---- runway maps --------------------------------------------------------------------
rm = d["runway_maps"]
L = rm["settings"]["levels"]
ix = {lv: i for i, lv in enumerate(L)}
fig, axs = plt.subplots(1, 2, figsize=(COL2, 3.0))
for ax, (s_, e_, name) in zip(axs, rm["settings"]["windows"]):
    g = np.full((len(L), len(L)), np.nan)
    for r in rm["rows"]:
        if r[0] == s_:
            g[ix[int(r[2])], ix[int(r[3])]] = r[4]
    v = g[np.isfinite(g)]
    print("%s: %.2f..%.2f us, %d cells, %.1f..%.1f ADU, median %.1f"
          % (name, s_, e_, v.size, v.min(), v.max(), np.median(v)))
    im = ax.imshow(g, origin="lower", cmap="magma", aspect="equal", interpolation="nearest",
                   norm=LogNorm(vmin=max(1, v.min()), vmax=v.max()),
                   extent=(-0.5, 255.5, -0.5, 255.5))
    ax.set_title("%s\n%.0f–%.0f $\\mu$s after frame 0's vsync (%.0f $\\mu$s)"
                 % (name, s_, e_, e_ - s_), fontsize=8)
    ax.set_xlabel("level of the runway's frame")
    ax.set_xticks([0, 64, 128, 192, 255])
    ax.set_yticks([0, 64, 128, 192, 255])
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03, label="ROI mean (ADU)")
axs[0].set_ylabel("level of the frames before and after")
fig.tight_layout(pad=0.3)
fig.savefig(os.path.join(HERE, "runway_maps.pdf"))
print("figures written to", HERE)

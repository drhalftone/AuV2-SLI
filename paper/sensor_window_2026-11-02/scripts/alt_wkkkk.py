"""WKKKK impulse sweep on the CAM_ALT bitstream, then deconvolve the full-frame
measurement by the window measurement to get the shutter's low-pass kernel.

Projector: one white frame (level 255) then four black, a 41.67 ms cycle, set over
COM5 with the merged build's impulse override. Every projected frame fires a
trigger at +delay; the header's roi_phase says which frame of the cycle did, so a
sweep over ONE frame of delay covers the whole 41.67 ms timeline:
    t = roi_phase * 8333.33 us + delay.
Full and window pictures alternate, and 2 and 5 are coprime, so each kind
covers all five phases.

Window = the light during the exposure (read out within ~30 us).
Full   = the same light plus whatever the pixels collect before row 512 is read.
With both on the same circular 41.67 ms timeline,
    F = h (*) W,   H = F W* / (|W|^2 + lambda max|W|^2),   h = IFFT(H)
and g(s) = h(-s) is the shutter efficiency s after the exposure: a spike at 0
(the exposure itself) and the runway tail.

Usage: alt_wkkkk.py OUTPREFIX [--expo-us 1.875] [--m 4] [--lam 1e-3]
"""
import argparse
import csv
import sys
import time

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
import serial

sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\host")
sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\ft_usb_video\host")
from frame_sweep import wr, rd, R_IMPCYC, R_IMPLVL, R_IMPRGB, R_IMPLVL2, R_IMPRGB2, R_ROICTL
import struct
import campack
from alt_sweep import Link, MAGIC, NMAGIC, HDR, ROW_BYTES, R0, C0, WR

FRAME_US = 1e6 / 120.0
NPH = 5
ND = 166                                  # delays per frame -> 830-point timeline
DSTEP = FRAME_US / ND                     # ~50.2 us


def projector(port, white, black):
    s = serial.Serial(port, 115200, timeout=0.05)
    try:
        wr(s, R_IMPCYC, NPH)
        for r, v in ((R_IMPLVL2, white), (R_IMPLVL, black), (R_IMPRGB, 7), (R_IMPRGB2, 7)):
            wr(s, r, v)
        wr(s, R_ROICTL, 0x80)
        return rd(s, R_IMPLVL2), rd(s, R_IMPLVL)
    finally:
        s.close()


def collect_phased(link, m, timeout=6.0):
    """ROI means keyed by (kind, phase) until every one of the 10 has >= m."""
    got = {(k, p): [] for k in (0, 1) for p in range(NPH)}
    clip = 0
    magic = struct.pack("<I", MAGIC)
    t0 = time.time()
    while min(len(v) for v in got.values()) < m and time.time() - t0 < timeout:
        n = link.dev.readPipe(0x82, link.buf, len(link.buf))
        link.acc += bytes(memoryview(link.buf)[:n])
        end = 0
        pos = link.acc.find(magic)
        while pos >= 0 and pos + HDR <= len(link.acc):
            h = struct.unpack_from("<8I", link.acc, pos)
            ncol, nrow, fb = h[2] & 0xFFFF, h[2] >> 16, h[4]
            if (h[7] == NMAGIC and campack.geometry_ok(ncol, nrow)
                    and fb == campack.fbytes_for(ncol, nrow, h[6])):
                if pos + HDR + fb > len(link.acc):
                    break
                p0 = pos + HDR
                kind = (h[3] >> 31) & 1
                valid, phase = h[5] & 1, (h[5] >> 1) & 7
                if valid and phase < NPH and len(got[(kind, phase)]) < m:
                    if kind:
                        w = campack.unpack10_flat(bytes(link.acc[p0:p0 + WR * 32 * 10 // 8]))
                        b = w.reshape(WR, 32)[8:24, 8:24]
                    else:
                        rows = campack.unpack10_flat(
                            bytes(link.acc[p0 + R0 * ROW_BYTES: p0 + (R0 + 16) * ROW_BYTES]))
                        b = rows.reshape(16, 1280)[:, C0:C0 + 16]
                    got[(kind, phase)].append(b.mean())
                    clip += int((b >= 1020).sum())
                end = pos + HDR + fb
                pos = link.acc.find(magic, end)
            else:
                pos = link.acc.find(magic, pos + 4)
        if end:
            del link.acc[:end]
        elif len(link.acc) > (8 << 20):
            del link.acc[:len(link.acc) - (4 << 20)]
    return got, clip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--expo-us", type=float, default=1.875)
    ap.add_argument("--m", type=int, default=4)
    ap.add_argument("--lam", type=float, default=1e-3)
    ap.add_argument("--port", default="COM5")
    a = ap.parse_args()

    link = Link()
    link.cmd(1, max(1, int(a.expo_us / 0.375 + 1e-6)))

    # ---- dark levels: every frame black -----------------------------------
    print("projector all black:", projector(a.port, 0, 0))
    link.cmd(7, (1 << 27) | 0)
    link.drain(0.3)
    g, _ = collect_phased(link, 12)
    print("phase coverage (full, window):",
          [(p, len(g[(0, p)]), len(g[(1, p)])) for p in range(NPH)])
    if any(not g[(k, p)] for k in (0, 1) for p in range(NPH)):
        link.dev.close()
        sys.exit("some (mode, phase) never arrived -- the header's roi_phase is not "
                 "usable on this build; nothing swept")
    dark = [np.mean([x for p in range(NPH) for x in g[(k, p)]]) for k in (0, 1)]
    print("dark: full %.2f  window %.2f" % tuple(dark))

    # ---- WKKKK sweep -------------------------------------------------------
    print("projector W K K K K:", projector(a.port, 255, 0))
    T = np.arange(NPH * ND) * DSTEP
    F = np.full(T.size, np.nan)
    W = np.full(T.size, np.nan)

    plt.ion()
    fig, ax = plt.subplots(figsize=(12, 5))
    lf, = ax.plot([], [], ".", ms=3, label="full frame (green box)")
    lw, = ax.plot([], [], ".", ms=3, label="window (yellow box)")
    ax.set_xlim(0, NPH * FRAME_US / 1000)
    ax.set_xlabel("time in the W K K K K cycle (ms)")
    ax.set_ylabel("ROI mean minus dark (ADU)")
    ax.set_title("W K K K K, exposure %.3f us" % a.expo_us)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right")
    fig.tight_layout()
    plt.pause(0.05)

    t0 = time.time()
    clip_total = 0
    try:
        for i in range(ND):
            d = i * DSTEP
            link.cmd(7, (1 << 27) | int(round(d * 100)))
            link.drain(0.12)
            g, c = collect_phased(link, a.m)
            clip_total += c
            for p in range(NPH):
                j = p * ND + i
                if g[(0, p)]:
                    F[j] = np.mean(g[(0, p)]) - dark[0]
                if g[(1, p)]:
                    W[j] = np.mean(g[(1, p)]) - dark[1]
            ok = ~np.isnan(F)
            lf.set_data(T[ok] / 1000, F[ok])
            ok = ~np.isnan(W)
            lw.set_data(T[ok] / 1000, W[ok])
            ax.relim()
            ax.autoscale_view(scalex=False)
            plt.pause(0.01)
            if i % 20 == 0:
                print("delay %5.0f us  (%.0f s)" % (d, time.time() - t0), flush=True)
    finally:
        link.cmd(7, (1 << 27) | 335000)
        link.dev.close()

    with open(a.out + ".csv", "w", newline="") as fh:
        w_ = csv.writer(fh)
        w_.writerow(["t_us", "full_minus_dark", "window_minus_dark"])
        for t, x, y in zip(T, F, W):
            w_.writerow(["%.2f" % t, "%.3f" % x, "%.3f" % y])
    print("missing points: full %d, window %d;  clipped ROI pixels seen: %d"
          % (np.isnan(F).sum(), np.isnan(W).sum(), clip_total))

    # ---- deconvolution -----------------------------------------------------
    Fi = np.interp(T, T[~np.isnan(F)], F[~np.isnan(F)], period=T[-1] + DSTEP)
    Wi = np.interp(T, T[~np.isnan(W)], W[~np.isnan(W)], period=T[-1] + DSTEP)
    Fh, Wh = np.fft.rfft(Fi), np.fft.rfft(Wi)
    H = Fh * np.conj(Wh) / (np.abs(Wh) ** 2 + a.lam * np.max(np.abs(Wh)) ** 2)
    h = np.fft.irfft(H, n=T.size)
    gk = np.roll(h[::-1], 1)                  # g(s) = h(-s): s = time AFTER the exposure
    s = np.arange(T.size) * DSTEP
    np.save(a.out + "_kernel.npy", np.column_stack([s, gk]))

    pred = np.fft.irfft(np.fft.rfft(Wi) * H, n=T.size)
    r2 = 1 - ((Fi - pred) ** 2).sum() / ((Fi - Fi.mean()) ** 2).sum()
    print("\nkernel (shutter efficiency vs time after the exposure), lambda %.0e:" % a.lam)
    print("  spike g(0) = %.3f   total sum = %.3f   reconstruction R^2 = %.4f"
          % (gk[0], gk.sum(), r2))
    for lo, hi in ((0, 1), (1, 3), (3, 6), (6, 10), (10, 20), (20, 41.7)):
        m = (s >= lo * 1000 + DSTEP / 2) & (s < hi * 1000)
        print("  tail %4.0f-%4.1f ms: sum %.3f  mean per sample %.2e"
              % (lo, hi, gk[m].sum(), gk[m].mean() if m.any() else 0))

    fig2, ax2 = plt.subplots(figsize=(12, 4.5))
    ax2.plot(s / 1000, gk, "-", lw=1)
    ax2.set_xlabel("time after the exposure (ms)")
    ax2.set_ylabel("kernel weight per %.1f us sample" % DSTEP)
    ax2.set_title("full-frame shutter kernel, row 512 (spike = exposure, tail = runway)")
    ax2.grid(alpha=0.3)
    ax2.set_xlim(0, 41.7)
    tail = gk[1:]
    ax2.set_ylim(min(0, np.percentile(tail, 1)) * 1.2, max(np.percentile(tail, 99.5) * 1.5, 1e-3))
    fig2.tight_layout()
    fig2.savefig(a.out + "_kernel.png", dpi=120)
    ax.plot(T / 1000, pred, "-", color="k", lw=0.8, label="window (*) kernel")
    ax.legend(loc="upper right")
    fig.savefig(a.out + ".png", dpi=120)
    print("saved", a.out + ".csv", a.out + ".png", a.out + "_kernel.png",
          "  total %.0f s" % (time.time() - t0))
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()

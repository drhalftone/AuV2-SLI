"""Delay sweep on the CAM_ALT bitstream: full-frame and window ROI means from the SAME
run, alternating picture by picture, with a live plot.

Then tests the runway hypothesis: a full-frame readout reaches the ROI (row 512)
about 2.8 ms after the exposure ends, and the pixels keep collecting a little light
until then; the window is read within ~30 us. If so,

    full(d) - window(d) = k * sum of window(d') for d' in [d, d + T) + c

with T near the ROI's readout delay. The fit scans T and reports the best.

Usage: alt_sweep.py OUTPREFIX [--expo-us 4.875] [--step 50] [--n 10]
Projector level is whatever is set (alt_align / COM5); the viewer must be closed.
"""
import argparse
import csv
import ctypes
import struct
import sys
import time

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\ft_usb_video\host")
import campack
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

MAGIC, HDR = 0x30494C53, 32
NMAGIC = (~MAGIC) & 0xFFFFFFFF
FRAME_US = 1e6 / 120.0
ROW_BYTES = 1280 * 10 // 8
R0, C0 = 512, 640            # full-frame ROI
WR = 24                      # window rows; ROI at window rows 8..23, columns 8..23


class Link:
    def __init__(self):
        self.dev = None
        for _ in range(10):
            self.dev = ftd3xx.create(0, FT_OPEN_BY_INDEX)
            if self.dev is not None:
                break
            time.sleep(0.3)
        if self.dev is None:
            sys.exit("no D3XX device -- close the viewer first")
        for fn in ("abortPipe", "flushPipe"):
            try:
                getattr(self.dev, fn)(0x82)
            except Exception:
                pass
        self.dev.setPipeTimeout(0x82, 1000)
        self.buf = ctypes.create_string_buffer(1 << 21)
        self.acc = bytearray()

    def cmd(self, op, payload):
        self.dev.writePipe(0x02, ctypes.create_string_buffer(
            struct.pack("<I", (op << 28) | (payload & 0x0FFFFFFF))), 4)

    def drain(self, seconds):
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.dev.readPipe(0x82, self.buf, len(self.buf))
        self.acc = bytearray()

    def collect(self, n, timeout=5.0):
        """n ROI means of each kind: (full list, window list, clipped-pixel counts)."""
        full, win, clip = [], [], [0, 0]
        magic = struct.pack("<I", MAGIC)
        t0 = time.time()
        while (len(full) < n or len(win) < n) and time.time() - t0 < timeout:
            k = self.dev.readPipe(0x82, self.buf, len(self.buf))
            self.acc += bytes(memoryview(self.buf)[:k])
            end = 0
            pos = self.acc.find(magic)
            while pos >= 0 and pos + HDR <= len(self.acc):
                h = struct.unpack_from("<8I", self.acc, pos)
                ncol, nrow, fb = h[2] & 0xFFFF, h[2] >> 16, h[4]
                if (h[7] == NMAGIC and campack.geometry_ok(ncol, nrow)
                        and fb == campack.fbytes_for(ncol, nrow, h[6]) and h[6] == campack.FMT_PACK10):
                    if pos + HDR + fb > len(self.acc):
                        break
                    p = pos + HDR
                    if (h[3] >> 31) & 1:
                        if len(win) < n:
                            w = campack.unpack10_flat(bytes(self.acc[p:p + WR * 32 * 10 // 8]))
                            w = w.reshape(WR, 32)[8:24, 8:24]
                            win.append(w.mean())
                            clip[1] += int((w >= 1020).sum())
                    elif len(full) < n:
                        rows = campack.unpack10_flat(
                            bytes(self.acc[p + R0 * ROW_BYTES: p + (R0 + 16) * ROW_BYTES]))
                        b = rows.reshape(16, 1280)[:, C0:C0 + 16]
                        full.append(b.mean())
                        clip[0] += int((b >= 1020).sum())
                    end = pos + HDR + fb
                    pos = self.acc.find(magic, end)
                else:
                    pos = self.acc.find(magic, pos + 4)
            if end:
                del self.acc[:end]
            elif len(self.acc) > (8 << 20):
                del self.acc[:len(self.acc) - (4 << 20)]
        return full, win, clip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--expo-us", type=float, default=4.875)
    ap.add_argument("--step", type=float, default=50.0)
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args()

    delays = np.arange(0.0, FRAME_US, a.step)
    F = np.full(delays.size, np.nan)
    W = np.full(delays.size, np.nan)
    clipped = np.zeros((delays.size, 2), int)

    plt.ion()
    fig, ax = plt.subplots(figsize=(11, 5))
    lf, = ax.plot([], [], ".-", label="full frame (green box)")
    lw, = ax.plot([], [], ".-", label="window (yellow box)")
    ax.set_xlim(0, FRAME_US)
    ax.set_xlabel("genlock delay after vsync (us)")
    ax.set_ylabel("ROI mean (ADU)")
    ax.set_title("CAM_ALT, same run, exposure %.3f us, %g us steps" % (a.expo_us, a.step))
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right")
    fig.tight_layout()
    plt.pause(0.05)

    link = Link()
    link.cmd(1, max(1, int(a.expo_us / 0.375 + 1e-6)))
    t0 = time.time()
    try:
        for i, d in enumerate(delays):
            link.cmd(7, (1 << 27) | int(round(d * 100)))      # us -> 10 ns ticks
            link.drain(0.12)                                    # frames already in flight
            f, w, c = link.collect(a.n)
            F[i] = np.mean(f) if f else np.nan
            W[i] = np.mean(w) if w else np.nan
            clipped[i] = c
            lf.set_data(delays[:i + 1], F[:i + 1])
            lw.set_data(delays[:i + 1], W[:i + 1])
            ax.relim()
            ax.autoscale_view(scalex=False)
            plt.pause(0.01)
            if i % 20 == 0:
                print("%5.0f us  full %7.2f  window %7.2f  (%.0f s)"
                      % (d, F[i], W[i], time.time() - t0), flush=True)
    finally:
        link.cmd(7, (1 << 27) | 335000)
        link.dev.close()

    with open(a.out + ".csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["delay_us", "full", "window", "full_clipped_px", "window_clipped_px"])
        for d, x, y, (c0, c1) in zip(delays, F, W, clipped):
            wr.writerow(["%.1f" % d, "%.3f" % x, "%.3f" % y, c0, c1])
    print("points with clipped pixels: full %d, window %d"
          % ((clipped[:, 0] > 0).sum(), (clipped[:, 1] > 0).sum()))

    # ---- runway fit -------------------------------------------------------
    ok = ~(np.isnan(F) | np.isnan(W)) & (clipped.sum(axis=1) == 0)
    base = np.nanpercentile(W, 5)
    Wl = np.nan_to_num(W - base)                     # light above the dark level
    best = None
    for T in np.arange(a.step, 6000 + a.step, a.step):
        m = int(round(T / a.step))
        # sum of the window light over [d, d+T), circular over the frame
        S = np.array([np.roll(Wl, -i)[:m].sum() for i in range(delays.size)]) * a.step
        A = np.column_stack([S[ok], np.ones(ok.sum())])
        y = (F - W)[ok]
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        r = y - A @ coef
        r2 = 1 - (r ** 2).sum() / ((y - y.mean()) ** 2).sum()
        if best is None or r2 > best[0]:
            best = (r2, T, coef[0], coef[1], S)
    r2, T, k, c, S = best
    print("runway fit on %d unclipped points: T = %.0f us, k = %.3g per us of window "
          "light, c = %.2f ADU, R^2 = %.4f" % (ok.sum(), T, k, c, r2))
    print("  (k x %.3f us = %.4f: stray light per unit time relative to the exposure)"
          % (a.expo_us, k * a.expo_us))
    model = W + k * S + c
    ax.plot(delays, model, "--", color="k", lw=1,
            label="window + runway model, T = %.0f us (R$^2$ %.3f)" % (T, r2))
    ax.legend(loc="upper right")
    fig.savefig(a.out + ".png", dpi=120)
    print("saved", a.out + ".csv", a.out + ".png", "  total %.0f s" % (time.time() - t0))
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()

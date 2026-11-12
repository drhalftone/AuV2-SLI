"""Side-by-side live view for the CAM_ALT bitstream: full frame vs the 32x24 window.

The CAM_ALT=1 merged build alternates the sensor between the full array and the
runtime window around the ROI. Header word 3 bit 31 marks a window picture; its
slot is published short, so only the first rows of 32 pixels are real (the rest
is stale data from an older frame in that DDR slot).

Panels, left to right:
  1. the full frame, with the GREEN box at the ROI (counted rows 512..527,
     columns 640..655 -- the fabric's full-frame ROI and cam_live's green box);
  2. the full frame cut to the window's footprint, counted rows 504..543 and
     columns 632..663, enlarged, same box at rows 8..23 / columns 8..23;
  3. the window picture, its first 40 rows of 32, enlarged, with the YELLOW box
     where the profiling build averages in window mode (rows 8..23 counted from
     frame start, window kernels 1..2 = columns 8..23).
If the window starts with the same number of black rows as a full frame, panels
2 and 3 show the same scene and the boxes cover the same pixels.

Rows are COUNTED FROM FRAME START, black reference rows included, in every
panel -- the same convention as roi_mean.v and the merged capture.

Usage: alt_view.py [--expo-us 4.875] [--delay-ms 3.35] [--level 64] [--port COM5]
       --level sets the projected white level over COM5 (merged build's impulse
       override); --level -1 leaves the projector alone.
"""
import argparse
import ctypes
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\ft_usb_video\host")
sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\host")
import campack
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

IN_PIPE, OUT_PIPE = 0x82, 0x02
MAGIC, HDR = 0x30494C53, 32
NMAGIC = (~MAGIC) & 0xFFFFFFFF
CH = 1 << 22
EXPO_UNIT_US = 0.375
ROI_C0, ROI_R0 = 640, 512                 # full-frame ROI (col8 80, row8 64)
WIN_C0, WIN_R0 = ROI_C0 - 8, ROI_R0 - 8   # the window's sensor origin (col8-1, row8*8-8)
WIN_W, SHOW_ROWS = 32, 40                 # 24 window rows + up to 16 black/stale rows
ZOOM = 10


class Cam:
    def __init__(self):
        self.dev = None
        for _ in range(10):
            self.dev = ftd3xx.create(0, FT_OPEN_BY_INDEX)
            if self.dev is not None:
                break
            time.sleep(0.3)
        if self.dev is None:
            raise RuntimeError("no D3XX device -- is the Ft+ connected?")
        for fn in ("abortPipe", "flushPipe"):
            try:
                getattr(self.dev, fn)(IN_PIPE)
            except Exception:
                pass
        self.dev.setPipeTimeout(IN_PIPE, 1000)
        self.lock = threading.Lock()
        self.buf = ctypes.create_string_buffer(CH)

    def read(self):
        with self.lock:
            try:
                n = self.dev.readPipe(IN_PIPE, self.buf, CH)
            except Exception:
                return b""
            return bytes(memoryview(self.buf)[:n]) if n else b""

    def command(self, op, payload):
        b = ctypes.create_string_buffer(struct.pack("<I", (op << 28) | (payload & 0x0FFFFFFF)))
        with self.lock:
            try:
                self.dev.writePipe(OUT_PIPE, b, 4)
                return True
            except Exception:
                return False

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


class Reader(threading.Thread):
    """Keeps the newest FULL and the newest WINDOW payload, separately."""

    def __init__(self, cam):
        super().__init__(daemon=True)
        self.cam, self.stop = cam, threading.Event()
        self.lock = threading.Lock()
        self.latest = {0: None, 1: None}
        self.count = {0: 0, 1: 0}

    def run(self):
        acc = bytearray()
        magic = struct.pack("<I", MAGIC)
        while not self.stop.is_set():
            chunk = self.cam.read()
            if not chunk:
                continue
            acc += chunk
            got, end = {}, 0
            pos = acc.find(magic)
            while pos >= 0 and pos + HDR <= len(acc):
                h = struct.unpack_from("<8I", acc, pos)
                ncol, nrow, fb = h[2] & 0xFFFF, h[2] >> 16, h[4]
                if (h[7] == NMAGIC and campack.geometry_ok(ncol, nrow)
                        and fb == campack.fbytes_for(ncol, nrow, h[6])):
                    if pos + HDR + fb > len(acc):
                        break                               # still arriving
                    kind = (h[3] >> 31) & 1
                    got[kind] = (h[1], h[6], ncol, nrow, pos, fb)   # copied below
                    end = pos + HDR + fb
                    pos = acc.find(magic, end)
                else:
                    pos = acc.find(magic, pos + 4)
            with self.lock:
                for k, (idx, fmt, ncol, nrow, p, fb) in got.items():
                    self.latest[k] = (idx, fmt, ncol, nrow, bytes(acc[p + HDR: p + HDR + fb]))
                    self.count[k] += 1
            if end:
                del acc[:end]
            elif len(acc) > 3 * CH:
                del acc[:len(acc) - CH]

    def take(self):
        with self.lock:
            return dict(self.latest), dict(self.count)


def to_img8(a, lo, hi):
    return np.clip((a.astype(np.float64) - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)


class App:
    def __init__(self, root, cam, a):
        self.root, self.cam, self.args = root, cam, a
        self.reader = Reader(cam)
        self.reader.start()
        root.title("full frame vs 32x24 window (CAM_ALT)")

        bar = ttk.Frame(root)
        bar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)
        ttk.Label(bar, text="exposure us").pack(side=tk.LEFT)
        self.expo = tk.StringVar(value=str(a.expo_us))
        ttk.Entry(bar, textvariable=self.expo, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(bar, text="delay ms").pack(side=tk.LEFT, padx=(10, 0))
        self.dly = tk.StringVar(value=str(a.delay_ms))
        ttk.Entry(bar, textvariable=self.dly, width=8).pack(side=tk.LEFT, padx=2)
        self.sync = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="sync to projector", variable=self.sync).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="apply", command=self.apply).pack(side=tk.LEFT, padx=4)
        self.flip = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="un-mirror window kernels", variable=self.flip).pack(side=tk.LEFT, padx=10)
        self.span = tk.StringVar(value="auto")
        ttk.Label(bar, text="display range").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Combobox(bar, textvariable=self.span, width=8,
                     values=("auto", "0-1023", "0-200", "0-64")).pack(side=tk.LEFT)

        pan = ttk.Frame(root)
        pan.pack(side=tk.TOP, padx=6)
        self.lbl = []
        for title in ("full frame (green box = ROI)",
                      "full frame, window footprint (rows 504-543)",
                      "WINDOW picture, rows 0-39 (yellow = profiling ROI)"):
            f = ttk.Frame(pan)
            f.pack(side=tk.LEFT, padx=4, anchor=tk.N)
            ttk.Label(f, text=title).pack()
            l = ttk.Label(f)
            l.pack()
            self.lbl.append(l)
        self.stats = ttk.Label(root, text="waiting for frames...", font=("Consolas", 10),
                               justify=tk.LEFT)
        self.stats.pack(side=tk.TOP, anchor=tk.W, padx=8, pady=4)
        self.imgs = [None, None, None]
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.apply()
        self.tick()

    def apply(self):
        try:
            us, ms = float(self.expo.get()), float(self.dly.get())
        except ValueError:
            return
        self.cam.command(1, max(1, min(int(us / EXPO_UNIT_US + 1e-6), 0xFFFF)))
        ticks = max(0, min(int(round(ms * 100000.0)), 0xFFFFFF))
        self.cam.command(7, ((1 if self.sync.get() else 0) << 27) | ticks)

    def rng(self, a):
        s = self.span.get()
        if s != "auto" and "-" in s:
            lo, hi = s.split("-")
            return float(lo), float(hi)
        return float(np.percentile(a, 1)), float(np.percentile(a, 99.5)) + 1

    def tick(self):
        latest, count = self.reader.take()
        full, win = latest[0], latest[1]
        lines = ["frames received: full %d   window %d" % (count[0], count[1])]
        if full is not None:
            idx, fmt, ncol, nrow, raw = full
            a = campack.to_frame(raw, fmt, ncol, nrow)
            lo, hi = self.rng(a)
            # 1. whole frame, half size, green box drawn oversized so it is visible
            im = Image.fromarray(to_img8(a[::2, ::2], lo, hi)).convert("RGB")
            d = ImageDraw.Draw(im)
            x0, y0 = ROI_C0 // 2, ROI_R0 // 2
            d.rectangle([x0 - 3, y0 - 3, x0 + 8 + 3, y0 + 8 + 3], outline=(0, 255, 0), width=2)
            self.show(0, im)
            # 2. the window's footprint in the full frame
            cut = a[WIN_R0:WIN_R0 + SHOW_ROWS, WIN_C0:WIN_C0 + WIN_W]
            self.show(1, self.zoomed(cut, lo, hi, (0, 255, 0)))
            roi = a[ROI_R0:ROI_R0 + 16, ROI_C0:ROI_C0 + 16].astype(np.float64)
            lines.append("FULL   frame %6d   green-box mean %7.2f   max %4d   "
                         "first rows (black?) %s" % (idx, roi.mean(), roi.max(),
                         " ".join("%.0f" % v for v in a[:8, 8:-8].mean(axis=1))))
        if win is not None:
            idx, fmt, ncol, nrow, raw = win
            n = WIN_W * SHOW_ROWS
            w = campack.unpack10_flat(raw[: n * 10 // 8]).reshape(SHOW_ROWS, WIN_W)
            if self.flip.get():
                # The window starts at sensor kernel col8-1 = 79 (odd), but the
                # decoder restarts every line as an even kernel: each kernel arrives
                # mirrored. Flip each 8-pixel group back.
                w = w.reshape(SHOW_ROWS, WIN_W // 8, 8)[:, :, ::-1].reshape(SHOW_ROWS, WIN_W)
            lo, hi = self.rng(w[:24]) if full is None else self.rng(
                campack.to_frame(full[4], full[1], full[2], full[3]))
            self.show(2, self.zoomed(w, lo, hi, (255, 220, 0)))
            box = w[8:24, 8:24].astype(np.float64)
            lines.append("WINDOW frame %6d   yellow-box mean %7.2f   max %4d   "
                         "row means 0-29: %s" % (idx, box.mean(), box.max(),
                         " ".join("%.0f" % v for v in w[:30].mean(axis=1))))
        self.stats.configure(text="\n".join(lines))
        self.root.after(100, self.tick)

    def zoomed(self, a, lo, hi, colour):
        im = Image.fromarray(to_img8(a, lo, hi)).resize(
            (a.shape[1] * ZOOM, a.shape[0] * ZOOM), Image.NEAREST).convert("RGB")
        d = ImageDraw.Draw(im)
        for c in range(0, a.shape[1] + 1, 8):              # kernel boundaries
            d.line([c * ZOOM, 0, c * ZOOM, im.height], fill=(70, 70, 140))
        d.line([0, 24 * ZOOM, im.width, 24 * ZOOM], fill=(140, 70, 70))   # end of 24 rows
        d.rectangle([8 * ZOOM, 8 * ZOOM, 24 * ZOOM - 1, 24 * ZOOM - 1], outline=colour, width=2)
        return im

    def show(self, i, im):
        self.imgs[i] = ImageTk.PhotoImage(im)
        self.lbl[i].configure(image=self.imgs[i])

    def quit(self):
        self.reader.stop.set()
        time.sleep(0.2)
        self.cam.close()
        self.root.destroy()


def set_projector(port, level):
    import serial
    from frame_sweep import wr, R_IMPCYC, R_IMPLVL, R_IMPRGB, R_IMPLVL2, R_IMPRGB2, R_ROICTL
    s = serial.Serial(port, 115200, timeout=0.05)
    try:
        wr(s, R_IMPCYC, 5)
        for r, v in ((R_IMPLVL, level), (R_IMPLVL2, level), (R_IMPRGB, 7), (R_IMPRGB2, 7)):
            wr(s, r, v)
        wr(s, R_ROICTL, 0x80)          # impulse override on: every frame white at `level`
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expo-us", type=float, default=4.875)
    ap.add_argument("--delay-ms", type=float, default=3.35)
    ap.add_argument("--level", type=int, default=64)
    ap.add_argument("--port", default="COM5")
    a = ap.parse_args()
    if a.level >= 0:
        set_projector(a.port, a.level)
    cam = Cam()
    root = tk.Tk()
    App(root, cam, a)
    root.mainloop()


if __name__ == "__main__":
    main()

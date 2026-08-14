"""Live viewer for the PYTHON1300 over the Ft+.

Reader thread pulls the pipe flat out and keeps only the NEWEST complete frame;
the GUI paints whatever is current at ~30 Hz. That split is deliberate: the
camera runs at 120 Hz and the link carries ~325 MB/s, but no Python GUI can paint
1280x1024 that fast. Trying to display every frame would build an ever-growing
backlog and show older and older pictures -- the opposite of live. Dropping
frames on the display side is the correct behaviour, and the counter reports how
many were dropped so it is visible rather than hidden.

Pixels are 10-bit in 16-bit little-endian (header format 2). Conversion is numpy;
doing it in pure Python is ~1 s per frame and would cap the display near 1 fps.

Controls talk to the FPGA over the SAME Ft+ handle the reader uses -- opening a
second D3XX handle while the reader holds the device does not work.
"""
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import ctypes
import numpy as np
from PIL import Image, ImageTk

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

IN_PIPE, OUT_PIPE = 0x82, 0x02
MAGIC = 0x30494C53
NCOL, NROW = 1280, 1024
NPIX = NCOL * NROW
FBYTES = NPIX * 2
HDR = 32
CH = 1 << 22
EXPO_UNIT_US = 0.375
DISP_W, DISP_H = 800, 640


class Cam:
    """Owns the device. All pipe access goes through here under one lock."""

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
        try:
            self.dev.setPipeTimeout(IN_PIPE, 1000)
        except Exception:
            pass
        try:
            self.dev.setStreamPipe(IN_PIPE, CH)
            self.streamed = True
        except Exception:
            self.streamed = False
        self.lock = threading.Lock()
        self.buf = ctypes.create_string_buffer(CH)

    def read(self):
        with self.lock:
            try:
                n = self.dev.readPipe(IN_PIPE, self.buf, CH)
            except Exception:
                return b""
            return self.buf.raw[:n] if n else b""

    def command(self, op, payload):
        word = (op << 28) | (payload & 0x0FFFFFFF)
        b = ctypes.create_string_buffer(struct.pack("<I", word))
        with self.lock:
            try:
                self.dev.writePipe(OUT_PIPE, b, 4)
                return True
            except Exception:
                return False

    def close(self):
        with self.lock:
            try:
                if self.streamed:
                    self.dev.clearStreamPipe(IN_PIPE)
            except Exception:
                pass
            try:
                self.dev.close()
            except Exception:
                pass


class Reader(threading.Thread):
    """Consume the pipe continuously; publish only the newest complete frame."""

    def __init__(self, cam):
        super().__init__(daemon=True)
        self.cam = cam
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.latest = None            # (slot, frame_idx, bytes)
        self.bytes_total = 0
        self.frames_seen = 0
        self.frames_shown = 0
        self.t0 = time.time()

    def run(self):
        acc = bytearray()
        magic = struct.pack("<I", MAGIC)
        while not self.stop.is_set():
            chunk = self.cam.read()
            if not chunk:
                continue
            self.bytes_total += len(chunk)
            acc += chunk

            # Keep only the LAST complete frame in the buffer. Scanning from the
            # end means a slow GUI cannot make us fall behind the camera.
            last = None
            pos = acc.rfind(magic)
            while pos >= 0:
                if pos + HDR + FBYTES <= len(acc):
                    h = struct.unpack_from("<8I", acc, pos)
                    if h[7] == (~MAGIC & 0xFFFFFFFF) and h[4] == FBYTES:
                        last = (h[3] & 0x3F, h[1],
                                bytes(acc[pos + HDR: pos + HDR + FBYTES]))
                        break
                pos = acc.rfind(magic, 0, pos)

            if last is not None:
                with self.lock:
                    self.latest = last
                    self.frames_seen += 1
                acc = bytearray()          # drop everything consumed or stale
            elif len(acc) > 3 * (FBYTES + HDR):
                del acc[:len(acc) - (FBYTES + HDR)]   # bound the buffer

    def take(self):
        with self.lock:
            f = self.latest
            self.latest = None
            return f


class App:
    def __init__(self, root, cam):
        self.cam = cam
        self.root = root
        self.reader = Reader(cam)
        self.reader.start()
        self.last_stat = time.time()
        self.last_bytes = 0
        self.last_frames = 0
        self.shown = 0
        self.auto = tk.BooleanVar(value=True)

        root.title("PYTHON1300 live -- Ft+")
        self.canvas = tk.Label(root, bg="black")
        self.canvas.pack(side=tk.TOP, padx=4, pady=4)

        bar = ttk.Frame(root)
        bar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=2)

        ttk.Checkbutton(bar, text="auto contrast", variable=self.auto).pack(side=tk.LEFT)
        ttk.Button(bar, text="Grab scan", command=self.grab).pack(side=tk.LEFT, padx=6)

        ttk.Label(bar, text="exposure us").pack(side=tk.LEFT, padx=(12, 2))
        self.expo = tk.IntVar(value=600)
        ttk.Scale(bar, from_=40, to=4000, variable=self.expo, length=180,
                  command=lambda _e: None).pack(side=tk.LEFT)
        ttk.Button(bar, text="set", command=self.set_expo).pack(side=tk.LEFT, padx=4)

        self.stats = ttk.Label(root, text="starting...", font=("Consolas", 9))
        self.stats.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(0, 4))

        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.tick()

    def set_expo(self):
        units = int(round(self.expo.get() / EXPO_UNIT_US))
        self.cam.command(1, max(1, min(units, 0xFFFF)))

    def grab(self):
        self.cam.command(3, 0)

    def tick(self):
        f = self.reader.take()
        if f is not None:
            slot, idx, raw = f
            a = np.frombuffer(raw, dtype="<u2").reshape(NROW, NCOL)
            if self.auto.get():
                lo = int(a.min())
                hi = int(a.max())
                span = max(hi - lo, 1)
                img = ((a.astype(np.int32) - lo) * 255 // span).astype(np.uint8)
            else:
                img = (a >> 2).astype(np.uint8)      # 10-bit -> 8-bit, no stretch
            im = Image.fromarray(img).resize((DISP_W, DISP_H))
            self.photo = ImageTk.PhotoImage(im)
            self.canvas.configure(image=self.photo)
            self.shown += 1
            self.slot, self.idx = slot, idx
            self.lo, self.hi = int(a.min()), int(a.max())

        now = time.time()
        if now - self.last_stat >= 0.5:
            dt = now - self.last_stat
            mbs = (self.reader.bytes_total - self.last_bytes) / dt / 1e6
            fps_in = (self.reader.frames_seen - self.last_frames) / dt
            fps_disp = self.shown / dt
            dropped = max(0, (self.reader.frames_seen - self.last_frames) - self.shown)
            self.last_stat, self.last_bytes = now, self.reader.bytes_total
            self.last_frames = self.reader.frames_seen
            self.shown = 0
            self.stats.configure(
                text=("link %6.1f MB/s   frames in %5.1f/s   displayed %4.1f/s   "
                      "dropped %3d/s   slot %s  idx %s  range %s..%s"
                      % (mbs, fps_in, fps_disp, dropped,
                         getattr(self, "slot", "-"), getattr(self, "idx", "-"),
                         getattr(self, "lo", "-"), getattr(self, "hi", "-"))))
        self.root.after(16, self.tick)          # ~60 Hz GUI poll

    def quit(self):
        self.reader.stop.set()
        self.reader.join(timeout=1.0)
        self.cam.close()
        self.root.destroy()


if __name__ == "__main__":
    try:
        cam = Cam()
    except RuntimeError as e:
        sys.exit(str(e))
    root = tk.Tk()
    App(root, cam)
    root.mainloop()

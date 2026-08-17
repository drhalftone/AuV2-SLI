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
import os
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

import ctypes
import numpy as np
from PIL import Image, ImageTk

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

# Pillow moved the resampling constants to an enum in 9.1 and removed the old
# aliases in 10. Resolve once rather than let the viewer die on the first frame.
RESAMPLE = getattr(getattr(Image, "Resampling", Image), "BILINEAR")

IN_PIPE, OUT_PIPE = 0x82, 0x02
MAGIC = 0x30494C53
NCOL, NROW = 1280, 1024
NPIX = NCOL * NROW
# Payload is DENSE PACKED 10-BIT now (4 px in 5 bytes), not 10-bit in u16, so a
# frame is 1.64 MB rather than 2.62 MB. That 37.5% was pure zero padding, and
# removing it is what puts 120 Hz inside the link's budget.
FBYTES = campack.FBYTES_PACK10
HDR = 32
CH = 1 << 22
EXPO_UNIT_US = 0.375
DISP_W, DISP_H = 800, 640        # initial preview size; the image now follows the window

# EXPOSURE MUST STOP SHORT OF THE FRAME PERIOD, AND THE MARGIN IS MEASURED.
#
# Capping at the period itself (8333 us at 120 Hz) was wrong: the sensor cannot
# integrate for a whole period AND still answer the next trigger, so it skips
# every other one and the delivered rate HALVES. Swept against the real
# delivered rate -- not the status UART, which keeps reporting a healthy 120 Hz
# because the FPGA goes on triggering regardless:
#
#     5000..8280 us   113-121 fps   full rate, ldrop 0
#          8300 us       0.0 fps    collapses
#          8333 us      59.8 fps    every other trigger missed
#
# The limit is the LAST MEASURED-GOOD VALUE, 8280 us. That is deliberate but
# tight: the collapse at 8300 us is only 20 us away, and the edge behaved
# differently between runs (8333 us gave 59.8 fps once and 0 another time). If
# full-rate capture ever becomes intermittent near the top of the slider, this
# margin is the first thing to suspect -- back it off toward 8000 us, which
# still buys 96% of the light a full period could give.
FRAME_HZ = 120.0
PERIOD_US = 1e6 / FRAME_HZ
EXPO_MAX_US = 8280               # us, measured at 120 Hz; 8300 collapses
SAT_LEVEL = 1020                 # 10-bit full scale is 1023
CAPTURE_DIR = "captures"         # created on demand, next to the script


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
            # memoryview slice, NOT .raw[:n] -- .raw materialises the whole 4 MiB
            # buffer as a bytes object before the slice throws most of it away,
            # on every single read.
            return bytes(memoryview(self.buf)[:n]) if n else b""

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
        self.latest = None            # (slot, frame_idx, bytes, fmt, ldrop)
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
                        # h[6] is the payload format; carry it rather than assume
                        # one, because reading packed bytes as u16 does not fail
                        # loudly -- it just paints a plausible wrong picture.
                        # h[3] bits 29:14 carry ldrop -- frames the writer had to
                        # pad because kernels went missing. A RISING ldrop is the
                        # signal that the camera->DDR FIFO is overflowing, which
                        # is invisible in the picture until it is severe.
                        last = (h[3] & 0x3F, h[1],
                                bytes(acc[pos + HDR: pos + HDR + FBYTES]), h[6],
                                (h[3] >> 14) & 0xFFFF)
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
        self.slo = None
        self.shi = None

        self.dw, self.dh = DISP_W, DISP_H     # current preview area, tracked live
        self.save_dir = os.path.abspath(CAPTURE_DIR)   # remembered between saves
        self.ldrop0 = None                    # ldrop at start, to report GROWTH
        self.ldrop = 0
        self.sat = 0.0

        root.title("PYTHON1300 live -- Ft+")
        # Open at a defined size instead of letting the first frame decide it.
        root.geometry("%dx%d" % (DISP_W + 20, DISP_H + 90))
        # Below this the controls would start being clipped.
        root.minsize(640, 480)

        # PACK ORDER IS THE WHOLE TRICK FOR "CONTROLS ALWAYS VISIBLE".
        # Tk hands out space in packing order, so the status line and the button
        # bar are packed FIRST, against the bottom edge. They therefore always
        # get their height, and the image takes whatever is left. Packing the
        # image first -- the obvious order -- lets a large frame push the
        # controls off the bottom of the window, which is what used to happen.
        self.stats = ttk.Label(root, text="starting...", font=("Consolas", 9),
                               anchor="w")
        self.stats.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 4))

        bar = ttk.Frame(root)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=2)

        # THE IMAGE MUST NOT BE ABLE TO RESIZE ITS OWN CONTAINER.
        #
        # A Label asks its parent for whatever its image needs, so measuring the
        # Label and then scaling the image to that measurement is a feedback
        # loop: each frame made the Label a little larger, which made the next
        # frame larger again, and the window visibly inflated after opening.
        #
        # The image therefore lives inside a Frame with geometry propagation
        # DISABLED. The Frame's size comes from the window and nothing else, and
        # it is the Frame we measure. The Label can now only ever display.
        self.view = tk.Frame(root, bg="black")
        self.view.pack(side=tk.TOP, expand=True, fill=tk.BOTH, padx=4, pady=4)
        self.view.pack_propagate(False)
        self.canvas = tk.Label(self.view, bg="black")
        self.canvas.pack(expand=True)
        self.view.bind("<Configure>", self.on_resize)

        ttk.Checkbutton(bar, text="auto contrast", variable=self.auto).pack(side=tk.LEFT)
        ttk.Button(bar, text="Save TIFF", command=self.save_tiff).pack(side=tk.LEFT, padx=6)
        self.save_lbl = ttk.Label(bar, text="", width=30)
        self.save_lbl.pack(side=tk.LEFT)

        ttk.Label(bar, text="exposure us").pack(side=tk.LEFT, padx=(12, 2))
        self.expo = tk.IntVar(value=600)
        sc = ttk.Scale(bar, from_=40, to=EXPO_MAX_US, variable=self.expo, length=200,
                       command=self.on_expo_move)
        sc.pack(side=tk.LEFT)
        # Apply on RELEASE, not on every pixel of drag: each change is a USB
        # command and the sensor needs a frame to act on it, so streaming
        # hundreds of them while dragging just floods the control channel.
        sc.bind("<ButtonRelease-1>", lambda _e: self.set_expo())
        self.expo_lbl = ttk.Label(bar, text="", width=22)
        self.expo_lbl.pack(side=tk.LEFT, padx=4)
        # No "set" button: the exposure is sent when the slider is RELEASED, so a
        # button could only re-send a value that is already in effect.

        self.on_expo_move(None)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.tick()

    def on_resize(self, ev):
        self.dw, self.dh = max(ev.width, 1), max(ev.height, 1)

    def map_via_lut(self, a, lo, gain):
        """10-bit -> 8-bit through a LOOKUP TABLE rather than pixel arithmetic.

        The direct form -- clip((a - lo) * gain) via float32 -- converts and
        scales 1.31 M pixels every frame, 5.1 ms of the budget. The mapping only
        has 1024 distinct inputs, so it is built once into a table and applied as
        a gather. Table build is a few microseconds on 1024 entries; the gather
        is integer and touches each pixel once.

        The table is sized for the full uint16 range, not 1024, so a stray
        out-of-range value clamps instead of raising IndexError mid-frame.
        """
        xs = np.arange(1024, dtype=np.float32)
        lut = np.empty(65536, dtype=np.uint8)
        lut[:1024] = np.clip((xs - lo) * gain, 0, 255).astype(np.uint8)
        lut[1024:] = 255
        return lut[a]

    def on_expo_move(self, _ev):
        """Live feedback while dragging -- no command sent until release."""
        self.expo_lbl.configure(
            text="%d / %d us" % (int(self.expo.get()), EXPO_MAX_US))

    def set_expo(self):
        us = min(int(self.expo.get()), EXPO_MAX_US)
        units = int(round(us / EXPO_UNIT_US))
        self.cam.command(1, max(1, min(units, 0xFFFF)))

    def save_tiff(self):
        """Write the frame currently on screen to a 16-bit TIFF.

        THE SENSOR VALUES ARE SAVED, NOT THE PICTURE. The display applies an
        auto-contrast stretch to make the scene visible; writing that would bake
        a viewing decision into the measurement and throw away 2 bits. What goes
        to disk is the raw 10-bit pixel, 0..1023.

        TIFF has no usable 10-bit grey mode -- the tag permits BitsPerSample=10
        but almost nothing reads it -- so the container is 16-bit, which holds
        all ten bits exactly and opens anywhere. Values are NOT rescaled to
        16-bit full scale: 1023 stays 1023, so counts remain sensor counts.
        """
        # Grab the frame BEFORE opening the dialog. The dialog is modal, so tick()
        # stops and the picture freezes -- what gets written is exactly the frame
        # that was on screen when the button was pressed, not whatever happens to
        # be newest when the user finishes choosing a name.
        a = getattr(self, "last_frame", None)
        idx, slot = getattr(self, "idx", 0), getattr(self, "slot", "-")
        if a is None:
            self.save_lbl.configure(text="no frame yet")
            return
        name = "cam_%s_idx%s.tif" % (time.strftime("%Y%m%d_%H%M%S"), idx)
        init_dir = (self.save_dir if os.path.isdir(self.save_dir)
                    else os.path.dirname(os.path.abspath(__file__)))
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save frame as 16-bit TIFF",
            initialdir=init_dir,
            initialfile=name,
            defaultextension=".tif",
            filetypes=[("TIFF, 16-bit", "*.tif *.tiff"), ("All files", "*.*")])
        if not path:
            self.save_lbl.configure(text="cancelled")
            return
        # Reopen where they last saved, rather than sending them back to the
        # default every time.
        self.save_dir = os.path.dirname(path) or init_dir
        name = os.path.basename(path)
        desc = ("PYTHON1300 %dx%d 10-bit in 16-bit TIFF; values 0..1023 unscaled; "
                "exposure %d us; frame_idx %s; slot %s"
                % (NCOL, NROW, int(self.expo.get()), idx, slot))
        try:
            # 270 = ImageDescription, so the capture carries its own settings.
            Image.fromarray(a).save(path, format="TIFF", tiffinfo={270: desc})
        except Exception as e:
            self.save_lbl.configure(text="SAVE FAILED: %s" % e)
            return
        self.save_lbl.configure(text="saved %s" % name)

    def tick(self):
        f = self.reader.take()
        if f is not None:
            slot, idx, raw, fmt, ldrop = f
            a = campack.to_frame(raw, fmt)
            # ldrop is free-running since power-up, so the useful quantity is its
            # GROWTH over this session, not its absolute value.
            if self.ldrop0 is None:
                self.ldrop0 = ldrop
            self.ldrop = ldrop - self.ldrop0
            self.sat = 100.0 * float((a >= SAT_LEVEL).mean())
            # STATISTICS ON A SUBSAMPLE, NOT THE WHOLE FRAME. np.percentile sorts
            # every one of 1.31 M pixels to find two numbers -- 8.6 ms each, and
            # it was called twice per frame, over half the entire frame budget.
            # Every 4th pixel in each axis is 82k samples, far more than enough
            # to place a 1st/99th percentile, and 16x cheaper. It also frees the
            # GIL sooner, which is what was starving the reader thread.
            sub = a[::4, ::4]
            self.sat = 100.0 * float((sub >= SAT_LEVEL).mean())
            if self.auto.get():
                # SLOW-ADAPTING scale, not per-frame. Normalising each frame to
                # its own min/max remaps the whole image whenever those extremes
                # move -- one hot pixel, sensor noise or ambient flicker is
                # enough -- and the result looks exactly like the display
                # flickering. The mapping now eases toward the measured range so
                # it is stable frame to frame, and percentiles are used instead
                # of min/max so a single outlying pixel cannot drive it.
                lo, hi = np.percentile(sub, (1, 99))
                if self.slo is None:
                    self.slo, self.shi = float(lo), float(hi)
                else:
                    k = 0.05                      # ~20-frame time constant
                    self.slo += k * (float(lo) - self.slo)
                    self.shi += k * (float(hi) - self.shi)
                span = max(self.shi - self.slo, 1.0)
                img = self.map_via_lut(a, self.slo, 255.0 / span)
            else:
                img = self.map_via_lut(a, 0.0, 0.25)  # 10-bit -> 8-bit, no stretch
            # Fit the preview to whatever the window currently gives us, KEEPING
            # THE ASPECT RATIO. Stretching to the raw widget size would distort a
            # 5:4 sensor into whatever shape the window happens to be, and on a
            # metrology camera a silently non-square pixel is worse than a small
            # image.
            scale = min(self.dw / float(NCOL), self.dh / float(NROW))
            tw = max(1, int(NCOL * scale))
            th = max(1, int(NROW * scale))
            im = Image.fromarray(img).resize((tw, th), RESAMPLE)
            self.photo = ImageTk.PhotoImage(im)
            self.canvas.configure(image=self.photo)
            self.shown += 1
            self.slot, self.idx = slot, idx
            self.lo, self.hi = int(sub.min()), int(sub.max())
            # Keep the RAW frame, not the contrast-mapped one, so Save TIFF
            # writes sensor counts rather than a viewing decision. This is a
            # reference to the array we already have -- no copy.
            self.last_frame = a

        now = time.time()
        if now - self.last_stat >= 0.5:
            dt = now - self.last_stat
            nbytes = self.reader.bytes_total - self.last_bytes
            mbs = nbytes / dt / 1e6
            # FRAMES ON THE WIRE, DERIVED FROM BYTES -- not from how many the
            # reader bothered to extract.
            #
            # The reader keeps only the NEWEST complete frame and throws the rest
            # away, so its extraction count says how often the GUI asked for a
            # picture, not how fast the camera is running. Reading that as "the
            # frame rate" makes a perfectly healthy 120 Hz camera look like 47.
            # The stream is contiguous fixed-size frames, so bytes/frame-size is
            # the true delivered rate and costs nothing to compute.
            fps_wire = nbytes / float(FBYTES + HDR) / dt
            fps_got = (self.reader.frames_seen - self.last_frames) / dt
            fps_disp = self.shown / dt
            dropped = max(0.0, fps_wire - fps_disp)
            self.last_stat, self.last_bytes = now, self.reader.bytes_total
            self.last_frames = self.reader.frames_seen
            self.shown = 0
            # ldrop and sat are the two numbers that turn "looks fine" into
            # evidence. ldrop rising means kernels are being LOST in the camera
            # FIFO -- the picture stays plausible while it happens. sat rising
            # means the exposure is clipping, which also looks like a bright,
            # stable, perfectly healthy image.
            self.stats.configure(
                text=("link %6.1f MB/s  camera %5.1f/s  shown %4.1f/s  skipped %5.1f/s   "
                      "ldrop %s%d   sat %5.2f%%   slot %s  idx %s  range %s..%s"
                      % (mbs, fps_wire, fps_disp, dropped,
                         "+" if self.ldrop else " ", self.ldrop, self.sat,
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

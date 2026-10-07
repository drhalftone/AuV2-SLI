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
from PIL import Image, ImageDraw, ImageTk

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

# Pillow moved the resampling constants to an enum in 9.1 and removed the old
# aliases in 10. Resolve once rather than let the viewer die on the first frame.
RESAMPLE = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
# Zoomed far enough that one image pixel covers several screen pixels, bilinear
# smears exactly the pixel-to-pixel structure you zoomed in to look at.
NEAREST = getattr(getattr(Image, "Resampling", Image), "NEAREST")

# ---- zoom / pan -------------------------------------------------------------
ZOOM_STEP = 1.25          # per wheel notch
ZOOM_MAX = 64.0           # x the fit-to-window scale
FLING_TAU = 0.35          # s, momentum decay time constant after a throw
FLING_MIN = 20.0          # screen px/s, below this the image stops
FLING_WINDOW = 0.08       # s of drag history the release velocity is taken from

IN_PIPE, OUT_PIPE = 0x82, 0x02
MAGIC = 0x30494C53
# Control replies share the IN pipe with video: same 32-byte header shape,
# RMAGIC ("SLI1"), format 4, w2 = true byte count, w4 = padded count to skip.
# See host/ftlink.py for the full protocol notes.
RMAGIC = 0x31494C53
RPL_FMT = 4
SYNC, OP_R = 0xA5, 0x52
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
# 8280 us WAS THE LAST MEASURED-GOOD VALUE, AND IT WEDGED THE SENSOR on
# 2026-10-07: dragged to the top of the slider, the part stopped integrating with
# its last exposure at 8.29 ms and only a reconfiguration brought it back. The
# FPGA's limit (MAXEXP, reg 0x53) said 8279 us, and that wedged it too: the gap
# behind it predated the CDS timing program. Re-measured: the sensor needs ~78 us
# after an exposure, so MAXEXP is now period - 90 us = 8243 us at 120 Hz.
#
# THE SLIDER NOW TAKES ITS TOP FROM THE FPGA. The viewer reads 0x53..0x55 over the
# Ft+ control tunnel once a second (see poll_regs), so the ceiling is the board's
# figure for the rate it is actually running at, genlocked or not. The FPGA also
# CLAMPS every exposure command to that figure (cam_expo_safety.v), so even a
# wrong number here cannot wedge it any more. EXPO_MAX_US is only the fallback
# until the first reply arrives -- deliberately below the edge, not at it.
FRAME_HZ = 120.0
PERIOD_US = 1e6 / FRAME_HZ
EXPO_MAX_US = 8200               # us, fallback only; the FPGA's MAXEXP replaces it
DLY_MAX_MS  = 50.0               # G3 asks for >= 0-50 ms; the register holds 167 ms
# The exposure ceiling is FRAME PERIOD MINUS A RESERVE, so it moves with the rate.
# Genlocked to a slower display the budget GROWS -- 13.3 ms at 75 Hz, 16.6 ms at
# 60 Hz -- and capping at the free-running 8280 us would throw that light away.
# 90 us is the FPGA's own figure: GAP_TICKS (80 us, measured 2026-10-07) + MARGIN_TICKS.
EXPO_RESERVE_US = 90.0
EXPO_REG_MAX_US = 65535 * 0.375  # 24576 us -- the 16-bit exposure register itself
SAT_LEVEL = 1020                 # 10-bit full scale is 1023
CAPTURE_DIR = "captures"         # created on demand, next to the script

# ROI PLACEMENT THE HOST WILL AVERAGE OVER. Defaults to the fabric's own reset
# values; override when the ROI has been moved with UART regs 0x18/0x19, or the
# host would be averaging different pixels from the FPGA and reporting the
# difference as an error.
ROI_COL8 = campack.ROI_COL8_DEFAULT
ROI_ROW8 = campack.ROI_ROW8_DEFAULT
for _i, _a in enumerate(sys.argv):
    if _a == "--roi-col8" and _i + 1 < len(sys.argv):
        ROI_COL8 = int(sys.argv[_i + 1])
    elif _a == "--roi-row8" and _i + 1 < len(sys.argv):
        ROI_ROW8 = int(sys.argv[_i + 1])

# COLOUR SENSOR (NOIP1SE1300A). The pixels arrive as a raw Bayer mosaic; the
# viewer demosaics for DISPLAY only -- Save TIFF still writes the raw mosaic.
# --color starts in colour mode; --bayer picks the 2x2 phase (which colour sits
# at row 0, col 0 depends on where readout starts, so it is a setting, not a
# constant -- if skin looks blue, try another phase).
BAYER_PATTERNS = ("RGGB", "GRBG", "GBRG", "BGGR")
COLOR = "--color" in sys.argv
BAYER = "GRBG"
for _i, _a in enumerate(sys.argv):
    if _a == "--bayer" and _i + 1 < len(sys.argv):
        BAYER = sys.argv[_i + 1].upper()
        COLOR = True
if BAYER not in BAYER_PATTERNS:
    sys.exit("--bayer must be one of %s" % ", ".join(BAYER_PATTERNS))


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
        # NOT on FTDI's WinUSB D3XX library (FTD3XXWU.dll): setStreamPipe is
        # accepted there, and then every readPipe returns 0 bytes -- measured,
        # 80k empty reads in 3 s and no picture. Plain reads stream normally.
        winusb = "FTD3XXWU" in getattr(getattr(ftd3xx, "_ftd3xx_win32", None),
                                       "_libname", "").upper()
        self.streamed = False
        if not winusb:
            try:
                self.dev.setStreamPipe(IN_PIPE, CH)
                self.streamed = True
            except Exception:
                pass
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

    def send_bytes(self, payload):
        """0xA5 control-protocol bytes, tunnelled as opcode-0 words.

        Three bytes per word with an explicit count: {4'd0, count, b2, b1, b0}.
        They cannot go down raw: a protocol byte landing in the top nibble would
        fire a camera opcode -- 0x1? is opcode 1 and rewrites the exposure.
        """
        ok = True
        for i in range(0, len(payload), 3):
            chunk = payload[i:i + 3]
            w = len(chunk) << 24
            for j, b in enumerate(chunk):
                w |= b << (8 * j)
            ok = self.command(0, w) and ok
        return ok

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
        self.latest = None            # (slot, frame_idx, bytes, fmt, ldrop, roiw)
        self.rx = bytearray()         # control-reply byte stream (see take_rx)
        self.bytes_total = 0
        self.frames_seen = 0
        self.frames_shown = 0
        self.t0 = time.time()

    def run(self):
        acc = bytearray()
        magic = struct.pack("<I", MAGIC)
        rmagic = struct.pack("<I", RMAGIC)
        while not self.stop.is_set():
            chunk = self.cam.read()
            if not chunk:
                continue
            self.bytes_total += len(chunk)
            acc += chunk

            # CONTROL REPLIES FIRST, AND CUT OUT OF THE STREAM. They leave only at
            # frame boundaries, so one never sits inside a frame's payload; taking
            # them out here means the frame search below never has to know they
            # exist. A reply is a transport chunk, not a message -- the bytes are
            # appended to a stream and the 0xA5 framing is parsed on the GUI side.
            rpos = acc.find(rmagic)
            while rpos >= 0 and rpos + HDR <= len(acc):
                h = struct.unpack_from("<8I", acc, rpos)
                if (h[7] == (~RMAGIC & 0xFFFFFFFF) and h[6] == RPL_FMT
                        and h[4] <= 4096):
                    end = rpos + HDR + h[4]
                    if end > len(acc):
                        break                    # rest of it is still in flight
                    with self.lock:
                        self.rx += acc[rpos + HDR: rpos + HDR + min(h[2], h[4])]
                    del acc[rpos:end]
                    rpos = acc.find(rmagic, rpos)
                else:
                    rpos = acc.find(rmagic, rpos + 4)

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
                        # h[5] low 24 bits carry the fabric's ROI measurement for
                        # THESE pixels (see campack.parse_header). Carried raw and
                        # decoded in the GUI so the reader thread stays a pure
                        # byte pump.
                        last = (h[3] & 0x3F, h[1],
                                bytes(acc[pos + HDR: pos + HDR + FBYTES]), h[6],
                                (h[3] >> 14) & 0xFFFF, h[5])
                        break
                pos = acc.rfind(magic, 0, pos)

            if last is not None:
                with self.lock:
                    self.latest = last
                    self.frames_seen += 1
                # Drop everything up to the end of that frame, but KEEP what
                # follows: a control reply leaves right after a frame, and
                # clearing the whole buffer here threw away any that had only
                # partly arrived.
                del acc[:pos + HDR + FBYTES]
            elif len(acc) > 3 * (FBYTES + HDR):
                del acc[:len(acc) - (FBYTES + HDR)]   # bound the buffer

    def take(self):
        with self.lock:
            f = self.latest
            self.latest = None
            return f

    def take_rx(self):
        with self.lock:
            b = bytes(self.rx)
            self.rx.clear()
            return b


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
        # OFF BY DEFAULT. Auto contrast rescales the display to the frame's own 1st
        # and 99th percentiles, which is what you want for looking at a scene and
        # exactly wrong for judging light output: it hides both the absolute level
        # and any change in it, because a dimming field is renormalised straight back
        # to full scale. With it off the mapping is a fixed 10-bit -> 8-bit divide, so
        # what is on screen is proportional to sensor counts and two frames can be
        # compared by eye. Tick the box when the picture matters more than the level.
        self.auto = tk.BooleanVar(value=False)
        self.slo = None
        self.shi = None
        self.color = tk.BooleanVar(value=COLOR)
        self.bayer = tk.StringVar(value=BAYER)
        self.awb = tk.BooleanVar(value=True)
        self.wb = None                        # eased (r, g, b) gains

        self.dw, self.dh = DISP_W, DISP_H     # current preview area, tracked live
        # View: zoom relative to fit-to-window, centred on sensor pixel (cx, cy).
        self.zoom = 1.0
        self.cx, self.cy = NCOL / 2.0, NROW / 2.0
        self.vel = (0.0, 0.0)                 # fling velocity, screen px/s
        self.drag = None                      # last (x_root, y_root) while dragging
        self.drag_hist = []                   # recent (t, x_root, y_root)
        self.last_anim = time.time()
        self.view_dirty = False               # repaint the last frame without a new one
        self.last_img = None
        self.save_dir = os.path.abspath(CAPTURE_DIR)   # remembered between saves
        self.ldrop0 = None                    # ldrop at start, to report GROWTH
        self.ldrop = 0
        self.cam_fps = None                   # measured from frame_idx, not display fps
        self.rate_idx0 = None
        self.rate_t0 = 0.0
        self.expo_ceil = None
        # Read back from the FPGA over the control tunnel (poll_regs). None until
        # the first reply: MAXEXP in exposure units, and the exposure the sensor
        # was actually given -- which the FPGA may have clamped or shortened.
        self.fpga_max_units = None
        self.fpga_expo = None
        self.regs = {}
        self.rx_buf = b""
        self.last_poll = 0.0
        self.poll_i = 0
        self.expo_sent_t = 0.0               # readback is stale for a moment after
        self.sat = 0.0
        # ROI state, defaulted so the status line renders before the first frame.
        self.roi_valid = 0
        self.roi_mean = 0
        self.roi_npx = 0
        self.roi_blk = 0
        self.roi_phase = 7
        self.roi_host = None

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

        # The ROI line gets its own row rather than being appended to the stats
        # string: it is the thing being scrutinised, and it changes colour.
        self.roi_stats = ttk.Label(root, text="ROI  waiting for a frame...",
                                   font=("Consolas", 9), anchor="w")
        self.roi_stats.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 2))

        bar = ttk.Frame(root)
        bar2 = ttk.Frame(root)
        # PACK ORDER IS REVERSED for side=BOTTOM: the FIRST widget packed sits
        # lowest. bar2 is therefore packed first so it lands BELOW bar, even
        # though bar is populated first.
        bar2.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 4))
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
        # The Label only covers the picture; the Frame covers the letterbox
        # margins. Bind both so the gestures work anywhere in the view.
        for w in (self.view, self.canvas):
            w.bind("<MouseWheel>", self.on_wheel)                       # Windows / macOS
            w.bind("<Button-4>", lambda e: self.on_wheel(e, +120))      # X11
            w.bind("<Button-5>", lambda e: self.on_wheel(e, -120))
            w.bind("<ButtonPress-1>", self.on_press)
            w.bind("<B1-Motion>", self.on_motion)
            w.bind("<ButtonRelease-1>", self.on_release)
            w.bind("<Double-Button-1>", self.on_reset_view)

        ttk.Checkbutton(bar, text="auto contrast", variable=self.auto).pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="colour", variable=self.color).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Combobox(bar, textvariable=self.bayer, values=BAYER_PATTERNS, width=5,
                     state="readonly").pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(bar, text="auto WB", variable=self.awb).pack(side=tk.LEFT)
        ttk.Button(bar, text="Save TIFF", command=self.save_tiff).pack(side=tk.LEFT, padx=6)
        self.save_lbl = ttk.Label(bar, text="", width=30)
        self.save_lbl.pack(side=tk.LEFT)

        ttk.Label(bar, text="exposure us").pack(side=tk.LEFT, padx=(12, 2))
        self.expo = tk.IntVar(value=600)
        sc = ttk.Scale(bar, from_=40, to=EXPO_MAX_US, variable=self.expo, length=200,
                       command=self.on_expo_move)
        sc.pack(side=tk.LEFT)
        self.expo_scale = sc          # retune_expo_ceiling moves its upper bound
        # Apply on RELEASE, not on every pixel of drag: each change is a USB
        # command and the sensor needs a frame to act on it, so streaming
        # hundreds of them while dragging just floods the control channel.
        self.expo_dragging = False
        sc.bind("<ButtonPress-1>", lambda _e: setattr(self, "expo_dragging", True))
        sc.bind("<ButtonRelease-1>", self.on_expo_release)
        self.expo_lbl = ttk.Label(bar, text="", width=22)
        self.expo_lbl.pack(side=tk.LEFT, padx=4)
        # No "set" button: the exposure is sent when the slider is RELEASED, so a
        # button could only re-send a value that is already in effect.

        # ---- genlock: one exposure per PROJECTED frame -------------------
        # Both fields ride ONE command word (opcode 7: {en, 2'b0, dly[23:0]}), so
        # either control sends the pair -- otherwise toggling the checkbox would
        # silently zero the delay.
        #
        # ext_sync is the OUTPUT vsync, so this locks to whatever the board is
        # PROJECTING -- identical whether that video came from a PC over HDMI or
        # from the board's own offline generator.
        self.sync = tk.BooleanVar(value=False)
        self.sync_cb = ttk.Checkbutton(bar2, text="sync to projector", variable=self.sync,
                                       command=self.set_genlock)
        self.sync_cb.pack(side=tk.LEFT)
        self.proj_lbl = ttk.Label(bar2, text="", foreground="#a06000")
        self.proj_lbl.pack(side=tk.LEFT)
        self.projector = None                 # None until the board has answered

        ttk.Label(bar2, text="trig delay ms").pack(side=tk.LEFT, padx=(12, 2))
        self.dly = tk.DoubleVar(value=0.0)
        sd = ttk.Scale(bar2, from_=0.0, to=DLY_MAX_MS, variable=self.dly, length=200,
                       command=self.on_dly_move)
        sd.pack(side=tk.LEFT)
        self.dly_scale = sd
        # Applied on RELEASE for the same reason as exposure: every drag pixel
        # would otherwise be a USB command.
        sd.bind("<ButtonRelease-1>", lambda _e: self.set_genlock())
        self.dly_lbl = ttk.Label(bar2, text="", width=18)
        self.dly_lbl.pack(side=tk.LEFT, padx=4)

        self.on_expo_move(None)
        self.on_dly_move(None)
        # MAKE THE CAMERA MATCH THE SLIDERS. The camera keeps whatever the last
        # session (or script) left in it, and the controls only send on release,
        # so without this the UI can show 600 us / sync off while the video is
        # something else entirely. Push the UI's values rather than read the
        # camera's: readback lives on the UART, which this viewer does not own.
        # Exposure FIRST -- 600 us is safe at any rate -- THEN genlock, so a long
        # exposure left over from a genlocked 60 Hz session is shortened before
        # the sensor drops back to the 8.33 ms free-running period (see
        # set_genlock).
        if not (self.set_expo() and self.set_genlock()):
            self.save_lbl.configure(text="could not send exposure/sync to camera")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.tick()

    def on_resize(self, ev):
        self.dw, self.dh = max(ev.width, 1), max(ev.height, 1)
        self.clamp_view()

    # ---- zoom / pan ---------------------------------------------------------
    # The view is (zoom, cx, cy): screen px per sensor px is fit_scale * zoom,
    # and sensor point (cx, cy) sits at the middle of the view. Everything below
    # maps view coordinates through that, so the picture never needs to be
    # rendered larger than the window -- only the visible crop is resampled.

    def view_scale(self):
        return min(self.dw / float(NCOL), self.dh / float(NROW)) * self.zoom

    def clamp_view(self):
        """Keep the picture covering the view on any axis where it can.

        On an axis where the zoomed sensor is narrower than the view it is
        centred instead, the same letterboxing as the unzoomed picture.
        """
        s = self.view_scale()
        for ax, n, d in (("cx", NCOL, self.dw), ("cy", NROW, self.dh)):
            half = d / (2.0 * s)
            if half * 2.0 >= n:
                setattr(self, ax, n / 2.0)
            else:
                setattr(self, ax, min(max(getattr(self, ax), half), n - half))

    def view_xy(self, ev):
        # Events arrive in the coordinates of whichever widget was hit; root
        # coordinates make the Label and the Frame agree.
        return (ev.x_root - self.view.winfo_rootx(),
                ev.y_root - self.view.winfo_rooty())

    def on_wheel(self, ev, delta=None):
        """Zoom about the pointer: the sensor pixel under it stays under it."""
        notches = (ev.delta if delta is None else delta) / 120.0
        new = min(max(self.zoom * ZOOM_STEP ** notches, 1.0), ZOOM_MAX)
        if new == self.zoom:
            return
        vx, vy = self.view_xy(ev)
        s0 = self.view_scale()
        px = self.cx + (vx - self.dw / 2.0) / s0
        py = self.cy + (vy - self.dh / 2.0) / s0
        self.zoom = new
        s1 = self.view_scale()
        self.cx = px - (vx - self.dw / 2.0) / s1
        self.cy = py - (vy - self.dh / 2.0) / s1
        self.clamp_view()
        self.view_dirty = True

    def on_press(self, ev):
        self.vel = (0.0, 0.0)                 # grabbing a moving image stops it
        self.drag = (ev.x_root, ev.y_root)
        self.drag_hist = [(time.time(), ev.x_root, ev.y_root)]
        self.canvas.configure(cursor="fleur")

    def on_motion(self, ev):
        if self.drag is None:
            return
        dx, dy = ev.x_root - self.drag[0], ev.y_root - self.drag[1]
        self.drag = (ev.x_root, ev.y_root)
        self.pan(dx, dy)
        now = time.time()
        self.drag_hist.append((now, ev.x_root, ev.y_root))
        while len(self.drag_hist) > 2 and now - self.drag_hist[0][0] > FLING_WINDOW:
            self.drag_hist.pop(0)

    def on_release(self, ev):
        """Throw: the release velocity is the pointer's speed over the last
        FLING_WINDOW. A pointer held still before letting go throws nothing."""
        self.canvas.configure(cursor="")
        if self.drag is None:
            return
        self.drag = None
        now = time.time()
        h = [p for p in self.drag_hist if now - p[0] <= FLING_WINDOW]
        if len(h) >= 2 and h[-1][0] > h[0][0]:
            dt = h[-1][0] - h[0][0]
            self.vel = ((h[-1][1] - h[0][1]) / dt, (h[-1][2] - h[0][2]) / dt)
        self.last_anim = now

    def on_reset_view(self, _ev):
        self.zoom, self.vel = 1.0, (0.0, 0.0)
        self.cx, self.cy = NCOL / 2.0, NROW / 2.0
        self.view_dirty = True

    def pan(self, dx, dy):
        """Move the picture by (dx, dy) SCREEN pixels, as if dragged."""
        s = self.view_scale()
        ox, oy = self.cx, self.cy
        self.cx -= dx / s
        self.cy -= dy / s
        self.clamp_view()
        self.view_dirty = True
        return self.cx != ox, self.cy != oy

    def animate(self):
        """Coast after a throw, decaying exponentially; an edge stops that axis."""
        now = time.time()
        dt, self.last_anim = now - self.last_anim, now
        vx, vy = self.vel
        if self.drag is not None or (vx == 0.0 and vy == 0.0):
            return
        movx, movy = self.pan(vx * dt, vy * dt)
        k = np.exp(-dt / FLING_TAU)
        vx = vx * k if movx else 0.0
        vy = vy * k if movy else 0.0
        if np.hypot(vx, vy) < FLING_MIN:
            vx = vy = 0.0
        self.vel = (vx, vy)

    def render(self):
        """Resample the visible part of the last mapped frame into the view."""
        img = self.last_img
        s = self.view_scale()
        k = img.shape[1] / float(NCOL)        # image px per sensor px (colour is 1/2)
        hw, hh = self.dw / (2.0 * s), self.dh / (2.0 * s)
        sx0, sx1 = max(self.cx - hw, 0.0), min(self.cx + hw, float(NCOL))
        sy0, sy1 = max(self.cy - hh, 0.0), min(self.cy + hh, float(NROW))
        tw = max(1, int(round((sx1 - sx0) * s)))
        th = max(1, int(round((sy1 - sy0) * s)))
        im = Image.fromarray(img).resize(
            (tw, th), NEAREST if s * k >= 3.0 else RESAMPLE,
            box=(sx0 * k, sy0 * k, sx1 * k, sy1 * k))
        # ---- SHOW WHERE THE ROI ACTUALLY IS -----------------------------------
        # 16x16 out of 1280x1024 is a tenth of a percent of the frame, and at
        # preview scale it is smaller than one screen pixel. The whole reason
        # for putting a live picture behind this number is to see whether the
        # patch is on the projected spot at all, so the box is drawn OUTSIDE
        # the patch and deliberately oversized -- a marker, not a rectangle
        # you are meant to read pixel values out of. Green when the fabric
        # agrees with the host, red when it does not.
        if im.mode != "RGB":
            im = im.convert("RGB")
        d = ImageDraw.Draw(im)
        agree = (self.roi_valid and self.roi_host is not None
                 and self.roi_mean == self.roi_host)
        x0 = (ROI_COL8 * 8 - sx0) * s
        y0 = (ROI_ROW8 * 8 - sy0) * s
        x1 = x0 + campack.ROI_N * s
        y1 = y0 + campack.ROI_N * s
        pad = 6
        d.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad],
                    outline=(0, 255, 0) if agree else (255, 40, 40), width=2)
        self.photo = ImageTk.PhotoImage(im)
        self.canvas.configure(image=self.photo)
        self.view_dirty = False

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

    def debayer(self, a, lo, gain):
        """Raw Bayer mosaic -> half-resolution RGB, 8-bit.

        Each 2x2 cell becomes one RGB pixel (the two greens averaged). That is
        640x512, which is still more than the preview shows, and it costs a few
        strided slices instead of an interpolating demosaic -- the reader thread
        is starved by anything that holds the GIL for long.

        Auto WB is gray-world: gains that make the scene's mean R, G and B equal,
        eased frame to frame like auto contrast so the colour does not pump.
        """
        p = self.bayer.get()
        cells = {}
        greens = []
        for k, (dy, dx) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
            plane = a[dy::2, dx::2].astype(np.float32)
            if p[k] == "G":
                greens.append(plane)
            else:
                cells[p[k]] = plane
        rgb = np.stack((cells["R"], (greens[0] + greens[1]) * 0.5, cells["B"]), axis=-1)
        rgb -= lo
        if self.awb.get():
            m = np.maximum(rgb[::4, ::4].reshape(-1, 3).mean(axis=0), 1.0)
            target = m[1] / m
            if self.wb is None:
                self.wb = target
            else:
                self.wb += 0.05 * (target - self.wb)
            rgb *= self.wb * gain
        else:
            rgb *= gain
        return np.clip(rgb, 0, 255).astype(np.uint8)

    def on_expo_release(self, _ev):
        self.expo_dragging = False
        self.set_expo()

    def on_expo_move(self, _ev):
        """Live feedback while dragging -- no command sent until release."""
        self.expo_lbl.configure(
            text="%d / %d us%s" % (int(self.expo.get()), self.expo_ceiling_us(),
                                   "" if self.fpga_max_units is not None else " (est)"))

    def set_expo(self):
        us = min(int(self.expo.get()), self.expo_ceiling_us())
        units = int(us / EXPO_UNIT_US)           # floor: never round UP past the limit
        if self.fpga_max_units is not None:
            units = min(units, self.fpga_max_units)
        self.expo_sent_t = time.time()
        return self.cam.command(1, max(1, min(units, 0xFFFF)))

    # ---- register readback over the Ft+ control tunnel ----------------------
    # 0x40/0x41 EXPO0 (what the sensor was given), 0x53/0x54 MAXEXP in exposure
    # units, 0x55 {7: valid, 6: register-limited}, 0x20 MODE {6: edid_ok}.
    # Polled, not awaited: replies leave only at frame boundaries (~8 ms at
    # 120 Hz), and the GUI must never block on the camera.
    POLL_REGS = (0x40, 0x41, 0x53, 0x54, 0x55, 0x20)

    def update_projector(self, present):
        """Sync is only offered when a projector is attached.

        THE OUTPUT VSYNC IS NOT THE TEST. The board generates its own output
        timing with no projector attached -- measured: ext_sync at 75 Hz with
        edid_ok = 0 -- so "vsync is arriving" would happily lock the camera to a
        picture nobody is showing. edid_ok (reg 0x20 bit 6) is the projector
        answering its EDID read. No HDMI source is fine: the board then projects
        its offline patterns, and syncing to those is a real use.
        """
        if present == self.projector:
            return
        self.projector = present
        st = "!disabled" if present else "disabled"
        self.sync_cb.state([st])
        self.dly_scale.state([st])
        self.proj_lbl.configure(text="" if present else " (no projector)")
        if not present and self.sync.get():
            # Lost the projector while locked: fall back to free-running.
            # set_genlock shortens the exposure first if it has to.
            self.sync.set(False)
            self.set_genlock()

    def poll_regs(self):
        # ONE READ IN FLIGHT AT A TIME. Five requests sent back to back lost two
        # replies on hardware (0x41 and 0x55 every time); spaced 50 ms apart, all
        # five came back. So one register goes out every 0.2 s, round robin: the
        # whole set refreshes once a second.
        now = time.time()
        if now - self.last_poll >= 0.2:
            self.last_poll = now
            a = self.POLL_REGS[self.poll_i % len(self.POLL_REGS)]
            self.poll_i += 1
            self.cam.send_bytes(bytes([SYNC, OP_R, a, (256 - ((OP_R + a) & 0xFF)) & 0xFF]))
        # A read reply is {addr, value, checksum} with addr+value+ck == 0 mod 256.
        buf = self.rx_buf + self.reader.take_rx()
        i = 0
        while i + 3 <= len(buf):
            a, v, c = buf[i], buf[i + 1], buf[i + 2]
            if a in self.POLL_REGS and (a + v + c) & 0xFF == 0:
                self.regs[a] = v
                i += 3
            else:
                i += 1
        self.rx_buf = buf[i:][-64:]
        r = self.regs
        if all(k in r for k in (0x53, 0x54, 0x55)):
            mx = r[0x53] | (r[0x54] << 8)
            new = mx if (r[0x55] & 0x80) and mx > 0 else None
            if new != self.fpga_max_units:
                self.fpga_max_units = new
                self.expo_ceil = None             # force the slider to re-range
                self.retune_expo_ceiling()
        if 0x20 in r:
            self.update_projector(bool(r[0x20] & 0x40))
        if 0x40 in r and 0x41 in r:
            self.fpga_expo = r[0x40] | (r[0x41] << 8)
            # FOLLOW THE CAMERA, not the slider: the FPGA clamps over-long
            # requests and shortens the exposure itself when the rate rises, and
            # the slider has to say what the sensor is really doing. Skipped
            # while the slider is held and just after a send, when the readback
            # still describes the previous value.
            if now - self.expo_sent_t > 1.5 and not self.expo_dragging:
                us = int(round(self.fpga_expo * EXPO_UNIT_US))
                if abs(us - int(self.expo.get())) > 1:
                    self.expo.set(us)
                    self.on_expo_move(None)

    def track_rate(self, idx):
        """Measure the TRUE camera rate from frame_idx, not from display fps.

        The display deliberately drops frames -- the reader keeps only the newest
        -- so painted frames per second says nothing about what the sensor is
        doing. frame_idx increments once per frame the FPGA emitted, so its delta
        over wall time is the real rate whether or not we painted them.
        """
        now = time.time()
        if self.rate_idx0 is None or idx < self.rate_idx0:
            self.rate_idx0, self.rate_t0 = idx, now
            return
        dt = now - self.rate_t0
        if dt < 1.0:
            return
        rate = (idx - self.rate_idx0) / dt
        self.rate_idx0, self.rate_t0 = idx, now
        if rate <= 1.0:
            return
        self.cam_fps = rate
        self.retune_expo_ceiling()

    def expo_ceiling_us(self):
        """Largest exposure the CURRENT frame period allows.

        The FPGA's MAXEXP when it has answered -- the figure the fabric itself
        clamps to. Until then an estimate from the measured rate, kept 10 us
        further from the edge than the FPGA's own margin, because a measured
        rate is noisier than a measured period.
        """
        if self.fpga_max_units is not None:
            return int(max(40.0, self.fpga_max_units * EXPO_UNIT_US))
        if self.cam_fps is None:
            return EXPO_MAX_US
        us = 1e6 / self.cam_fps - EXPO_RESERVE_US - 10.0
        return int(max(40.0, min(us, EXPO_REG_MAX_US)))

    def retune_expo_ceiling(self):
        """Move the slider's ceiling to match the measured rate.

        Only acts on a real change, so the widget is not rebuilt every second.
        If the ceiling DROPS below the current setting the value is clamped and
        RE-SENT -- see set_genlock for why that ordering matters.
        """
        ceil = self.expo_ceiling_us()
        if self.expo_ceil is not None and abs(ceil - self.expo_ceil) < 100:
            return
        self.expo_ceil = ceil
        self.expo_scale.configure(to=ceil)
        if int(self.expo.get()) > ceil:
            self.expo.set(ceil)
            self.set_expo()
        self.on_expo_move(None)

    def on_dly_move(self, _ev):
        ms = float(self.dly.get())
        self.dly_lbl.config(text="%.2f ms (%d us)" % (ms, int(round(ms * 1000))))

    def set_genlock(self):
        """Send enable + delay together as one opcode-7 word.

        WHAT SYNC CHANGES. Off, the sensor free-runs on trig_per (120 Hz). On, it
        is triggered once per projected frame, so the rate follows the DISPLAY --
        75 Hz against a 75 Hz output, 60 against 60. A rate drop after ticking the
        box is the feature working, not a fault.

        WHY THE DELAY EXISTS. Pixels do not reach the screen when their vsync goes
        out: the projector adds latency, and a DLP with sequential RGB LEDs has
        real gaps between the colours. The delay places the exposure deliberately
        inside the projected frame rather than merely starting it with the frame.

        A delay longer than one frame is FINE and does not cost frame rate --
        triggers stack, several are in flight at once, and the pattern still
        advances at the full display rate. It costs latency: the capture of
        pattern k arrives while a later pattern is on screen.
        """
        ticks = int(round(float(self.dly.get()) * 100000.0))   # ms -> 10 ns ticks
        ticks = max(0, min(ticks, 0xFFFFFF))                   # 24 bits = 167.7 ms
        en = 1 if self.sync.get() else 0

        # ORDER MATTERS WHEN TURNING SYNC OFF, and getting it wrong wedges the
        # sensor. Genlocked to a slow display the exposure ceiling GROWS -- 16.6 ms
        # at 60 Hz. Unticking the box returns the camera to its free-running
        # 120 Hz, an 8.33 ms period, and an exposure LONGER THAN THE FRAME PERIOD
        # stops delivery until the part is reconfigured (measured: 8300 us at
        # 120 Hz collapses to 0.0 fps). So shorten the exposure FIRST, then change
        # the rate -- never the other way round, which leaves a window where the
        # commanded exposure exceeds the period that is about to take effect.
        if en == 0 and int(self.expo.get()) > EXPO_MAX_US:
            self.expo.set(EXPO_MAX_US)
            self.cam.command(1, int(round(EXPO_MAX_US / EXPO_UNIT_US)))
            self.expo_scale.configure(to=EXPO_MAX_US)
            self.expo_ceil = EXPO_MAX_US
            self.on_expo_move(None)

        return self.cam.command(7, (en << 27) | ticks)

    def save_tiff(self):
        """Write the frame currently on screen to a 16-bit TIFF.

        THE SENSOR VALUES ARE SAVED, NOT THE PICTURE. The display applies an
        auto-contrast stretch to make the scene visible; writing that would bake
        a viewing decision into the measurement and throw away 2 bits. What goes
        to disk is the raw 10-bit pixel, 0..1023.

        TIFF has no usable 10-bit grey mode -- the tag permits BitsPerSample=10
        but almost nothing reads it -- so the container is 16-bit unsigned, and
        the ten bits go in the TOP of the word (pixel << 6, low 6 bits zero).
        Unshifted, 1023 is 1.6% of 65535 and every viewer shows a black frame;
        shifted, full scale is white. Nothing is lost: >> 6 gives the exact
        sensor count back.
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
        desc = ("PYTHON1300 %dx%d 10-bit in 16-bit TIFF, MSB-aligned (count << 6; "
                ">> 6 for sensor counts 0..1023); "
                "raw Bayer mosaic (viewer phase %s); "
                "exposure %d us; frame_idx %s; slot %s"
                % (NCOL, NROW, self.bayer.get(), int(self.expo.get()), idx, slot))
        try:
            # 270 = ImageDescription, so the capture carries its own settings.
            msb = (a.astype(np.uint16) << 6)
            Image.fromarray(msb).save(path, format="TIFF", tiffinfo={270: desc})
        except Exception as e:
            self.save_lbl.configure(text="SAVE FAILED: %s" % e)
            return
        self.save_lbl.configure(text="saved %s" % name)

    def tick(self):
        f = self.reader.take()
        if f is not None:
            slot, idx, raw, fmt, ldrop, roiw = f
            a = campack.to_frame(raw, fmt)
            # ---- the fabric's ROI mean, and OUR OWN mean of the same pixels ----
            # These come from one set of pixels by two independent routes, so the
            # DIFFERENCE is the measurement being validated, not the mean itself.
            # The fabric truncates (sum >> 8) and campack.roi_mean_host does too, so
            # anything other than delta == 0 is a real disagreement, not rounding.
            self.roi_valid = roiw & 1
            self.roi_mean = (roiw >> 14) & 0x3FF
            self.roi_npx = (roiw >> 5) & 0x1FF
            self.roi_blk = (roiw >> 4) & 1
            self.roi_phase = (roiw >> 1) & 7
            self.roi_host = campack.roi_mean_host(a, ROI_COL8, ROI_ROW8)
            # ldrop is free-running since power-up, so the useful quantity is its
            # GROWTH over this session, not its absolute value.
            if self.ldrop0 is None:
                self.ldrop0 = ldrop
            self.ldrop = ldrop - self.ldrop0
            self.track_rate(idx)
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
                lo, gain = self.slo, 255.0 / span
            else:
                lo, gain = 0.0, 0.25                  # 10-bit -> 8-bit, no stretch
            if self.color.get():
                img = self.debayer(a, lo, gain)
            else:
                img = self.map_via_lut(a, lo, gain)
            # Fit the preview to whatever the window currently gives us, KEEPING
            # THE ASPECT RATIO. Stretching to the raw widget size would distort a
            # 5:4 sensor into whatever shape the window happens to be, and on a
            # metrology camera a silently non-square pixel is worse than a small
            # image. render() applies the zoom/pan on top of that fit.
            self.last_img = img
            self.render()
            self.shown += 1
            self.slot, self.idx = slot, idx
            self.lo, self.hi = int(sub.min()), int(sub.max())
            # Keep the RAW frame, not the contrast-mapped one, so Save TIFF
            # writes sensor counts rather than a viewing decision. This is a
            # reference to the array we already have -- no copy.
            self.last_frame = a

        self.poll_regs()
        # Panning and coasting must move the picture between camera frames too,
        # so the last mapped frame is re-rendered whenever the view changed.
        self.animate()
        if self.view_dirty and self.last_img is not None:
            self.render()

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
            # ---- the ROI line: fabric vs host, and the two witnesses -----------
            # npx and the FPGA/host delta are the whole point. A mean on its own
            # cannot tell a dark patch from a patch that is off the sensor, and it
            # cannot tell correct fabric arithmetic from plausible wrong arithmetic.
            # Both failures are silent without these, and both are cheap to show.
            if not getattr(self, "roi_valid", 0):
                self.roi_stats.configure(
                    text="ROI  no measurement paired with this frame (roi_valid=0)",
                    foreground="#a06000")
            else:
                hostm = self.roi_host
                delta = None if hostm is None else self.roi_mean - hostm
                bad = (self.roi_npx != 256) or (delta not in (0, None))
                self.roi_stats.configure(
                    text=("ROI 16x16 @ col %d row %d    FPGA %4d    host %s    delta %s"
                          "    npx %d%s    phase %s%s"
                          % (ROI_COL8 * 8, ROI_ROW8 * 8,
                             self.roi_mean,
                             "----" if hostm is None else "%4d" % hostm,
                             "--" if delta is None else "%+d" % delta,
                             self.roi_npx,
                             "" if self.roi_npx == 256 else "  <- MUST BE 256",
                             self.roi_phase,
                             "  (no trigger)" if self.roi_phase == 7 else
                             ("  BLACK ROWS" if self.roi_blk else ""))),
                    foreground="#c00000" if bad else "#006000")
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

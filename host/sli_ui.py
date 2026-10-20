#!/usr/bin/env python3
"""Projector-profiling app, document style.

    python host/sli_ui.py [profile.sliprof]

SELF-CONTAINED: this one file is the whole app -- copy it anywhere. It needs numpy,
pyserial and matplotlib (Tk comes with Python), and ftd3xx only when the board's genlock
is off and has to be turned on over the Ft+. The board's offline mode table is embedded
(MODE_ROWS); keep it in step with sources_1/imports/RTL/mode_table.vh. Full description:
host/SLI_UI.md.

A DOCUMENT is one profiling session: the connection it was measured on (COM port, video
output, frame period, exposure limit), the WWWWW and WKKKK sweeps, and the chosen exposure
edges. It is saved as one JSON file, *.sliprof. The app starts with no document.

FILE MENU
  New...      the setup dialog (COM port + the projector's mode, offline); OK sets the
              board's mode, opens the port, turns genlock on over the Ft+, checks the
              frame period, and opens an empty document connected to the board. Cancel
              leaves no document.
  Open...     a saved .sliprof. It is NOT connected: the first Run reconnects with its
              saved port, projector and mode, or shows the setup dialog if that fails.
  Save / Save As... / Close / Exit -- unsaved changes are asked about first.

THE VIEW. One plot of the 5-frame (41.7 ms) timeline: WWWWW in blue -- always white,
frame 0 at 255 and frames 1-4 at 254 so frame 0's tag still anchors the cycle
(lag_sweep.py --bg 254) -- and WKKKK in red, one white frame and four black (lag_sweep.py
with no --bg). Raw ROI mean.

Over it, the EXPOSURE EDGES: two vertical lines, each with a grab box near the top. The
LEADING edge (green) is when the exposure opens -- the genlock delay after frame 0's vsync
-- and the TRAILING edge (purple) when it closes. Drag a box, or type a value. Until you
move them they sit on the window of the longest allowed exposure that collects the most of
the white frame's light ("Best window" puts them back there at the current width). The
readout gives delay, exposure (in opcode 1's 0.375 us units), the share of the white
frame's light inside, and whether the exposure is over the limit.

MOUSE. Only the time axis zooms and pans; the ADU axis always spans every value the sweeps
have read, and grows as a sweep runs. The time axis never goes past frame 0's vsync or
the end of frame 4. Wheel zooms time around the cursor. Drag to pan;
let go while moving and it glides to a stop. Double-click (or "Whole view") shows all five
frames again. The grab boxes are a fixed size on screen at a fixed height in the plot, so
zoom never loses them; pan an edge out of view and its box goes with it.

ADJUST IRIS holds a 5 us exposure on the brightest delay of the WWWWW sweep and shows every
reading live, with the saturated-pixel count, while the lens iris is turned: aim for the
green band with nothing clipping. Done records the setting in the document and, if the
document already holds sweeps, says they were taken at the old setting and need re-running.

Run WWWWW / Run WKKKK replace that sweep; Stop ends it after the current delay and keeps
what it has. The Sweep menu imports a lag_sweep.py CSV (one written before the vblank tag
fix is corrected on import) and exports the sweeps as CSV and the window as JSON. While a
sweep runs, every reading is also written to a CSV in the temp folder, so a crash does
not lose it; the status line names the file.
"""
import collections
import csv
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import serial
from serial.tools import list_ports
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.transforms import blended_transform_factory

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ======================================================================================
# The board, over the UART (COM port)
# ======================================================================================
# SELF-CONTAINED. Everything below was lifted from lag_core.py, frame_sweep.py,
# gray_pair.py, sli_background.py and ftlink.py -- only what the three tabs call -- so
# this file runs on its own. Those modules are unchanged and still serve the command-line
# tools; a fix to the protocol there has to be made here too.

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
R_IMPLVL2, R_IMPRGB2 = 0x07, 0x08                # the odd frame's level and LED mask
R_IMPCYC, R_IMPLVL = 0x11, 0x12                  # cycle length / position, the others' level
R_SLICTL = 0x13                                  # SLI pattern engine; 0 = off
R_MODEFORCE = 0x14                               # pin the offline mode: 0x80 | idx
R_ROICTL, R_EXPO_LO, R_EXPO_HI = 0x17, 0x1A, 0x1B
R_DLY0, R_DLY1, R_DLY2, R_IMPRGB = 0x1C, 0x1D, 0x1E, 0x1F
EXPO_UNIT_US = 0.375                             # opcode 1's exposure unit
TICK_US = 0.01                                   # genlock delay unit (100 MHz)
ACTIVE_US = 432.5                                # 800x600@120: vsync -> first active pixel
RESERVE_TICKS = 9000                             # usb_link.v: 80 us sensor gap + 10 us margin

# One line per camera frame: R=mmm,nnn,rrggbb,cc[,hh,ll]<CR><LF>
#   mmm ROI mean (10-bit)   nnn pixels in the ROI (must be 0x100)   rrggbb the top-left
#   pixel transmitted on the frame that was on screen at the trigger   cc trigger ordinal
#   hh/ll saturated (>= 1020) / clamped-low (<= 3) ROI pixels -- newer bitstreams only.
# The CRLF anchor stops a half-arrived line from matching on its fixed-width fields.
LINE = re.compile(b"R=([0-9A-F]{3}),([0-9A-F]{3}),([0-9A-F]{6}),([0-9A-F]{2})"
                  + b"(?:,([0-9A-F]{2}),([0-9A-F]{2}))?"
                  + bytes([13, 10]))


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.02)


def wr_now(ser, addr, val):
    """A register write without wr's 20 ms sleep (the ACK is ignored)."""
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))


def rd(ser, addr, window=0.6):
    """Register read. The reply is THREE bytes -- addr, value, checksum -- with no sync
    prefix, sharing the line with ASCII telemetry; scan for a triple whose checksum closes."""
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    buf = bytearray()
    dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def reg(ser, addr):
    v = rd(ser, addr)
    return 0 if v is None else v


def set_delay(ser, ticks):
    wr(ser, R_DLY0, ticks & 0xFF)
    wr(ser, R_DLY1, (ticks >> 8) & 0xFF)
    wr(ser, R_DLY2, (ticks >> 16) & 0xFF)


def get_delay(ser):
    lo, mid, hi = rd(ser, 0x5A), rd(ser, 0x5B), rd(ser, 0x5C)
    if None in (lo, mid, hi):
        return None
    return lo | (mid << 8) | (hi << 16)


def wait_delay(ser, want, timeout=1.5):
    """Poll 0x5A..0x5C until they show the delay just written. Returns (value, matched).
    The readback LAGS the write: it is served from a snapshot republished at ~10 Hz, so a
    single read straight after a write returns the previous delay."""
    t0 = time.time()
    got = None
    while time.time() - t0 < timeout:
        got = get_delay(ser)
        if got == want:
            return got, True
    return got, False


def collect(ser, nframes, timeout=6.0, sat=False):
    """Up to nframes camera lines as (mean, npx, tlp, tcnt); sat=True appends (nhi, nlo),
    None from a bitstream too old to send them -- which is not the same as zero."""
    out, buf, t0 = [], b"", time.time()
    while len(out) < nframes and time.time() - t0 < timeout:
        buf += ser.read(4096)
        ms = list(LINE.finditer(buf))
        if ms:
            for m in ms:
                row = (int(m.group(1), 16), int(m.group(2), 16),
                       int(m.group(3), 16), int(m.group(4), 16))
                if sat:
                    row += ((int(m.group(5), 16) if m.group(5) else None),
                            (int(m.group(6), 16) if m.group(6) else None))
                out.append(row)
            buf = buf[ms[-1].end():]
        elif len(buf) > 8192:
            buf = buf[-64:]
    return out[:nframes]


def spin(dt):
    """Busy-wait dt seconds. NOT time.sleep(): Windows quantises sleep to the timer tick,
    so a requested 0.5 ms can return 15 ms later -- longer than a 120 Hz frame."""
    end = time.perf_counter() + dt
    while time.perf_counter() < end:
        pass


# ---- the board's offline modes --------------------------------------------------------
# EMBEDDED COPY of sources_1/imports/RTL/mode_table.vh (2026-10-19, idx 13 included). The
# index is what the FPGA reports in MODE (0x20) and takes in MODEFORCE (0x14), so this
# table MUST match the bitstream on the board: if mode_table.vh changes, change it here.
#        idx: (HACT, VACT, Hz, pclk kHz, HFP, HSYNC, HBP, VFP, VSYNC, VBP, Hpol, Vpol)
MODE_ROWS = {0: (800, 600, 120, 73270, 48, 32, 80, 3, 6, 27, 1, 0),        # CVT-RB
             1: (640, 480, 120, 52420, 48, 32, 80, 3, 4, 59, 1, 0),        # CVT-RB
             2: (1024, 768, 75, 78750, 16, 96, 176, 1, 3, 28, 1, 1),       # DMT
             3: (800, 600, 75, 49500, 16, 80, 160, 1, 3, 21, 1, 1),        # DMT
             4: (640, 480, 75, 31500, 16, 64, 120, 1, 3, 16, 0, 0),        # DMT
             5: (1024, 768, 70, 75000, 24, 136, 144, 3, 6, 29, 0, 0),      # DMT
             6: (800, 600, 72, 50000, 56, 120, 64, 37, 6, 23, 1, 1),       # DMT
             7: (640, 480, 72, 31500, 24, 40, 128, 9, 3, 28, 0, 0),        # DMT
             8: (1280, 720, 60, 74250, 110, 40, 220, 5, 5, 20, 1, 1),      # CEA/DMT
             9: (1280, 800, 60, 71110, 48, 32, 80, 3, 6, 14, 1, 0),        # CVT-RB
             10: (1024, 768, 60, 65000, 24, 136, 160, 3, 6, 29, 0, 0),     # DMT
             11: (800, 600, 60, 40000, 40, 128, 88, 1, 4, 23, 1, 1),       # DMT
             12: (640, 480, 60, 25175, 16, 96, 48, 10, 2, 33, 0, 0),       # DMT, failsafe
             13: (1280, 1024, 60, 108000, 48, 112, 248, 1, 3, 38, 1, 1)}   # DMT, 108 MHz


def mode_table():
    """{idx: dict(idx, w, h, hz, khz, vtotal, vstart)}. vstart is the lines from the vsync
    edge to the first active line (VSYNC + VBP)."""
    out = {}
    for i, (w, h, hz, khz, hfp, hs, hbp, vfp, vs, vbp, hp, vp) in MODE_ROWS.items():
        out[i] = dict(idx=i, w=w, h=h, hz=hz, khz=khz, vtotal=h + vfp + vs + vbp,
                      vstart=vs + vbp)
    return out


def mode_name(m):
    return "%dx%d @ %d Hz" % (m["w"], m["h"], m["hz"])


def board_modes(ser):
    """(modes the projector's EDID supports, as table rows, index in use). The mask is
    regs 0x29/0x2A, bit i = table index i; MODE 0x20 low nibble is the index in use."""
    tbl = mode_table()
    mask = reg(ser, 0x29) | (reg(ser, 0x2A) << 8)
    return [tbl[i] for i in sorted(tbl) if mask >> i & 1], reg(ser, 0x20) & 0x0F


def set_board_mode(ser, idx):
    """Pin the offline mode to `idx` over MODEFORCE. It is sticky until released or the
    FPGA reloads. Waits for the index to land and the frame period to settle."""
    if reg(ser, 0x20) & 0x0F == idx:
        return
    wr(ser, R_MODEFORCE, 0x80 | idx)
    t0 = time.time()
    while reg(ser, 0x20) & 0x0F != idx:
        if time.time() - t0 > 5:
            raise RuntimeError("the board did not switch to mode %d" % idx)
        time.sleep(0.2)
    time.sleep(3)                                    # projector re-sync, period re-measured


def read_edid(ser, tgt=0x03, n=256):
    """The projector's EDID as the board read it off HDMI-OUT (table 0x03). The reply
    shares the line with ASCII telemetry, so scan for a frame whose checksum closes:
    tgt, n bytes, checksum."""
    to = ser.timeout
    ser.timeout = 0.05
    try:
        ser.reset_input_buffer()
        ser.write(bytes([SYNC, 0x72, tgt, ck(0x72 + tgt)]))
        need, buf, dl = n + 2, bytearray(), time.time() + 4.0
        while time.time() < dl:
            buf += ser.read(ser.in_waiting or 1)
            for i in range(len(buf) - need + 1):
                if buf[i] == tgt and sum(buf[i:i + need]) & 0xFF == 0:
                    return bytes(buf[i + 1:i + 1 + n])
        return None
    finally:
        ser.timeout = to


def edid_name(b):
    """'Name (MFG)' from an EDID: the monitor-name descriptor (tag 0xFC), and the
    three-letter manufacturer ID from bytes 8-9. None if the block is not valid."""
    if not b or b[:8] != bytes([0, 255, 255, 255, 255, 255, 255, 0]) or sum(b[:128]) & 0xFF:
        return None
    w = b[8] << 8 | b[9]
    mfg = "".join(chr(64 + (w >> k & 31)) for k in (10, 5, 0))
    name = None
    for o in range(54, 126, 18):
        if b[o:o + 3] == bytes(3) and b[o + 3] == 0xFC:
            name = b[o + 5:o + 18].split(b"\n")[0].decode("ascii", "replace").strip()
    return "%s (%s)" % (name, mfg) if name else mfg


def projector_name(ser):
    """The projector's name from its EDID, or None."""
    return edid_name(read_edid(ser))


def active_us(ser, T):
    """Vsync -> first active pixel for the mode in use, from the measured frame period."""
    m = mode_table()[reg(ser, 0x20) & 0x0F]
    return T * m["vstart"] / m["vtotal"]


def max_expo_units(T_us):
    """The FPGA's MAXEXP (regs 0x53/0x54) for frame period T_us, computed the way
    usb_link.v does it: (period ticks - RESERVE_TICKS) * 27962 >> 20, i.e. / 37.5,
    rounded down, capped at the 16-bit register. Longer drops 120 fps to 60."""
    ticks = int(round(T_us / TICK_US))
    if ticks <= RESERVE_TICKS:
        return 0
    return min(0xFFFF, ((ticks - RESERVE_TICKS) * 27962) >> 20)


# ---- genlock, over the Ft+ ------------------------------------------------------------
def genlock_on(ser):
    """Turn genlock on if it is off (gl_en resets to 0 on the merged build). The UART has
    no register for it: it is camera opcode 7, which only the Ft+ (FT601, D3XX) can send --
    one raw 32-bit word down OUT pipe 0x02. ftd3xx is imported here so a board whose
    genlock is already on never needs it."""
    if reg(ser, 0x5D) & 1:
        return
    try:
        import ctypes
        import struct
        import ftd3xx
        from ftd3xx.defines import FT_OPEN_BY_INDEX
    except ImportError:
        raise RuntimeError("genlock is off, and turning it on needs the Ft+ driver: "
                           "pip install ftd3xx (and FTDI's D3XX driver)")
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        raise RuntimeError("genlock is off and no Ft+ (D3XX device) was found to turn it on")
    try:
        word = (7 << 28) | (1 << 27)                 # opcode 7: gl_en = 1, delay 0
        buf = ctypes.create_string_buffer(struct.pack("<I", word))
        try:
            d.writePipe(0x02, buf, 4)
        except Exception:
            d.writePipeEx(0x02, buf, 4)
        time.sleep(0.3)
    finally:
        try:
            d.close()
        except Exception:
            pass


def preflight(port):
    """Open the port and check the board is ready to measure: generating its own video
    (no PC on HDMI-IN), genlock on and live, a plausible frame period. Returns (open
    serial, frame period us, exposure limit in 0.375 us units)."""
    ser = serial.Serial(port, 115200, timeout=0.05)
    time.sleep(0.3)
    try:
        st, md = reg(ser, 0x02), reg(ser, 0x20)      # STATUS bit 4: 1 = pass-through
        if st & 0x10:
            raise RuntimeError("the board is in pass-through: unplug the PC's HDMI from "
                               "the board to run offline")
        if not md & 0x80:
            raise RuntimeError("the board has no valid offline mode (MODE 0x%02X)" % md)
        genlock_on(ser)
        T = (reg(ser, 0x4A) | (reg(ser, 0x4B) << 8) | (reg(ser, 0x4C) << 16)) * TICK_US
        mx = reg(ser, 0x53) | (reg(ser, 0x54) << 8)
        gl = reg(ser, 0x5D)
        if not (1000 < T < 60000) or (gl & 3) != 3:
            raise RuntimeError("frame %.1f us, genlock 0x%02X -- is video running?" % (T, gl))
    except Exception:
        ser.close()
        raise
    return ser, T, mx


# ---- the 5-frame impulse sweep (Profile and RGB filters tabs) -------------------------
def start_impulse(ser, level, bg, expo, mx):
    """5-frame cycle: `level` at position 0, `bg` at 1-4, all LEDs. Returns the real exposure."""
    u = max(1, int(round(expo / EXPO_UNIT_US)))
    if u > mx:
        raise RuntimeError("%.2f us is over the exposure limit" % expo)
    wr(ser, R_IMPCYC, 5)                             # 5-frame cycle, odd frame at position 0
    wr(ser, R_IMPLVL, bg & 0xFF)
    wr(ser, R_IMPRGB, 0x07)
    wr(ser, R_IMPLVL2, level & 0xFF)
    wr(ser, R_IMPRGB2, 0x07)
    wr(ser, R_ROICTL, 0xC0)                          # impulse_en | per-frame stream
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    time.sleep(0.4)
    return u * EXPO_UNIT_US


def stop_impulse(ser):
    wr(ser, R_ROICTL, 0x00)
    set_delay(ser, 0)


def delays_for(T, e, step):
    return list(np.arange(0.0, T - e - 1.0, step))


def positions(rows, d, lit_px, active_us=ACTIVE_US):
    """Camera lines at one delay -> {position: [ROI means]}."""
    by = collections.defaultdict(list)
    # FRAME NUMBERING. The profiling bitstream counts frames in `cc`; the merged one
    # sends cc = 0 on every line. There, the per-frame stream still delivers one line
    # per frame, so the line's ARRIVAL ORDER is the frame number -- and the anchor
    # check below catches a dropped one.
    use_cc = any((rows[k][3] - rows[k - 1][3]) & 0xFF for k in range(1, len(rows)))
    absns, absn = [], 0
    for k, r in enumerate(rows):
        if k:
            absn += ((r[3] - rows[k - 1][3]) & 0xFF) if use_cc else 1
        absns.append(absn)
    # THE WHITE FRAME'S TAG. The merged build latches only RED of the transmitted
    # pixel, so white reads FF0000 there and FFFFFF on the profiling bitstream.
    marks = [n for n, r in zip(absns, rows) if r[2] in (lit_px, lit_px & 0xFF0000)]
    # Re-anchor on EVERY white frame, and drop any cycle whose marks are not exactly 5
    # apart: a missed line then costs one cycle, instead of shifting every later
    # reading onto the wrong position.
    for n, r in zip(absns, rows):
        prev = [m for m in marks if m <= n]
        nxt = [m for m in marks if m > n]
        if not prev or n - prev[-1] > 4:
            continue
        if nxt and nxt[0] - prev[-1] != 5:
            continue
        # A trigger before the first active pixel carries the previous frame's tag:
        # it belongs one position later (4 -> 0 of the next cycle).
        by[(n - prev[-1] + (d < active_us)) % 5].append(r[0])
    return by


SWEEP_CSV_HEAD = ["T_us", "expo_us", "delay_us", "position", "t_us", "mean", "sd", "n",
                  "ref", "tag_fix_us"]


def sweep(ser, T, e, delays, out, level=255, bg=0, frames=20, settle=4,
          active_us=ACTIVE_US, stop=None, log=print):
    """Step the delay; per delay kept, yield (i, len(delays), delay, [5 means], ref, rows).

    `rows` are the five SWEEP_CSV_HEAD rows for that delay. Every reading is also written
    to `out` as it arrives. `stop` is an optional threading.Event checked between delays.
    The caller still owns `ser` and should call stop_impulse() afterwards.
    """
    lit_px = (level << 16) | (level << 8) | level
    with open(out or os.devnull, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SWEEP_CSV_HEAD)
        for i, d in enumerate(delays):
            if stop is not None and stop.is_set():
                return
            want = int(round(d / TICK_US))
            set_delay(ser, want)
            if i % 25 == 0:
                wait_delay(ser, want)
            ser.reset_input_buffer()
            collect(ser, settle, timeout=4)
            rows = [r for r in collect(ser, frames, timeout=6) if r[1] == 256]
            by = positions(rows, d, lit_px, active_us)
            if len(by) < 5:
                log("  delay %.1f: positions seen %s -- skipped"
                    % (d, sorted((k, len(v)) for k, v in by.items())))
                continue
            means = [float(np.mean(by[k])) for k in range(5)]
            ref = 0.0 if bg else float(np.median(means))
            rows = [[round(T, 2), round(e, 3), round(d, 2), k, round(k * T + d, 2),
                     round(means[k], 3), round(float(np.std(by[k])), 3), len(by[k]),
                     round(ref, 3), active_us] for k in range(5)]
            w.writerows(rows)
            if i % 10 == 0:
                fh.flush()
            yield i, len(delays), d, means, ref, rows


# ---- grey pairs over the runway (Runway maps tab) -------------------------------------
class Stream:
    """Camera lines one at a time, as (mean, npx, level-from-tag, nhi)."""

    def __init__(self, ser):
        self.ser, self.buf, self.q = ser, b"", []

    def get(self, timeout=0.5):
        t0 = time.time()
        while not self.q:
            if time.time() - t0 > timeout:
                return None
            c = self.ser.read(4096)
            if not c:
                spin(0.0005)
                continue
            self.buf += c
            ms = list(LINE.finditer(self.buf))
            for m in ms:
                self.q.append((int(m.group(1), 16), int(m.group(2), 16),
                               (int(m.group(3), 16) >> 16) & 0xFF,
                               int(m.group(5), 16) if m.group(5) else 0))
            if ms:
                self.buf = self.buf[ms[-1].end():]
            elif len(self.buf) > 8192:
                self.buf = self.buf[-64:]
        return self.q.pop(0)


PAIR_CSV_HEAD = ["delay_us", "expo_us", "prev_level", "this_level", "mean", "sd", "n",
                 "nhi_max", "T_us"]


def pairs_on(ser):
    """Impulse generator: the 2-frame A B A B cycle, all LEDs, per-frame stream."""
    wr(ser, R_SLICTL, 0x00)                          # no SLI patterns underneath
    wr(ser, R_IMPCYC, 0x02)                          # 2-frame cycle, bpos 0
    wr(ser, R_IMPRGB, 0x07)
    wr(ser, R_IMPRGB2, 0x07)
    wr(ser, R_ROICTL, 0xC0)                          # impulse + per-frame stream


def pairs_off(ser):
    """Back to the defaults: no stream, the 5-frame cycle, white, delay 0."""
    ser.timeout = 0.05
    time.sleep(0.05)
    wr(ser, R_ROICTL, 0x00)
    wr(ser, R_IMPCYC, 0x05)                          # back to the 5-frame default
    wr(ser, R_IMPLVL, 0xFF)
    wr(ser, R_IMPLVL2, 0xFF)
    set_delay(ser, 0)


def measure_window(ser, T, d, units, pairs, per=10, skip=2):
    """One window (delay d us, `units` x 0.375 us) through the (A, B) pairs, pairs_on
    already called. Yields (A, B, cells, seen) per pair, cells = [(prev, this, mean,
    sd, n, nhi_max)] for both orders; seen is False when the level change never showed
    in the tag."""
    wr(ser, R_EXPO_LO, units & 0xFF)
    wr(ser, R_EXPO_HI, (units >> 8) & 0xFF)
    want = int(round(d / TICK_US))
    set_delay(ser, want)
    if not wait_delay(ser, want)[1]:
        raise RuntimeError("delay %.1f did not land" % d)
    ser.timeout = 0
    ser.reset_input_buffer()
    st = Stream(ser)
    old, curA = set(), None
    for A, B in pairs:
        if A != curA:
            wr_now(ser, R_IMPLVL2, A)
            curA = A
        wr_now(ser, R_IMPLVL, B)
        newset = {A, B}
        fresh = newset - old                      # a level only the new pair has
        # WAIT FOR THE NEW LEVELS TO BE ON AIR. The tag names the level transmitted in
        # each frame; once the new pair shows, drop `skip` more frames so the image on
        # screen (one frame behind) is new as well.
        prev, seen, t_w = None, False, time.time()
        while time.time() - t_w < 1.0:
            r = st.get()
            if r is None:
                break
            lvl = r[2]
            if fresh:
                seen = lvl in fresh
            else:                                 # e.g. (255,255) after (254,255)
                seen = prev is not None and prev == lvl == A
            prev = lvl
            if seen:
                break
        for _ in range(skip):
            st.get()
        by = {}
        need = [(A, B), (B, A)] if A != B else [(A, B)]
        prev, t_c = None, time.time()
        while time.time() - t_c < max(2.0, 3e-6 * T * per) and \
                min(len(by.get(c, [])) for c in need) < per:
            r = st.get()
            if r is None:
                break
            if r[1] == 256 and prev is not None and (prev, r[2]) in need:
                by.setdefault((prev, r[2]), []).append((r[0], r[3]))
            prev = r[2]
        cells = []
        for (pl, tl), v in by.items():
            ms = [x[0] for x in v]
            cells.append((pl, tl, float(np.mean(ms)), float(np.std(ms)), len(ms),
                          max(x[1] for x in v)))
        old = newset
        yield A, B, cells, seen


def runway_windows(edges, Tp):
    """The runway as measurement windows, split where the previous frame's exposure
    (trail - T) ends: [(start, end, name)] in us into the runway's frame."""
    if "runway_us" not in edges:
        raise ValueError("no runway -- place it in the Profile tab")
    rw, lead, cut = edges["runway_us"], edges["lead_us"], edges["trail_us"] - Tp
    k = int(rw // Tp)
    parts = []
    if cut > rw:
        parts.append((rw, min(cut, lead), "runway inside the previous exposure"))
    if lead > max(rw, cut):
        parts.append((max(rw, cut), lead, "runway between exposures"))
    return [(round(s_ - k * Tp, 2), round(e_ - k * Tp, 2), n) for s_, e_, n in parts]


# ======================================================================================
# The app
# ======================================================================================
PAD = dict(padx=8, pady=4)
EXT = ".sliprof"
FORMAT = "auv2-sli-profile"

# the two sweeps a document holds: key -> (tab title, level of frame 0, level of frames 1-4)
SWEEPS = {"wwwww": ("WWWWW", 255, 254),
          "wkkkk": ("WKKKK", 255, 0)}
# WWWWW repeated through a colour filter on the lens, one per filter (the RGB filters tab).
# Until 2026-10-19 these were WKKKK (keys wkkkk_r/g/b); such sweeps stay in old documents
# and can still be exported from the Sweep menu, but the tab shows only these.
FILTER_SWEEPS = {"wwwww_r": ("WWWWW red filter", 255, 254),
                 "wwwww_g": ("WWWWW green filter", 255, 254),
                 "wwwww_b": ("WWWWW blue filter", 255, 254)}
OLD_FILTER_SWEEPS = {"wkkkk_r": ("WKKKK red filter", 255, 0),
                     "wkkkk_g": ("WKKKK green filter", 255, 0),
                     "wkkkk_b": ("WKKKK blue filter", 255, 0)}
ALL_SWEEPS = dict(SWEEPS, **FILTER_SWEEPS, **OLD_FILTER_SWEEPS)
SETTINGS = dict(expo_us=5.0, step_us=5.0, frames=20, settle=4, active_us=ACTIVE_US)


# ======================================================================================
# The document
# ======================================================================================
class Document:
    """One profiling session. Rows are SWEEP_CSV_HEAD rows, already tag-corrected."""

    def __init__(self):
        self.path = None
        self.dirty = False
        self.port = self.display = None
        self.T = None                                # frame period, us
        self.mx = None                               # exposure limit, 0.375 us units
        self.sweeps = {}                             # key -> {"settings", "rows", "when", "complete"}
        self.edges = None                            # {"lead_us", "trail_us", "runway_us"}
        self.iris = None                             # last Adjust iris: {"delay_us", ...}
        self.projector = None                        # {"name", "idx", "w", "h", "hz", "khz"}
        self.runway_maps = None                      # {"settings", "rows", "when", "complete"}
        self.created = time.strftime("%Y-%m-%d %H:%M:%S")
        self.ser = None                              # open serial port while connected

    @property
    def name(self):
        return os.path.basename(self.path) if self.path else "Untitled"

    def points(self, key):
        """[(t_us, mean)] sorted by t, or []."""
        sw = self.sweeps.get(key)
        if not sw:
            return []
        return sorted((float(r[4]), float(r[5])) for r in sw["rows"])

    # ---- file ------------------------------------------------------------------------
    def save(self, path):
        doc = {"format": FORMAT, "version": 1, "created": self.created,
               "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
               "port": self.port, "display": self.display, "T_us": self.T,
               "max_expo_units": self.mx, "edges": self.edges, "iris": self.iris,
               "projector": self.projector, "runway_maps": self.runway_maps,
               "csv_head": SWEEP_CSV_HEAD, "sweeps": self.sweeps}
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, path)                        # never leave a half-written document
        self.path, self.dirty = path, False

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            j = json.load(fh)
        if j.get("format") != FORMAT:
            raise ValueError("%s is not an AuV2-SLI profile" % os.path.basename(path))
        d = cls()
        d.path = path
        d.created = j.get("created", d.created)
        d.port, d.display = j.get("port"), j.get("display")
        d.T, d.mx = j.get("T_us"), j.get("max_expo_units")
        d.edges = j.get("edges")
        d.iris = j.get("iris")
        d.projector = j.get("projector")             # None in profiles saved before 2026-10-16
        d.runway_maps = j.get("runway_maps")
        d.sweeps = j.get("sweeps", {})
        return d

    # ---- CSV -------------------------------------------------------------------------
    def import_csv(self, key, path):
        with open(path) as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise ValueError("%s has no readings" % os.path.basename(path))
        T = float(rows[0]["T_us"])
        if self.T and abs(T - self.T) > 1.0:
            raise ValueError("%s was measured at a %.1f us frame, this document is %.1f us"
                             % (os.path.basename(path), T, self.T))
        # A CSV written before lag_sweep corrected the vblank tag has no tag_fix_us: move
        # its readings before the first active pixel one position later, as lag_core does.
        fix = None if "tag_fix_us" in rows[0] else ACTIVE_US
        out = []
        for r in rows:
            d, k = float(r["delay_us"]), int(r["position"])
            if fix is not None and d < fix:
                k = (k + 1) % 5
            out.append([float(r["T_us"]), float(r["expo_us"]), d, k, round(k * T + d, 2),
                        float(r["mean"]), float(r["sd"]), int(r["n"]), float(r["ref"]),
                        float(r.get("tag_fix_us") or ACTIVE_US)])
        self.T = self.T or T
        self.sweeps[key] = {"settings": {"imported_from": os.path.abspath(path),
                                         "tag_fixed_on_import": fix is not None},
                            "rows": out, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "complete": True}
        self.dirty = True

    def export_csv(self, key, path):
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(SWEEP_CSV_HEAD)
            w.writerows(self.sweeps[key]["rows"])

    # ---- connection ------------------------------------------------------------------
    def disconnect(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None


# ======================================================================================
# Setup dialog: COM port + projector video output
# ======================================================================================
class SetupDialog(tk.Toplevel):
    """Modal. OK switches the board to the chosen mode, runs the preflight and sets
    self.result = (ser, T, mx, port, display, projector); Cancel leaves it None.
    `projector` (a document's saved one) preselects its mode, so OK restores it."""

    def __init__(self, master, port=None, display=None, projector=None):
        super().__init__(master)
        self.title("Connect to the board")
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        # ---- COM port ----------------------------------------------------------------
        f = ttk.LabelFrame(self, text="Camera COM port")
        f.grid(row=0, column=0, sticky="ew", **PAD)
        self.port = tk.StringVar()
        self.port_box = ttk.Combobox(f, textvariable=self.port, state="readonly", width=48)
        self.port_box.grid(row=0, column=0, columnspan=2, sticky="ew", **PAD)
        self.port_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_modes())
        ttk.Button(f, text="Refresh", command=lambda: (self.refresh_ports(),
                                                       self.refresh_modes())).grid(row=1, column=0,
                                                                                   **PAD)
        ttk.Button(f, text="Check", command=self.check_port).grid(row=1, column=1, **PAD)
        self.port_msg = ttk.Label(f, text="", width=56)
        self.port_msg.grid(row=2, column=0, columnspan=2, sticky="w", **PAD)

        # ---- projector mode (offline: the board generates the video) ----------------------
        g = self.mode_frame = ttk.LabelFrame(
            self, text="Projector resolution and frame rate (board's own video)")
        g.grid(row=1, column=0, sticky="ew", **PAD)
        self.mode = tk.StringVar()
        self.mode_box = ttk.Combobox(g, textvariable=self.mode, state="readonly", width=48)
        self.mode_box.grid(row=0, column=0, columnspan=2, sticky="ew", **PAD)
        ttk.Button(g, text="Ask the board", command=self.refresh_modes).grid(row=1, column=0,
                                                                          **PAD)
        self.mode_msg = ttk.Label(g, text="", width=56)
        self.mode_msg.grid(row=2, column=0, columnspan=2, sticky="w", **PAD)

        # ---- OK / Cancel -------------------------------------------------------------
        b = ttk.Frame(self)
        b.grid(row=2, column=0, sticky="ew", **PAD)
        self.msg = ttk.Label(b, text="", foreground="#b00020", wraplength=330)
        self.msg.pack(side="left", fill="x", expand=True)
        ttk.Button(b, text="Cancel", command=self.cancel).pack(side="right", padx=4)
        self.ok_btn = ttk.Button(b, text="OK", command=self.ok, default="active")
        self.ok_btn.pack(side="right", padx=4)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.cancel())

        self.ports, self.modes, self.cur_idx, self.proj = [], [], None, None
        self.want_port, self.want_proj = port, projector
        self.refresh_ports()
        self.refresh_modes()
        self.grab_set()
        self.focus_force()

    # ---- COM port --------------------------------------------------------------------
    def refresh_ports(self):
        self.ports = sorted(list_ports.comports(), key=lambda p: p.device)
        self.port_box["values"] = ["%s  -  %s" % (p.device, p.description) for p in self.ports]
        if self.ports:
            names = [p.device for p in self.ports]
            cur = self.port_device() or self.want_port
            self.port_box.current(names.index(cur) if cur in names else 0)
            self.port_msg["text"] = "%d port(s) found" % len(self.ports)
        else:
            self.port.set("")
            self.port_msg["text"] = "no serial ports -- is the board plugged in?"

    def port_device(self):
        return self.port.get().split()[0] if self.port.get() else None

    def check_port(self):
        dev = self.port_device()
        if not dev:
            return
        try:
            with serial.Serial(dev, 115200, timeout=0.05) as ser:
                time.sleep(0.3)
                v = [rd(ser, a) for a in (0x4A, 0x4B, 0x4C, 0x5D)]
        except serial.SerialException as e:
            self.port_msg["text"] = "%s: cannot open (%s)" % (dev, e.__class__.__name__)
            return
        if None in v:
            self.port_msg["text"] = "%s: no reply -- not the camera board?" % dev
            return
        T = (v[0] | (v[1] << 8) | (v[2] << 16)) * TICK_US
        gl = v[3]
        state = "live" if (gl & 3) == 3 else ("enabled, NOT live" if gl & 1 else "OFF")
        self.port_msg["text"] = "%s: frame %.1f us (%.2f Hz), genlock %s (0x%02X)" % (
            dev, T, 1e6 / T if T else 0, state, gl)

    # ---- projector mode --------------------------------------------------------------
    def refresh_modes(self):
        """The modes the projector's EDID supports (regs 0x29/0x2A), the one in use marked
        and selected."""
        dev = self.port_device()
        self.modes, self.cur_idx = [], None
        self.mode_box["values"] = []
        self.mode.set("")
        if not dev:
            self.mode_msg["text"] = "choose a COM port first"
            return
        try:
            with serial.Serial(dev, 115200, timeout=0.05) as ser:
                time.sleep(0.3)
                self.modes, self.cur_idx = board_modes(ser)
                passthru = reg(ser, 0x02) & 0x10
                self.proj = projector_name(ser)   # from the projector's EDID
        except serial.SerialException as e:
            self.mode_msg["text"] = "%s: cannot open (%s)" % (dev, e.__class__.__name__)
            return
        self.mode_frame["text"] = ("Projector: %s" % self.proj if self.proj else
                                   "Projector: (no EDID name)")
        if not self.modes:
            self.mode_msg["text"] = "%s: no modes -- not the camera board, or no projector?" % dev
            return
        self.mode_box["values"] = [mode_name(m) + ("   (in use)" if m["idx"] == self.cur_idx
                                                      else "") for m in self.modes]
        idx = [m["idx"] for m in self.modes]
        saved = self.want_proj["idx"] if self.want_proj else None
        pick = saved if saved in idx else self.cur_idx
        self.mode_box.current(idx.index(pick) if pick in idx else 0)
        note = []
        if saved is not None:
            sm = mode_table()[saved]
            note.append("the document's mode, %s, %s" % (
                mode_name(sm), "is selected" if saved in idx else
                "is NOT supported by this projector"))
            if self.want_proj.get("name") != self.proj:
                note.append("document projector %s, attached %s"
                            % (self.want_proj.get("name"), self.proj))
        if passthru:
            note.append("PC video is plugged in: unplug it to run offline")
        self.mode_msg["text"] = "%d modes the projector supports%s" % (
            len(self.modes), "".join("\n" + n for n in note))

    # ---- OK / Cancel -----------------------------------------------------------------
    def ok(self):
        port = self.port_device()
        if not port or not self.modes:
            self.msg["text"] = "choose a COM port and a projector mode"
            return
        want = self.modes[self.mode_box.current()]
        # What the document records as its video: the projector and the mode, offline.
        disp = "%s, %s, offline" % (self.proj or "projector", mode_name(want))
        self.msg["text"] = ("checking..." if want["idx"] == self.cur_idx else
                            "switching to %s..." % mode_name(want))
        self.ok_btn.state(["disabled"])
        self.update_idletasks()
        try:
            ser, T, mx = connect_board(port, want["idx"], self.cur_idx)
        except Exception as e:                       # serial, Ft+, board: show, stay
            self.msg["text"] = str(e)
            self.ok_btn.state(["!disabled"])
            self.refresh_modes()
            return
        proj = dict(name=self.proj, **{k: want[k] for k in ("idx", "w", "h", "hz", "khz")})
        self.result = (ser, T, mx, port, disp, proj)
        self.grab_release()
        self.destroy()

    def cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()


def connect_board(port, idx, cur_idx):
    """Put the board in mode `idx` (if it is not there already), then the offline
    preflight. Returns (ser, T, mx); raises on any failure."""
    if idx != cur_idx:
        with serial.Serial(port, 115200, timeout=0.05) as ser:
            time.sleep(0.3)
            set_board_mode(ser, idx)
    ser, T, mx = preflight(port)
    SETTINGS["active_us"] = active_us(ser, T)     # vsync -> first active line
    return ser, T, mx


def reconnect(doc):
    """Connect a reopened document without the dialog: its saved COM port, the same
    projector (EDID name) and its saved mode. Returns (ser, T, mx, port, display,
    projector) like SetupDialog, or None when anything differs or fails -- then the
    dialog is shown."""
    proj = doc.projector
    if not doc.port or not proj or proj.get("idx") is None:
        return None
    try:
        with serial.Serial(doc.port, 115200, timeout=0.05) as ser:
            time.sleep(0.3)
            modes, cur = board_modes(ser)
            name = projector_name(ser)
        if name != proj.get("name") or proj["idx"] not in [m["idx"] for m in modes]:
            return None
        ser, T, mx = connect_board(doc.port, proj["idx"], cur)
    except Exception:
        return None
    m = next(m for m in modes if m["idx"] == proj["idx"])
    disp = "%s, %s, offline" % (name or "projector", mode_name(m))
    return ser, T, mx, doc.port, disp, dict(proj)


# ======================================================================================
# Shared plot pieces
# ======================================================================================
# Room around the axes in PIXELS. The tick labels, axis labels and "frame k" captions are
# a fixed size in points, so margins given as fractions of the figure clip them when the
# window gets small; these are converted to fractions on every resize (fit_margins).
MARGIN_PX = dict(left=72, right=14, top=30, bottom=52)


def fit_margins(fig):
    w, h = fig.get_size_inches() * fig.dpi
    m = MARGIN_PX
    # never let the margins eat the plot: keep at least 20 % of each direction for it
    left = min(m["left"] / w, 0.4)
    right = max(1 - m["right"] / w, left + 0.2)
    bottom = min(m["bottom"] / h, 0.4)
    top = max(1 - m["top"] / h, bottom + 0.2)
    fig.subplots_adjust(left=left, right=right, bottom=bottom, top=top)


def timeline_axes(fig, T):
    fit_margins(fig)
    ax = fig.add_subplot(111)
    for k in range(1, 5):
        ax.axvline(k * T, color="#888", lw=.8)
    for k in range(5):
        ax.text((k + .5) * T, 1.01, "frame %d" % k, transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", fontsize=9, color="#555")
    ax.set_xlim(0, 5 * T)
    ax.set_xlabel("time after the vsync of frame 0 (us)")
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=.3)
    return ax


EDGE_COLORS = dict(lead="#2e9e4f", trail="#7b3fb5", runway="#0891b2", bg="#d97706")


def runway_and_background(ax, T, rw, lead, trail):
    """The shading every tab draws under the edges; returns the artists.
    THE RUNWAY (cyan): light before the exposure, rw -> lead.
    THE BACKGROUND EXPOSURE (orange, hatched, dashed ends): the rest of the frame, from
    the trailing edge to the next leading edge (trail -> lead + T), as it repeats in
    every frame. Wraps at 5T."""
    art = []
    if rw < lead:
        art.append(ax.axvspan(rw, lead, color=EDGE_COLORS["runway"], alpha=.15, lw=0))
    T5, b0 = 5 * T, trail % T
    bw = max(0.0, lead + T - trail) if trail - lead < T else 0.0
    for f in range(5):
        s_, e_ = f * T + b0, f * T + b0 + bw
        for x0, x1 in ([(s_, e_)] if e_ <= T5 else [(s_, T5), (0.0, e_ - T5)]):
            art.append(ax.axvspan(x0, x1, color=EDGE_COLORS["bg"], alpha=.35, lw=0,
                                  hatch="///", fill=False))
        for x in (s_, e_ % T5 if e_ > T5 else e_):
            art.append(ax.axvline(x, color=EDGE_COLORS["bg"], lw=1.0, ls="--"))
    return art


# ======================================================================================
# Running a sweep (no Tk in here except through the queue)
# ======================================================================================
class SweepRunner:
    """One sweep in a worker thread. The view drains `q` on the Tk thread."""

    def __init__(self, key):
        self.key = key
        self.title, self.level, self.bg = ALL_SWEEPS[key]
        self.worker = None
        self.stop_ev = threading.Event()
        self.q = queue.Queue()
        self.log_csv = None
        self.t0 = 0.0

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self, doc):
        """Program the impulse cycle and start the worker. Raises on a bad setup."""
        e = start_impulse(doc.ser, self.level, self.bg, SETTINGS["expo_us"], doc.mx)
        delays = delays_for(doc.T, e, SETTINGS["step_us"])
        self.log_csv = os.path.join(tempfile.gettempdir(), "auv2sli_%s_%s.csv"
                                    % (self.key, time.strftime("%Y-%m-%d_%H%M%S")))
        doc.sweeps[self.key] = {"settings": dict(SETTINGS, level=self.level, bg=self.bg,
                                                 expo_real_us=e, port=doc.port,
                                                 display=doc.display),
                                "rows": [], "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "complete": False}
        self.stop_ev.clear()
        self.t0 = time.time()
        self.worker = threading.Thread(target=self.work, args=(doc.ser, doc.T, e, delays),
                                       daemon=True)
        self.worker.start()

    def work(self, ser, T, e, delays):
        try:
            for i, n, d, means, ref, rows in sweep(
                    ser, T, e, delays, self.log_csv, self.level, self.bg,
                    SETTINGS["frames"], SETTINGS["settle"], SETTINGS["active_us"],
                    stop=self.stop_ev, log=lambda s: self.q.put(("log", s))):
                self.q.put(("rows", (i, n, rows)))
            self.q.put(("end", None))
        except Exception as ex:
            self.q.put(("error", ex))
        finally:
            try:
                stop_impulse(ser)
            except Exception:
                pass

    def stop(self):
        self.stop_ev.set()

    def shutdown(self):
        """Stop and wait; the worker takes the camera back out of impulse mode."""
        if self.running:
            self.stop_ev.set()
            self.worker.join(timeout=15)


# ======================================================================================
# Running sweeps from a view: both tabs start, follow and stop them the same way
# ======================================================================================
class SweepHost:
    """For a ttk.Frame with self.app, self.runners {key: SweepRunner}, self.status and
    buttons(). One sweep at a time in the whole app (App.busy)."""

    def busy_runner(self):
        return next((r for r in self.runners.values() if r.running), None)

    def run(self, key):
        doc = self.app.doc
        title = ALL_SWEEPS[key][0]
        if self.app.busy():
            return
        if doc.sweeps.get(key) and not messagebox.askokcancel(
                "Replace sweep", "Run a new %s sweep and replace the one in this document?"
                % title, parent=self):
            return
        if not self.app.ensure_connected():
            return
        r = self.runners[key]
        try:
            r.start(doc)
        except Exception as ex:
            messagebox.showerror("Cannot start", str(ex), parent=self)
            return
        self.app.changed()
        self.after(200, lambda: self.poll(r))

    def poll(self, r):
        sw = self.app.doc.sweeps.get(r.key)
        last = None
        while True:
            try:
                kind, v = r.q.get_nowait()
            except queue.Empty:
                break
            if kind == "rows":
                i, n, rows = v
                sw["rows"] += rows
                last = (i, n)
            elif kind == "log":
                print(v, flush=True)
            else:
                self.finish(r, sw, v if kind == "error" else None)
                return
        if last:
            self.app.changed()
            i, n = last
            el = time.time() - r.t0
            sat = sum(float(x[5]) >= 1000 for x in sw["rows"])
            self.status["text"] = (
                "%s running: delay %d/%d   %.0f s elapsed, ~%.0f s left   saturated %d/%d   "
                "log %s" % (r.title, i + 1, n, el, el / (i + 1) * (n - i - 1), sat,
                            len(sw["rows"]), r.log_csv))
        self.after(200, lambda: self.poll(r))

    def stop(self):
        r = self.busy_runner()
        if r:
            r.stop()
            self.buttons()
            self.status["text"] = "%s: stopping after this delay..." % r.title

    def finish(self, r, sw, err):
        r.worker.join(timeout=5)
        sw["complete"] = err is None and not r.stop_ev.is_set()
        self.app.changed()
        if err is not None:
            self.status["text"] = "%s ERROR: %s   (%d readings kept; log %s)" % (
                r.title, err, len(sw["rows"]), r.log_csv)



# ======================================================================================
# The profile view: both sweeps on one plot, the exposure edges on top
# ======================================================================================
class ProfileView(SweepHost, ttk.Frame):
    """WWWWW (blue) and WKKKK (red) on one timeline, with the exposure edges.

    MOUSE. Wheel zooms the time axis around the cursor; only time zooms and pans -- the
    ADU axis always spans every value the sweeps have read (fit_y), so a live sweep grows
    it. Dragging empty plot pans in time, and a flick keeps it gliding (momentum).
    Double-click returns to the whole 5-frame view. The edges are vertical lines with a
    grab box near the top; drag the box. The boxes sit at a fixed height in the plot and
    are a fixed size on screen, so zooming never loses them -- panning one out of the
    visible range clips it away like any other data.

    THE RUNWAY (cyan, dash-dot, lower box) marks where projector light may start before
    the exposure: the runway runs from it to the lead. It is always at or before the lead
    and moves with it; dragged on its own it stops at the lead and one frame before it.
    Until placed it is found from WKKKK (light above 2 % of peak).
    """

    COLORS = {"wwwww": "#1f5fd0", "wkkkk": "#d02a2a"}
    LEAD, TRAIL = EDGE_COLORS["lead"], EDGE_COLORS["trail"]
    RW = EDGE_COLORS["runway"]                       # runway: projector light before the lead
    BG = EDGE_COLORS["bg"]                           # background exposure (trail -> next lead)
    BOX_Y = 0.93                                     # grab boxes, in axes fraction
    RW_Y = 0.85                                      # the runway box sits lower, so it can
                                                     # be grabbed even on top of the lead
    GRAB_PX = 10
    FRICTION = 4.0                                   # momentum decays as exp(-FRICTION t)
    TICK_MS = 16

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.runners = {k: SweepRunner(k) for k in SWEEPS}
        self.auto = True                             # view follows the data until touched
        self.drag = None                             # "runway" / "lead" / "trail" / "pan"
        self.momentum = None
        self.sig_version = None
        self.iris_dlg = None
        self.sig = None

        # ---- controls ----------------------------------------------------------------
        bar = ttk.Frame(self)
        bar.pack(fill="x", **PAD)
        self.run_btns = {}
        for k in SWEEPS:
            b = ttk.Button(bar, text="Run %s" % SWEEPS[k][0], command=lambda k=k: self.run(k))
            b.pack(side="left", padx=(0, 4))
            self.run_btns[k] = b
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop)
        self.stop_btn.pack(side="left", padx=(0, 4))
        self.iris_btn = ttk.Button(bar, text="Adjust iris...", command=self.adjust_iris)
        self.iris_btn.pack(side="left", padx=(0, 16))
        ttk.Label(bar, text="runway (us)").pack(side="left")
        self.rw_v, self.lead_v, self.trail_v = tk.StringVar(), tk.StringVar(), tk.StringVar()
        rwe = ttk.Entry(bar, textvariable=self.rw_v, width=10)
        rwe.pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="leading edge (us)").pack(side="left")
        le = ttk.Entry(bar, textvariable=self.lead_v, width=10)
        le.pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="trailing edge (us)").pack(side="left")
        te = ttk.Entry(bar, textvariable=self.trail_v, width=10)
        te.pack(side="left", padx=(4, 12))
        for w in (rwe, le, te):
            w.bind("<Return>", lambda e: self.typed())
            w.bind("<FocusOut>", lambda e: self.typed())
        self.best_btn = ttk.Button(bar, text="Best window", command=self.best)
        self.best_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Whole view", command=self.reset_view).pack(side="right")
        self.status = ttk.Label(self, text="", font=("Consolas", 9))
        self.status.pack(fill="x", padx=8)
        self.readout = ttk.Label(self, text="", font=("Consolas", 9))
        self.readout.pack(fill="x", padx=8)
        self.cursor = ttk.Label(self, text="", font=("Consolas", 9))
        self.cursor.pack(fill="x", padx=8)

        # ---- plot --------------------------------------------------------------------
        self.fig = Figure(figsize=(13, 6))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        for ev, fn in (("button_press_event", self.press), ("motion_notify_event", self.motion),
                       ("button_release_event", self.release), ("scroll_event", self.scroll),
                       ("resize_event", lambda e: fit_margins(self.fig)),
                       ("axes_leave_event", lambda e: self.cursor.config(text=""))):
            self.canvas.mpl_connect(ev, fn)

        self.T = None
        self.build()

    # ---- building / drawing ----------------------------------------------------------
    def build(self):
        """(Re)create the axes for the document's frame period."""
        doc = self.app.doc
        self.T = doc.T or 8333.3
        # THE 120 FPS LIMIT, from the frame period exactly as the FPGA computes MAXEXP
        # (usb_link.v): T - 80 us sensor gap - 10 us margin, in whole 0.375 us units.
        # The board's own answer wins when connected; longer drops the camera to 60 fps.
        self.max_us = (doc.mx or max_expo_units(self.T)) * EXPO_UNIT_US
        self.fig.clear()
        self.bg_art = []                             # cleared with the figure
        ax = self.ax = timeline_axes(self.fig, self.T)
        self.lines = {k: ax.plot([], [], lw=.7, color=self.COLORS[k], label=SWEEPS[k][0])[0]
                      for k in SWEEPS}
        ax.legend(loc="upper right")
        bt = blended_transform_factory(ax.transData, ax.transAxes)
        self.span = ax.axvspan(0, 1, color=self.LEAD, alpha=.10, lw=0)
        self.l_rw = ax.axvline(0, color=self.RW, lw=1.4, ls="-.")
        self.b_rw, = ax.plot([0], [self.RW_Y], marker="s", ms=11, color=self.RW,
                              mec="white", mew=1.2, transform=bt, clip_on=True, zorder=5)
        self.l_lead = ax.axvline(0, color=self.LEAD, lw=1.4)
        self.l_trail = ax.axvline(0, color=self.TRAIL, lw=1.4)
        self.b_lead, = ax.plot([0], [self.BOX_Y], marker="s", ms=11, color=self.LEAD,
                               mec="white", mew=1.2, transform=bt, clip_on=True, zorder=5)
        self.b_trail, = ax.plot([0], [self.BOX_Y], marker="s", ms=11, color=self.TRAIL,
                                mec="white", mew=1.2, transform=bt, clip_on=True, zorder=5)
        e = doc.edges
        self.edges_auto = not e                      # follow the best window until touched
        self.lead, self.trail = (e["lead_us"], e["trail_us"]) if e else (0.0, 0.0)
        # The runway start is kept as its distance before the lead; None until placed (or in
        # profiles saved before 2026-10-16), and then it is found from the WKKKK sweep.
        self.rw_gap = (e["lead_us"] - e["runway_us"]) if e and "runway_us" in e else None
        self.refresh()

    def refresh(self):
        """Redraw from the document: traces, edges, buttons, status."""
        doc = self.app.doc
        if (doc.T or 8333.3) != self.T:
            self.build()
            return
        for k, ln in self.lines.items():
            p = doc.points(k)
            ln.set_data([q[0] for q in p], [q[1] for q in p])
        self.update_sig()
        if self.edges_auto:
            self.lead = (self.best_start(self.max_us) if self.sig is not None
                         else SETTINGS["active_us"])
            self.trail = self.lead + self.max_us
        if self.edges_auto or self.rw_gap is None:
            self.rw = self.lead - self.auto_rw_gap()
        else:
            self.rw = min(max(self.lead - self.rw_gap, self.rw_lo()), self.lead)
        self.place_edges()
        if self.auto:
            self.ax.set_xlim(0, 5 * self.T)
        self.fit_y()
        self.buttons()
        if not self.busy_runner():
            self.status["text"] = "   ".join(
                "%s: %s" % (SWEEPS[k][0], self.summary(doc.sweeps[k]) if doc.sweeps.get(k)
                            else "none -- Run or Sweep > Import") for k in SWEEPS)
        self.canvas.draw_idle()

    def fit_y(self):
        """The ADU axis always spans every value the sweeps have read, whatever the zoom."""
        ys = [np.asarray(ln.get_ydata(), float) for ln in self.lines.values()]
        ys = np.concatenate([y for y in ys if y.size] or [np.array([0.0, 1023.0])])
        lo, hi = float(np.min(ys)), float(np.max(ys))
        pad = 0.05 * (hi - lo or 1.0)
        self.ax.set_ylim(lo - pad, hi + pad)

    @staticmethod
    def summary(sw):
        rows = sw["rows"]
        sat = sum(float(r[5]) >= 1000 for r in rows)
        src = sw["settings"].get("imported_from")
        return ("%s%d readings, %d saturated, %s %s"
                % ("INCOMPLETE " if not sw.get("complete", True) else "", len(rows), sat,
                   "imported" if src else "measured", sw.get("when", "")))

    def buttons(self):
        r = self.busy_runner()
        for b in list(self.run_btns.values()) + [self.iris_btn]:
            b.state(["disabled"] if self.app.busy() else ["!disabled"])
        self.stop_btn.state(["!disabled"] if r and not r.stop_ev.is_set() else ["disabled"])
        self.best_btn.state(["!disabled"] if self.sig is not None else ["disabled"])
        for v in (self.app.fview, self.app.rview):   # the iris also holds the other tabs
            if v is not None:
                v.buttons()

    # ---- edges -----------------------------------------------------------------------
    def update_sig(self):
        """The white frame's light on a uniform grid: WKKKK minus its dark level (the
        median of frames 2-4, which the white frame never reaches)."""
        imp = self.app.doc.points("wkkkk")
        if len(imp) == (self.sig_version or 0):
            return
        self.sig_version = len(imp)
        if len(imp) < 10:
            self.sig = None
            return
        T = self.T
        t = np.array([q[0] for q in imp])
        v = np.array([q[1] for q in imp])
        dark = float(np.median(v[t >= 2 * T])) if np.any(t >= 2 * T) else float(v.min())
        self.dt = float(np.median(np.diff(t))) or 5.0
        self.grid = np.arange(0.0, 5 * T, self.dt)
        self.sig = np.clip(np.interp(self.grid, t, v - dark, period=5 * T), 0, None)
        self.total = float(self.sig.sum())

    def captured(self, a, b):
        """Share of the white frame's light between a and b (circular over 5 frames)."""
        if self.sig is None or self.total <= 0:
            return None
        idx = np.arange(int(round(a / self.dt)), int(round(b / self.dt))) % len(self.grid)
        return float(self.sig[idx].sum() / self.total)

    def best_start(self, width):
        """Start of the `width`-wide window holding the most of the white frame's light."""
        n = max(1, int(round(width / self.dt)))
        ext = np.concatenate([self.sig, self.sig[:n]])
        box = np.convolve(ext, np.ones(n), mode="valid")[:len(self.sig)]
        return float(self.grid[int(np.argmax(box))])

    def auto_rw_gap(self):
        """How far before the lead the white frame's light starts: walk back from the lead
        while the WKKKK light stays above 2 % of its peak, no more than one frame. 0 without
        a WKKKK sweep."""
        if self.sig is None or self.sig.max() <= 0:
            return 0.0
        thr, i0 = 0.02 * float(self.sig.max()), int(round(self.lead / self.dt))
        room = int(self.T / self.dt)
        n = 0
        while n < room and self.sig[(i0 - n - 1) % len(self.sig)] > thr:
            n += 1
        return min(n * self.dt, self.lead)

    def rw_lo(self):
        """The runway can reach back one frame before the lead, into the previous frame."""
        return max(0.0, self.lead - self.T)

    def set_edges(self, lead, trail, moving="trail"):
        """Edges, with the exposure held to the limit. Moving the lead (green)
        carries the trail (purple) and the runway start (cyan) with it; moving the
        trail keeps the lead anchored and stops the trail at the limit."""
        w = self.max_us
        gap = self.lead - self.rw
        if moving == "lead":
            width = min(max(self.trail - self.lead, EXPO_UNIT_US), w)
            lead = min(max(lead, 0.0), 5 * self.T)
            trail = lead + width
        else:
            lead = min(max(lead, 0.0), 5 * self.T)
            trail = min(max(trail, lead + EXPO_UNIT_US), lead + w)
        self.lead, self.trail = lead, trail
        self.store_edges(min(max(lead - gap, self.rw_lo()), lead))

    def set_runway(self, x):
        """The runway start: always at or before the lead, within one frame of it."""
        self.store_edges(min(max(x, self.rw_lo()), self.lead))

    def store_edges(self, rw):
        self.rw, self.rw_gap = rw, self.lead - rw
        self.edges_auto = False
        self.app.doc.edges = {"lead_us": round(self.lead, 2), "trail_us": round(self.trail, 2),
                              "runway_us": round(rw, 2)}
        self.app.changed(redraw=False)
        self.place_edges()
        self.canvas.draw_idle()

    def place_edges(self):
        lead, trail, rw = self.lead, self.trail, self.rw
        self.rw_v.set("%.1f" % rw)
        self.lead_v.set("%.1f" % lead)
        self.trail_v.set("%.1f" % trail)
        self.l_rw.set_xdata([rw, rw])
        self.b_rw.set_xdata([rw])
        self.l_lead.set_xdata([lead, lead])
        self.l_trail.set_xdata([trail, trail])
        self.b_lead.set_xdata([lead])
        self.b_trail.set_xdata([trail])
        self.span.remove()
        self.span = self.ax.axvspan(lead, trail, color=self.LEAD, alpha=.15, lw=0)
        for a_ in getattr(self, "bg_art", []):
            a_.remove()
        self.bg_art = runway_and_background(self.ax, self.T, rw, lead, trail)
        fa, fb = lead, trail
        b0 = trail % self.T
        bw = max(0.0, lead + self.T - trail) if trail - lead < self.T else 0.0
        units = int((fb - fa) / EXPO_UNIT_US)
        expo = units * EXPO_UNIT_US
        k = int(fa // self.T)
        over = expo > self.max_us
        cap = self.captured(fa, fb)
        rcap = self.captured(rw, lead) if rw < lead else (0.0 if cap is not None else None)
        bu = int(bw / EXPO_UNIT_US)
        self.readout["text"] = (
            "foreground: delay %.2f us (frame %d + %.2f us)   %.3f us = %d units"
            "   white-frame light inside %s   limit %.2f us (T - 90 us, 120 fps)%s\n"
            "background (dashed, every frame): delay %.2f us into the frame   %.3f us = %d units"
            "   ends %.2f us into the frame\n"
            "runway (cyan): starts %.2f us (frame %d + %.2f us), %.2f us before the exposure"
            "   white-frame light on it %s"
            % (fa, k, fa - k * self.T, expo, units,
               "%.1f %%" % (100 * cap) if cap is not None else "- (needs WKKKK)",
               self.max_us, "   OVER THE LIMIT" if over else "",
               b0, bu * EXPO_UNIT_US, bu, (b0 + bu * EXPO_UNIT_US) % self.T,
               rw, int(rw // self.T), rw - int(rw // self.T) * self.T, lead - rw,
               "%.1f %%" % (100 * rcap) if rcap is not None else "- (needs WKKKK)"))
        self.readout["foreground"] = "#b00020" if over else ""

    def typed(self):
        try:
            lead, trail = float(self.lead_v.get()), float(self.trail_v.get())
            rw = float(self.rw_v.get())
        except ValueError:
            lead, trail, rw = self.lead, self.trail, self.rw
        if (lead, trail) != (self.lead, self.trail) or trail - lead > self.max_us:
            self.set_edges(lead, trail, "lead" if lead != self.lead else "trail")
        elif abs(rw - self.rw) > 1e-6:
            self.set_runway(rw)
        else:
            self.place_edges()

    def best(self):
        """Keep the current exposure (capped at the limit), move it to the most light."""
        if self.sig is None:
            return
        w = min(self.trail - self.lead, self.max_us)
        s = self.best_start(w)
        self.set_edges(s, s + w)

    # ---- mouse: drag edges, pan with momentum, wheel zoom ----------------------------
    def box_px(self, box):
        return box.get_transform().transform((box.get_xdata()[0], box.get_ydata()[0]))

    def data_x(self, px):
        return self.ax.transData.inverted().transform((px, 0))[0]

    def press(self, ev):
        if ev.button != 1 or ev.inaxes is not self.ax:
            return
        self.stop_momentum()
        if ev.dblclick:
            self.reset_view()
            return
        x0, x1 = self.ax.get_xlim()
        for name, box, x in (("lead", self.b_lead, self.lead), ("trail", self.b_trail, self.trail),
                             ("runway", self.b_rw, self.rw)):
            if x0 <= x <= x1:
                bx, by = self.box_px(box)
                if abs(bx - ev.x) <= self.GRAB_PX and abs(by - ev.y) <= self.GRAB_PX:
                    self.drag = name
                    self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow")
                    return
        self.drag = "pan"
        self.pan0 = (ev.x, self.ax.get_xlim())
        self.samples = [(time.time(), ev.x)]
        self.canvas.get_tk_widget().config(cursor="fleur")

    def show_cursor(self, ev):
        """The mouse position in data units: time (and frame + offset) and ADU."""
        if ev.inaxes is not self.ax:
            return
        x, y = self.ax.transData.inverted().transform((ev.x, ev.y))
        k = int(x // self.T)
        self.cursor["text"] = ("cursor: t %.2f us (frame %d + %.2f us)   %.1f ADU"
                               % (x, k, x - k * self.T, y))

    def motion(self, ev):
        self.show_cursor(ev)
        if self.drag in ("lead", "trail", "runway"):
            x = self.data_x(ev.x)
            if self.drag == "runway":
                self.set_runway(x)
            elif self.drag == "lead":
                self.set_edges(x, self.trail, "lead")
            else:
                self.set_edges(self.lead, x)
        elif self.drag == "pan":
            x0, xl = self.pan0
            self.pan_to(xl, ev.x - x0)
            self.samples = (self.samples + [(time.time(), ev.x)])[-6:]
        else:                                        # hover: show which boxes grab
            over = False
            if ev.inaxes is self.ax:
                for box in (self.b_lead, self.b_trail, self.b_rw):
                    bx, by = self.box_px(box)
                    if abs(bx - ev.x) <= self.GRAB_PX and abs(by - ev.y) <= self.GRAB_PX:
                        over = True
            self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow" if over else "")

    def release(self, ev):
        if self.drag == "pan" and len(self.samples) >= 2:
            (t0, xa), (t1, xb) = self.samples[0], self.samples[-1]
            if time.time() - t1 < 0.08 and t1 > t0:  # still moving when released: glide
                self.start_momentum((xb - xa) / (t1 - t0))
        self.drag = None
        self.canvas.get_tk_widget().config(cursor="")

    def set_x(self, lo, hi):
        """Set the time axis, kept inside frame 0's vsync .. the end of frame 4: no wider
        than the five frames, and slid back in if it hangs over either end. Returns True
        if it had to be held back (so a glide can stop at the wall)."""
        end = 5 * self.T
        w = min(hi - lo, end)
        lo2 = min(max(lo, 0.0), end - w)
        self.ax.set_xlim(lo2, lo2 + w)
        return abs(lo2 - lo) > 1e-9 or abs(w - (hi - lo)) > 1e-9

    def pan_to(self, xl, dx_px):
        """Set the time axis to xl shifted by a pixel offset. The ADU axis does not pan.
        Returns True when the view hit the start or end of the five frames."""
        sx = (xl[1] - xl[0]) / self.ax.bbox.width
        hit = self.set_x(xl[0] - dx_px * sx, xl[1] - dx_px * sx)
        self.auto = False
        self.canvas.draw_idle()
        return hit

    def start_momentum(self, vx):
        if abs(vx) < 150:                            # px/s: a slow release just stops
            return
        self.momentum = [vx, time.time()]
        self.after(self.TICK_MS, self.glide)

    def glide(self):
        if self.momentum is None:
            return
        vx, t = self.momentum
        now = time.time()
        hit = self.pan_to(self.ax.get_xlim(), vx * (now - t))
        vx *= np.exp(-self.FRICTION * (now - t))
        if hit or abs(vx) < 20:                      # stop at the first / last frame
            self.momentum = None
            return
        self.momentum = [vx, now]
        self.after(self.TICK_MS, self.glide)

    def stop_momentum(self):
        self.momentum = None

    def scroll(self, ev):
        if ev.inaxes is not self.ax:
            return
        self.stop_momentum()
        f = 0.8 if ev.button == "up" else 1 / 0.8    # up = zoom in; time axis only
        x0, x1 = self.ax.get_xlim()
        c = ev.xdata
        if (x1 - x0) * f < 2.0 and f < 1:             # no closer than 2 us across
            return
        self.set_x(c - (c - x0) * f, c + (x1 - c) * f)
        self.auto = False
        self.canvas.draw_idle()

    def reset_view(self):
        self.stop_momentum()
        self.auto = True
        self.ax.set_xlim(0, 5 * self.T)
        self.canvas.draw_idle()

    def shutdown(self):
        self.stop_momentum()
        if self.iris_dlg is not None:
            self.iris_dlg.done()
        for r in self.runners.values():
            r.shutdown()

    @property
    def running(self):
        return self.busy_runner() is not None or self.iris_dlg is not None

    # ---- iris ------------------------------------------------------------------------
    def peak_delay(self):
        """Delay (us after vsync, within one frame) of the brightest WWWWW reading, or None.

        The five positions are all white, so the sweep is folded onto one frame and
        averaged per delay. When the brightest readings clip at 1023 there is no single
        maximum: the middle of the longest run of clipped delays is the one most surely
        inside a bright slot, so that is taken.
        """
        sw = self.app.doc.sweeps.get("wwwww")
        if not sw or not sw["rows"]:
            return None
        acc = {}
        for r in sw["rows"]:
            acc.setdefault(round(float(r[2]), 2), []).append(float(r[5]))
        ds = sorted(acc)
        avg = np.array([np.mean(acc[d]) for d in ds])
        top = avg.max()
        hot = avg >= top - 0.5
        best, i = None, 0
        while i < len(ds):
            if not hot[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(ds) and hot[j + 1]:
                j += 1
            if best is None or j - i > best[1] - best[0]:
                best = (i, j)
            i = j + 1
        return float(ds[(best[0] + best[1]) // 2]), float(top)

    def adjust_iris(self):
        if self.running or not self.app.ensure_connected():
            return
        pk = self.peak_delay()
        if pk is None:
            messagebox.showinfo("Adjust iris", "Run (or import) a WWWWW sweep first -- the "
                                "iris is set at its brightest delay.", parent=self)
            return
        self.iris_dlg = IrisDialog(self, self.app.doc, pk[0], pk[1])
        self.buttons()

    # ---- import / export (Sweep menu) ------------------------------------------------
    def import_csv(self, key):
        doc = self.app.doc
        title = ALL_SWEEPS[key][0]
        if self.running:
            return
        if doc.sweeps.get(key) and not messagebox.askokcancel(
                "Replace sweep", "Replace this document's %s sweep?" % title, parent=self):
            return
        path = filedialog.askopenfilename(parent=self, initialdir=ROOT,
                                          title="Import %s sweep" % title,
                                          filetypes=[("lag_sweep CSV", "*.csv"), ("All", "*")])
        if not path:
            return
        try:
            doc.import_csv(key, path)
        except Exception as e:
            messagebox.showerror("Import failed", str(e), parent=self)
            return
        self.app.changed()

    def export_csv(self, key):
        if not self.app.doc.sweeps.get(key):
            messagebox.showinfo("Nothing to export", "No %s sweep in this document."
                                % ALL_SWEEPS[key][0], parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, initialdir=ROOT, defaultextension=".csv",
            initialfile="lag_sweep_%s_%s.csv" % (key, time.strftime("%Y-%m-%d")),
            filetypes=[("CSV", "*.csv")])
        if path:
            self.app.doc.export_csv(key, path)

    def export_json(self):
        path = filedialog.asksaveasfilename(
            parent=self, initialdir=ROOT, defaultextension=".json",
            initialfile="exposure_window_%s.json" % time.strftime("%Y-%m-%d"),
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        units = int(round((self.trail - self.lead) / EXPO_UNIT_US))
        cap = self.captured(self.lead, self.trail)
        with open(path, "w") as fh:
            json.dump({"T_us": self.T, "runway_us": round(self.rw, 2),
                       "lead_us": round(self.lead, 2),
                       "trail_us": round(self.trail, 2),
                       "delay_ticks": int(round(self.lead / TICK_US)),
                       "expo_units": units, "expo_us": units * EXPO_UNIT_US,
                       "white_frame_light_inside": None if cap is None else round(cap, 4),
                       "max_expo_us": self.max_us}, fh, indent=2)


# ======================================================================================
# The RGB filters tab: WWWWW through each colour filter, on one plot
# ======================================================================================
class FilterView(SweepHost, ttk.Frame):
    """WWWWW repeated with a red, a green and a blue filter on the lens, on one timeline.

    The filter is changed by hand: mount it, then press its Run button. Each sweep is kept
    in the document under its own key, so a filter can be re-run without touching the
    others. "Show" picks one view: Raw; Normalized (each sweep's dark level -- the
    median of frames 2-4 -- subtracted and its peak scaled to 1); or Fit to white (each
    filter scaled by its least-squares weight so the three add up to WWWWW).
    WWWWW never goes dark, so the dark level is the camera's: the median of frames 2-4
    of the Profile tab's WKKKK sweep (else each sweep's minimum).
    The profile tab's exposure edges and runway are drawn too; dragging them here goes
    through the profile tab's rules.
    Wheel zooms time, drag pans, double-click shows all five frames.
    """

    COLORS = {"wwwww_r": "#d02a2a", "wwwww_g": "#1e9e3a", "wwwww_b": "#1f5fd0"}
    LETTER = {"wwwww_r": "R", "wwwww_g": "G", "wwwww_b": "B"}

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.runners = {k: SweepRunner(k) for k in FILTER_SWEEPS}
        self.pan0 = None

        bar = ttk.Frame(self)
        bar.pack(fill="x", **PAD)
        self.run_btns = {}
        for k in FILTER_SWEEPS:
            b = ttk.Button(bar, text="Scan %s filter (WWWWW)" % self.LETTER[k],
                           command=lambda k=k: self.run(k))
            b.pack(side="left", padx=(0, 4))
            self.run_btns[k] = b
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop)
        self.stop_btn.pack(side="left", padx=(0, 16))
        # ONE VIEW AT A TIME: the raw sweeps, each normalized, or fitted to the white
        self.show_v = tk.StringVar(value="raw")
        ttk.Label(bar, text="show").pack(side="left", padx=(0, 4))
        for val, txt in (("raw", "Raw"), ("norm", "Normalized (dark subtracted, peak = 1)"),
                         ("fit", "Fit to white (WWWWW)")):
            ttk.Radiobutton(bar, text=txt, value=val, variable=self.show_v,
                            command=self.refresh).pack(side="left", padx=(0, 10))
        ttk.Button(bar, text="Whole view", command=self.reset_view).pack(side="right")
        self.status = ttk.Label(self, text="", font=("Consolas", 9))
        self.status.pack(fill="x", padx=8)
        self.fit_info = ttk.Label(self, text="", font=("Consolas", 9))
        self.fit_info.pack(fill="x", padx=8)
        self.cursor = ttk.Label(self, text="", font=("Consolas", 9))
        self.cursor.pack(fill="x", padx=8)

        self.fig = Figure(figsize=(13, 6))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        for ev, fn in (("button_press_event", self.press), ("motion_notify_event", self.motion),
                       ("button_release_event", self.release), ("scroll_event", self.scroll),
                       ("resize_event", lambda e: fit_margins(self.fig)),
                       ("axes_leave_event", lambda e: self.cursor.config(text=""))):
            self.canvas.mpl_connect(ev, fn)
        self.T = None
        self.build()

    def build(self):
        self.T = self.app.doc.T or 8333.3
        self.fig.clear()
        ax = self.ax = timeline_axes(self.fig, self.T)
        self.lines = {k: ax.plot([], [], lw=.8, color=self.COLORS[k],
                                 label=FILTER_SWEEPS[k][0])[0] for k in FILTER_SWEEPS}
        # fit view: the white sweep as a wide grey band UNDER the scaled filter lines, so
        # each filter's segments stay visible where it carries the white's light
        self.l_white, = ax.plot([], [], lw=4.0, color="#c8c8c8", label="WWWWW (white)",
                                zorder=1, solid_capstyle="butt")
        self.l_fit, = ax.plot([], [], lw=.9, ls="--", color="#222", label="fit (sum)",
                              zorder=4)
        for ln in self.lines.values():
            ln.set_zorder(3)
        ax.legend(loc="upper right")
        # the Profile tab's edges, as drawn there, with the same grab boxes. A drag here
        # goes through the Profile tab (set_edges / set_runway), so the rules are shared.
        bt = blended_transform_factory(ax.transData, ax.transAxes)
        self.marks, self.boxes = [], {}
        for k, ls, y in (("runway", "-.", ProfileView.RW_Y), ("lead", "-", ProfileView.BOX_Y),
                         ("trail", "-", ProfileView.BOX_Y)):
            self.marks.append(ax.axvline(0, color=EDGE_COLORS[k], lw=1.4, ls=ls))
            self.boxes[k], = ax.plot([0], [y], marker="s", ms=11, color=EDGE_COLORS[k],
                                     mec="white", mew=1.2, transform=bt, clip_on=True,
                                     zorder=5)
        self.edge_art = []
        self.drag = None
        self.refresh()

    def refresh(self):
        doc = self.app.doc
        if (doc.T or 8333.3) != self.T:
            self.build()
            return
        norm, T = self.show_v.get() == "norm", self.T
        fit = self.fit_white() if self.show_v.get() == "fit" else None
        ys = []
        for k, ln in self.lines.items():
            p = doc.points(k)
            t = np.array([q[0] for q in p])
            v = np.array([q[1] for q in p])
            if fit is not None:
                t, v = fit["t"], fit["parts"][k]
            elif norm and len(v):
                dark = self.cam_dark(v)
                pk = float(v.max()) - dark
                v = (v - dark) / (pk if pk > 0 else 1.0)
            ln.set_data(t, v)
            ln.set_linewidth(1.4 if fit is not None else .8)
            ln.set_label("%s x %.3f" % (FILTER_SWEEPS[k][0], fit["coef"][k]) if fit is not None
                         else FILTER_SWEEPS[k][0])
            if len(v):
                ys.append(v)
        for ln, key, lab in ((self.l_white, "white", "WWWWW (white)"),
                             (self.l_fit, "fit", "fit (sum)")):
            ln.set_data(fit["t"], fit[key]) if fit is not None else ln.set_data([], [])
            ln.set_visible(fit is not None)
            ln.set_label(lab if fit is not None else "_" + lab)   # "_": not in the legend
        if fit is not None:
            ys.append(fit["white"])
            c = fit["coef"]
            self.fit_info["text"] = (
                "fit to white: WWWWW - dark = %.4f R + %.4f G + %.4f B %+.2f ADU  (dark %.2f ADU)"
                "   residual rms %.2f ADU (lit part %.2f), max %.1f ADU at %.1f us   R2 %.5f"
                % (c["wwwww_r"], c["wwwww_g"], c["wwwww_b"], fit["offset"], fit["dark"],
                   fit["rms"], fit["rms_lit"], fit["max"], fit["t_max"], fit["r2"]))
        else:
            self.fit_info["text"] = ("fit to white: needs the WWWWW sweep (Profile tab) and "
                                     "all three filter sweeps" if self.show_v.get() == "fit" else "")
        self.ax.legend(loc="upper right")
        self.place_marks()
        y = np.concatenate(ys) if ys else np.array([0.0, 1.0 if norm else 1023.0])
        lo, hi = float(y.min()), float(y.max())
        pad = 0.05 * (hi - lo or 1.0)
        self.ax.set_ylim(lo - pad, hi + pad)
        self.ax.set_ylabel("ADU above dark" if fit is not None else
                           "dark subtracted, / peak" if norm else "ROI mean (ADU)")
        self.buttons()
        if not self.busy_runner():
            self.status["text"] = "   ".join(
                "%s: %s" % (self.LETTER[k], ProfileView.summary(doc.sweeps[k])
                            if doc.sweeps.get(k) else "none")
                for k in FILTER_SWEEPS) + "   -- mount the filter, then Scan it"
        self.canvas.draw_idle()

    def place_marks(self):
        """The edges, runway and shading, as the Profile tab has them now."""
        pv = self.app.view
        e = (dict(lead_us=pv.lead, trail_us=pv.trail, runway_us=pv.rw)
             if pv is not None and pv.trail > pv.lead else None)
        for m, k in zip(self.marks, ("runway", "lead", "trail")):
            x = e["%s_us" % k] if e else None
            m.set_visible(x is not None)
            self.boxes[k].set_visible(x is not None)
            if x is not None:
                m.set_xdata([x, x])
                self.boxes[k].set_xdata([x])
        for a_ in self.edge_art:
            a_.remove()
        self.edge_art = []
        if e:
            lead, trail = e["lead_us"], e["trail_us"]
            self.edge_art.append(self.ax.axvspan(lead, trail, color=EDGE_COLORS["lead"],
                                                 alpha=.15, lw=0))
            self.edge_art += runway_and_background(self.ax, self.T, e["runway_us"], lead,
                                                   trail)

    def grab(self, ev):
        """The edge whose box is under the mouse, or None."""
        x0, x1 = self.ax.get_xlim()
        for k in ("lead", "trail", "runway"):
            b = self.boxes[k]
            if b.get_visible() and x0 <= b.get_xdata()[0] <= x1:
                bx, by = b.get_transform().transform((b.get_xdata()[0], b.get_ydata()[0]))
                if abs(bx - ev.x) <= ProfileView.GRAB_PX and abs(by - ev.y) <= ProfileView.GRAB_PX:
                    return k
        return None

    def cam_dark(self, v=None):
        """The camera's dark level: frames 2-4 of the Profile tab's WKKKK sweep (dark
        there), else the minimum of v. WWWWW sweeps are never dark themselves."""
        p = self.app.doc.points("wkkkk")
        t = np.array([q[0] for q in p])
        if len(p) >= 10 and np.any(t >= 2 * self.T):
            return float(np.median(np.array([q[1] for q in p])[t >= 2 * self.T]))
        return float(np.min(v)) if v is not None and len(v) else 0.0

    def fit_white(self):
        """WWWWW as a least-squares combination of the three filter sweeps plus a constant,
        all less the camera dark, the filters interpolated onto the white's delays. None
        unless all four sweeps exist."""
        doc = self.app.doc
        p = doc.points("wwwww")
        if len(p) < 10 or not all(len(doc.points(k)) >= 10 for k in FILTER_SWEEPS):
            return None
        dark = self.cam_dark(np.array([q[1] for q in p]))
        t = np.array([q[0] for q in p])
        w = np.array([q[1] for q in p]) - dark
        X = []
        for k in FILTER_SWEEPS:
            q = doc.points(k)
            X.append(np.interp(t, [a_ for a_, _ in q], [b_ - dark for _, b_ in q]))
        X = np.column_stack(X)
        c = np.linalg.lstsq(np.column_stack([X, np.ones_like(w)]), w, rcond=None)[0]
        f = X @ c[:3] + c[3]
        res = w - f
        lit = w > 0.05 * w.max()
        i = int(np.argmax(np.abs(res)))
        return {"t": t, "white": w, "fit": f, "dark": dark, "offset": float(c[3]),
                "coef": dict(zip(FILTER_SWEEPS, c[:3])),
                "parts": {k: c[j] * X[:, j] for j, k in enumerate(FILTER_SWEEPS)},
                "rms": float(np.sqrt(np.mean(res ** 2))),
                "rms_lit": float(np.sqrt(np.mean(res[lit] ** 2))) if lit.any() else 0.0,
                "max": float(abs(res[i])), "t_max": float(t[i]),
                "r2": float(1 - np.sum(res ** 2) / np.sum((w - w.mean()) ** 2))}

    def buttons(self):
        r = self.busy_runner()
        for b in self.run_btns.values():
            b.state(["disabled"] if self.app.busy() else ["!disabled"])
        self.stop_btn.state(["!disabled"] if r and not r.stop_ev.is_set() else ["disabled"])

    def shutdown(self):
        for r in self.runners.values():
            r.shutdown()

    @property
    def running(self):
        return self.busy_runner() is not None

    # ---- mouse: drag edges, wheel zoom, drag pan, double-click whole view -------------
    def press(self, ev):
        if ev.button != 1 or ev.inaxes is not self.ax:
            return
        if ev.dblclick:
            self.reset_view()
            return
        self.drag = self.grab(ev)
        if self.drag:
            self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow")
            return
        self.pan0 = (ev.x, self.ax.get_xlim())

    def motion(self, ev):
        if ev.inaxes is self.ax:
            x, y = self.ax.transData.inverted().transform((ev.x, ev.y))
            k = int(x // self.T)
            self.cursor["text"] = ("cursor: t %.2f us (frame %d + %.2f us)   %.3g"
                                   % (x, k, x - k * self.T, y))
        if self.drag:                                # through the Profile tab's rules
            pv = self.app.view
            x = self.ax.transData.inverted().transform((ev.x, 0))[0]
            if self.drag == "runway":
                pv.set_runway(x)
            elif self.drag == "lead":
                pv.set_edges(x, pv.trail, "lead")
            else:
                pv.set_edges(pv.lead, x)
            self.place_marks()
            self.canvas.draw_idle()
        elif self.pan0 is not None:
            x0, (a, b) = self.pan0
            dx = (ev.x - x0) * (b - a) / self.ax.bbox.width
            self.set_x(a - dx, b - dx)
            self.canvas.draw_idle()
        elif ev.inaxes is self.ax:                   # hover: show which boxes grab
            self.canvas.get_tk_widget().config(
                cursor="sb_h_double_arrow" if self.grab(ev) else "")

    def release(self, ev):
        self.pan0 = self.drag = None
        self.canvas.get_tk_widget().config(cursor="")

    def set_x(self, lo, hi):
        end = 5 * self.T
        w = min(hi - lo, end)
        lo = min(max(lo, 0.0), end - w)
        self.ax.set_xlim(lo, lo + w)

    def scroll(self, ev):
        if ev.inaxes is not self.ax:
            return
        f = 0.8 if ev.button == "up" else 1 / 0.8
        x0, x1 = self.ax.get_xlim()
        c = ev.xdata
        if (x1 - x0) * f < 2.0 and f < 1:
            return
        self.set_x(c - (c - x0) * f, c + (x1 - c) * f)
        self.canvas.draw_idle()

    def reset_view(self):
        self.ax.set_xlim(0, 5 * self.T)
        self.canvas.draw_idle()



RUNWAY_LEVELS = [("every 8th level (33)", list(range(0, 256, 8)) + [255]),
                 ("every 4th level (65)", list(range(0, 256, 4)) + [255]),
                 ("every 2nd level (129)", list(range(0, 256, 2)) + [255]),
                 ("all 256 levels", list(range(256)))]


# ======================================================================================
# The runway maps tab: gray_pair over the runway, one map per part
# ======================================================================================
class RunwayView(ttk.Frame):
    """The runway (profile tab, cyan) measured as grey-pair maps, one per part: the part
    inside the previous frame's exposure and the part between exposures (gray_pair.py
    --runway does the same from the command line).

    The impulse generator alternates two full-field levels A B A B. A reading in the
    runway's frame k sees that frame's level (x) and the level of frames k-1 and k+1,
    which are the same (y): the frame before, and the exposure frame after. Each map
    pixel is the ROI mean for one (y, x) pair; log colour, each map scaled on its own.
    """

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.worker = None
        self.stop_ev = threading.Event()
        self.q = queue.Queue()
        self.t0, self.k_done, self.n_todo, self.nfail = 0.0, 0, 0, 0
        self.log_csv = None

        bar = ttk.Frame(self)
        bar.pack(fill="x", **PAD)
        self.run_btn = ttk.Button(bar, text="Run runway maps", command=self.run)
        self.run_btn.pack(side="left", padx=(0, 4))
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop)
        self.stop_btn.pack(side="left", padx=(0, 16))
        self.lv = tk.StringVar(value=RUNWAY_LEVELS[0][0])
        self.lv_box = ttk.Combobox(bar, textvariable=self.lv, state="readonly", width=24,
                                   values=[n for n, _ in RUNWAY_LEVELS])
        self.lv_box.pack(side="left", padx=(0, 12))
        self.lv_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(bar, text="samples per cell").pack(side="left")
        self.per_v = tk.IntVar(value=10)
        self.per_box = ttk.Spinbox(bar, from_=2, to=100, textvariable=self.per_v, width=5,
                                   command=self.refresh)
        self.per_box.pack(side="left", padx=(4, 12))
        self.status = ttk.Label(self, text="", font=("Consolas", 9))
        self.status.pack(fill="x", padx=8)
        self.info = ttk.Label(self, text="", font=("Consolas", 9))
        self.info.pack(fill="x", padx=8)

        self.fig = Figure(figsize=(13, 6))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.layout = None
        self.refresh()

    # ---- what to measure / what was measured -----------------------------------------
    def windows(self):
        """The current runway's parts [(start, end, name)] (us into its frame), or []."""
        d = self.app.doc
        if not d.T or not d.edges or "runway_us" not in d.edges:
            return []
        try:
            return runway_windows(d.edges, d.T)
        except ValueError:
            return []

    def levels(self):
        return dict(RUNWAY_LEVELS)[self.lv.get()]

    def shown(self):
        """(windows, levels) of the maps on screen: the document's, else the plan."""
        rm = self.app.doc.runway_maps
        if rm:
            return [tuple(w) for w in rm["settings"]["windows"]], rm["settings"]["levels"]
        return self.windows(), self.levels()

    def estimate(self, n_win, L):
        pairs = len(L) * (len(L) + 1) // 2
        try:
            per = max(2, int(self.per_v.get()))
        except (tk.TclError, ValueError):
            per = 10
        return n_win * pairs * (2 * 2 * per + 2 + 3) * (self.app.doc.T or 8333.3) * 1e-6

    # ---- drawing ---------------------------------------------------------------------
    def build(self, win, L):
        from matplotlib.colors import LogNorm
        self.fig.clear()
        self.L, self.idx = L, {lv: i for i, lv in enumerate(L)}
        self.grids = [np.full((len(L), len(L)), np.nan) for _ in win]
        self.ims = []
        n = max(1, len(win))
        self.fig.subplots_adjust(left=0.07, right=0.97, top=0.86, bottom=0.11, wspace=0.3)
        step = max(1, len(L) // 8)
        for i, (s_, e_, name) in enumerate(win):
            ax = self.fig.add_subplot(1, n, i + 1)
            im = ax.imshow(self.grids[i], origin="lower", cmap="magma", aspect="auto",
                           norm=LogNorm(vmin=1, vmax=1000), interpolation="nearest")
            ax.set_xticks(range(0, len(L), step))
            ax.set_xticklabels(L[::step], fontsize=8)
            ax.set_yticks(range(0, len(L), step))
            ax.set_yticklabels(L[::step], fontsize=8)
            ax.set_xlabel("level of the runway's frame")
            ax.set_ylabel("level of the frames before and after (A B A B: same level)")
            ax.set_title("%s\n%.2f .. %.2f us into the frame (%.2f us)"
                         % (name, s_, e_, e_ - s_), fontsize=10, pad=8)
            self.fig.colorbar(im, ax=ax, label="ROI mean (ADU, log scale)")
            self.ims.append(im)
        if not win:
            self.fig.text(0.5, 0.5, "No runway yet: set the edges and the runway in the "
                          "Profile tab.", ha="center", va="center", color="#666")
        self.layout = (tuple(win), tuple(L))

    def refresh(self):
        win, L = self.shown()
        if self.layout != (tuple(win), tuple(L)):
            self.build(win, L)
            rm = self.app.doc.runway_maps
            self.add_rows(rm["rows"] if rm else [])
        self.draw()
        self.buttons()
        if not self.running:
            rm = self.app.doc.runway_maps
            plan = self.windows()
            self.status["text"] = (
                "%s%d cells, measured %s, %s" % (
                    "" if rm.get("complete", True) else "INCOMPLETE ", len(rm["rows"]),
                    rm["when"], self.settings_text(rm["settings"]))
                if rm else "no runway maps yet -- Run measures the runway's two parts")
            self.info["text"] = (
                "runway now: %s   %s, ~%.0f min" % (
                    "   ".join("%s %.2f..%.2f us" % (n, s_, e_) for s_, e_, n in plan),
                    self.lv.get(), self.estimate(len(plan), self.levels()) / 60)
                if plan else "runway: none -- set it in the Profile tab")

    @staticmethod
    def settings_text(st):
        return "%d levels, %d per cell" % (len(st["levels"]), st["per"])

    def add_rows(self, rows):
        delays = [w[0] for w in self.layout[0]]
        for r in rows:
            d, pl, tl, m_ = r[0], int(r[2]), int(r[3]), float(r[4])
            if d in delays and pl in self.idx and tl in self.idx:
                self.grids[delays.index(d)][self.idx[pl], self.idx[tl]] = m_

    def draw(self):
        for g, im in zip(self.grids, self.ims):
            im.set_data(g)
            v = g[np.isfinite(g) & (g > 0)]
            if v.size:
                im.set_clim(max(1.0, float(v.min())), max(float(v.max()), float(v.min()) + 1))
        self.canvas.draw_idle()

    # ---- running ---------------------------------------------------------------------
    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def buttons(self):
        busy = self.app.busy()
        self.run_btn.state(["disabled"] if busy or not self.windows() else ["!disabled"])
        for w in (self.lv_box, self.per_box):
            w.state(["disabled"] if self.running else ["!disabled"])
        self.lv_box.state(["readonly"])
        self.stop_btn.state(["!disabled"] if self.running and not self.stop_ev.is_set()
                            else ["disabled"])

    def run(self):
        doc, win = self.app.doc, self.windows()
        if self.app.busy() or not win:
            return
        if doc.runway_maps and not messagebox.askokcancel(
                "Replace runway maps", "Measure new runway maps and replace the ones in "
                "this document?", parent=self):
            return
        if not self.app.ensure_connected():
            return
        try:
            per = max(2, int(self.per_v.get()))
        except (tk.TclError, ValueError):
            per = 10
        L = self.levels()
        units = {s_: max(1, int((e_ - s_) / EXPO_UNIT_US)) for s_, e_, _ in win}
        if max(units.values()) > doc.mx:
            messagebox.showerror("Cannot start", "A runway part is longer than the %.1f us "
                                 "exposure limit." % (doc.mx * EXPO_UNIT_US), parent=self)
            return
        pairs = [(A, B) for i, A in enumerate(L) for B in L[i:]]
        doc.runway_maps = {"settings": {"windows": [list(w) for w in win], "levels": L,
                                        "per": per, "skip": 2, "edges": dict(doc.edges)},
                           "rows": [], "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "complete": False}
        self.log_csv = os.path.join(tempfile.gettempdir(), "auv2sli_runway_%s.csv"
                                    % time.strftime("%Y-%m-%d_%H%M%S"))
        self.stop_ev.clear()
        self.t0, self.k_done, self.nfail = time.time(), 0, 0
        self.n_todo = len(pairs) * len(win)
        self.layout = None                           # new maps: start from empty grids
        self.worker = threading.Thread(target=self.work, daemon=True,
                                       args=(doc.ser, doc.T, win, units, pairs, per))
        self.worker.start()
        self.app.changed()
        self.after(300, self.poll)

    def work(self, ser, T, win, units, pairs, per):
        try:
            with open(self.log_csv, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(PAIR_CSV_HEAD)
                pairs_on(ser)
                for s_, e_, _ in win:
                    e = units[s_] * EXPO_UNIT_US
                    for A, B, cells, seen in measure_window(ser, T, s_, units[s_], pairs,
                                                            per, 2):
                        rows = [[s_, round(e, 3), pl, tl, round(m_, 3), round(sd, 3), n,
                                 nhi, round(T, 2)] for pl, tl, m_, sd, n, nhi in cells]
                        w.writerows(rows)
                        self.q.put(("rows", (s_, A, B, seen, rows)))
                        if self.stop_ev.is_set():
                            break
                    if self.stop_ev.is_set():
                        break
            self.q.put(("end", None))
        except Exception as ex:
            self.q.put(("error", ex))
        finally:
            try:
                pairs_off(ser)
            except Exception:
                pass

    def poll(self):
        rm = self.app.doc.runway_maps
        new, last = [], None
        while True:
            try:
                kind, v = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "rows":
                d, A, B, seen, rows = v
                new += rows
                self.k_done += 1
                self.nfail += not seen
                last = (d, A, B)
            else:
                self.worker.join(timeout=5)
                rm["rows"] += new
                self.add_rows(new)
                rm["complete"] = kind == "end" and not self.stop_ev.is_set()
                self.app.changed()
                if kind == "error":
                    self.status["text"] = "runway maps ERROR: %s   (%d cells kept; log %s)" % (
                        v, len(rm["rows"]), self.log_csv)
                return
        if new:
            rm["rows"] += new
            self.add_rows(new)
            self.draw()
            self.app.doc.dirty = True
            self.app.update_title()
            el = time.time() - self.t0
            self.status["text"] = (
                "runway maps running: window %.2f us   pair (%d,%d)   %d/%d   %.1f min, "
                "~%.1f min left   level change not seen %d   log %s"
                % (last[0], last[1], last[2], self.k_done, self.n_todo, el / 60,
                   el / self.k_done * (self.n_todo - self.k_done) / 60, self.nfail,
                   self.log_csv))
        self.after(300, self.poll)

    def stop(self):
        if self.running:
            self.stop_ev.set()
            self.buttons()
            self.status["text"] = "runway maps: stopping after this pair..."

    def shutdown(self):
        if self.running:
            self.stop_ev.set()
            self.worker.join(timeout=15)


# ======================================================================================
# Adjust iris: live reading at the WWWWW peak
# ======================================================================================
class IrisDialog(tk.Toplevel):
    """Hold a short exposure on the brightest part of the WWWWW sweep and show every
    reading live, so the lens iris can be turned until the peak no longer clips.

    The camera runs the same FPGA white cycle the WWWWW sweep used (255 / 254), at the
    peak's delay, so what is read here is the top of that sweep. The aim is the target
    band with NO saturated pixels: the mean can sit below 1023 while the brightest ROI
    pixels already clip, so the saturated count is the number that says "close it more".

    Done records the setting in the document. Sweeps measured before an iris change are
    no longer comparable, so it says so if the document already holds any.
    """

    N = 300                                          # readings on the chart
    TARGET = (750.0, 900.0)                          # ADU: bright, with headroom

    def __init__(self, view, doc, delay_us, sweep_peak):
        super().__init__(view)
        self.view, self.doc = view, doc
        self.title("Adjust iris")
        self.geometry("900x560+80+80")
        self.protocol("WM_DELETE_WINDOW", self.done)
        self.delay_us = delay_us
        self.q = queue.Queue()
        self.stop_ev = threading.Event()
        self.means, self.sats = [], []

        top = ttk.Frame(self)
        top.pack(fill="x", **PAD)
        ttk.Label(top, text="delay (us)").pack(side="left")
        self.delay_v = tk.StringVar(value="%.1f" % delay_us)
        de = ttk.Entry(top, textvariable=self.delay_v, width=9)
        de.pack(side="left", padx=(4, 12))
        de.bind("<Return>", lambda e: self.retarget())
        ttk.Label(top, text="exposure %.3f us   (WWWWW peak %.0f ADU at this delay when swept)"
                  % (13 * EXPO_UNIT_US, sweep_peak), foreground="#555").pack(side="left")
        ttk.Button(top, text="Done", command=self.done).pack(side="right")
        self.big = ttk.Label(self, text="starting...", font=("Consolas", 22, "bold"))
        self.big.pack(fill="x", padx=8)
        self.hint = ttk.Label(self, text="", font=("Segoe UI", 11))
        self.hint.pack(fill="x", padx=8)

        self.fig = Figure(figsize=(9, 4.5))
        self.ax = self.fig.add_subplot(211)
        self.az = self.fig.add_subplot(212, sharex=self.ax)
        self.ax.axhspan(*self.TARGET, color="#2e9e4f", alpha=.12, lw=0)
        self.ax.axhline(1023, color="#d02a2a", ls="--", lw=1)
        self.ax.set_ylim(0, 1080)
        self.ax.set_xlim(0, self.N)
        self.ax.set_ylabel("ROI mean (ADU)")
        self.ax.grid(alpha=.3)
        self.az.set_ylim(-5, 261)
        self.az.set_ylabel("saturated px")
        self.az.set_xlabel("reading (newest on the right)")
        self.az.grid(alpha=.3)
        self.line, = self.ax.plot([], [], lw=1.2, color="#1f5fd0")
        self.sline, = self.az.plot([], [], lw=1.2, color="#d02a2a")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("resize_event", lambda e: fit_margins(self.fig))
        fit_margins(self.fig)

        try:
            start_impulse(doc.ser, 255, 254, 5.0, doc.mx)   # the WWWWW cycle, 13 units
            self.set_delay(delay_us)
        except Exception as ex:
            messagebox.showerror("Adjust iris", str(ex), parent=self)
            self.after(10, self.done)
            return
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()
        self.after(100, self.poll)

    def set_delay(self, d):
        ticks = int(round(d / TICK_US))
        set_delay(self.doc.ser, ticks)
        wait_delay(self.doc.ser, ticks)
        self.delay_us = d

    def retarget(self):
        try:
            d = float(self.delay_v.get())
        except ValueError:
            return
        if not (0 <= d < (self.doc.T or 8333.3)):
            return
        self.stop_ev.set()
        self.worker.join(timeout=3)
        self.set_delay(d)
        self.means, self.sats = [], []
        self.stop_ev.clear()
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()

    def work(self):
        """Worker thread: read ROI lines until stopped."""
        ser = self.doc.ser
        try:
            ser.reset_input_buffer()
            while not self.stop_ev.is_set():
                rows = collect(ser, 4, timeout=0.5, sat=True)
                for r in rows:
                    if r[1] == 256:
                        self.q.put(("r", (r[0], r[4])))
        except Exception as ex:
            self.q.put(("error", ex))

    def poll(self):
        if not self.winfo_exists():
            return
        new = False
        while True:
            try:
                kind, v = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "r":
                self.means.append(v[0])
                self.sats.append(v[1])
                new = True
            elif kind == "error":
                self.big["text"] = "ERROR: %s" % v
        if new:
            self.means, self.sats = self.means[-self.N:], self.sats[-self.N:]
            x = range(len(self.means))
            self.line.set_data(x, self.means)
            self.sline.set_data(x, [s if s is not None else np.nan for s in self.sats])
            m, s = self.means[-1], self.sats[-1]
            recent = self.means[-30:]
            self.big["text"] = "%6.1f ADU   %s saturated px   (last 30: %.1f .. %.1f)" % (
                m, "-" if s is None else "%3d" % s, min(recent), max(recent))
            clipped = m >= 1022 or (s is not None and s > 0)
            if clipped:
                self.hint["text"], self.hint["foreground"] = \
                    "SATURATED -- close the iris", "#b00020"
            elif m < self.TARGET[0]:
                self.hint["text"], self.hint["foreground"] = \
                    "below the target band -- open the iris a little", "#a06000"
            elif m > self.TARGET[1]:
                self.hint["text"], self.hint["foreground"] = \
                    "above the target band, not clipping -- close it slightly for headroom", \
                    "#a06000"
            else:
                self.hint["text"], self.hint["foreground"] = \
                    "in the target band, nothing clipping -- Done", "#2e7d32"
            self.canvas.draw_idle()
        self.after(100, self.poll)

    def done(self):
        self.stop_ev.set()
        w = getattr(self, "worker", None)
        if w is not None:
            w.join(timeout=3)
        try:
            stop_impulse(self.doc.ser)
        except Exception:
            pass
        view, doc = self.view, self.doc
        if self.means:
            recent = self.means[-30:]
            sats = [s for s in self.sats[-30:] if s is not None]
            had = [k for k in ALL_SWEEPS if doc.sweeps.get(k)]
            doc.iris = {"delay_us": round(self.delay_us, 2), "expo_us": 13 * EXPO_UNIT_US,
                        "peak_mean": round(float(np.median(recent)), 1),
                        "saturated_px": int(max(sats)) if sats else None,
                        "when": time.strftime("%Y-%m-%d %H:%M:%S")}
            view.iris_dlg = None
            self.destroy()
            view.app.changed()
            if had:
                messagebox.showinfo(
                    "Re-run the sweeps",
                    "The %s sweep%s in this document %s measured before this iris setting, "
                    "so %s no longer comparable. Run %s again."
                    % (" and ".join(ALL_SWEEPS[k][0] for k in had), "s" if len(had) > 1 else "",
                       "were" if len(had) > 1 else "was", "they are" if len(had) > 1 else "it is",
                       "them" if len(had) > 1 else "it"), parent=view)
        else:
            view.iris_dlg = None
            self.destroy()
            view.buttons()


# ======================================================================================
# Help > Instructions
# ======================================================================================
INSTRUCTIONS = [
    ("Profiling a projector, step by step", None),
    ("1. Connect", """\
Unplug the PC's HDMI from the board: the board runs offline and makes its own video.

File > New... opens the Connect dialog. Pick the COM port (the board is usually COM5).
The Projector box shows the projector's name, read from its EDID, and the modes it
supports. Pick 800x600 @ 120 Hz for 120 fps work; "Ask the board" re-reads them. OK
switches the board to that mode, turns genlock on and measures the frame period."""),
    ("2. Run WWWWW, then adjust the iris", """\
Run WWWWW (blue): every frame white. It shows when the projector puts out light, period
after period.

Adjust iris... then holds a short exposure on that sweep's brightest point and shows each
reading live. Turn the lens iris until the reading sits in the green band with no
saturated pixels, and press Done. If the iris changed, run WWWWW again: sweeps taken at
the old setting are flagged as needing a re-run."""),
    ("3. Run WKKKK", """\
Run WKKKK (red): one white frame and four black. It shows the light of a single frame.

Where blue and red agree you are looking at one frame's light; where red has light the
blue period does not explain, light is spilling from a neighbouring frame.

Stop ends either sweep after the current delay and keeps what it has."""),
    ("4. Place the exposure", """\
GREEN line (leading edge): when the exposure opens, as a delay after frame 0's vsync.

PURPLE line (trailing edge): when it closes.

Dragging green moves purple (and the runway) with it at the same width. Dragging purple
keeps green fixed and stops at the exposure limit: T - 90 us, the longest exposure the
FPGA allows before the camera drops from 120 to 60 fps.

Until you move them they sit on the longest window holding the most white-frame light.

"Best window" puts them back there at the current width."""),
    ("5. Mark the runway", """\
CYAN dash-dot line (lower grab box): where projector light may start BEFORE the
exposure. The runway is the cyan-shaded stretch from it to the green line. It is always
at or before green, moves with green, and can reach back one frame (into the K frame
before the W). Until placed it starts where the WKKKK light ahead of green drops below
2 % of its peak. Use it to choose where to sample the projector for the 2-D maps."""),
    ("6. Save", """\
File > Save writes a .sliprof: the sweeps, the edges, the runway, the iris setting and
the projector (name and mode). Opening it later and pressing Run reconnects on its own:
same COM port, same projector, and the board put back in the saved mode. The Connect
dialog appears only if that fails or a different projector is attached.

Sweep > Export writes the sweeps as CSV and the window (with the runway) as JSON."""),
    ("7. RGB filters tab", """\
Repeats the WWWWW sweep through a colour filter on the lens, one sweep per filter, all on
one plot (red, green and blue traces). Mount a filter, then press its Scan button; change
the filter and scan the next. Each filter's sweep is kept in the document on its own, so
re-scanning one leaves the others.

"Show" picks one view at a time. Raw: the sweeps as measured. Normalized: each sweep's
dark level subtracted and its peak scaled to 1, to compare the shapes. Fit to white:
each filter scaled by the weight that makes the three add up to the WWWWW sweep (grey
band), with the weights and the residual on the line below. The profile tab's runway and
edges are drawn here too, and can be dragged on either tab. Only one sweep runs at a time
in the whole app."""),
    ("8. Runway maps tab", """\
Measures the runway (cyan, Profile tab) as grey-pair maps: the impulse generator
alternates two full-field levels A B A B and each map pixel is the ROI mean for one
pair. The runway is split where the previous frame's exposure ends, giving two maps: the
part inside the previous exposure and the part between exposures.

x is the level of the runway's frame; y is the level of the frames before and after it,
which A B A B makes the same: the frame before and the exposure frame after.

Pick how many levels (every 8th takes minutes, all 256 hours) and press Run. Stop keeps
what has been measured. The maps are saved in the document."""),
    ("The plot", None),
    ("Mouse", """\
Wheel: zoom the time axis around the cursor.

Drag empty plot: pan in time; let go while moving and it glides.

Double-click, or "Whole view": back to all five frames.

Drag a coloured box to move its line. Only time zooms; the ADU axis always fits every
reading."""),
    ("Shading", """\
Green: the exposure.

Cyan: the runway.

Orange hatching with dashed edges: the background
exposure -- the rest of each frame, from the trailing edge to the next leading edge."""),
    ("Readout", """\
Line 1: exposure delay (frame + offset), length in 0.375 us units, share of the
white-frame light inside, and the limit (red if over it).

Line 2: the background exposure in every frame.

Line 3: where the runway starts, how far before the exposure, and its share of the
white-frame light.

Line 4: the cursor's time and ADU."""),
    ("Tips", None),
    ("", """\
Keep the PC's HDMI unplugged: the Connect dialog refuses pass-through video.

While a sweep runs every reading is also written to a CSV in the temp folder, named on
the status line, so a crash does not lose it."""),
]


class HelpDialog(tk.Toplevel):
    """The instructions, in a scrolling window that can stay open beside the plot."""

    def __init__(self, master):
        super().__init__(master)
        self.title("Instructions - AuV2-SLI projector profiling")
        self.geometry("720x640+120+80")
        txt = tk.Text(self, wrap="word", padx=14, pady=10, relief="flat",
                      font=("Segoe UI", 10), background="white")
        sb = ttk.Scrollbar(self, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        ttk.Button(self, text="Close", command=self.destroy).pack(side="bottom", pady=6)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.tag_configure("h1", font=("Segoe UI", 13, "bold"), spacing1=12, spacing3=4)
        txt.tag_configure("h2", font=("Segoe UI", 10, "bold"), spacing1=8, spacing3=2)
        txt.tag_configure("body", lmargin1=12, lmargin2=12, spacing3=5)
        for head, body in INSTRUCTIONS:
            if body is None:
                txt.insert("end", head + "\n", "h1")
                continue
            if head:
                txt.insert("end", head + "\n", "h2")
            # source lines wrap at ~90 columns: join them; a blank line starts a paragraph
            paras = [" ".join(p.split()) for p in body.split("\n\n")]
            txt.insert("end", "\n".join(paras) + "\n", "body")
        txt.configure(state="disabled")
        self.bind("<Escape>", lambda e: self.destroy())


# ======================================================================================
# The application window
# ======================================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.doc = None
        self.view = self.fview = self.rview = self.tabs = None
        self.geometry("1300x820+40+40")
        self.protocol("WM_DELETE_WINDOW", self.exit)

        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=False)
        f.add_command(label="New...", accelerator="Ctrl+N", command=self.new)
        f.add_command(label="Open...", accelerator="Ctrl+O", command=self.open)
        f.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        f.add_command(label="Save As...", accelerator="Ctrl+Shift+S", command=self.save_as)
        f.add_command(label="Close", accelerator="Ctrl+W", command=self.close)
        f.add_separator()
        f.add_command(label="Exit", command=self.exit)
        m.add_cascade(label="File", menu=f)
        s = tk.Menu(m, tearoff=False)
        for k in ALL_SWEEPS:
            s.add_command(label="Import %s CSV..." % ALL_SWEEPS[k][0],
                          command=lambda k=k: self.view and self.view.import_csv(k))
        s.add_separator()
        for k in ALL_SWEEPS:
            s.add_command(label="Export %s CSV..." % ALL_SWEEPS[k][0],
                          command=lambda k=k: self.view and self.view.export_csv(k))
        s.add_command(label="Export exposure window JSON...",
                      command=lambda: self.view and self.view.export_json())
        m.add_cascade(label="Sweep", menu=s)
        h = tk.Menu(m, tearoff=False)
        h.add_command(label="Instructions", accelerator="F1", command=self.instructions)
        m.add_cascade(label="Help", menu=h)
        self.help_dlg = None
        self.file_menu, self.sweep_menu, self.menubar = f, s, m
        self.config(menu=m)
        self.bind_all("<Control-n>", lambda e: self.new())
        self.bind_all("<Control-o>", lambda e: self.open())
        self.bind_all("<Control-s>", lambda e: self.save())
        self.bind_all("<Control-S>", lambda e: self.save_as())
        self.bind_all("<Control-w>", lambda e: self.close())
        self.bind_all("<F1>", lambda e: self.instructions())

        self.empty = ttk.Label(self, text="No document.\n\nFile > New...   to connect and "
                               "measure\nFile > Open...  to load a saved profile\n"
                               "Help > Instructions (F1)  for how to profile",
                               justify="center", foreground="#666", font=("Segoe UI", 12))
        self.conn = ttk.Label(self, text="", font=("Consolas", 9), anchor="w")
        self.show_doc()

    def tab_shown(self):
        """Edges moved on the Profile tab only redraw that tab; catch the others up."""
        w = self.nametowidget(self.tabs.select())
        if w is not self.view:
            w.refresh()

    def instructions(self):
        """One instructions window; asking again brings it to the front."""
        if self.help_dlg is not None and self.help_dlg.winfo_exists():
            self.help_dlg.deiconify()
            self.help_dlg.lift()
            return
        self.help_dlg = HelpDialog(self)

    # ---- state -----------------------------------------------------------------------
    def busy(self):
        """A sweep (either tab) or the iris dialog has the camera."""
        return any(v is not None and v.running for v in (self.view, self.fview, self.rview))

    def changed(self, redraw=True):
        """Something in the document changed: title, and the view if asked."""
        if self.doc is not None:
            self.doc.dirty = True
        if redraw and self.view is not None:
            self.view.refresh()
            self.fview.refresh()
            self.rview.refresh()
        self.update_title()

    def update_title(self):
        has = self.doc is not None
        for item in ("Save", "Save As...", "Close"):
            self.file_menu.entryconfig(item, state="normal" if has else "disabled")
        self.menubar.entryconfig("Sweep", state="normal" if has else "disabled")
        if not has:
            self.title("AuV2-SLI projector profiling")
            return
        d = self.doc
        self.title("%s%s - AuV2-SLI projector profiling" % (d.name, " *" if d.dirty else ""))
        self.conn["text"] = ("  %s   %s   frame %s   exposure limit %s   %s" % (
            d.port or "-", d.display or "-", "%.1f us" % d.T if d.T else "-",
            "%.1f us" % (d.mx * EXPO_UNIT_US) if d.mx else "-",
            "CONNECTED" if d.ser is not None else "not connected (Run connects)"))

    def show_doc(self):
        """Swap between the empty window and the document's view."""
        if self.tabs is not None:
            self.tabs.destroy()
            self.tabs = self.view = self.fview = self.rview = None
        self.empty.pack_forget()
        self.conn.pack_forget()
        if self.doc is None:
            self.empty.pack(expand=True)
        else:
            self.conn.pack(side="bottom", fill="x")
            self.tabs = ttk.Notebook(self)
            self.view = ProfileView(self.tabs, self)
            self.fview = FilterView(self.tabs, self)
            self.tabs.add(self.view, text="Profile")
            self.rview = RunwayView(self.tabs, self)
            self.tabs.add(self.fview, text="RGB filters")
            self.tabs.add(self.rview, text="Runway maps")
            self.tabs.pack(fill="both", expand=True)
            self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.tab_shown())
        self.update_title()

    # ---- connection ------------------------------------------------------------------
    def ensure_connected(self):
        d = self.doc
        if d.ser is not None:
            return True
        # A reopened document reconnects as it was saved; the dialog only if that fails.
        self.conn["text"] = "  connecting to %s as saved..." % (d.port or "-")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            res = reconnect(d)
        finally:
            self.config(cursor="")
        if res is None:
            dlg = SetupDialog(self, d.port, d.display, d.projector)
            self.wait_window(dlg)
            if dlg.result is None:
                self.update_title()
                return False
            res = dlg.result
        ser, T, mx, port, disp, proj = res
        old = d.projector
        if old and any(d.sweeps.values()) and (old.get("name"), old["idx"]) != (proj["name"],
                                                                               proj["idx"]):
            if not messagebox.askyesno(
                    "Different projector setup",
                    "This document was measured on %s at %s.\nThe board is now driving %s "
                    "at %s.\n\nConnect anyway?" % (
                        old.get("name"), mode_name(old), proj["name"], mode_name(proj)),
                    parent=self):
                ser.close()
                return False
        if d.T and abs(T - d.T) > 1.0 and any(d.sweeps.values()):
            ser.close()
            messagebox.showerror(
                "Different frame period",
                "The board runs a %.1f us frame; this document was measured at %.1f us.\n"
                "Start a new document for this mode." % (T, d.T), parent=self)
            return False
        d.ser, d.T, d.mx, d.port, d.display = ser, T, mx, port, disp
        if d.projector != proj:
            d.projector = proj
            self.changed(redraw=False)
        self.update_title()
        return True

    # ---- file menu -------------------------------------------------------------------
    def new(self):
        if not self.close():
            return
        dlg = SetupDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:                       # Cancel: stay with no document
            return
        d = Document()
        d.ser, d.T, d.mx, d.port, d.display, d.projector = dlg.result
        self.doc = d
        self.show_doc()

    def open(self, path=None):
        if not self.close():
            return
        path = path or filedialog.askopenfilename(
            parent=self, initialdir=ROOT, title="Open profile",
            filetypes=[("AuV2-SLI profile", "*" + EXT), ("All", "*")])
        if not path:
            return
        try:
            self.doc = Document.load(path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return
        self.show_doc()

    def save(self):
        if self.doc is None:
            return False
        if self.doc.path is None:
            return self.save_as()
        return self._write(self.doc.path)

    def save_as(self):
        if self.doc is None:
            return False
        path = filedialog.asksaveasfilename(
            parent=self, initialdir=ROOT, defaultextension=EXT,
            initialfile=self.doc.name if self.doc.path else
            "profile_%s%s" % (time.strftime("%Y-%m-%d"), EXT),
            filetypes=[("AuV2-SLI profile", "*" + EXT)])
        return bool(path) and self._write(path)

    def _write(self, path):
        if self.busy():
            messagebox.showinfo("Sweep running", "Stop the sweep before saving.", parent=self)
            return False
        try:
            self.doc.save(path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return False
        self.update_title()
        return True

    def close(self):
        """Close the document. False if the user backed out."""
        if self.doc is None:
            return True
        if self.busy():
            if not messagebox.askokcancel("Sweep running",
                                          "A sweep is running. Stop it and close?",
                                          parent=self):
                return False
            self.view.shutdown()
            self.fview.shutdown()
            self.rview.shutdown()
        if self.doc.dirty:
            r = messagebox.askyesnocancel("Unsaved changes",
                                          "Save changes to %s?" % self.doc.name, parent=self)
            if r is None or (r and not self.save()):
                return False
        if self.view is not None:
            self.view.stop_momentum()
        self.doc.disconnect()
        self.doc = None
        self.show_doc()
        return True

    def exit(self):
        if self.close():
            self.destroy()


if __name__ == "__main__":
    app = App()
    if len(sys.argv) > 1:
        app.after(100, lambda: app.open(sys.argv[1]))
    app.mainloop()

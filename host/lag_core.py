#!/usr/bin/env python3
"""The 5-frame delay sweep as functions, shared by host/lag_sweep.py and host/sli_ui.py.

See lag_sweep.py for what the sweep measures. This module only measures: no plotting, no
argparse, no sys.exit -- failures raise RuntimeError so a UI can show them.

    ser, T, mx = preflight("COM5", r"\\\\.\\DISPLAY5")
    e = start_impulse(ser, level=255, bg=254, expo=5.0, mx=mx)
    for i, n, d, means, ref in sweep(ser, T, e, delays, ...):
        ...
    stop_impulse(ser)
"""
import collections
import csv
import os
import re
import time

import numpy as np

from align_roi import displays, set_mode
from frame_sweep import (wr, rd, set_delay, wait_delay, collect, EXPO_UNIT_US,  # noqa
                         TICK_US, R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC, R_IMPLVL,
                         R_IMPRGB, R_IMPLVL2, R_IMPRGB2)

MODE = (800, 600, 120)
ACTIVE_US = 432.5          # 800x600@120: vsync -> first active pixel, 33 lines
OFFLINE = "offline"         # display name for the board's own video, no PC on HDMI-IN
RESERVE_TICKS = 9000       # usb_link.v: 80 us sensor gap + 10 us margin, in 10 ns ticks


# ---- the board's offline modes --------------------------------------------------------
# Parsed from the RTL so they cannot drift from what the fabric generates.
_RTL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "sources_1", "imports", "RTL", "mode_table.vh")
_MROW = re.compile(r"`MROW\(\s*" + r",\s*".join([r"(\d+)"] * 13) + r"\)")


def mode_table():
    """{idx: dict(idx, w, h, hz, khz, vtotal, vstart)} from mode_table.vh. vstart is the
    lines from the vsync edge to the first active line (VSYNC + VBP)."""
    out = {}
    for line in open(_RTL):
        m = _MROW.search(line)
        if m:
            i, w, h, hz, khz, hfp, hs, hbp, vfp, vs, vbp, hp, vp = (int(x) for x in m.groups())
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


def set_board_mode(ser, idx, log=print):
    """Pin the offline mode to `idx` over MODEFORCE (0x14). It is sticky until released
    or the FPGA reloads. Waits for the index to land and the frame period to settle."""
    if reg(ser, 0x20) & 0x0F == idx:
        return
    wr(ser, 0x14, 0x80 | idx)
    t0 = time.time()
    while reg(ser, 0x20) & 0x0F != idx:
        if time.time() - t0 > 5:
            raise RuntimeError("the board did not switch to mode %d" % idx)
        time.sleep(0.2)
    time.sleep(3)                                    # projector re-sync, period re-measured
    log("offline mode -> %s" % mode_name(mode_table()[idx]))


def read_edid(ser, tgt=0x03, n=256):
    """The projector's EDID as the board read it off HDMI-OUT (table 0x03), over the UART.
    The reply shares the line with ASCII telemetry, so scan for a frame whose checksum
    closes: tgt, n bytes, checksum (as host/offline_mode.py does)."""
    ck = (256 - ((0x72 + tgt) & 0xFF)) & 0xFF
    to = ser.timeout
    ser.timeout = 0.05
    try:
        ser.reset_input_buffer()
        ser.write(bytes([0xA5, 0x72, tgt, ck]))
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


def projector_display():
    """The one display not at the desktop origin, or None."""
    proj = [d for d in displays() if (d[1], d[2]) != (0, 0)]
    return proj[0][0] if len(proj) == 1 else None


def ensure_mode(dev, log=print):
    """Put display `dev` at 800x600@120 if it is not already."""
    cur = [d for d in displays() if d[0] == dev]
    if not cur:
        raise RuntimeError("display %s is not attached" % dev)
    if tuple(cur[0][3:]) == MODE:
        return
    ok, msg = set_mode(dev, *MODE)
    log("display %s was %dx%d@%d -> %dx%d@%d: %s" % (dev, *cur[0][3:], *MODE, msg))
    if not ok:
        raise RuntimeError("could not set %s to %dx%d@%d: %s" % (dev, *MODE, msg))
    time.sleep(3)                                    # let the FPGA re-measure the frame


def reg(ser, addr):
    v = rd(ser, addr)
    return 0 if v is None else v


def genlock_on(ser, log=print):
    """Turn genlock on over the Ft+ if it is off. gl_en resets to 0 on the merged build."""
    if reg(ser, 0x5D) & 1:
        return
    from ftlink import FtLink
    ft = FtLink()
    ft.send_word((7 << 28) | (1 << 27))              # opcode 7: gl_en = 1, delay 0
    time.sleep(0.3)
    ft.close()
    log("genlock was off -> on: 0x5D = 0x%02X" % reg(ser, 0x5D))


def check_offline(ser):
    """The board is generating its own video (no PC on HDMI-IN) at 800x600@120.
    STATUS (0x02) bit 4 is sel: 1 = pass-through. MODE (0x20) {valid, edid_ok, idx}.
    Returns the mode in use (a mode_table row)."""
    st, md = reg(ser, 0x02), reg(ser, 0x20)
    if st & 0x10:
        raise RuntimeError("the board is in pass-through: unplug the PC's HDMI from the "
                           "board to run offline")
    if not md & 0x80:
        raise RuntimeError("the board has no valid offline mode (MODE 0x%02X)" % md)
    return mode_table()[md & 0x0F]


def preflight(port, display, log=print):
    """Display mode, serial port, genlock. Returns (open serial, frame period us, max expo units).
    display None or OFFLINE: no PC video; the board must be generating 800x600@120 itself."""
    import serial
    offline = display in (None, OFFLINE)
    if not offline:
        ensure_mode(display, log)
    ser = serial.Serial(port, 115200, timeout=0.05)
    time.sleep(0.3)
    try:
        if offline:
            m = check_offline(ser)
            log("offline: the board is generating %s" % mode_name(m))
        genlock_on(ser, log)
        T = (reg(ser, 0x4A) | (reg(ser, 0x4B) << 8) | (reg(ser, 0x4C) << 16)) * TICK_US
        mx = reg(ser, 0x53) | (reg(ser, 0x54) << 8)
        gl = reg(ser, 0x5D)
        if not (1000 < T < 60000) or (gl & 3) != 3:
            raise RuntimeError("frame %.1f us, genlock 0x%02X -- is video running?" % (T, gl))
    except Exception:
        ser.close()
        raise
    return ser, T, mx


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


def delays_for(T, e, step, dmin=0.0, dmax=None):
    top = T - e - 1.0 if dmax is None else min(dmax, T - e - 1.0)
    return list(np.arange(dmin, top, step))


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
    # pixel (build_merged.tcl: cam_frame_ft takes 23:16), so white reads FF0000 there
    # and FFFFFF on the profiling bitstream.
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


CSV_HEAD = ["T_us", "expo_us", "delay_us", "position", "t_us", "mean", "sd", "n", "ref",
            "tag_fix_us"]


def sweep(ser, T, e, delays, out, level=255, bg=0, frames=20, settle=4,
          active_us=ACTIVE_US, stop=None, log=print):
    """Step the delay; per delay kept, yield (i, len(delays), delay, [5 means], ref, rows).

    `rows` are the five CSV_HEAD rows for that delay. Every reading is also written to
    `out` as it arrives (None: no file). `stop` is an optional threading.Event checked
    between delays. The caller still owns `ser` and should call stop_impulse() afterwards.
    """
    lit_px = (level << 16) | (level << 8) | level
    with open(out or os.devnull, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEAD)
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

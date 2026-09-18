#!/usr/bin/env python3
"""Find which projected pixels land in the camera's 16x16 ROI.

    python host/align_roi.py COM6 --set-mode --live

WHAT THIS IS FOR. The camera sits BARE -- no lens -- about 17 mm in front of the
projector's lens, so it is not imaging the screen. But at that distance the light
from different DMD pixels has not merged yet: screen position still maps to
position across the sensor, at very roughly 40 um per screen pixel. The 16x16 ROI
is 77 um across, so it sees only a couple of screen pixels' worth of the image.

This finds WHICH ones. Drive a white square around a black screen over HDMI,
watch the ROI mean, and the square's position when the mean peaks is the part of
the projected image that lands in the measurement window. After that, anything
the ROI reports can be attributed to known pixels.

WHY IT SHRINKS INSTEAD OF RASTERING. A 16x16 square rastered over 800x600 at a
step that cannot miss is 1900 positions. Worse, a coarse raster with a small
square steps straight over the peak and finds nothing, which looks exactly like a
misaligned rig. So the search starts with a LARGE square, which cannot fall
between samples because it covers everything it steps over, takes the brightest
tile, halves the square, and searches inside the winner. Five halvings from 256
down to 16 is about 125 measurements instead of 1900, and every level's evidence
is an integral over a region rather than a sample of a point.

SATURATION IS THE TRAP IN THAT PLAN. A 256x256 square puts 256x more light on the
sensor than a 16x16 one. If two tiles both clip, the comparison between them is
meaningless and the search follows the wrong one. So every level is a scan that
must finish WITHOUT any reading passing --ceiling (600 ADU). The moment one does,
the exposure is shortened and that level's scan starts again from the top.

The FPGA must be passing HDMI through, not generating its own patterns -- this
tool sets that up and puts it back afterwards.
"""
import argparse
import csv
import ctypes
import ctypes.wintypes as wt
import sys
import time
import tkinter as tk

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from frame_sweep import (wr, rd, set_delay, wait_delay, collect,        # noqa: E402
                         EXPO_UNIT_US, TICK_US, R_ROICTL,
                         R_EXPO_LO, R_EXPO_HI)

R_SLICTL = 0x13
DM_BITSPERPEL, DM_PELSWIDTH = 0x00040000, 0x00080000
DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x00100000, 0x00400000
CDS_UPDATEREGISTRY, CDS_TEST = 0x01, 0x02
ENUM_CURRENT_SETTINGS = -1


class DEVMODE(ctypes.Structure):
    _fields_ = [("dmDeviceName", wt.WCHAR * 32),
                ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD),
                ("dmFields", wt.DWORD),
                ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                ("dmDisplayOrientation", wt.DWORD),
                ("dmDisplayFixedOutput", wt.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short), ("dmFormName", wt.WCHAR * 32),
                ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD),
                ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD),
                ("dmDisplayFlags", wt.DWORD), ("dmDisplayFrequency", wt.DWORD),
                ("dmICMMethod", wt.DWORD), ("dmICMIntent", wt.DWORD),
                ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD),
                ("dmReserved1", wt.DWORD), ("dmReserved2", wt.DWORD),
                ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD)]


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32),
                ("DeviceString", wt.WCHAR * 128), ("StateFlags", wt.DWORD),
                ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]


def displays():
    """(device name, x, y, w, h, Hz) for every attached display."""
    u = ctypes.windll.user32
    out, i = [], 0
    while True:
        d = DISPLAY_DEVICE()
        d.cb = ctypes.sizeof(d)
        if not u.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
            break
        i += 1
        if not (d.StateFlags & 0x01):          # not attached to the desktop
            continue
        m = DEVMODE()
        m.dmSize = ctypes.sizeof(m)
        if u.EnumDisplaySettingsW(d.DeviceName, ENUM_CURRENT_SETTINGS,
                                  ctypes.byref(m)):
            out.append((d.DeviceName, m.dmPositionX, m.dmPositionY,
                        m.dmPelsWidth, m.dmPelsHeight, m.dmDisplayFrequency))
    return out


def set_mode(dev, w, h, hz):
    """Ask the display for w x h @ hz. Returns (ok, message)."""
    u = ctypes.windll.user32
    m = DEVMODE()
    m.dmSize = ctypes.sizeof(m)
    if not u.EnumDisplaySettingsW(dev, ENUM_CURRENT_SETTINGS, ctypes.byref(m)):
        return False, "cannot read current settings"
    m.dmPelsWidth, m.dmPelsHeight, m.dmDisplayFrequency = w, h, hz
    m.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    r = u.ChangeDisplaySettingsExW(dev, ctypes.byref(m), None, CDS_TEST, None)
    if r != 0:
        return False, "mode refused (ChangeDisplaySettingsEx test returned %d)" % r
    r = u.ChangeDisplaySettingsExW(dev, ctypes.byref(m), None,
                                   CDS_UPDATEREGISTRY, None)
    return r == 0, "applied" if r == 0 else "apply returned %d" % r


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--set-mode", action="store_true",
                    help="switch the projector display to --width x --height @ --hz first")
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--height", type=int, default=600)
    ap.add_argument("--hz", type=int, default=120)
    ap.add_argument("--device", help="display device name, e.g. \\\\.\\DISPLAY2")
    ap.add_argument("--start", type=int, default=256, help="first square size, px")
    ap.add_argument("--size", type=int, default=16, help="final square size, px")
    ap.add_argument("--delay", type=float, default=540.0,
                    help="genlock delay, us. 540 with a 6175 us exposure spans the "
                         "whole emission window measured in README 3.1, so the "
                         "reading is the frame's total light rather than a slice.")
    ap.add_argument("--expo", type=float, default=300.0,
                    help="starting exposure, us. Only a starting point -- it is "
                         "re-derived from the measurement at every level.")
    ap.add_argument("--ceiling", type=float, default=600.0,
                    help="raw ADU no reading may exceed. The moment one does, the "
                         "scan stops, the exposure is shortened, and that level "
                         "starts again from its first position. Well under 1023: "
                         "by 1000 the sensor is already clipping, and two clipped "
                         "tiles cannot be told apart.")
    ap.add_argument("--frames", type=int, default=10, help="frames averaged per position")
    ap.add_argument("--settle", type=int, default=8,
                    help="frames discarded after moving the square. The projector is "
                         "a frame behind and the DLP takes a few more to settle.")
    ap.add_argument("--out", default="align_roi.csv")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    # ---- which display is the projector ------------------------------------
    devs = displays()
    if not devs:
        sys.exit("no displays enumerated")
    print("displays:")
    for n, x, y, w, h, hz in devs:
        print("   %-16s %4dx%-4d @%3d Hz  at %+d%+d" % (n, w, h, hz, x, y))
    if a.device:
        pick = [d for d in devs if d[0].lower() == a.device.lower()]
        if not pick:
            sys.exit("no display named %s" % a.device)
    else:
        pick = [d for d in devs if (d[1], d[2]) != (0, 0)] or devs[-1:]
        if len(pick) > 1:
            sys.exit("%d non-primary displays -- name one with --device" % len(pick))
    dev, DX, DY, DW, DH, DHZ = pick[0]
    print("\nprojector display: %s" % dev)

    # THE MODE IS ENFORCED, NOT OPTIONAL. Windows dropped this display back to
    # 1280x720@60 between two runs, and a run without --set-mode then completed
    # and reported an "ALIGNED" square at 60 Hz -- a different frame, different
    # sub-field timing, and a genlock delay pointing at the wrong light. So the
    # mode is checked every run and set whenever it does not match.
    if a.set_mode or (DW, DH, DHZ) != (a.width, a.height, a.hz):
        if (DW, DH, DHZ) != (a.width, a.height, a.hz):
            print("display is %dx%d@%d, want %dx%d@%d" % (DW, DH, DHZ, a.width,
                                                     a.height, a.hz))
        ok, msg = set_mode(dev, a.width, a.height, a.hz)
        print("set %dx%d@%d: %s" % (a.width, a.height, a.hz, msg))
        if not ok:
            sys.exit("could not set the mode -- is it in the projector's EDID?")
        time.sleep(2.5)
        dev, DX, DY, DW, DH, DHZ = [d for d in displays() if d[0] == dev][0]
    print("using %dx%d @%d Hz at %+d%+d\n" % (DW, DH, DHZ, DX, DY))

    # ---- the FPGA: pass HDMI through, stream the ROI ------------------------
    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    print("frame period  %.1f us  (%.2f Hz)   genlock 0x%02X" % (T_us, 1e6 / T_us, gl))
    # And check it where it counts: the FPGA's own measured frame period. Windows
    # reporting the mode is not the same as the projector receiving it.
    if abs(1e6 / T_us - a.hz) > 1.0:
        ser.close()
        sys.exit("the FPGA measures %.2f Hz but %d Hz was asked for -- refusing to "
                 "align at the wrong frame rate" % (1e6 / T_us, a.hz))
    if (gl & 3) != 3:
        ser.close(); sys.exit("genlock not live -- is the profiling bitstream loaded?")

    wr(ser, R_SLICTL, 0x00)          # release the switch/mode overrides: pass-through
    wr(ser, R_ROICTL, 0x40)          # imp_en OFF, ROI stream ON
    expo_us = [a.expo]

    def set_expo(us):
        u = max(1, int(round(us / EXPO_UNIT_US)))
        wr(ser, R_EXPO_LO, u & 0xFF)
        wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
        expo_us[0] = u * EXPO_UNIT_US
        return expo_us[0]

    set_expo(a.expo)
    tick = int(round(a.delay / TICK_US))
    set_delay(ser, tick)
    got, ok = wait_delay(ser, tick)
    if not ok:
        ser.close(); sys.exit("delay did not land (asked %d, holds %s)" % (tick, got))
    print("delay %.0f us, exposure %.0f us\n" % (a.delay, expo_us[0]))

    # ---- one Tk interpreter: matplotlib first, screen as a Toplevel ---------
    # (hdmi_ramp.py paid for this lesson: two roots in one process and the fill
    #  window dies mid-run while the sweep keeps measuring an undriven screen.)
    plt = live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, (axm, axp) = plt.subplots(2, 1, figsize=(7.5, 9),
                                       gridspec_kw={"height_ratios": [3, 1]})
        axm.set_xlim(0, DW); axm.set_ylim(DH, 0); axm.set_aspect("equal")
        axm.set_xlabel("projected x (px)"); axm.set_ylabel("projected y (px)")
        axm.grid(alpha=0.25)
        axp.set_xlabel("measurement"); axp.set_ylabel("ROI mean (ADU)")
        axp.grid(alpha=0.3)
        prof, = axp.plot([], [], lw=1, marker=".", ms=3, color="#1f5fd0")
        hdr = fig.text(0.02, 0.985, "", va="top", ha="left", family="monospace",
                       fontsize=9, linespacing=1.6)
        fig.subplots_adjust(left=0.12, right=0.98, top=0.90, bottom=0.07, hspace=0.28)
        try:                       # keep the plot OFF the projected display
            fig.canvas.manager.window.wm_geometry("+40+40")
        except Exception:
            pass
        live = (plt, fig, axm, axp, prof, hdr)

    root = tk.Toplevel() if plt is not None else tk.Tk()
    root.overrideredirect(True)
    root.geometry("%dx%d+%d+%d" % (DW, DH, DX, DY))
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, bg="black", highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    rect = canvas.create_rectangle(0, 0, 0, 0, fill="white", outline="")
    root.update()

    fh = open(a.out, "w", newline="")
    wcsv = csv.writer(fh)
    wcsv.writerow(["level", "size", "x", "y", "expo_us", "mean", "nframes",
                   "sat_px_max", "low_px_max"])
    fh.flush()

    dark = [0.0]          # the black-screen floor, so "signal" means something
    samples = []          # (x, y, size, mean) for the diagram
    seq = []              # every reading, in order, for the profile
    npos = [0]

    def show(x, y, s):
        canvas.coords(rect, x, y, x + s, y + s)
        root.update()

    lastsat = [None, None]   # worst saturated / clamped-low pixel count, last position
    warned = [False, False]

    def measure(x, y, s):
        show(x, y, s)
        ser.reset_input_buffer()
        collect(ser, a.settle, timeout=3.0)
        rows = collect(ser, a.frames, timeout=5.0, sat=True)
        ok = [r for r in rows if r[1] == 256]
        v = sum(r[0] for r in ok) / len(ok) if ok else float("nan")
        # THE WORST FRAME, NOT THE AVERAGE. One frame with clipped pixels is enough
        # to make that position's mean untrustworthy.
        his = [r[4] for r in ok if r[4] is not None]
        los = [r[5] for r in ok if r[5] is not None]
        lastsat[0] = max(his) if his else None
        lastsat[1] = max(los) if los else None
        if ok and lastsat[0] is None and not warned[0]:
            warned[0] = True
            print("  WARNING: this bitstream sends no saturated-pixel count -- only the "
                  "mean is being checked against the ceiling")
        if lastsat[1] and not warned[1]:
            warned[1] = True
            print("  WARNING: %d ROI pixels read <= 3 at (%d,%d) -- clamped at the "
                  "bottom. The black level is wrong; means are biased high."
                  % (lastsat[1], x, y))
        npos[0] += 1
        seq.append((v, expo_us[0]))
        samples.append((x, y, s, v))
        wcsv.writerow([len(samples), s, x, y, round(expo_us[0], 2),
                       round(v, 2), len(ok), lastsat[0], lastsat[1]])
        fh.flush()
        return v

    def redraw(cur, best, note):
        if not live:
            return
        plt, fig, axm, axp, prof, hdr = live
        axm.clear()
        axm.set_xlim(0, DW); axm.set_ylim(DH, 0); axm.set_aspect("equal")
        axm.set_xlabel("projected x (px)"); axm.set_ylabel("projected y (px)")
        axm.grid(alpha=0.25)
        vals = [v for _, _, _, v in samples if v == v]
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
        rng = max(1e-6, hi - lo)
        import matplotlib.patches as mp
        for x, y, s, v in samples:
            if v != v:
                continue
            f = (v - lo) / rng
            axm.add_patch(mp.Rectangle((x, y), s, s, facecolor=plt.cm.viridis(f),
                                       edgecolor="none", alpha=0.55))
        if cur:
            x, y, s = cur
            axm.add_patch(mp.Rectangle((x, y), s, s, facecolor="none",
                                       edgecolor="white", lw=1.6))
        if best:
            x, y, s, v = best
            axm.plot([x + s / 2.0], [y + s / 2.0], marker="+", ms=14,
                     color="#d0242a", mew=2)
        # ONE COLOUR PER EXPOSURE. A restart changes the exposure, and readings
        # taken at different exposures are not comparable -- drawn as one line they
        # look like a single noisy trace. Each run of readings at one exposure gets
        # its own colour, a divider where it changed, and its exposure written on.
        axp.cla()
        axp.set_xlabel("measurement"); axp.set_ylabel("ROI mean (ADU)")
        axp.grid(alpha=0.3)
        axp.axhline(a.ceiling, color="#c0392b", lw=1, ls="--")
        pal = ["#1f5fd0", "#e07b00", "#1a8f3c", "#8e44ad", "#b8860b",
               "#008b8b", "#c2185b", "#5d6d7e"]
        runs, start = [], 0
        for i in range(1, len(seq) + 1):
            if i == len(seq) or seq[i][1] != seq[start][1]:
                runs.append((start, i, seq[start][1]))
                start = i
        for k, (i0, i1, e) in enumerate(runs):
            col = pal[k % len(pal)]
            axp.plot(range(i0, i1), [q[0] for q in seq[i0:i1]], lw=1,
                     marker=".", ms=3, color=col)
            if i0 > 0:
                axp.axvline(i0 - 0.5, color="#999999", lw=0.8)
            axp.text(i0, a.ceiling, " %.0f us" % e, color=col, fontsize=7,
                     va="bottom", ha="left", rotation=90)
        if seq:
            axp.set_xlim(-1, max(10, len(seq)))
            lo = min(q[0] for q in seq)
            axp.set_ylim(min(lo, dark[0]) - 20, max(a.ceiling + 90,
                                                      max(q[0] for q in seq) + 20))
        hdr.set_text(note)
        try:
            if not plt.fignum_exists(fig.number):
                raise RuntimeError
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
        except Exception:
            print("plot gone -- continuing")

    # The floor is measured, not assumed: everything below is reasoned about in
    # ADU ABOVE IT, and the floor moves with exposure, filter and ambient light.
    def measure_dark():
        # Re-measured after every exposure change: the black-screen floor includes
        # the projector's own black-level light, which scales with exposure.
        show(0, 0, 0)
        ser.reset_input_buffer()
        collect(ser, a.settle, timeout=3.0)
        r = collect(ser, a.frames, timeout=5.0)
        g = [m for m, n, t, c in r if n == 256]
        dark[0] = sum(g) / len(g) if g else dark[0]
        return dark[0]

    measure_dark()
    print("dark floor %.1f ADU (black screen, %.0f us)   ceiling %.0f ADU"
          % (dark[0], expo_us[0], a.ceiling))
    if dark[0] >= a.ceiling:
        ser.close()
        sys.exit("the BLACK screen already reads %.0f, at or over the %.0f ceiling -- "
                 "lower --expo" % (dark[0], a.ceiling))

    # ---- the search: big square first, halve, search the winner ------------
    best = None
    try:
        size = a.start
        x0, y0, x1, y1 = 0, 0, DW, DH       # region under consideration
        level = 0
        while True:
            level += 1
            step = max(1, size // 2)
            xs = list(range(max(0, x0), max(1, min(DW - size, x1 - size) + 1), step)) \
                 or [max(0, min(DW - size, x0))]
            ys = list(range(max(0, y0), max(1, min(DH - size, y1 - size) + 1), step)) \
                 or [max(0, min(DH - size, y0))]
            # The stepped list can stop short of the right and bottom edges, so a peak
            # living there is never sampled. Add the flush-to-edge positions.
            for lst, span in ((xs, DW), (ys, DH)):
                edge = span - size
                if edge >= 0 and lst[-1] < edge and edge <= (x1 if lst is xs else y1):
                    lst.append(edge)
            # ABORT-AND-RESTART. The scan is only accepted if it gets through every
            # position without a single reading over --ceiling. The instant one
            # goes over, the exposure is shortened and the level starts again from
            # its first position -- the readings taken so far were at the wrong
            # exposure and are thrown away rather than compared against the rest.
            #
            # WHY IT BISECTS. The reading is not proportional to exposure. With the
            # genlock delay at 540 us and a colour filter in the beam, the window
            # only reaches that colour's sub-fields once the exposure is long enough
            # to cover them -- blue's start at 2740 us -- so the light arrives in
            # steps. A proportional correction overshoots both ways and the old
            # version hunted between 2000 and 7700 us on both runs. The reading IS
            # still monotonic in exposure, so bracketing between the longest
            # exposure that finished too dim and the shortest that went over always
            # converges, however lumpy the response is.
            lo_e, hi_e = None, None           # too dim / went over
            restarts = 0
            # The frame limit, and the sensor's own: commanding more than reg 0x53
            # wedges capture until the FPGA is reconfigured (README 7.4).
            maxe = max(50.0, T_us - a.delay - 50.0)
            mx_units = (rd(ser, 0x53) or 0) | ((rd(ser, 0x54) or 0) << 8)
            if (rd(ser, 0x55) or 0) & 0x80 and mx_units:
                maxe = min(maxe, mx_units * EXPO_UNIT_US)
            while True:
                lvl, over = [], None
                for yy in ys:
                    for xx in xs:
                        v = measure(xx, yy, size)
                        lvl.append((xx, yy, size, v))
                        b = max(lvl, key=lambda t: t[3])
                        note = ("level %d   square %d px   %d x %d positions   "
                                "exposure %.0f us   restarts %d"
                                % (level, size, len(xs), len(ys), expo_us[0],
                                   restarts)
                                + chr(10)
                                + "at (%d,%d)  mean %7.2f  sat px %s  low px %s   "
                                  "best %7.2f at (%d,%d)   ceiling %.0f   %d meas"
                                % (xx, yy, v,
                                   "-" if lastsat[0] is None else lastsat[0],
                                   "-" if lastsat[1] is None else lastsat[1],
                                   b[3], b[0], b[1], a.ceiling, npos[0]))
                        redraw((xx, yy, size), best or b, note)
                        # Over EITHER limit: the mean past the ceiling, or ANY
                        # pixel saturated -- a mean can sit under 600 while a few
                        # pixels are clipped at 1023 inside it.
                        if v > a.ceiling or (lastsat[0] or 0) > 0:
                            over = (xx, yy, v, lastsat[0] or 0)
                            break
                    if over:
                        break

                e = expo_us[0]
                aim = dark[0] + 0.8 * (a.ceiling - dark[0])   # land with headroom
                if over:
                    hi_e = e if hi_e is None else min(hi_e, e)
                    if over[2] >= 1015.0:
                        new = e / 4.0                 # railed: true value unknown
                    elif over[2] <= a.ceiling:
                        # stopped by clipped PIXELS with the mean still in range, so
                        # the mean says nothing about how far over they are
                        new = e * 0.6
                    else:
                        new = e * (aim - dark[0]) / max(1.0, over[2] - dark[0])
                    if lo_e is not None:
                        new = max(new, (lo_e * hi_e) ** 0.5)
                    new = min(new, 0.9 * e)           # must actually get shorter
                    if over[2] > a.ceiling:
                        why = "mean %.0f at (%d,%d) is over %.0f" % (
                            over[2], over[0], over[1], a.ceiling)
                    else:
                        why = "%d saturated pixels at (%d,%d), mean only %.0f" % (
                            over[3], over[0], over[1], over[2])
                else:
                    b = max(lvl, key=lambda t: t[3])
                    floor_mid = dark[0] + 0.5 * (a.ceiling - dark[0])
                    if b[3] >= floor_mid or e >= 0.995 * maxe:
                        break                         # completed, or as long as it gets
                    lo_e = e if lo_e is None else max(lo_e, e)
                    if hi_e is not None and hi_e / lo_e < 1.15:
                        break                         # bracket closed: accept
                    new = e * (aim - dark[0]) / max(1.0, b[3] - dark[0])
                    new = min(new, 4.0 * e)
                    if hi_e is not None:
                        new = min(new, (lo_e * hi_e) ** 0.5)
                    new = max(new, 1.1 * e)           # must actually get longer
                    why = "completed but best is only %.0f" % b[3]

                new = min(max(new, 0.4), maxe)
                if abs(new - e) < 0.01 * e:
                    print("  level %d: exposure cannot move past %.1f us -- taking the "
                          "best found" % (level, e))
                    break                             # nothing left to adjust
                restarts += 1
                if restarts > 16:
                    print("  level %d: gave up after 16 restarts at %.1f us"
                          % (level, e))
                    break
                set_expo(new)
                measure_dark()
                print("  level %d: %s -- exposure %.1f -> %.1f us, floor %.0f, "
                      "scan restarts" % (level, why, e, expo_us[0], dark[0]))
                samples[:] = [q for q in samples if q[2] != size]
            b = max(lvl, key=lambda t: t[3])
            best = b
            print("  level %d: square %d px -> best (%d,%d) mean %.2f  [%d positions]"
                  % (level, size, b[0], b[1], b[3], len(lvl)))
            if size <= a.size:
                break
            # Search the winning tile PLUS half a tile of margin on every side. Refining
            # strictly inside the winner cannot follow a peak that sits on its edge --
            # and the coarse tiles are integrals over a wide area, so the winner's
            # centre is not where the peak has to be.
            m = size // 2
            x0, y0 = max(0, b[0] - m), max(0, b[1] - m)
            x1, y1 = min(DW, b[0] + size + m), min(DH, b[1] + size + m)
            size = max(a.size, size // 2)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        fh.close()

    if best:
        bx, by, bs, bv = best
        cx, cy = bx + bs // 2, by + bs // 2
        print("\nALIGNED")
        print("  the ROI sees the projected square at (%d,%d)-(%d,%d)"
              % (bx, by, bx + bs, by + bs))
        print("  centre (%d,%d) of %dx%d   mean %.2f ADU   exposure %.0f us"
              % (cx, cy, DW, DH, bv, expo_us[0]))
        print("  as a fraction of the frame: x %.4f  y %.4f"
              % (cx / float(DW), cy / float(DH)))
        show(bx, by, bs)
        redraw((bx, by, bs), best,
               "ALIGNED   square %d px at (%d,%d)   centre (%d,%d)   mean %.1f ADU"
               % (bs, bx, by, cx, cy, bv))
        print("\nthe square is left on screen at the winning position.")
    print("wrote %s" % a.out)

    wr(ser, R_ROICTL, 0x00)
    set_delay(ser, 0)
    ser.close()
    if live:
        print("close the plot window to exit.")
        live[0].ioff()
        live[0].show()
    # Closing the plot window tears down the interpreter both windows share, so by
    # the time we get here the Toplevel may already be gone. Not an error.
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()

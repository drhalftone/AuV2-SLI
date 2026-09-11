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
sensor than a 16x16 one. If two tiles both rail at 1023 the comparison between
them is meaningless and the search follows the wrong one. So the exposure is
re-chosen at every level: measure, and if the level's best reading is near the
ceiling, cut the exposure and do that level again.

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
    ap.add_argument("--expo", type=float, default=6175.0, help="exposure, us")
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

    if a.set_mode:
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
    wcsv.writerow(["level", "size", "x", "y", "expo_us", "mean", "nframes"])
    fh.flush()

    samples = []          # (x, y, size, mean) for the diagram
    seq = []              # every reading, in order, for the profile
    npos = [0]

    def show(x, y, s):
        canvas.coords(rect, x, y, x + s, y + s)
        root.update()

    def measure(x, y, s):
        show(x, y, s)
        ser.reset_input_buffer()
        collect(ser, a.settle, timeout=3.0)
        rows = collect(ser, a.frames, timeout=5.0)
        good = [m for m, n, t, c in rows if n == 256]
        v = sum(good) / len(good) if good else float("nan")
        npos[0] += 1
        seq.append(v)
        samples.append((x, y, s, v))
        wcsv.writerow([len(samples), s, x, y, round(expo_us[0], 2),
                       round(v, 2), len(good)])
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
        prof.set_data(range(len(seq)), seq)
        axp.relim(); axp.autoscale_view()
        hdr.set_text(note)
        try:
            if not plt.fignum_exists(fig.number):
                raise RuntimeError
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
        except Exception:
            print("plot gone -- continuing")

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
            while True:                      # repeat the level if it saturates
                lvl = []
                for yy in ys:
                    for xx in xs:
                        v = measure(xx, yy, size)
                        lvl.append((xx, yy, size, v))
                        b = max(lvl, key=lambda t: t[3])
                        redraw((xx, yy, size), best or b,
                               "level %d   square %d px   %d x %d positions   "
                               "exposure %.0f us" % (level, size, len(xs), len(ys))
                               + chr(10)
                               + "at (%d,%d)  mean %7.2f   best so far %7.2f at "
                                 "(%d,%d)   %d measurements"
                                 % (xx, yy, v, b[3], b[0], b[1], npos[0]))
                b = max(lvl, key=lambda t: t[3])
                if b[3] < 950 or expo_us[0] <= 30:
                    break
                # A big square can rail the sensor, and two railed tiles cannot be
                # told apart -- so back the exposure off and do the level again.
                new = max(20.0, expo_us[0] / 4.0)
                print("  level %d saturated (best %.0f) -- exposure %.0f -> %.0f us"
                      % (level, b[3], expo_us[0], new))
                set_expo(new)
                samples[:] = [s for s in samples if s[2] != size]
            best = b
            print("  level %d: square %d px -> best (%d,%d) mean %.2f  [%d positions]"
                  % (level, size, b[0], b[1], b[3], len(lvl)))
            if size <= a.size:
                break
            x0, y0 = b[0], b[1]
            x1, y1 = b[0] + size, b[1] + size
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

    wr(ser, R_ROICTL, 0x80)
    set_delay(ser, 0)
    ser.close()
    if live:
        print("close the plot window to exit.")
        live[0].ioff()
        live[0].show()
    root.destroy()


if __name__ == "__main__":
    main()

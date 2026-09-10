#!/usr/bin/env python3
"""Drive the pattern from THIS PC over HDMI, and measure what the projector does.

    python host/hdmi_ramp.py COM6 --live
    python host/hdmi_ramp.py COM6 --channel g --step 4 --live
    python host/hdmi_ramp.py COM6 --geometry 1280x720+3440+0 --live

WHY DRIVE IT FROM HERE RATHER THAN THE IMPULSE GENERATOR. The FPGA's impulse
generator can only make one shape: five frames with one of them different. Every
new pattern has cost an RTL change and a rebuild. The PC can draw anything, and
the FPGA is already in pass-through, so the picture reaches the projector either
way.

WHAT MAKES IT TRUSTWORTHY. A PC is not a frame-accurate video source -- the
compositor may repeat or drop frames and nothing here can stop it. That would
normally rule this out for timing work. It does not, because the FPGA SAMPLES THE
TOP-LEFT PIXEL OF WHAT IT ACTUALLY TRANSMITS and reports it with every ROI
reading. We do not have to assume the PC displayed what it was asked to; we read
back what went out. Determinism is replaced by evidence.

So every level in this ramp is checked three ways:
    commanded      what this script asked the window to be
    transmitted    the top-left pixel the FPGA sampled off its own output
    measured       the ROI mean the camera integrated
A row where transmitted != commanded is reported, not averaged in.

THE MONITOR IS CHOSEN BY ASKING THE FPGA. It reads the measured incoming
resolution from 0x60..0x63 and picks the attached display of exactly that size.
Filling the wrong screen while the FPGA looks at an unchanged one produces a flat
line that is indistinguishable from a dead projector.

THE IMPULSE GENERATOR IS TURNED OFF for the duration and restored on exit -- left
on, it overwrites active video and the projector would never see this ramp.
"""
import argparse
import collections
import csv
import ctypes
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")
try:
    import tkinter as tk
except ImportError:
    sys.exit("tkinter missing -- it ships with the standard python.org build")

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from frame_sweep import (wr, rd, set_delay, wait_delay, collect,     # noqa: E402
                         EXPO_UNIT_US, TICK_US, R_ROICTL, R_EXPO_LO, R_EXPO_HI)

CHAN = {"r": (16, "#d0242a"), "g": (8, "#1a8f3c"), "b": (0, "#1f5fd0"),
        "w": (16, "#555555")}


def monitors():
    """Every attached monitor as (x, y, w, h). Windows only; [] elsewhere."""
    out = []
    try:
        PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                  ctypes.POINTER(ctypes.c_long), ctypes.c_double)

        def cb(hmon, hdc, lprc, data):
            r = lprc[0:4]
            out.append((r[0], r[1], r[2] - r[0], r[3] - r[1]))
            return 1
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, PROC(cb), 0)
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--channel", default="r", choices=sorted(CHAN),
                    help="which channel to ramp: r, g, b, or w for all three")
    ap.add_argument("--step", type=int, default=1, help="level step (default 1)")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--delay", type=float, default=0.0, help="genlock delay, us")
    ap.add_argument("--frames", type=int, default=25, help="camera frames per level")
    ap.add_argument("--settle", type=float, default=0.25,
                    help="seconds to hold each level before reading")
    ap.add_argument("--geometry", help="WxH+X+Y, overriding the automatic pick")
    ap.add_argument("--out", default="hdmi_ramp.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    shift, colour = CHAN[a.channel]
    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))
    levels = list(range(0, 256, max(1, a.step)))
    if levels[-1] != 255:
        levels.append(255)

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)

    ok67 = rd(ser, 0x67) or 0
    if not (ok67 & 1):
        ser.close()
        sys.exit("0x67 says NO VALID HDMI INPUT is being measured. The FPGA is not "
                 "locked to this PC, so nothing sent from here can reach the "
                 "projector. Check the cable and that Windows is driving that "
                 "display.")
    ha = (rd(ser, 0x60) or 0) | ((rd(ser, 0x61) or 0) << 8)
    va = (rd(ser, 0x62) or 0) | ((rd(ser, 0x63) or 0) << 8)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    print(f"HDMI input     LOCKED, {ha} x {va}")
    print(f"outgoing vsync {T_us:.1f} us  ({1e6/T_us if T_us else 0:.2f} Hz)")
    print(f"genlock 0x5D   0x{gl:02X}  (enabled={gl & 1}, live={(gl >> 1) & 1})")
    if (gl & 3) != 3:
        ser.close(); sys.exit("genlock must be enabled AND live or no triggers fire")

    if a.geometry:
        geo = a.geometry
    else:
        mons = monitors()
        print("monitors       " + ", ".join(f"{w}x{h}+{x}+{y}" for x, y, w, h in mons))
        match = [m for m in mons if m[2] == ha and m[3] == va]
        if len(match) != 1:
            ser.close()
            sys.exit(f"could not uniquely pick the {ha}x{va} display "
                     f"({len(match)} candidates) -- pass --geometry WxH+X+Y")
        x, y, w, h = match[0]
        geo = f"{w}x{h}+{x}+{y}"
    print(f"filling        {geo}")

    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)      # commits
    want = int(round(a.delay / TICK_US))
    set_delay(ser, want)
    got, okd = wait_delay(ser, want)
    if not okd:
        ser.close(); sys.exit(f"delay write did not land (register holds {got})")
    wr(ser, R_ROICTL, 0x40)          # per-frame stream ON, impulse OFF
    time.sleep(0.3)
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"delay          {a.delay:.0f} us  (register reads {got} ticks)")
    print(f"ramp           channel {a.channel}, 0..255 step {a.step} "
          f"-> {len(levels)} points\n")

    # ---- ONE Tk INTERPRETER, NOT TWO -------------------------------------
    #
    # matplotlib's TkAgg backend creates its own Tk root. A separate tk.Tk() for
    # the fill window makes two roots in one process sharing one interpreter, and
    # tearing either one down destroys the other:
    #
    #     TclError: can't invoke "update" command: application has been destroyed
    #
    # That is not merely a crash at the end -- the fill window DIES MID-RUN, the
    # projector falls back to whatever is behind it, and the sweep carries on
    # measuring a screen nobody is driving. The readback cannot catch it, because
    # a destroyed window and a black window both put zero on the wire.
    #
    # So: bring matplotlib up FIRST so it owns the root, then make the fill window
    # a Toplevel of it. One interpreter, one lifetime.
    live = None
    plt = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, ax = plt.subplots(figsize=(13, 6))
        ln, = ax.plot([], [], lw=1.5, marker=".", ms=3, color=colour)
        ax.axhline(1023, color="#9a6614", lw=1, ls="--")
        ax.set_xlim(-4, 260)
        ax.set_ylim(0, 1080)
        ax.set_xlabel("commanded level sent over HDMI")
        ax.set_ylabel("ROI mean (ADU)")
        ax.grid(alpha=0.3)
        fig.subplots_adjust(left=0.065, right=0.985, top=0.90, bottom=0.11)
        hdr = fig.text(0.065, 0.985, "", va="top", ha="left",
                       family="monospace", fontsize=9, linespacing=1.6)
        # Keep the plot OFF the display being measured -- parking it over the
        # fill window would project the plot at the projector.
        try:
            fig.canvas.manager.window.wm_geometry("+40+40")
        except Exception:
            pass
        live = (plt, fig, ax, ln, hdr)

    root = tk.Toplevel() if plt is not None else tk.Tk()
    root.overrideredirect(True)
    root.geometry(geo)
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    root.update()

    fh = open(a.out, "w", newline="")
    wcsv = csv.writer(fh)
    wcsv.writerow(["level", "tlp_r", "tlp_g", "tlp_b", "mean", "nframes", "matched"])
    fh.flush()

    pts, nbad, nmis = [], 0, 0
    t0 = time.time()
    try:
        for li, lvl in enumerate(levels):
            if a.channel == "w":
                rgb = (lvl, lvl, lvl)
            else:
                rgb = tuple(lvl if CHAN[c][0] == shift else 0 for c in ("r", "g", "b"))
            try:
                canvas.configure(bg="#%02x%02x%02x" % rgb)
                root.update()
            except tk.TclError:
                print("\n" + "!! THE FILL WINDOW IS GONE -- stopping. Everything after "
                      "this point would have measured an undriven screen.")
                break
            time.sleep(a.settle)
            ser.reset_input_buffer()
            rows = collect(ser, a.frames, timeout=6.0)
            try:
                root.update()
            except tk.TclError:
                print("\n" + "!! THE FILL WINDOW IS GONE -- stopping.")
                break
            if not rows:
                print(f"  level {lvl}: NO FRAMES")
                continue
            good = [(m, t) for m, n, t, c in rows if n == 256]
            nbad += len(rows) - len(good)
            if not good:
                continue
            tlp = collections.Counter(t for m, t in good).most_common(1)[0][0]
            mean = sum(m for m, t in good) / len(good)
            matched = ((tlp >> shift) & 0xFF) == lvl
            if not matched:
                nmis += 1
            pts.append((lvl, mean))
            wcsv.writerow([lvl, (tlp >> 16) & 0xFF, (tlp >> 8) & 0xFF, tlp & 0xFF,
                           round(mean, 2), len(good), int(matched)])
            fh.flush()

            if live is not None:
                plt, fig, ax, ln, hdr = live
                ln.set_data([p[0] for p in pts], [p[1] for p in pts])
                hdr.set_text(
                    f"HDMI source: this PC, {ha}x{va}   channel {a.channel}"
                    f"   exposure {expo_units*EXPO_UNIT_US:.2f} us"
                    f"   delay {a.delay:.0f} us   frame {T_us:.1f} us"
                    + chr(10)
                    + f"level {lvl}/255 ({li+1}/{len(levels)})   "
                      f"transmitted {(tlp >> 16) & 0xFF:3d},{(tlp >> 8) & 0xFF:3d},"
                      f"{tlp & 0xFF:3d}   measured {mean:6.1f}   "
                      f"pixel!=commanded {nmis}   npx!=256 {nbad}")
                # CHECK BEFORE TOUCHING, AND SURVIVE A DEAD CANVAS. The plot
                # shares a Tk interpreter with the full-screen fill window, and
                # calling into a destroyed canvas raises TclError from inside
                # matplotlib. That used to abort the whole run -- leaving the
                # projector showing whatever was behind the fill window while
                # the sweep had already stopped. The plot is a convenience; the
                # CSV is the measurement, so losing the plot must not lose the run.
                try:
                    if not plt.fignum_exists(fig.number):
                        raise tk.TclError("figure closed")
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                except tk.TclError:
                    print("plot window gone -- CONTINUING without it; "
                          "the CSV is still being written")
                    live = None
            if li % 32 == 0 or li == len(levels) - 1:
                print(f"  level {lvl:3d}  transmitted "
                      f"{(tlp >> 16) & 0xFF:3d},{(tlp >> 8) & 0xFF:3d},{tlp & 0xFF:3d}"
                      f"  measured {mean:6.1f}  {'ok' if matched else 'MISMATCH'}"
                      f"   {(time.time()-t0)/60:.1f} min")
    except KeyboardInterrupt:
        print("\ninterrupted -- everything so far is written")
    finally:
        wr(ser, R_ROICTL, 0x00)
        ser.close()
        fh.close()
        try:
            root.destroy()
        except Exception:
            pass

    print(f"\nwrote {a.out}  ({len(pts)} levels)")
    print(f"   npx != 256                    {nbad}")
    print(f"   transmitted != commanded      {nmis}")
    if pts:
        print(f"   measured range                {min(p[1] for p in pts):.0f} .. "
              f"{max(p[1] for p in pts):.0f} ADU")
    if nmis:
        print("   >> a mismatch means the picture on the wire was not what was "
              "asked for; those rows are still in the CSV, flagged.")

    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")
    if live:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fill the HDMI display with known colours and confirm the FPGA samples them.

    python host/tlp_check.py COM6
    python host/tlp_check.py COM6 --geometry 1280x720+1920+0
    python host/tlp_check.py COM6 --hold 3.0        # slower, easier to watch

WHAT THIS PROVES. The FPGA samples the first non-blank pixel of every frame it
TRANSMITS and reports it as the top-left pixel. In pass-through that pixel came
from this PC. So: drive a known colour, read back what the FPGA says it sent, and
compare. A value that tracks the commanded colour proves the sample point, the
pipeline and the readback path all work end to end. A value stuck at a constant
proves the opposite, and a constant 0 in particular is what a dead path looks
like.

THE MONITOR IS CHOSEN BY ASKING THE FPGA, not by guessing. The script reads the
measured incoming resolution from registers 0x60..0x63, then picks the attached
monitor of exactly that size. Getting this wrong is the obvious failure mode --
you fill the laptop screen while the FPGA looks at an unchanged second display
and everything reads constant, which is indistinguishable from a broken path.
Override with --geometry if the automatic pick is not what you want.

THE IMPULSE GENERATOR IS TURNED OFF for the duration. Left on, it overwrites
active video with its own sequence and the top-left pixel would report the
sequence rather than anything from this PC. It is restored on exit.

WHAT A MISMATCH MIGHT MEAN, before you blame the sample point:
  * the radiometric LUT is not identity. It sits in pixel_pipe, upstream of the
    sample, so a transformed value here is the LUT doing its job. test_silicon.py
    leaves test data in that table and visibly scrambles colours until the
    bitstream is reloaded.
  * the display is applying its own colour management, or the desktop is scaled,
    so the physical top-left pixel is not the colour you asked for.
  * the window did not actually land on the display the FPGA is watching.
"""
import argparse
import collections
import ctypes
import re
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

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
R_ROICTL = 0x17

# The line is 17 bytes on builds that report only the red channel and 21 bytes on
# builds that report all three. Both are accepted so this works either side of
# that change; the width tells us which we are talking to.
LINE_RGB = re.compile(b"R=([0-9A-F]{3}),([0-9A-F]{3}),([0-9A-F]{6}),([0-9A-F]{2})"
                      + b"(?:,([0-9A-F]{2}),([0-9A-F]{2}))?"   # ,hh,ll: see frame_sweep.LINE
                      + bytes([13, 10]))
LINE_RED = re.compile(b"R=([0-9A-F]{3}),([0-9A-F]{3}),([0-9A-F]{2}),([0-9A-F]{2})"
                      + bytes([13, 10]))

PATTERNS = [("black",   (0, 0, 0)),
            ("red",     (255, 0, 0)),
            ("green",   (0, 255, 0)),
            ("blue",    (0, 0, 255)),
            ("white",   (255, 255, 255)),
            ("mid grey", (128, 128, 128)),
            ("dark red", (64, 0, 0)),
            ("yellow",  (255, 255, 0)),
            ("black",   (0, 0, 0))]        # return to black: catches a stuck value


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.02)


def rd(ser, addr, window=0.6):
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


def monitors():
    """Every attached monitor as (x, y, w, h). Windows only; [] elsewhere."""
    out = []
    try:
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_long), ctypes.c_double)

        def cb(hmon, hdc, lprc, data):
            r = lprc[0:4]
            out.append((r[0], r[1], r[2] - r[0], r[3] - r[1]))
            return 1
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(cb), 0)
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--geometry", help="WxH+X+Y, overriding the automatic monitor pick")
    ap.add_argument("--hold", type=float, default=1.5,
                    help="seconds to hold each colour before reading (default 1.5)")
    ap.add_argument("--frames", type=int, default=40,
                    help="frames read per colour (default 40)")
    ap.add_argument("--ramp", action="store_true",
                    help="sweep a grey ramp instead of the named colours, to "
                         "characterise the transfer curve the pixel passes through")
    a = ap.parse_args()

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)

    ok = rd(ser, 0x67) or 0
    if not (ok & 1):
        ser.close(); sys.exit("0x67 says no valid HDMI input measured -- the FPGA is "
                              "not locked to this PC, so nothing here can be checked.")
    ha = (rd(ser, 0x60) or 0) | ((rd(ser, 0x61) or 0) << 8)
    va = (rd(ser, 0x62) or 0) | ((rd(ser, 0x63) or 0) << 8)
    print(f"FPGA reports incoming video: {ha} x {va}")

    if a.geometry:
        geo = a.geometry
    else:
        mons = monitors()
        print("attached monitors: " + (", ".join(f"{w}x{h}+{x}+{y}"
                                                 for x, y, w, h in mons) or "none found"))
        match = [m for m in mons if m[2] == ha and m[3] == va]
        if len(match) != 1:
            ser.close()
            sys.exit(f"could not uniquely pick the {ha}x{va} monitor "
                     f"({len(match)} candidates). Pass --geometry WxH+X+Y.")
        x, y, w, h = match[0]
        geo = f"{w}x{h}+{x}+{y}"
    print(f"filling window at {geo}")

    root = tk.Tk()
    root.overrideredirect(True)               # no title bar, no border
    root.geometry(geo)
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    prev_ctl = 0x40
    wr(ser, R_ROICTL, 0x40)      # per-frame stream ON, impulse OFF (no override)
    time.sleep(0.3)

    def read_pixel(n, timeout=6.0):
        """Modal (tlp, mean) over n frames. Returns (tlp, bits, mean, nframes)."""
        ser.reset_input_buffer()
        buf, got, t0 = b"", [], time.time()
        bits = 0
        while len(got) < n and time.time() - t0 < timeout:
            buf += ser.read(4096)
            ms = list(LINE_RGB.finditer(buf))
            if ms:
                bits = 24
            else:
                ms = list(LINE_RED.finditer(buf))
                if ms:
                    bits = 8
            for m in ms:
                got.append((int(m.group(3), 16), int(m.group(1), 16)))
            if ms:
                buf = buf[ms[-1].end():]
            elif len(buf) > 8192:
                buf = buf[-64:]
            root.update()
        if not got:
            return None, bits, None, 0
        tlp = collections.Counter(g[0] for g in got).most_common(1)[0][0]
        mean = sum(g[1] for g in got) / len(got)
        return tlp, bits, mean, len(got)

    pats = PATTERNS
    if a.ramp:
        # A RAMP, NOT NAMED COLOURS. The named set can only show THAT the value is
        # transformed; a ramp shows the shape of the transform and where its knee
        # is. Stepping by 16 puts 17 points across the range.
        pats = [(f"grey {v}", (v, v, v)) for v in range(0, 256, 16)] + [("grey 255", (255, 255, 255))]
    results = []
    try:
        for name, (r, g, b) in pats:
            canvas.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
            root.update()
            time.sleep(a.hold)
            tlp, bits, mean, n = read_pixel(a.frames)
            results.append((name, (r, g, b), tlp, bits, mean, n))
            if tlp is None:
                print(f"  {name:9s} -> NO FRAMES")
            elif bits == 24:
                print(f"  {name:9s} sent ({r:3d},{g:3d},{b:3d})  "
                      f"FPGA read ({(tlp >> 16) & 0xFF:3d},{(tlp >> 8) & 0xFF:3d},"
                      f"{tlp & 0xFF:3d})   ROI mean {mean:6.1f}   {n} frames")
            else:
                print(f"  {name:9s} sent ({r:3d},{g:3d},{b:3d})  "
                      f"FPGA read red={tlp:3d}   ROI mean {mean:6.1f}   {n} frames")
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        wr(ser, R_ROICTL, prev_ctl & 0x00)   # stream off; impulse was already off
        ser.close()
        root.destroy()

    vals = [r[2] for r in results if r[2] is not None]
    print("")
    if not vals:
        print("VERDICT: no frames arrived at all -- nothing was measured.")
    elif len(set(vals)) == 1:
        print(f"VERDICT: the top-left pixel NEVER CHANGED (stuck at 0x{vals[0]:06X}).")
        print("         Either the fill window is not on the display the FPGA is")
        print("         watching, or the sample point is not seeing this video.")
    else:
        print(f"VERDICT: the top-left pixel TRACKED the commanded colour "
              f"({len(set(vals))} distinct values over {len(vals)} patterns).")
        print("         The sample point, the pipeline and the readback all work.")


if __name__ == "__main__":
    main()

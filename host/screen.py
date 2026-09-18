#!/usr/bin/env python3
"""Put a pixel buffer on the projector, one image pixel per HDMI pixel.

    python host/screen.py --selftest COM6        # prove the mapping is 1:1
    python host/screen.py --demo fringes         # show a pattern and hold it

    from screen import Screen
    s = Screen(set_mode=True)                    # 800x600 @120 on the non-primary display
    buf = np.zeros((s.h, s.w, 3), np.uint8)      # H x W x 3, uint8, exactly the panel size
    buf[486:518, 372:404] = 255
    s.show(buf)

WHY A BUFFER. Every measurement here needs *specific projector pixels* lit -- the ROI
square, the hole, the fringe -- and drawing them as widgets or canvas items leaves the
mapping between what was asked for and what reached the DMD to chance. A buffer the size
of the panel, blitted 1:1, makes that mapping the identity, and `--selftest` checks it
against the FPGA rather than trusting it.

WHAT CAN BREAK 1:1, and what this does about it:
  - Windows DPI virtualisation scales windows behind your back -> the process is marked
    per-monitor DPI aware BEFORE Tk starts.
  - A resampled blit smears a one-pixel line into two -> the image is passed at exactly
    the panel size and never resized, so no filtering runs.
  - Window decoration or a compositor offset shifts everything by a few pixels ->
    the window is borderless (overrideredirect) at the display's own origin.
  - The panel is not the mode you think -> the size is read back and checked, and
    set_mode=True asks for the mode first.

THE SELF-TEST IS THE POINT. pixel_pipe reports the FIRST ACTIVE PIXEL it transmitted with
every ROI line (the "top-left pixel", tlp). Writing a known colour into buf[0, 0] and
reading it back closes the loop from numpy array to HDMI: if they differ, the picture is
being scaled, shifted or colour-mangled, and no measurement above it means anything.
"""
import argparse
import sys
import time
import tkinter as tk

import numpy as np

try:
    from PIL import Image, ImageTk
except ImportError:                                            # pragma: no cover
    sys.exit("pillow missing:  pip install pillow")

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from align_roi import displays, set_mode                        # noqa: E402


def _dpi_aware():
    """Tell Windows not to scale this process's windows. Must run before Tk starts."""
    import ctypes
    try:                                                        # Win 10 1703+
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor v2"
    except Exception:
        pass
    try:                                                        # Win 8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


class Screen(object):
    """A borderless window covering one display, fed whole frames as numpy arrays."""

    def __init__(self, device=None, width=800, height=600, hz=120, set_mode_first=False,
                 master=None, verbose=True):
        self.dpi = _dpi_aware()
        devs = [d for d in displays() if (d[1], d[2]) != (0, 0)]   # non-primary displays
        if device:
            devs = [d for d in displays() if d[0] == device]
        if not devs:
            raise RuntimeError("no projector display found (only the primary is attached?)")
        d = devs[0]
        if set_mode_first and (d[3], d[4], d[5]) != (width, height, hz):
            ok, msg = set_mode(d[0], width, height, hz)
            if verbose:
                print("set %s to %dx%d@%d: %s" % (d[0], width, height, hz, msg))
            time.sleep(4)
            d = [x for x in displays() if x[0] == d[0]][0]
        self.device, self.x, self.y, self.w, self.h, self.hz = d
        if (self.w, self.h) != (width, height):
            raise RuntimeError("%s is %dx%d@%d, expected %dx%d@%d -- pass set_mode_first=True"
                               % (self.device, self.w, self.h, self.hz, width, height, hz))

        # matplotlib may already own the Tk interpreter; a second Tk() would be a second
        # event loop in one process.
        self.root = tk.Toplevel(master) if (master is not None or tk._default_root) else tk.Tk()
        self.root.overrideredirect(True)
        self.root.geometry("%dx%d+%d+%d" % (self.w, self.h, self.x, self.y))
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black", cursor="none")
        self.label = tk.Label(self.root, bd=0, highlightthickness=0, bg="black")
        self.label.place(x=0, y=0, width=self.w, height=self.h)
        self._img = None
        self.root.update()
        got = (self.root.winfo_width(), self.root.winfo_height(),
               self.root.winfo_rootx(), self.root.winfo_rooty())
        if got != (self.w, self.h, self.x, self.y):
            raise RuntimeError("window landed at %dx%d%+d%+d, wanted %dx%d%+d%+d "
                               "(DPI awareness %s)" % (got + (self.w, self.h, self.x, self.y,
                                                              self.dpi)))
        if verbose:
            print("screen %s %dx%d@%d at %+d%+d, DPI awareness %s"
                  % (self.device, self.w, self.h, self.hz, self.x, self.y, self.dpi))

    # ---- drawing ----------------------------------------------------------------
    def blank(self):
        """An all-black buffer of the right shape and dtype."""
        return np.zeros((self.h, self.w, 3), np.uint8)

    def show(self, buf, settle=0.0):
        """Blit an H x W x 3 (or H x W) uint8 array. No scaling, no filtering."""
        a = np.asarray(buf)
        if a.ndim == 2:
            a = np.dstack([a] * 3)
        if a.shape[:2] != (self.h, self.w):
            raise ValueError("buffer is %dx%d, panel is %dx%d -- resizing would break 1:1"
                             % (a.shape[1], a.shape[0], self.w, self.h))
        if a.dtype != np.uint8:
            raise ValueError("buffer must be uint8, got %s" % a.dtype)
        # keep a reference: Tk drops the image the moment Python does
        self._img = ImageTk.PhotoImage(Image.fromarray(np.ascontiguousarray(a), "RGB"))
        self.label.configure(image=self._img)
        self.root.update()
        if settle:
            time.sleep(settle)
            self.root.update()

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


# ---- patterns ---------------------------------------------------------------------
def square(s, cx, cy, size, level=255, bg=0, colour=(1, 1, 1)):
    """A square of `size` px centred on (cx, cy) over a flat background."""
    buf = np.full((s.h, s.w, 3), bg, np.uint8)
    x0, y0 = cx - size // 2, cy - size // 2
    buf[max(0, y0):y0 + size, max(0, x0):x0 + size] = [int(level * c) for c in colour]
    return buf


def fringes(s, period, phase=0.0, orient=0, amp=127.5, offset=127.5):
    """One cosine period every `period` px -- horizontal stripes unless orient=1."""
    n = s.h if orient == 0 else s.w
    v = offset + amp * np.cos(2 * np.pi * (np.arange(n) / float(period)) + phase)
    line = np.clip(np.rint(v), 0, 255).astype(np.uint8)
    img = np.repeat(line[:, None], s.w, 1) if orient == 0 else np.repeat(line[None, :], s.h, 0)
    return np.dstack([img] * 3)


# ---- self-test --------------------------------------------------------------------
def selftest(port):
    """Write known colours into pixel (0,0) and ask the FPGA what it transmitted."""
    import collections
    import serial
    from frame_sweep import wr, rd, collect, R_ROICTL

    s = Screen(set_mode_first=True)
    ser = serial.Serial(port, 115200, timeout=0.05)
    time.sleep(0.3)
    wr(ser, 0x13, 0x00)                       # pass HDMI through, no FPGA pattern
    wr(ser, 0x16, 0x00)
    wr(ser, R_ROICTL, 0x40)                   # ROI stream on, impulse generator off
    print("genlock 0x%02X" % (rd(ser, 0x5D) or 0))
    bad = 0
    for want in ((0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255),
                 (18, 52, 86)):
        buf = s.blank()
        buf[0, 0] = want                      # only the first pixel differs
        buf[10:20, 10:20] = 200               # something else on screen, away from (0,0)
        s.show(buf, settle=0.4)
        ser.reset_input_buffer()
        collect(ser, 6, timeout=3)
        rows = collect(ser, 20, timeout=4, sat=True)
        seen = collections.Counter("%06X" % r[2] for r in rows)
        got = seen.most_common(1)[0][0] if seen else "none"
        ok = got == "%02X%02X%02X" % want
        bad += 0 if ok else 1
        print("  buf[0,0] = %-15s FPGA transmitted %s   %s%s"
              % (str(tuple(want)), got, "OK" if ok else "MISMATCH",
                 "" if len(seen) < 2 else "   (also saw %s)" % dict(seen)))
    wr(ser, R_ROICTL, 0x00)
    ser.close()
    s.close()
    print("1:1 mapping %s" % ("CONFIRMED" if bad == 0 else "FAILED on %d of 6" % bad))
    return bad == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--selftest", action="store_true",
                    help="check the buffer reaches the DMD unchanged, via the FPGA")
    ap.add_argument("--demo", choices=("fringes", "square", "ramp"),
                    help="show a pattern and hold it until Enter")
    ap.add_argument("--period", type=int, default=8, help="fringe period, px")
    ap.add_argument("--x", type=int, default=384)
    ap.add_argument("--y", type=int, default=496)
    ap.add_argument("--size", type=int, default=32)
    ap.add_argument("--level", type=int, default=255)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest(a.port) else 1)
    if a.demo:
        s = Screen(set_mode_first=True)
        if a.demo == "fringes":
            s.show(fringes(s, a.period))
        elif a.demo == "square":
            s.show(square(s, a.x, a.y, a.size, a.level))
        else:
            s.show(np.repeat(np.repeat((np.arange(s.w) * 256 // s.w).astype(np.uint8)[None, :],
                                       s.h, 0)[:, :, None], 3, 2))
        input("showing %s -- press Enter to close " % a.demo)
        s.close()
        return
    ap.print_help()


if __name__ == "__main__":
    main()

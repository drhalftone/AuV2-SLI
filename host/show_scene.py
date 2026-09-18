#!/usr/bin/env python3
"""Put the matched-background measurement scene on the projector and hold it.

    python host/show_scene.py --notch-w 184 --notch-h 184 --target 255 --size 32

The camera ROI sits at the BOTTOM EDGE of the projected frame, so the black hole is
open at the bottom: the white background is an upside-down U (across the top, down
both sides), and the target square sits in the notch, flush with the bottom edge.

Import `build()` from other host/ scripts to draw the same scene into a buffer
without opening a window of their own -- see square_delay_sweep.py.
"""
import sys
import time
import argparse

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from screen import Screen                                       # noqa: E402


def build(s, notch_w, notch_h, target, size, bg=255, cx=384):
    """White background with a bottom-open notch, target square inside it."""
    buf = s.blank()
    buf[:, :] = bg
    if notch_w and notch_h:
        x0 = max(0, cx - notch_w // 2)
        buf[s.h - notch_h:s.h, x0:x0 + notch_w] = 0        # notch up from the bottom
    if size:
        sx = max(0, cx - size // 2)
        buf[s.h - size:s.h, sx:sx + size] = target         # target on the bottom edge
    return buf


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notch-w", type=int, default=184)
    ap.add_argument("--notch-h", type=int, default=184)
    ap.add_argument("--target", type=int, default=255)
    ap.add_argument("--size", type=int, default=32)
    ap.add_argument("--bg", type=int, default=255)
    ap.add_argument("--x", type=int, default=384)
    a = ap.parse_args()
    s = Screen(set_mode_first=True)
    s.show(build(s, a.notch_w, a.notch_h, a.target, a.size, a.bg, a.x))
    print("background %d, notch %dx%d up from the bottom at x=%d, target %dx%d level %d"
          % (a.bg, a.notch_w, a.notch_h, a.x, a.size, a.size, a.target), flush=True)
    print("holding -- Ctrl+C or stop this task to clear the screen", flush=True)
    while True:
        s.root.update()
        time.sleep(0.05)


if __name__ == "__main__":
    main()

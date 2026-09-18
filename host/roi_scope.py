#!/usr/bin/env python3
"""Rolling scope: the ROI mean of the last N triggers, updating live.

    python host/roi_scope.py COM6
    python host/roi_scope.py COM6 --expo 5 --delay 0 --colour red --bright 0
    python host/roi_scope.py COM6 -n 2000

ONE POINT PER TRIGGER, NOT PER ARRIVED FRAME. The x axis is the trigger ordinal
the FPGA stamps on every line, so each sample sits at the position of the trigger
that produced it. A frame that never arrived leaves a NaN, which matplotlib draws
as a break in the trace -- a gap of exactly the right width, in the right place.

That distinction is the whole point. Plotting in arrival order would close a gap
up silently: 1000 points would still be drawn, the trace would still look
continuous, and a dropped frame would shift everything after it by one while
looking completely healthy. This is the same failure the old five-frame block
format had, one level down.

A mean whose npx is not 256 is also plotted as NaN. The ROI has drifted off the
sensor and the number is meaningless -- drawing it would put a plausible value on
the screen with nothing to say it is wrong.

THE ONE LIMIT, STATED: the ordinal is 8 bits, so it wraps every 256 triggers --
2.1 s at 120 Hz. A gap longer than that aliases and is under-counted. Any outage
that long will be obvious on screen for other reasons.

Line format, 21 bytes:  R=mmm,nnn,rrggbb,cc<CR><LF>  -- see roi_line.v.
"""
import argparse
import collections
import math
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
R_IMPCYC, R_IMPLVL = 0x11, 0x12
R_ROICTL, R_EXPO_LO, R_EXPO_HI = 0x17, 0x1A, 0x1B
R_DLY0, R_DLY1, R_DLY2, R_IMPRGB = 0x1C, 0x1D, 0x1E, 0x1F
COLOURS = {"white": 0x07, "red": 0x04, "green": 0x02, "blue": 0x01}
EXPO_UNIT_US = 0.375
TICK_US = 0.01

LINE = re.compile(b"R=([0-9A-F]{3}),([0-9A-F]{3}),([0-9A-F]{6}),([0-9A-F]{2})"
                  + b"(?:,([0-9A-F]{2}),([0-9A-F]{2}))?"   # ,hh,ll: see frame_sweep.LINE
                  + bytes([13, 10]))
NAN = float("nan")


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.02)


def rd(ser, addr, window=0.6):
    # The reply is THREE bytes: addr, value, checksum. No sync prefix.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("-n", "--frames", type=int, default=200,
                    help="triggers held in the window (default 200)")
    ap.add_argument("--expo", type=float, help="exposure, us (unset = untouched)")
    ap.add_argument("--delay", type=float, help="genlock delay, us (unset = untouched)")
    ap.add_argument("--colour", "--color", dest="colour", choices=sorted(COLOURS))
    ap.add_argument("--level", type=int, help="flash level 0..255")
    ap.add_argument("--bright", type=int, choices=range(0, 5), metavar="P",
                    help="which sequence position carries the flash")
    ap.add_argument("--impulse", dest="impulse", action="store_true", default=None,
                    help="force the K/W impulse sequence ON (overrides pass-through video)")
    ap.add_argument("--no-impulse", dest="impulse", action="store_false",
                    help="force it OFF, so the ROI sees whatever video is passing through")
    ap.add_argument("--fps", type=float, default=20.0, help="screen redraws per second")
    a = ap.parse_args()

    ser = serial.Serial(a.port, 115200, timeout=0.02)
    time.sleep(0.3)

    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    print(f"frame period  {T_us:.1f} us  ({1e6/T_us:.2f} Hz)" if per
          else "frame period  UNREADABLE")
    print(f"genlock 0x5D  0x{gl:02X}  (enabled={gl & 1}, live={(gl >> 1) & 1})")
    if not (gl & 1):
        ser.close(); sys.exit("genlock is not enabled (0x5D bit0 = 0) -- no triggers.")
    if not (gl & 2):
        ser.close(); sys.exit("genlock enabled but NOT LIVE (0x5D bit1 = 0): the "
                              "projector vsync is not reaching the camera, so no "
                              "triggers will fire and no frames will arrive.")

    # Only touch what was asked for. A scope that silently reconfigures the thing
    # it is measuring is how you end up debugging your own instrument.
    if a.bright is not None:
        wr(ser, R_IMPCYC, ((a.bright & 7) << 4) | 5)
    if a.colour is not None:
        wr(ser, R_IMPRGB, COLOURS[a.colour])
    if a.level is not None:
        wr(ser, R_IMPLVL, a.level & 0xFF)
    if a.expo is not None:
        u = max(1, int(round(a.expo / EXPO_UNIT_US)))
        wr(ser, R_EXPO_LO, u & 0xFF)
        wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)          # commits
        print(f"exposure      {u} units = {u * EXPO_UNIT_US:.2f} us")
    if a.delay is not None:
        t = int(round(a.delay / TICK_US))
        wr(ser, R_DLY0, t & 0xFF)
        wr(ser, R_DLY1, (t >> 8) & 0xFF)
        wr(ser, R_DLY2, (t >> 16) & 0xFF)
        print(f"delay         {a.delay:.0f} us ({t} ticks)")

    # PRESERVE THE IMPULSE STATE UNLESS TOLD OTHERWISE. Writing 0xC0
    # unconditionally forced the K/W sequence on, which replaces active video --
    # so pointing the scope at pass-through material silently destroyed the thing
    # being looked at. Read the register, set the stream bit, leave bit 7 alone.
    cur = rd(ser, R_ROICTL)
    if cur is None:
        cur = 0x80
        print("ROICTL unreadable -- assuming impulse ON")
    if a.impulse is True:
        cur |= 0x80
    elif a.impulse is False:
        cur &= ~0x80
    ctl = (cur & 0x80) | 0x40        # keep impulse as-is, per-frame stream on
    print(f"ROICTL        0x{ctl:02X}  (impulse {'ON' if ctl & 0x80 else 'OFF'}, "
          f"per-frame stream ON)")
    wr(ser, R_ROICTL, ctl)
    time.sleep(0.3)

    # READ THESE BACK RATHER THAN ECHOING WHAT WAS WRITTEN. The point of an info
    # panel is to say what the hardware is doing; repeating the command line would
    # show the right numbers even when a write had not landed. The delay register
    # is served from a ~10 Hz snapshot, and the sleep above covers that latency.
    expo_u = (rd(ser, 0x40) or 0) | ((rd(ser, 0x41) or 0) << 8)
    dly_t = ((rd(ser, 0x5A) or 0) | ((rd(ser, 0x5B) or 0) << 8)
             | ((rd(ser, 0x5C) or 0) << 16))
    per2 = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
            | ((rd(ser, 0x4C) or 0) << 16))
    T2_us = per2 * TICK_US
    impcyc = rd(ser, 0x11) or 0
    imprgb = rd(ser, 0x1F) or 0
    cname = {7: "white", 4: "red", 2: "green", 1: "blue"}.get(imprgb & 7,
                                                              "mask %X" % (imprgb & 7))
    info = (f"exposure {expo_u * EXPO_UNIT_US:8.2f} us ({expo_u} units)      "
            f"delay {dly_t * TICK_US:9.2f} us ({dly_t} ticks)      "
            f"frame {T2_us:9.1f} us ({1e6 / T2_us if T2_us else 0:.2f} Hz)\n"
            f"delay is {100.0 * dly_t * TICK_US / T2_us if T2_us else 0:5.1f} % of the "
            f"frame      sequence {impcyc & 7} frames, flash on position "
            f"{(impcyc >> 4) & 7}, {cname}      "
            f"impulse {'ON' if ctl & 0x80 else 'OFF'}")
    ser.reset_input_buffer()

    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    plt.ion()

    N = a.frames
    fig, ax = plt.subplots(figsize=(13, 6))
    xs = list(range(N))
    trace, = ax.plot([], [], lw=0.9, marker=".", ms=2.5, color="#1f5fb0")
    ax.axhline(1023, color="#9a6614", lw=1, ls="--")
    ax.set_xlim(0, N)
    ax.set_ylim(0, 1080)
    ax.set_xlabel(f"most recent {N} triggers  (newest at the right; "
                  f"a break in the trace is a missing trigger)")
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=0.3)

    # RESERVE THE TOP MARGIN EXPLICITLY. tight_layout() sizes the axes for the
    # title that exists WHEN IT RUNS, so a multi-line title set later is simply
    # clipped by the window edge -- which is what was happening. Fixed margins
    # plus figure-level text avoid it, and the header can be as many lines as it
    # needs without the axes moving underneath it.
    fig.subplots_adjust(left=0.065, right=0.985, top=0.78, bottom=0.11)
    hdr = fig.text(0.065, 0.985, "", va="top", ha="left",
                   family="monospace", fontsize=9, linespacing=1.6)

    # One slot per TRIGGER. Missing triggers are NaN, so the deque's index is
    # elapsed triggers rather than arrived frames.
    vals = collections.deque(maxlen=N)
    nseen = nmiss = nbad = ndup = 0
    last_cc = None
    buf = b""
    t_start = time.time()
    t_rate, n_rate, rate, t_draw = t_start, 0, 0.0, 0.0

    print("\nreading -- CLOSE THE PLOT WINDOW to stop.\n")
    try:
        while plt.fignum_exists(fig.number):
            buf += ser.read(4096)
            ms = list(LINE.finditer(buf))
            if ms:
                for m in ms:
                    mean = int(m.group(1), 16)
                    npx = int(m.group(2), 16)
                    cc = int(m.group(4), 16)
                    nseen += 1
                    n_rate += 1

                    if last_cc is None:
                        step = 1
                    else:
                        step = (cc - last_cc) & 0xFF
                    if step == 0:
                        ndup += 1          # same ordinal twice: do not advance
                        continue
                    for _ in range(step - 1):
                        vals.append(NAN)   # triggers that produced no line
                        nmiss += 1
                    if npx == 256:
                        vals.append(float(mean))
                    else:
                        vals.append(NAN)   # ROI off the sensor: not a measurement
                        nbad += 1
                    last_cc = cc
                buf = buf[ms[-1].end():]
            elif len(buf) > 8192:
                buf = buf[-64:]

            now = time.time()
            if now - t_rate >= 0.5:
                rate = n_rate / (now - t_rate)
                n_rate = 0
                t_rate = now

            if now - t_draw >= 1.0 / a.fps and vals:
                t_draw = now
                v = list(vals)
                trace.set_data(xs[N - len(v):], v)      # right-aligned: it scrolls
                ok = [q for q in v if not math.isnan(q)]
                if ok:
                    lo, hi = min(ok), max(ok)
                    cur = ok[-1]
                    desc = f"last {cur:.0f}   window {lo:.0f}..{hi:.0f}"
                else:
                    desc = "no valid samples in the window"
                hdr.set_text(
                    info + "\n"
                    + f"{rate:5.1f} trig/s   {nseen} frames   "
                      f"missing {nmiss}   npx!=256 {nbad}   dup {ndup}      {desc}")
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
            else:
                fig.canvas.flush_events()
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        wr(ser, R_ROICTL, ctl & 0x80)   # stop the stream, leave the impulse as found
        ser.close()

    print(f"\n{nseen} frames seen in {time.time() - t_start:.1f} s")
    print(f"   missing triggers      {nmiss}")
    print(f"   npx != 256            {nbad}")
    print(f"   duplicate ordinals    {ndup}")


if __name__ == "__main__":
    main()

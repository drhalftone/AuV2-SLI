"""How long between vertical syncs, and how much does it move? (genlock G0)

    python host/measure_vsync_period.py [COM6]

This is the master clock genlock would lock the camera to, so its stability is a
CEILING on the achievable lock quality. There is no point specifying a trigger
delay to 750 ns against a master that wanders by more than that.

WHAT WAS ALREADY THERE AND WHY IT IS NOT ENOUGH. The telemetry line carries `N=`,
which is `vs_lat` -- vsync EDGES COUNTED in the last status window. It answers
"is vsync alive" and nothing else: at 120 Hz it reads 0x33 or 0x34 and dithers
between them, so it cannot resolve the period better than about 2%, and it cannot
see jitter at all. (That dither is also why the first version of the 3b test
wrongly called a healthy system failed -- it expected a counter and got a rate.)

WHAT THIS READS. The FPGA now counts 100 MHz ticks between rising edges of
out_vsync and publishes last / min / max over each status window:

    0x4A/0x4B/0x4C   last period, 10 ns units
    0x4D/0x4E/0x4F   minimum over the window
    0x50/0x51/0x52   maximum over the window

    max - min IS the master's jitter, at 10 ns resolution.

WHICH VSYNC, AND WHY IT MATTERS. out_vsync -- what is SENT TO THE PROJECTOR --
not the incoming source vsync. The projector is the thing being synchronised to,
so the source's timing is irrelevant to when a pattern is actually on screen.
This is a separate FPGA port from the one feeding `N=`, specifically so the two
cannot be confused.

WHAT TO EXPECT, AND WHAT WOULD BE ALARMING. In OFFLINE mode out_vsync is
generated from the local clock and should be near-perfect -- jitter at the 10 ns
measurement floor. In PASSTHROUGH the output is re-timed from the recovered input
clock, which is the clock already implicated in the SXGA passthrough failure
(1280x1024 passthrough blacks the projector while the same mode offline is rock
solid). A large jitter number there is a real result, not a broken test: it would
mean genlock is an offline-mode feature until the output clock is cleaned up.
"""
import os, sys, time

import serial

PORT = "COM6"
SECONDS = 5.0
for _a in sys.argv[1:]:
    if _a.startswith("--seconds="):
        SECONDS = float(_a.split("=", 1)[1])
    else:
        PORT = _a
SYNC, OP_R = 0xA5, 0x52
TICK_NS = 10.0


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd(ser, addr, window=0.7):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, b = time.time(), b""
    while time.time() - t0 < window:
        b += ser.read(64)
        for i in range(len(b) - 2):
            if b[i] == addr and ((b[i] + b[i + 1] + b[i + 2]) & 0xFF) == 0:
                return b[i + 1]
    return None


def rd24(ser, a):
    v = [rd(ser, a), rd(ser, a + 1), rd(ser, a + 2)]
    return None if None in v else v[0] | (v[1] << 8) | (v[2] << 16)


def main():
    ser = serial.Serial(PORT, 115200, timeout=0.3)
    time.sleep(0.4)

    # Which output mode are we in? It decides what the numbers mean.
    ser.reset_input_buffer()
    time.sleep(1.3)
    raw = ser.read(ser.in_waiting or 1).decode("latin1", "replace")
    line = ([l for l in raw.replace("\r", "").split("\n") if "S=" in l] or ["--"])[-1]
    sel = "passthrough" if " S=1" in (" " + line) else "offline"
    print("out_vsync period, from the FPGA's own 100 MHz counter\n")
    print("  telemetry : %s" % line.strip()[:76])
    print("  out mode  : %s\n" % sel)

    print("   sample     last_us      Hz      min_us     max_us    jitter_us")
    print("   " + "-" * 62)
    jit, lows, highs, lasts = [], [], [], []
    t0, i, bad = time.time(), 0, 0
    while time.time() - t0 < SECONDS:
        last, mn, mx = rd24(ser, 0x4A), rd24(ser, 0x4D), rd24(ser, 0x50)
        if None in (last, mn, mx):
            bad += 1
            time.sleep(0.3)
            continue
        lu, mnu, mxu = last * TICK_NS / 1e3, mn * TICK_NS / 1e3, mx * TICK_NS / 1e3
        hz = 1e6 / lu if lu else 0.0
        jit.append(mxu - mnu)
        lows.append(mnu); highs.append(mxu); lasts.append(lu)
        if i < 12 or (i % 10) == 0:      # keep long runs readable
            print("   %6d  %10.3f  %7.3f  %10.3f %10.3f   %10.3f"
                  % (i, lu, hz, mnu, mxu, mxu - mnu))
        i += 1
        time.sleep(0.30)
    ran = time.time() - t0
    ser.close()

    if jit:
        j = sum(jit) / len(jit)
        # THE NUMBER THAT MATTERS FOR G0 IS THE AGGREGATE, NOT A WINDOW.
        # min/max reset every status window (~0.5 s), so a per-window jitter only
        # describes ~50 frames. G0 asks for >= 30 s, which means the excursion
        # across EVERY window in the run -- a slow wander would hide completely
        # inside per-window figures that each look tiny.
        span = max(highs) - min(lows)
        print("\n  ran %.1f s, %d windows, %d read failures" % (ran, len(jit), bad))
        print("  mean period      : %.3f us  (%.4f Hz)"
              % (sum(lasts) / len(lasts), 1e6 / (sum(lasts) / len(lasts))))
        print("  per-window jitter: %.3f us mean" % j)
        print("  AGGREGATE across the whole run: min %.3f  max %.3f  span %.3f us"
              % (min(lows), max(highs), span))
        if ran < 30.0:
            print("\n  NOTE: G0 asks for >= 30 s and this ran %.1f s. Not a sign-off."
                  % ran)
        j = span
        if j <= 0.02:
            print("  -> at the 10 ns measurement floor. This master is clean enough")
            print("     that the camera's own trigger-to-exposure behaviour, not the")
            print("     vsync, sets the lock quality.")
        elif j < 1.0:
            print("  -> sub-microsecond. Comfortably below the ~5 us exposure-start")
            print("     jitter already measured at long exposures, so it is not the")
            print("     dominant term.")
        else:
            print("  -> %.1f us is LARGER than the sensor's own exposure-start jitter."
                  "\n     The master is then the limit, and locking to it cannot be"
                  "\n     tighter than this. Investigate before designing G3's delay." % j)


if __name__ == "__main__":
    main()

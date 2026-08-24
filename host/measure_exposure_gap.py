"""Is the blind window between exposures a constant, independent of exposure time?

    python host/measure_exposure_gap.py

WHY THIS MATTERS. For structured light you need to know when the sensor is not
looking, so a pattern transition can be placed there. The datasheet says the
non-integrating interval is built from two things that do NOT depend on exposure
-- the Frame Overhead Time, during which charge moves from photodiode to pixel
memory, and a reset of reset_length x mult_timer. That predicts a constant. It
also contains a clause that predicts the opposite at the short end:

    "If the exposure timer expires before the end of readout, the exposure time
     is extended until the end of the last active line."   (NOIP1SN1300A, p25)

so below some exposure the sensor silently integrates longer than commanded, and
neither the exposure nor the gap is what was asked for. Where that knee sits is
not documented and has never been measured here.

THE METHOD, and why it needs no new RTL. Both knobs are already runtime-settable
over the Ft+: opcode 1 sets exposure, opcode 2 sets the trigger period. So for a
given exposure, search for the SHORTEST trigger period that still delivers every
frame. That minimum is exactly the sensor's own floor:

    min_period(exposure) = FOT + reset + exposure = gap + exposure

  * if the gap is constant, this is a straight line of SLOPE 1 and the INTERCEPT
    IS THE GAP, in the same units, measured rather than inferred
  * below the knee the sensor extends the exposure to the end of readout, so
    min_period stops depending on exposure and the curve goes FLAT. The corner
    between the two regimes is the knee.

One sweep, both numbers.

HOW "delivers every frame" IS DECIDED. Commanded rate is 100 MHz / trig_per.
Delivered rate is counted off the IN pipe. When exposure + overhead exceeds the
period the sensor misses alternate triggers and the delivered rate HALVES -- the
documented failure. So the test is delivered >= 0.9 x commanded. Rate is measured
on the frame stream itself, not asked of a status register, because this project
has already had a status UART report a healthy 120 Hz while the delivered rate
had halved.

SAFETY. Exposure longer than the trigger period wedges the sensor until the FPGA
is reconfigured. So the period is ALWAYS widened before the exposure is raised,
never the other way round, and every point is approached by shortening the period
downward from a known-good value. If it wedges anyway, reload the bitstream --
that is the documented recovery and it costs a minute.
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ftlink import FtLink

CLK_HZ = 100_000_000
EXPO_UNIT_US = 0.375          # exposure register LSB, mult_timer / 72 MHz
CY_PER_US = CLK_HZ / 1e6      # 100 cycles per microsecond

# Exposure register values to probe. Deliberately spans two decades so the flat
# (extended) region and the slope-1 region can both be seen if they exist.
EXPOSURES = [100, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000, 20000]
if len(sys.argv) > 1:                 # override, e.g. a fine sweep above the knee
    EXPOSURES = [int(a) for a in sys.argv[1:]]

SETTLE = 0.45                 # let the sensor take up the new setting
WINDOW = 1.10                 # frame-rate measurement window, seconds

# NEVER PROBE A PERIOD SHORTER THAN THE SENSOR CAN READ OUT.
#
# The first version of this sweep searched down to 50 us and WEDGED the sensor on
# its third point. At 50 us roughly 118 triggers arrive during one 5.9 ms readout,
# and the datasheet is explicit that "the trigger pin needs to be kept low during
# the FOT" (p25). Violating that is not a missed frame, it is a part that stops
# until the FPGA is reconfigured.
#
# 4.5 ms is just inside the sensor's own ceiling (165 fps NROT = 6.06 ms,
# 210 fps ZROT = 4.76 ms), so the search can still bracket the floor from below
# without ever asking for something absurd.
FLOOR_US = 4500.0
BITSTREAM = "build_merged/Au2_SLI_merged.bin"
LOADER = (r"C:\Users\dllau\AppData\Local\AlchitryFlasher\tools\2.0.52"
          r"\bin\alchitry.exe")


def fps(link, secs=WINDOW):
    n0, t0 = link.frames, time.time()
    while time.time() - t0 < secs:
        link.pump()
    dt = time.time() - t0
    return (link.frames - n0) / dt


def set_period(link, cycles):
    link.send_word((2 << 28) | (int(cycles) & 0xFFFFFF))


def set_exposure(link, val):
    link.send_word((1 << 28) | (int(val) & 0xFFFF))


def delivers(link, cycles):
    """Does the camera keep up at this trigger period?"""
    want = CLK_HZ / float(cycles)
    got = fps(link)
    return got >= 0.9 * want, got, want


def reload_bitstream():
    """Recover a wedged sensor. Documented recovery: reconfigure the FPGA.

    Returns a NEW FtLink -- the old handle's device goes away across a reload.
    """
    import subprocess
    print("      ! sensor wedged -- reloading the bitstream to recover")
    subprocess.run([LOADER, "load", "--bin", BITSTREAM,
                    "--board", "PtV2", "--ram"],
                   capture_output=True)
    time.sleep(3.0)
    lk = FtLink()
    lk.drain()
    return lk


def alive(link):
    """Is the camera streaming at all, at a period it can certainly serve?"""
    set_period(link, int(CY_PER_US * 12000))     # 12 ms, twice the readout floor
    time.sleep(SETTLE)
    return fps(link, 0.8) > 20.0


def main():
    link = FtLink()
    link.drain()

    safe = 2_000_000            # 20 ms -- comfortably longer than anything probed
    set_period(link, safe)
    set_exposure(link, EXPOSURES[0])
    time.sleep(SETTLE)

    base = fps(link)
    print("baseline at 20 ms period: %.1f fps (expect ~%.1f)\n"
          % (base, CLK_HZ / float(safe)))
    if base < 10:
        link.close()
        sys.exit("camera is not streaming -- reload the bitstream first")

    print("  expo_reg   expo_us   min_period_us   implied_gap_us   fps_at_min")
    print("  " + "-" * 62)
    rows = []
    for e in EXPOSURES:
        # ALWAYS widen the period before raising the exposure.
        set_period(link, safe)
        time.sleep(SETTLE)
        set_exposure(link, e)
        time.sleep(SETTLE)

        expo_us = e * EXPO_UNIT_US
        # Binary search the shortest period that still keeps up. lo is known-bad,
        # hi is known-good; invariant maintained throughout. lo is FLOOR_US, not
        # something arbitrarily small -- see the note there.
        lo = int(CY_PER_US * FLOOR_US)
        hi = safe                            # 20 ms, certainly long enough
        ok_hi, got_hi, _ = delivers(link, hi)
        if not ok_hi:
            # Distinguish "this exposure is genuinely too long for 20 ms" from
            # "the sensor is wedged from an earlier point". Without this the
            # first wedge silently turns every remaining row into a zero, which
            # is exactly what the first run of this sweep produced.
            if not alive(link):
                link = reload_bitstream()
                set_period(link, safe); time.sleep(SETTLE)
                set_exposure(link, e);  time.sleep(SETTLE)
                ok_hi, got_hi, _ = delivers(link, hi)
            if not ok_hi:
                print("  %8d  %8.1f   (cannot keep up even at 20 ms: %.1f fps)"
                      % (e, expo_us, got_hi))
                continue
        for _ in range(11):
            mid = (lo + hi) // 2
            set_period(link, mid)
            time.sleep(SETTLE)
            ok, got, want = delivers(link, mid)
            if ok:
                hi = mid
            else:
                lo = mid
            if hi - lo < int(CY_PER_US * 5):     # 5 us resolution is plenty
                break
        set_period(link, hi)
        time.sleep(SETTLE)
        _, got, _ = delivers(link, hi)
        min_us = hi / CY_PER_US
        gap = min_us - expo_us
        # A point whose final rate is zero WEDGED during the search; its
        # "minimum period" is wherever the search happened to stop and means
        # nothing. Recording it poisons the slope -- the first run of this
        # reported a slope of 1.135 for data that was actually 1.0015.
        if got > 1.0:
            rows.append((e, expo_us, min_us, gap))
        else:
            print("      (discarded: wedged during the search, min_period is"
                  " meaningless)")
        print("  %8d  %8.1f   %13.1f   %14.1f   %8.1f"
              % (e, expo_us, min_us, gap, got))

    # park somewhere safe before leaving
    set_period(link, 833_333)
    time.sleep(SETTLE)
    set_exposure(link, 500)
    link.close()

    if len(rows) >= 3:
        print("\n  READING THE RESULT")
        gaps = [r[3] for r in rows]
        print("  implied gap: min %.1f  max %.1f  spread %.1f us"
              % (min(gaps), max(gaps), max(gaps) - min(gaps)))
        # Slope only over points ABOVE the readout floor. Below it the minimum
        # period is set by readout and does not move with exposure at all, so
        # including those points measures the knee, not the gap.
        floor = min(r[2] for r in rows)
        above = [r for r in rows if r[2] > floor + 50.0]
        if len(above) >= 2:
            dx = above[-1][1] - above[0][1]
            dy = above[-1][2] - above[0][2]
            if dx:
                print("  slope above the readout floor: %.4f   (1.0000 => the gap is"
                      " a constant)" % (dy / dx))
            g = [r[3] for r in above]
            print("  gap above the floor: mean %.1f us over %d points, spread %.1f us"
                  % (sum(g) / len(g), len(g), max(g) - min(g)))
        print("  readout floor: %.0f us (%.0f fps) -- below this the minimum period"
              "\n  does not depend on exposure at all." % (floor, 1e6 / floor))
        print("  A FLAT region at the short end is the sensor extending exposure to"
              "\n  the end of readout; the corner is the knee.")


if __name__ == "__main__":
    main()

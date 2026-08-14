"""Verify the frame rate over repeated 24-frame runs.

Each COM6 line is one completed 24-frame window, reporting the total wordclk
cycles for its 24 intervals plus the MIN and MAX single interval in that window.
Mean alone would hide a dropped frame -- one long interval and 23 short ones
averages out -- so min/max is what makes this a check.

    fps = 24 * 72e6 / wtot          (wordclk = recovered 72.000 MHz)

SCOPE: the trigger is generated from the 100 MHz board clock and wordclk is
recovered from the sensor, which is clocked from the same crystal. So this
measures REGULARITY and the trigger/readout ratio to very high precision, and it
does NOT measure absolute Hz against an external standard -- a crystal tens of
ppm off would still read 120.000 here.
"""
import sys
import time

import serial

TARGET_HZ = 120.0
NWIN = 24
WORDCLK = 72e6
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0

ser = serial.Serial("COM6", 1000000, timeout=2.0)
ser.reset_input_buffer()

rows = []
t0 = time.time()
print("  win     wtot(cyc)     fps        mean_us    min_us    max_us   spread_us")
while time.time() - t0 < SECONDS:
    try:
        ln = ser.readline().decode(errors="replace").strip()
    except Exception:
        continue
    # telemetry grew as fields were added; the benchmark fields kept their
    # offsets, so accept any line long enough to contain them
    if len(ln) < 20:
        continue
    try:
        wtot = int(ln[3:10], 16)
        wmin = int(ln[10:15], 16)
        wmax = int(ln[15:20], 16)
    except ValueError:
        continue
    if wtot == 0:
        continue
    fps = NWIN * WORDCLK / wtot
    mean_us = wtot / NWIN / WORDCLK * 1e6
    min_us = wmin / WORDCLK * 1e6
    max_us = wmax / WORDCLK * 1e6
    key = (wtot, wmin, wmax)
    if rows and rows[-1][0] == key:
        continue                      # same window re-read at 10 Hz
    rows.append((key, fps, mean_us, min_us, max_us))
    print("  %3d  %11d  %10.5f  %9.3f %9.3f %9.3f   %7.3f"
          % (len(rows), wtot, fps, mean_us, min_us, max_us, max_us - min_us),
          flush=True)
ser.close()

if not rows:
    sys.exit("no windows captured")

fps_l = [r[1] for r in rows]
spread = [r[4] - r[3] for r in rows]
print()
print("windows captured : %d   (%d frames total)" % (len(rows), len(rows) * NWIN))
print("fps  mean/min/max: %.5f / %.5f / %.5f" % (sum(fps_l) / len(fps_l), min(fps_l), max(fps_l)))
print("error vs %.1f Hz : %+.5f Hz  (%+.2f ppm)"
      % (TARGET_HZ, sum(fps_l) / len(fps_l) - TARGET_HZ,
         1e6 * (sum(fps_l) / len(fps_l) - TARGET_HZ) / TARGET_HZ))
print("worst intra-window spread (max-min interval): %.3f us" % max(spread))
worst = max(abs(f - TARGET_HZ) for f in fps_l)
print()
print("VERDICT:", "STEADY at %.1f Hz" % TARGET_HZ
      if worst < 0.01 and max(spread) < 1.0 else
      "NOT CLEAN -- see spread/fps columns above")

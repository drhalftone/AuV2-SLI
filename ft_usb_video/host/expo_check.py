"""Prove exposure is settable AT RUNTIME, and measure the response.

Sets exposure0 over the Ft+ (opcode 1) and reads back real frames at each
setting, reporting mean level. A working control is MONOTONIC: more exposure,
more signal, until the sensor saturates.

Two things this is designed to catch, because both have bitten this project:

  * A command that is accepted but has no effect. Brightness that does not move
    with the setting is the only honest evidence of that, so the mean is
    measured from pixels rather than trusted from the acknowledgement.
  * Saturation masquerading as success. A frame pinned at full scale looks
    bright and stable and tells you nothing, so the fraction of saturated
    pixels is reported alongside the mean.

EXPOSURE CANNOT EXCEED THE FRAME PERIOD. In triggered mode at 120 Hz the period
is 8.3333 ms, so exposure0 must stay under 22,222 units of 375 ns. Ask for more
and the sensor cannot deliver 120 Hz -- the script flags it rather than letting
the frame rate quietly collapse.

usage:  python expo_check.py [us ...]
"""
import ctypes, struct, sys, time

import numpy as np
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

IN_PIPE, OUT_PIPE = 0x82, 0x02
EXPO_UNIT_US = 0.375
PERIOD_US = 1e6 / 120.0            # 8333.3 us at 120 Hz
MAX_UNITS = int(PERIOD_US / EXPO_UNIT_US)

req_us = [float(x) for x in sys.argv[1:]] or [75, 150, 300, 600, 1200, 2400, 4800]

d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    raise SystemExit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try: getattr(d, fn)(IN_PIPE)
    except Exception: pass
d.setPipeTimeout(IN_PIPE, 1000)
buf = ctypes.create_string_buffer(1 << 22)


def set_expo(units):
    word = (1 << 28) | (units & 0x0FFFFFFF)
    b = ctypes.create_string_buffer(struct.pack("<I", word))
    try:
        d.writePipe(OUT_PIPE, b, 4)
    except Exception:
        d.writePipeEx(OUT_PIPE, b, 4)


def grab():
    """Read past the in-flight frames so the result reflects the NEW setting."""
    data = bytearray()
    t0 = time.time()
    while len(data) < 12_000_000 and time.time() - t0 < 3:
        n = d.readPipe(IN_PIPE, buf, 1 << 22)
        if n: data += buf.raw[:n]
    got = [f for _, f in campack.iter_frames(data)]
    return got[-1] if got else None


print("exposure unit %.3f us; at 120 Hz the period is %.0f us "
      "=> exposure0 must stay below %d units\n" % (EXPO_UNIT_US, PERIOD_US, MAX_UNITS))
print("%10s %8s %10s %10s %9s   %s" %
      ("req us", "units", "mean", "p99", "sat %", "note"))

prev = None
rows = []
for us in req_us:
    units = int(round(us / EXPO_UNIT_US))
    note = ""
    if units > MAX_UNITS:
        note = "EXCEEDS 120 Hz PERIOD"
    if not 1 <= units <= 0xFFFF:
        print("%10.0f %8d %10s %10s %9s   out of range, skipped" % (us, units, "-", "-", "-"))
        continue
    set_expo(units)
    time.sleep(0.35)                      # let the sequencer apply it
    f = grab()
    if f is None:
        print("%10.0f %8d %10s %10s %9s   NO FRAME" % (us, units, "-", "-", "-"))
        continue
    mean = float(f.mean())
    p99 = float(np.percentile(f, 99))
    sat = 100.0 * float((f >= 1020).mean())
    if sat > 5.0 and not note:
        note = "SATURATING"
    elif prev is not None and mean <= prev + 0.5 and not note:
        note = "did not rise"
    rows.append((us, mean))
    prev = mean
    print("%10.0f %8d %10.1f %10.1f %9.2f   %s" % (us, units, mean, p99, sat, note))

d.close()

print()
if len(rows) >= 2:
    ok = all(b[1] > a[1] for a, b in zip(rows, rows[1:]))
    print("VERDICT:", "exposure control WORKS -- mean rises monotonically"
          if ok else "NOT monotonic -- see the notes above")

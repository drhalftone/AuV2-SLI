"""Control the camera over the Ft+ -- exposure, frame rate, and re-arm.

The delivered system reaches the board ONLY through the Ft+, so commands go out
the FT601's OUT pipe (0x02) rather than over the Pt's COM6 UART, which is
bring-up scaffolding.

Commands are single 32-bit little-endian words: [31:28] opcode, [27:0] payload.
One word per command means the FPGA decoder is stateless -- a truncated USB
transfer cannot strand it half-way through a command.

    1  exposure0, payload[15:0]   units of 375 ns (mult_timer / f_pll)
    2  trigger period, payload[23:0] in 100 MHz clock cycles
    3  re-arm the burst capture (payload ignored)
    4  frames per scan, payload[5:0], 1..63

Re-arm matters: DDR holds one captured burst, so changing exposure alone changes
nothing the host can see until a NEW burst is taken.

usage:
    cam_ctl.py --exposure 1600         # 600 us
    cam_ctl.py --us 600                # same, in microseconds
    cam_ctl.py --fps 120               # trigger period for 120 Hz
    cam_ctl.py --frames 24             # 24 frames per scan
    cam_ctl.py --grab                  # re-arm and capture a new burst
    cam_ctl.py --us 300 --grab         # set exposure, then grab with it
"""
import argparse
import ctypes
import struct
import sys
import time

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

OUT_PIPE = 0x02
CLK_HZ = 100_000_000
EXPO_UNIT_US = 0.375

ap = argparse.ArgumentParser()
ap.add_argument("--exposure", type=int, help="exposure0 in 375 ns units")
ap.add_argument("--us", type=float, help="exposure in microseconds")
ap.add_argument("--fps", type=float, help="frame rate; sets the trigger period")
ap.add_argument("--period-cy", type=int, help="trigger period in 100 MHz cycles")
ap.add_argument("--frames", type=int, help="frames per scan (1..63)")
ap.add_argument("--grab", action="store_true", help="re-arm the burst capture")
ap.add_argument("--pipe", type=lambda x: int(x, 0), default=OUT_PIPE)
a = ap.parse_args()

cmds = []
if a.us is not None:
    units = int(round(a.us / EXPO_UNIT_US))
    if not 1 <= units <= 0xFFFF:
        sys.exit("exposure out of range: %d units" % units)
    cmds.append((1, units, "exposure0 = %d (%.1f us)" % (units, units * EXPO_UNIT_US)))
if a.exposure is not None:
    if not 1 <= a.exposure <= 0xFFFF:
        sys.exit("exposure out of range")
    cmds.append((1, a.exposure, "exposure0 = %d (%.1f us)"
                 % (a.exposure, a.exposure * EXPO_UNIT_US)))
if a.fps is not None:
    cy = int(round(CLK_HZ / a.fps))
    if not 1000 < cy <= 0xFFFFFF:
        sys.exit("fps out of range: %d cycles" % cy)
    cmds.append((2, cy, "period = %d cy (%.5f Hz)" % (cy, CLK_HZ / cy)))
if a.period_cy is not None:
    cmds.append((2, a.period_cy, "period = %d cy (%.5f Hz)"
                 % (a.period_cy, CLK_HZ / a.period_cy)))
if a.frames is not None:
    if not 1 <= a.frames <= 63:
        sys.exit("frames must be 1..63 (63 x 2.62 MB = 165 MB of 256)")
    cmds.append((4, a.frames, "frames per scan = %d (%.1f MB)"
                 % (a.frames, a.frames * 1280 * 1024 * 2 / 1e6)))
# order matters: length and exposure must land BEFORE the re-arm that uses them
if a.grab:
    cmds.append((3, 0, "re-arm burst capture"))
if not cmds:
    ap.print_help()
    sys.exit(0)

d = None
for _ in range(10):
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is not None:
        break
    time.sleep(0.3)
if d is None:
    sys.exit("no D3XX device")
try:
    d.setPipeTimeout(a.pipe, 1000)
except Exception:
    pass

for op, payload, desc in cmds:
    word = (op << 28) | (payload & 0x0FFFFFFF)
    buf = ctypes.create_string_buffer(struct.pack("<I", word))
    try:
        n = d.writePipe(a.pipe, buf, 4)
    except Exception:
        n = d.writePipeEx(a.pipe, buf, 4)
    print("sent %08X  %-34s (%s bytes)" % (word, desc, n))
    time.sleep(0.05)

d.close()
print()
print("verify in the COM6 telemetry: the last 8 hex chars are")
print("  expo_cur (4) then cmd_count (4) -- cmd_count increments per word received")

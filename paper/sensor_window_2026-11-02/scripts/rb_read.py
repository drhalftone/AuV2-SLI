"""Read the sensor-window debug readback from the rbdbg crop build.

  0x58..0x59  sensor reg 256  {x_end, x_start} in kernels   expect 0x9E01 (158, 1)
  0x5A..0x5B  sensor reg 257  y_start                       expect 0x0008
  0x5C        completed build-time window writes            expect >= 1
  0x8E..0x8F  sensor reg 195  roi_active                    expect 0x0001 (ROI 0)

Defaults after a sensor reset: 256 = 0x9F00, 257 = 0x0000, 195 = 0x0001.
"""
import sys
import time

sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\host")
from ftlink import FtLink


def rd(f, a):
    v = f.read_reg(a)
    if v is None:
        raise RuntimeError("no reply for reg 0x%02X" % a)
    return v


def snap(f):
    b = {a: rd(f, a) for a in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x8E, 0x8F, 0x90)}
    return ((b[0x59] << 8) | b[0x58], (b[0x5B] << 8) | b[0x5A], b[0x5C],
            (b[0x8F] << 8) | b[0x8E], b[0x90])


f = FtLink()
try:
    prev = None
    for i in range(6):
        s = snap(f)
        r256, r257, nfix, r195, nrew = s
        print("reg256 0x%04X (x_start %3d, x_end %3d)  reg257 0x%04X (%4d)  "
              "reg195 0x%04X  fix writes %d  late rewrites %d%s"
              % (r256, r256 & 0xFF, r256 >> 8, r257, r257 & 0x3FF, r195, nfix, nrew,
                 "" if prev is None or prev == s else "   (changed)"))
        prev = s
        time.sleep(0.4)
finally:
    f.close()

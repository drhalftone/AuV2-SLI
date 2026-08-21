"""Read the camera datapath status over the Pt's USB control link (regs 0x3A-0x41).

M4. Before this, camera health lived only on the camera's own 1 Mbaud UART --
so Port A could show the SLI control plane OR the camera, never both -- and on
the FT601 frame stream, which goes SILENT exactly when the camera fails. This
puts it in the register map, so it can be read while everything else keeps
working.

usage:  python host/read_cam_status.py [COM6]
"""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SYNC, OP_R = 0xA5, 0x52


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd(ser, addr, window=0.6):
    """Frame-scan past the ASCII telemetry sharing this UART."""
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, buf = time.time(), b""
    while time.time() - t0 < window:
        buf += ser.read(64)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


with serial.Serial(PORT, 115200, timeout=0.2) as s:
    time.sleep(0.3)
    v = [rd(s, a) for a in range(0x3A, 0x42)]

if any(x is None for x in v):
    raise SystemExit("read failed: %r  (is the merged bitstream loaded?)" % (v,))

alive, health = v[0], v[1]
ldrop = v[2] | (v[3] << 8)
period = (v[4] | (v[5] << 8)) * 16          # stored /16, in 72 MHz wordclk cycles
expo = v[6] | (v[7] << 8)

print("  0x3A alive  = 0x%02X   stw=%d rd_busy=%d calib=%d aligned=%d streaming=%d cap=%d"
      % (alive, alive >> 5, (alive >> 4) & 1, (alive >> 3) & 1,
         (alive >> 2) & 1, (alive >> 1) & 1, alive & 1))
print("  0x3B health = 0x%02X   cfifo_ovf=%d ufifo_ovf=%d ufifo_empty=%d txe=%d"
      % (health, (health >> 7) & 1, (health >> 6) & 1,
         (health >> 5) & 1, (health >> 4) & 1))
print("  0x3C ldrop  = %d" % ldrop)
print("  0x3E period = %d cycles => %.2f Hz"
      % (period, (72e6 / period) if period else 0.0))
print("  0x40 expo   = %d units = %.0f us" % (expo, expo * 0.375))

ok = ((alive >> 3) & 1) and ((alive >> 2) & 1) and ((alive >> 1) & 1) \
     and (alive & 1) and not ((health >> 7) & 1)
print()
print("VERDICT:", "camera HEALTHY" if ok
      else "camera NOT healthy -- see the flags above")

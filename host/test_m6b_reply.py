"""M6b: the control plane answers over the Ft+ -- no serial port involved.

M6a proved commands ARRIVE over USB3, but every readback still came back over
COM6, so the Pt UART was still load-bearing. This closes the loop: the reply
comes back interleaved with video on the IN pipe, and the serial port is used
only as an INDEPENDENT WITNESS -- never as the transport under test.

That distinction is the point. Verifying a USB3 reply by reading the same
register over USB3 would pass even if the FPGA were echoing something stale;
checking it over the other link proves the value the reply carried is the value
the register actually holds.

Checks, in order:
  1. register READ over USB3 only, cross-checked against serial
  2. register WRITE over USB3, ACK returned over USB3, value confirmed on serial
  3. replies keep working while VIDEO IS STREAMING (they share one pipe)
  4. the frame stream is unharmed: no frame_idx gaps, ldrop static
  5. reply latency, which is bounded by the frame period by construction

usage:  python host/test_m6b_reply.py [COM6]
"""
import os, sys, time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ftlink import FtLink, SYNC, OP_R, ck

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
ACK_K = 0x4B


def rd_serial(ser, addr, window=0.8):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, b = time.time(), b""
    while time.time() - t0 < window:
        b += ser.read(64)
        for i in range(len(b) - 2):
            if b[i] == addr and ((b[i] + b[i + 1] + b[i + 2]) & 0xFF) == 0:
                return b[i + 1]
    return None


fails = []
link = FtLink()
ser = serial.Serial(PORT, 115200, timeout=0.3)
time.sleep(0.4)

print("M6b -- control replies over the Ft+\n")

# ---- 1. read a register over USB3 -----------------------------------------
t0 = time.time()
ident = link.read_reg(0x00, timeout=3.0)
dt = time.time() - t0
ser_ident = rd_serial(ser, 0x00)
print("  [1] read 0x00 over USB3 -> %s   (serial says %s)   %.1f ms"
      % (("0x%02X" % ident) if ident is not None else "TIMEOUT",
         ("0x%02X" % ser_ident) if ser_ident is not None else "--", dt * 1e3))
if ident is None or ident != ser_ident:
    fails.append("register read over USB3 did not match serial")

# ---- 2. write over USB3, ACK over USB3, confirm on serial ------------------
print()
ok_w = 0
vals = (0xAB, 0x5A, 0x33, 0xC7)
for val in vals:
    before = rd_serial(ser, 0x13)
    if before == val:
        fails.append("0x13 already held 0x%02X -- test value not distinct" % val)
    ack = link.write_reg(0x13, val, timeout=3.0)
    rb_usb = link.read_reg(0x13, timeout=3.0)
    rb_ser = rd_serial(ser, 0x13)
    good = (ack == ACK_K and rb_usb is not None
            and (rb_usb & 0x7F) == (val & 0x7F)
            and rb_ser is not None and (rb_ser & 0x7F) == (val & 0x7F))
    ok_w += good
    print("  [2] write 0x13=0x%02X -> ack %s, USB3 reads %s, serial reads %s   %s"
          % (val,
             ("'%c'" % ack) if ack is not None else "TIMEOUT",
             ("0x%02X" % rb_usb) if rb_usb is not None else "--",
             ("0x%02X" % rb_ser) if rb_ser is not None else "--",
             "ok" if good else "MISMATCH"))
if ok_w != len(vals):
    fails.append("%d of %d write/readback round-trips failed" % (len(vals) - ok_w, len(vals)))

link.write_reg(0x13, 0x00, timeout=3.0)

# ---- 3/4. replies while video streams --------------------------------------
print()
ld0 = (rd_serial(ser, 0x3C) or 0) | ((rd_serial(ser, 0x3D) or 0) << 8)
f0 = link.frames
lat = []
bad = 0
t_end = time.time() + 15.0
n = 0
while time.time() < t_end:
    t0 = time.time()
    v = link.read_reg(0x00, timeout=2.0)
    lat.append((time.time() - t0) * 1e3)
    n += 1
    if v != ident:
        bad += 1
ld1 = (rd_serial(ser, 0x3C) or 0) | ((rd_serial(ser, 0x3D) or 0) << 8)
health = rd_serial(ser, 0x3B) or 0
frames = link.frames - f0
lat.sort()

print("  [3] %d register reads over 15 s while streaming, %d wrong" % (n, bad))
print("      latency  min %.1f  median %.1f  max %.1f ms"
      % (lat[0], lat[len(lat) // 2], lat[-1]))
print("  [4] frames seen %d   malformed packets %d   ldrop %d -> %d   ovf %d%d"
      % (frames, link.badpk, ld0, ld1, (health >> 7) & 1, (health >> 6) & 1))
if bad:               fails.append("%d reads returned the wrong value under load" % bad)
if link.badpk:        fails.append("%d malformed packets" % link.badpk)
if ld1 != ld0:        fails.append("ldrop moved %d -> %d" % (ld0, ld1))
if health & 0xC0:     fails.append("a FIFO overflow flag is set")
if frames < 100:      fails.append("frame stream stalled (only %d frames)" % frames)

ser.close()
link.close()

print()
if fails:
    print("FAIL:")
    for f in sorted(set(fails)):
        print("  - " + f)
else:
    print("PASS: the control plane answers over the Ft+, with video sharing the pipe.")

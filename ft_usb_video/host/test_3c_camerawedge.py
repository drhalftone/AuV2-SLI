"""M3 test 3c: does a WEDGED CAMERA disturb HDMI?

Deliberately breaks the sensor and requires HDMI to carry on untouched.

WHY THIS FAILURE. Exposure above ~8300 us at 120 Hz does not merely slow the
camera -- it WEDGES THE SENSOR. Restoring a valid exposure does not recover it,
and neither does a re-arm; it takes an FPGA reconfigure to re-run the SPI boot.
That makes it the most severe camera-side failure we can produce on demand, and
therefore the right thing to point at HDMI.

PASS = HDMI's VSYNC counter keeps advancing, its mode is unchanged, and the
0xA5 control plane still answers -- while the camera is dead.

RECOVERY IS NOT AUTOMATIC. This test leaves the sensor wedged. Reload the
bitstream afterwards:
    alchitry.exe load --bin <merged.bin> --board PtV2 --ram

usage:  python test_3c_camerawedge.py [COM6]
"""
import ctypes, struct, sys, time

import serial
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
WEDGE_US = 8400.0                    # past the 8280 us cliff, on purpose
SYNC, OP_R = 0xA5, 0x52


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd(ser, addr, window=0.6):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, buf = time.time(), b""
    while time.time() - t0 < window:
        buf += ser.read(64)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def hdmi_snapshot(ser, label):
    """VSYNC count comes from the telemetry line; mode/refresh from registers."""
    ser.reset_input_buffer()
    t0, vs = time.time(), []
    while time.time() - t0 < 2.5 and len(vs) < 3:
        ln = ser.readline().decode("ascii", "replace").strip()
        if ln.startswith("S=") and " N=" in ln:
            for tok in ln.split():
                if tok.startswith("N="):
                    vs.append(int(tok[2:], 16))
    mode, refr = rd(ser, 0x20), rd(ser, 0x21)
    idv = rd(ser, 0x00)
    print("  %-8s N=%s  MODE=%s REFR=%s  ID=%s"
          % (label, vs, ("0x%02X" % mode) if mode is not None else "--", refr,
             ("0x%02X" % idv) if idv is not None else "--"))
    return vs, mode, refr, idv


def camera_frames(secs=2.5):
    """Delivered frames per second.

    MEASURE THE RATE, NOT A CAPPED COUNT. The first version of this stopped at a
    40 MB ceiling -- about 24 frames -- so a healthy camera and a half-speed one
    both hit the cap and reported the same number, and the test declared itself
    inconclusive against a camera that may well have been broken. Bytes over a
    fixed wall-clock window cannot be fooled that way, and since frames are
    contiguous and fixed-size, bytes/frame-size IS the rate.
    """
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        return 0.0
    for fn in ("abortPipe", "flushPipe"):
        try: getattr(d, fn)(0x82)
        except Exception: pass
    d.setPipeTimeout(0x82, 1000)
    b = ctypes.create_string_buffer(1 << 22)
    nbytes = 0
    t0 = time.time()
    while time.time() - t0 < secs:
        n = d.readPipe(0x82, b, 1 << 22)
        if n: nbytes += n
    dt = time.time() - t0
    d.close()
    return nbytes / float(campack.FBYTES + campack.HDR) / dt


def set_expo(us):
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        raise SystemExit("no D3XX device to send the command")
    units = max(1, min(int(round(us / 0.375)), 0xFFFF))
    buf = ctypes.create_string_buffer(struct.pack("<I", (1 << 28) | units))
    try:    d.writePipe(0x02, buf, 4)
    except Exception: d.writePipeEx(0x02, buf, 4)
    d.close()
    return units


print("M3 / 3c -- wedge the camera, HDMI must not notice\n")
with serial.Serial(PORT, 115200, timeout=0.4) as s:
    time.sleep(0.3)
    vs0, mode0, refr0, id0 = hdmi_snapshot(s, "before")
    n0 = camera_frames()
    print("           camera rate before: %.1f fps" % n0)

    u = set_expo(WEDGE_US)
    print("\n  --> exposure set to %.0f us (%d units) -- past the 8280 us cliff\n" % (WEDGE_US, u))
    time.sleep(2.0)

    n1 = camera_frames()
    print("           camera rate after : %.1f fps" % n1)
    vs1, mode1, refr1, id1 = hdmi_snapshot(s, "after")

print()
cam_dead = (n0 > 50) and (n1 < 5)
cam_hurt = (n0 > 50) and (n1 < n0 * 0.75)
hdmi_counting = len(vs1) >= 2 and len(set(vs1)) > 1
hdmi_same = (mode0 == mode1) and (refr0 == refr1)
hdmi_alive = (id1 == 0x48)

print("  camera stopped / degraded : %s (%.1f -> %.1f fps)" % (cam_hurt, n0, n1))
print("  HDMI VSYNC still counting : %s %s" % (hdmi_counting, vs1))
print("  HDMI mode unchanged       : %s" % hdmi_same)
print("  HDMI control plane alive  : %s" % hdmi_alive)
print()
if not cam_hurt:
    print("INCONCLUSIVE: the camera did not actually break, so HDMI was never tested.")
elif hdmi_counting and hdmi_same and hdmi_alive:
    print("PASS: the camera is %s and HDMI is untouched." % ("dead" if cam_dead else "degraded"))
else:
    print("FAIL: HDMI was disturbed by a camera-side failure.")
print("\nNOTE: the sensor is wedged. Reload the bitstream to recover.")

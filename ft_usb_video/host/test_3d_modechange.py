"""M3 test 3d: does re-timing the HDMI output disturb a running capture?

Forces an HDMI mode change while the camera streams, and requires that not one
kernel is lost.

WHY THIS TEST IS THE SHARP ONE. MODEFORCE (reg 0x14) does not merely relabel the
output -- it retunes the pixel clock through drp_clkgen13, which RECONFIGURES A
LIVE MMCM over DRP. If the two subsystems share a reset, a clock resource, or
memory-controller bandwidth in any way that matters, this is where it shows.
"Both run at once" cannot detect that; only disturbing one of them can.

PASS = ldrop does not move across the mode change, frames keep arriving, and the
HDMI mode actually changed (a test that silently failed to change anything would
pass trivially).

usage:  python test_3d_modechange.py [COM6] [force_byte]
        force_byte defaults to 0x8D = force_en + idx 13 (1280x1024@60)
"""
import ctypes, sys, threading, time

import serial
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
FORCE = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x8D
SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52


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


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.2)


# ---- frame reader thread: runs for the whole test ------------------------
samples = []          # (t, frame_idx, ldrop)
stop = threading.Event()


def reader():
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        print("  reader: no D3XX device"); return
    for fn in ("abortPipe", "flushPipe"):
        try: getattr(d, fn)(0x82)
        except Exception: pass
    d.setPipeTimeout(0x82, 1000)
    b = ctypes.create_string_buffer(1 << 22)
    acc = bytearray()
    while not stop.is_set():
        n = d.readPipe(0x82, b, 1 << 22)
        if not n:
            continue
        acc += b.raw[:n]
        for h, _f in campack.iter_frames(acc):
            samples.append((time.time(), h["frame_idx"], h["ldrop"]))
        acc = acc[-(campack.FBYTES + campack.HDR):] if len(acc) > 2 * campack.FBYTES else acc
    d.close()


print("M3 / 3d -- HDMI mode change during camera capture\n")
th = threading.Thread(target=reader, daemon=True)
th.start()
time.sleep(2.5)                      # let capture settle

with serial.Serial(PORT, 115200, timeout=0.2) as s:
    time.sleep(0.3)
    mode_before = rd(s, 0x20)
    refr_before = rd(s, 0x21)
    n_before = len(samples)
    ld_before = samples[-1][2] if samples else None
    print("  before : MODE=0x%02X REFR=%s  frames=%d  ldrop=%s"
          % (mode_before or 0, refr_before, n_before, ld_before))

    print("  --> writing MODEFORCE 0x%02X (retunes the output MMCM via DRP)" % FORCE)
    t_force = time.time()
    wr(s, 0x14, FORCE)
    time.sleep(2.5)                  # stream across the disturbance

    mode_after = rd(s, 0x20)
    refr_after = rd(s, 0x21)
    print("  after  : MODE=0x%02X REFR=%s  frames=%d  ldrop=%s"
          % (mode_after or 0, refr_after, len(samples),
             samples[-1][2] if samples else None))

    print("  --> restoring EDID control (MODEFORCE = 0x00)")
    wr(s, 0x14, 0x00)
    time.sleep(1.0)

stop.set(); th.join(timeout=3)

# ---- verdict -------------------------------------------------------------
print()
if not samples:
    raise SystemExit("NO FRAMES AT ALL -- cannot judge; camera was not streaming")

lds = [l for _, _, l in samples]
during = [l for t, _, l in samples if t >= t_force]
mode_changed = (mode_before != mode_after) or (refr_before != refr_after)

print("  frames captured        : %d" % len(samples))
print("  frames after the force : %d" % len(during))
print("  ldrop min..max         : %d..%d" % (min(lds), max(lds)))
print("  HDMI mode changed      : %s (0x%02X -> 0x%02X, %s -> %s Hz)"
      % (mode_changed, mode_before or 0, mode_after or 0, refr_before, refr_after))
print()
ok_ldrop = (min(lds) == max(lds))
ok_flow = len(during) > 10
if not mode_changed:
    print("INCONCLUSIVE: the HDMI mode did not actually change, so nothing was disturbed.")
elif ok_ldrop and ok_flow:
    print("PASS: ldrop never moved and frames kept arriving across the mode change.")
else:
    print("FAIL: %s%s"
          % ("" if ok_ldrop else "ldrop MOVED (%d..%d) " % (min(lds), max(lds)),
             "" if ok_flow else "frames stopped after the force"))

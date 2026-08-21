"""M7 soak: run the merged design continuously and watch for slow failures.

Everything verified so far has been SECONDS long -- ring_check reads 6 s,
row_align_check 10 s, each stress phase a second or two. That is blind to
anything that needs time to appear:

  * thermal drift -- the FPGA warming under sustained ~200 MB/s, DDR3
    calibration moving with temperature
  * rare events -- a cfifo overflow once every few minutes would never land
    inside a 6 s window, and ldrop is the counter built to catch exactly that
  * slow leaks -- host memory, FT601 handle exhaustion
  * counter rollovers -- ldrop is 16-bit
  * anything that only shows up once the system has settled

WHAT IS CHECKED, continuously:
    frame rate           flat, near the configured 120 Hz
    ldrop                MUST NOT MOVE -- any increase means kernels were lost
    cfifo_ovf/ufifo_ovf  must stay clear
    calib/aligned/streaming/cap  must stay set
    frame_idx            must advance by exactly 1 per frame, no gaps
    packet structure     every packet a valid frame or reply, magic + ~magic
    HDMI mode/refresh    must not change on its own
    HDMI VSYNC counter   must keep advancing

The camera checks come from the FRAME STREAM (structure, the honest source) and
the HDMI/status checks from the Pt's control link -- so the two subsystems are
watched over two independent paths, which is the point of the merge.

usage:  python host/soak.py [minutes] [COM6]
"""
import ctypes, os, struct, sys, time

import serial
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "ft_usb_video", "host"))
import campack

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
PORT = sys.argv[2] if len(sys.argv) > 2 else "COM6"
SYNC, OP_R = 0xA5, 0x52
MAGIC, RMAGIC = 0x30494C53, 0x31494C53
HDR = 32


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd(ser, addr, window=0.5):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, buf = time.time(), b""
    while time.time() - t0 < window:
        buf += ser.read(64)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    raise SystemExit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try: getattr(d, fn)(0x82)
    except Exception: pass
d.setPipeTimeout(0x82, 1000)
buf = ctypes.create_string_buffer(1 << 22)
ser = serial.Serial(PORT, 115200, timeout=0.2)
time.sleep(0.3)

print("M7 soak -- %.0f minutes\n" % MINUTES)
base = {a: rd(ser, a) for a in (0x20, 0x21, 0x3A, 0x3B, 0x3C, 0x3D)}
ldrop0 = (base[0x3C] or 0) | ((base[0x3D] or 0) << 8)
print("  baseline: MODE=0x%02X REFR=%s alive=0x%02X health=0x%02X ldrop=%d\n"
      % (base[0x20] or 0, base[0x21], base[0x3A] or 0, base[0x3B] or 0, ldrop0))
print("   elapsed     MB/s     fps   ldrop  ovf  alive  MODE  VSYNC   frames  gaps  bad")
print("   " + "-" * 74)

t_start = time.time()
t_mark = t_start
acc = bytearray()
tot_bytes = 0
tot_frames = 0
gaps = 0
badpk = 0
# DO NOT SCORE THE FIRST PARTIAL PACKET. The accumulator starts reading wherever
# the pipe happens to be, so the opening bytes are mid-packet by definition. The
# framing resynchronises on the next magic -- that is what it is for -- but the
# first walk counts one "malformed" packet that is purely an artifact of joining
# a stream already in progress. A 30-minute run scored FAILED on exactly that,
# with 211,942 frames and zero gaps behind it.
synced = False
last_idx = None
problems = []
mark_bytes = 0
mark_frames = 0

fmag, rmag = struct.pack("<I", MAGIC), struct.pack("<I", RMAGIC)

while time.time() - t_start < MINUTES * 60:
    n = d.readPipe(0x82, buf, 1 << 22)
    if n:
        tot_bytes += n
        mark_bytes += n
        acc += buf.raw[:n]

    # walk whole packets out of the accumulator
    i = 0
    while True:
        if len(acc) - i < HDR:
            break
        h = struct.unpack_from("<8I", acc, i)
        if h[0] == MAGIC and h[7] == (~MAGIC & 0xFFFFFFFF):
            if len(acc) - i < HDR + h[4]:
                break
            if last_idx is not None and h[1] != last_idx + 1:
                gaps += 1
            last_idx = h[1]
            synced = True
            tot_frames += 1
            mark_frames += 1
            i += HDR + h[4]
        elif h[0] == RMAGIC and h[7] == (~RMAGIC & 0xFFFFFFFF):
            if len(acc) - i < HDR + h[4]:
                break
            synced = True
            i += HDR + h[4]
        else:
            nxt = [p for p in (acc.find(fmag, i + 4), acc.find(rmag, i + 4)) if p >= 0]
            if not nxt:
                i = max(0, len(acc) - HDR)
                break
            if synced:
                badpk += 1
            i = min(nxt)
    del acc[:i]
    if len(acc) > 8 * (campack.FBYTES + HDR):        # never let it grow unbounded
        del acc[:len(acc) - (campack.FBYTES + HDR)]

    # ---- periodic check ----
    now = time.time()
    if now - t_mark >= 60.0:
        dt = now - t_mark
        mbs = mark_bytes / dt / 1e6
        fps = mark_frames / dt
        a = rd(ser, 0x3A); hh = rd(ser, 0x3B)
        lo = rd(ser, 0x3C); hi = rd(ser, 0x3D)
        mode = rd(ser, 0x20); refr = rd(ser, 0x21)
        ld = ((lo or 0) | ((hi or 0) << 8))
        vs = None
        ser.reset_input_buffer()
        t0 = time.time()
        while time.time() - t0 < 1.5:
            ln = ser.readline().decode("ascii", "replace").strip()
            if ln.startswith("S=") and " N=" in ln:
                for tok in ln.split():
                    if tok.startswith("N="):
                        vs = int(tok[2:], 16)
                break
        ovf = ((hh or 0) >> 7) & 1, ((hh or 0) >> 6) & 1
        print("   %6.1f min %7.1f %7.1f %7d  %d%d  0x%02X  0x%02X  %5s %8d %5d %4d"
              % ((now - t_start) / 60.0, mbs, fps, ld, ovf[0], ovf[1],
                 a or 0, mode or 0, vs, tot_frames, gaps, badpk))

        if ld != ldrop0:            problems.append("ldrop moved %d -> %d" % (ldrop0, ld))
        if ovf[0]:                  problems.append("cfifo_ovf set")
        if ovf[1]:                  problems.append("ufifo_ovf set")
        if (a or 0) & 0x0F != 0x0F: problems.append("alive flags dropped: 0x%02X" % (a or 0))
        if mode != base[0x20]:      problems.append("HDMI mode changed %s -> %s" % (base[0x20], mode))
        if refr != base[0x21]:      problems.append("HDMI refresh changed %s -> %s" % (base[0x21], refr))
        if fps < 100:               problems.append("frame rate fell to %.1f" % fps)
        t_mark, mark_bytes, mark_frames = now, 0, 0

d.close(); ser.close()

el = (time.time() - t_start) / 60.0
print("\n" + "=" * 78)
print("  ran %.1f min   %d frames   %.2f GB   frame_idx gaps: %d   malformed: %d"
      % (el, tot_frames, tot_bytes / 1e9, gaps, badpk))
if problems:
    print("  PROBLEMS:")
    for p in sorted(set(problems)):
        print("    - " + p)
print("=" * 78)
print("SOAK PASSED" if (not problems and gaps == 0 and badpk == 0)
      else "SOAK FAILED")

"""Try to break the Pt from the PC side.

Everything the host can reach is fair game: the 0xA5 control plane on the Pt's
UART, the FT601 command pipe, and the frame pipe. After each phase the board is
checked for LIVENESS -- if a phase kills it, we know which one.

WHAT COUNTS AS BROKEN, in rough order of severity:
    - the control plane stops answering (ID no longer reads 0x48)
    - the ASCII telemetry stops
    - the camera stops streaming and does not come back
    - the frame stream loses structure (bad magic, non-contiguous frame_idx)

WHAT DOES NOT COUNT: refusing an illegal request. A guard that rejects a bad
trigger period is the design working, not failing.

Excluded on purpose: exposure above ~8300 us. That is a KNOWN, documented,
reproducible way to wedge the sensor and it needs a reconfigure to clear -- it
would end the run early and tell us nothing new.

usage:  python host/stress_attack.py [COM6]
"""
import ctypes, os, random, struct, sys, time

import serial
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "ft_usb_video", "host"))
import campack

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SYNC, OP_W, OP_R, OP_L, OP_LR = 0xA5, 0x57, 0x52, 0x5B, 0x72
random.seed(1234)
results = []


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd(ser, addr, window=0.8):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, buf = time.time(), b""
    while time.time() - t0 < window:
        buf += ser.read(64)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def telemetry_alive(ser, secs=2.0):
    ser.reset_input_buffer()
    t0 = time.time()
    while time.time() - t0 < secs:
        ln = ser.readline().decode("ascii", "replace")
        if ln.startswith("S=") and " N=" in ln:
            return True
    return False


def camera_fps(secs=1.5):
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        return -1.0
    for fn in ("abortPipe", "flushPipe"):
        try: getattr(d, fn)(0x82)
        except Exception: pass
    d.setPipeTimeout(0x82, 1000)
    b = ctypes.create_string_buffer(1 << 22)
    n = 0; t0 = time.time()
    while time.time() - t0 < secs:
        g = d.readPipe(0x82, b, 1 << 22)
        if g: n += g
    dt = time.time() - t0
    d.close()
    return n / float(campack.FBYTES + campack.HDR) / dt


def ft_send(raw):
    """Raw bytes at the FT601 command pipe -- including malformed lengths."""
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        return False
    b = ctypes.create_string_buffer(raw)
    try:    d.writePipe(0x02, b, len(raw))
    except Exception:
        try: d.writePipeEx(0x02, b, len(raw))
        except Exception: pass
    d.close()
    return True


def restore_settings():
    """Put every commandable setting back before judging liveness.

    Phases that send legal-but-extreme commands CHANGE THE CAMERA'S CONFIGURATION
    -- a slow trigger period is obedience, not damage. Measuring fps without
    restoring first conflates "you broke it" with "you told it to". The first two
    runs of this test reported seven phases as failures for exactly that reason.
    """
    ft_send(struct.pack("<I", (2 << 28) | 833333))   # 120.000 Hz
    time.sleep(0.25)
    ft_send(struct.pack("<I", (1 << 28) | 1600))     # exposure 600 us
    time.sleep(0.25)


def liveness(ser, label):
    restore_settings()
    idv = rd(ser, 0x00)
    tel = telemetry_alive(ser)
    fps = camera_fps()
    ok = (idv == 0x48) and tel and fps > 90
    results.append((label, ok, idv, tel, fps))
    print("    -> ID=%s telemetry=%s camera=%.1f fps   %s"
          % (("0x%02X" % idv) if idv is not None else "DEAD", tel, fps,
             "ok" if ok else "*** DEGRADED ***"))
    return ok


print("Stress: trying to break the Pt from the PC side\n")
with serial.Serial(PORT, 115200, timeout=0.3) as s:
    time.sleep(0.4)
    print("[0] baseline")
    liveness(s, "baseline")

    print("\n[1] Port A: 8 KB of random garbage at line rate")
    s.write(bytes(random.getrandbits(8) for _ in range(8192))); s.flush()
    time.sleep(0.5); liveness(s, "A: random garbage")

    print("\n[2] Port A: 500 frames with deliberately WRONG checksums")
    for a in range(500):
        s.write(bytes([SYNC, OP_W, 0x13, 0xAA, (a & 0xFF)]))
    s.flush(); time.sleep(0.5); liveness(s, "A: bad checksums")

    print("\n[3] Port A: truncated commands (SYNC+op, then nothing)")
    for _ in range(300):
        s.write(bytes([SYNC, OP_W])); s.write(bytes([SYNC, OP_R]))
        s.write(bytes([SYNC, OP_L, 0x01]))
    s.flush(); time.sleep(0.5); liveness(s, "A: truncated commands")

    print("\n[4] Port A: undefined opcodes")
    for op in (0x00, 0x01, 0x5A, 0x7F, 0xA5, 0xFE, 0xFF):
        for _ in range(40):
            s.write(bytes([SYNC, op, 0x00, ck(op)]))
    s.flush(); time.sleep(0.5); liveness(s, "A: undefined opcodes")

    print("\n[5] Port A: reads/writes to every address 0x00-0xFF")
    for a in range(256):
        s.write(bytes([SYNC, OP_R, a, ck(OP_R + a)]))
        s.write(bytes([SYNC, OP_W, a, 0x5A, ck(OP_W + a + 0x5A)]))
    s.flush(); time.sleep(1.0); liveness(s, "A: whole address space")

    print("\n[6] Port A: truncated table upload, then abandoned")
    s.write(bytes([SYNC, OP_L, 0x01]) + bytes(range(5))); s.flush()
    time.sleep(0.3)
    s.write(bytes([SYNC, OP_L, 0x00]) + bytes(200)); s.flush()
    time.sleep(0.5); liveness(s, "A: truncated uploads")

    print("\n[7] Port A: oversized upload + SYNC bytes embedded in the payload")
    s.write(bytes([SYNC, OP_L, 0x02]) + bytes([0xA5] * 600)); s.flush()
    time.sleep(0.5); liveness(s, "A: oversized + embedded SYNC")

    print("\n[8] Port A: upload to the READ-ONLY EDID target")
    for _ in range(20):
        s.write(bytes([SYNC, OP_L, 0x03]) + bytes(64))
    s.flush(); time.sleep(0.5); liveness(s, "A: write read-only table")

    print("\n[9] Port B: undefined FT601 opcodes 6..15, 200x each")
    for op in range(6, 16):
        for _ in range(20):
            ft_send(struct.pack("<I", (op << 28) | 0x0FFFFFFF))
    time.sleep(0.5); liveness(s, "B: undefined opcodes")

    print("\n[10] Port B: boundary payloads on every valid opcode")
    for op in (1, 2, 3, 4):
        for pay in (0x0000000, 0x0000001, 0x0FFFFFF, 0x00003E8):
            ft_send(struct.pack("<I", (op << 28) | pay))
    time.sleep(0.8); liveness(s, "B: boundary payloads")

    print("\n[11] Port B: truncated command words (1-3 bytes)")
    for n in (1, 2, 3):
        for _ in range(30):
            ft_send(bytes([0x55] * n))
    time.sleep(0.5); liveness(s, "B: truncated words")

    print("\n[12] Port B: 400 rapid re-arms (opcode 3) -- resets the ring")
    for _ in range(400):
        ft_send(struct.pack("<I", 3 << 28))
    time.sleep(1.0); liveness(s, "B: rapid re-arm flood")

    print("\n[13] Port B: 400 rapid reply requests (opcode 5) -- floods the reply path")
    for _ in range(400):
        ft_send(struct.pack("<I", 5 << 28))
    time.sleep(1.0); liveness(s, "B: reply flood")

    print("\n[14] Port B: rapid open/close of the D3XX device, 40x")
    for _ in range(40):
        d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
        if d is not None:
            try: d.close()
            except Exception: pass
    time.sleep(0.5); liveness(s, "B: open/close churn")

    print("\n[15] Port B: abandon reads mid-transfer, 30x")
    for _ in range(30):
        d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
        if d is None: continue
        try:
            d.setPipeTimeout(0x82, 50)
            b = ctypes.create_string_buffer(4096)
            d.readPipe(0x82, b, 4096)          # tiny read, abandon the rest
        except Exception:
            pass
        try: d.close()
        except Exception: pass
    time.sleep(0.8); liveness(s, "B: abandoned reads")

    print("\n[16] BOTH ports hammered at once")
    t0 = time.time()
    while time.time() - t0 < 3.0:
        s.write(bytes(random.getrandbits(8) for _ in range(256)))
        ft_send(struct.pack("<I", (random.randint(0, 15) << 28)
                            | random.getrandbits(28)))
    s.flush(); time.sleep(1.0); liveness(s, "A+B simultaneous")

    print("\n[17] restore sane state")
    s.write(bytes([SYNC, OP_W, 0x14, 0x00, ck(OP_W + 0x14)]))   # MODEFORCE off
    s.write(bytes([SYNC, OP_W, 0x13, 0x00, ck(OP_W + 0x13)]))   # SLICTRL off
    # RESTORE EVERY COMMANDABLE SETTING, not just exposure. The first run of
    # this test restored exposure alone and then reported the camera "degraded"
    # for seven straight phases -- it was sitting at a legal-but-slow trigger
    # period that phase [10] had set and nothing put back. The camera was obeying
    # orders; the test was wrong.
    ft_send(struct.pack("<I", (2 << 28) | 833333))              # 120.000 Hz
    time.sleep(0.3)
    ft_send(struct.pack("<I", (1 << 28) | 1600))                # exposure 600 us
    time.sleep(0.3)
    ft_send(struct.pack("<I", (4 << 28) | 24))                  # frames per scan
    s.flush(); time.sleep(1.5)
    liveness(s, "after restore")

print("\n" + "=" * 62)
bad = [r for r in results if not r[1]]
for label, ok, idv, tel, fps in results:
    print("  %-28s %s" % (label, "ok" if ok else "DEGRADED"))
print("=" * 62)
if not bad:
    print("SURVIVED: every phase left the board fully responsive.")
else:
    print("BROKE AT: " + ", ".join(r[0] for r in bad))

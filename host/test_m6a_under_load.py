"""M6a under load: send control commands WHILE the frame stream is running.

The passing M6a test ran on an idle pipe, which is the easy case. The real risk
is BUS TURNAROUND: ft601_sync_rx has to take the shared DATA bus away from the
transmitter to read a command, and it does that by gating the TX with rx_hold.
Get that wrong and the damage lands on the frame stream, not on the command --
the earlier version of exactly this handshake dropped bus_oe one cycle before
rx_hold reached the TX's WR# flop, so the FT601 latched a garbage word while
ft_data floated, and header spacing grew past the exact 2,621,472.

So this checks BOTH directions at once:
  * the command still executes    (register reads back over serial)
  * the frame stream is unharmed  (no frame_idx gaps, ldrop static, no overflow)

usage:  python host/test_m6a_under_load.py [seconds] [COM6]
"""
import ctypes, os, struct, sys, threading, time

import serial
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
PORT = sys.argv[2] if len(sys.argv) > 2 else "COM6"
SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
MAGIC, RMAGIC = 0x30494C53, 0x31494C53
HDR = 32


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd(ser, addr, window=0.6):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, b = time.time(), b""
    while time.time() - t0 < window:
        b += ser.read(64)
        for i in range(len(b) - 2):
            if b[i] == addr and ((b[i] + b[i + 1] + b[i + 2]) & 0xFF) == 0:
                return b[i + 1]
    return None


d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    raise SystemExit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try: getattr(d, fn)(0x82)
    except Exception: pass
d.setPipeTimeout(0x82, 1000)
ser = serial.Serial(PORT, 115200, timeout=0.3)
time.sleep(0.3)

ld0 = (rd(ser, 0x3C) or 0) | ((rd(ser, 0x3D) or 0) << 8)
print("M6a under load -- %.0f s of streaming with commands injected\n" % SECS)
print("  baseline ldrop = %d\n" % ld0)

# ---- writer thread: inject control commands into the OUT pipe mid-stream ----
sent, applied, mismatch = 0, 0, []
stop = threading.Event()


def injector():
    global sent, applied
    vals = [0xAB, 0x5A, 0x33, 0xC7, 0x11, 0xEE, 0x7D, 0x92]
    i = 0
    while not stop.is_set():
        val = vals[i % len(vals)]
        i += 1
        payload = bytes([SYNC, OP_W, 0x13, val, ck(OP_W + 0x13 + val)])
        words = b""
        for k in range(0, len(payload), 3):
            ch = payload[k:k + 3]
            w = (0 << 28) | (len(ch) << 24)
            for j, b in enumerate(ch):
                w |= b << (8 * j)
            words += struct.pack("<I", w)
        buf = ctypes.create_string_buffer(words)
        try:    d.writePipe(0x02, buf, len(words))
        except Exception: d.writePipeEx(0x02, buf, len(words))
        sent += 1
        time.sleep(0.35)
        got = rd(ser, 0x13)
        if got == val: applied += 1
        else:          mismatch.append((val, got))
        time.sleep(0.15)


th = threading.Thread(target=injector, daemon=True)
th.start()

buf = ctypes.create_string_buffer(1 << 22)
acc = bytearray()
fmag, rmag = struct.pack("<I", MAGIC), struct.pack("<I", RMAGIC)
tot_bytes = tot_frames = gaps = badpk = 0
synced, last_idx = False, None
t0 = time.time()

while time.time() - t0 < SECS:
    n = d.readPipe(0x82, buf, 1 << 22)
    if n:
        tot_bytes += n
        acc += buf.raw[:n]
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
            last_idx = h[1]; synced = True; tot_frames += 1
            i += HDR + h[4]
        elif h[0] == RMAGIC and h[7] == (~RMAGIC & 0xFFFFFFFF):
            if len(acc) - i < HDR + h[4]:
                break
            synced = True
            i += HDR + h[4]
        else:
            nxt = [p for p in (acc.find(fmag, i + 4), acc.find(rmag, i + 4)) if p >= 0]
            if not nxt:
                i = max(0, len(acc) - HDR); break
            if synced: badpk += 1        # never score the first partial packet
            i = min(nxt)
    del acc[:i]
    if len(acc) > (1 << 23):
        del acc[:len(acc) - (1 << 22)]

stop.set(); th.join(timeout=3)
el = time.time() - t0
ld1 = (rd(ser, 0x3C) or 0) | ((rd(ser, 0x3D) or 0) << 8)
health = rd(ser, 0x3B) or 0
alive = rd(ser, 0x3A) or 0
try:    d.writePipe(0x02, ctypes.create_string_buffer(
            struct.pack("<I", (3 << 24) | (SYNC) | (OP_W << 8) | (0x13 << 16))), 4)
except Exception: pass
ser.close(); d.close()

print("  streamed %.1f s   %.2f GB   %d frames   %.1f fps"
      % (el, tot_bytes / 1e9, tot_frames, tot_frames / el))
print("  commands sent %d, applied %d" % (sent, applied))
print("  frame_idx gaps %d   malformed %d" % (gaps, badpk))
print("  ldrop %d -> %d   cfifo_ovf %d ufifo_ovf %d   alive 0x%02X"
      % (ld0, ld1, (health >> 7) & 1, (health >> 6) & 1, alive))
if mismatch:
    print("  MISMATCHES: %s" % mismatch[:6])

ok = (applied == sent and sent > 0 and gaps == 0 and badpk == 0
      and ld1 == ld0 and not (health & 0xC0) and tot_frames > 0)
print("\n" + ("PASS: control commands execute mid-stream and the frame stream is unharmed."
              if ok else "FAIL"))

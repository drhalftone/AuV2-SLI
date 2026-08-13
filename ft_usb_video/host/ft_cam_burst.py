"""Verify an N-frame burst captured into DDR and streamed over the FT601.

cam_frame_ft captures NFRAMES consecutive sensor frames into DDR3 and then
re-streams them forever, so the host sees slots 0..NFRAMES-1 repeating. That
gives two independent checks in one capture:

  * SAME SLOT, different pass  -> must be BYTE-IDENTICAL. The DDR contents never
    change, so any difference is the USB bus. This is the check the 2026-07-30
    failure demands: that run measured 192 fps with zero dropped frames while
    ft_data[31:16] was wrong, so a rate number proves nothing.

  * DIFFERENT SLOTS -> must actually DIFFER. Eight identical frames would mean
    the burst captured one frame eight times, or that frames after the first were
    offset by the sensor's surplus kernels. Sensor noise alone makes two genuine
    exposures differ in most pixels.

Header word 3 carries the slot; slot must equal frame_idx mod NFRAMES.
"""
import collections
import ctypes
import struct
import sys
import time

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

PIPE = 0x82
MAGIC = 0x30494C53
NCOL, NROW = 1280, 1024
NBYTES = NCOL * NROW
HDR = 32
NFRAMES = 8
OUT = (r"C:\Users\dllau\AppData\Local\Temp\claude"
       r"\C--Users-dllau-Developer-AuV2-SLI"
       r"\38453fd4-3087-4efe-9b4a-ac6c0036de5c\scratchpad")

d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    sys.exit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try:
        getattr(d, fn)(PIPE)
    except Exception:
        pass
try:
    d.setPipeTimeout(PIPE, 1000)
except Exception:
    pass


def rd(size):
    buf = ctypes.create_string_buffer(size)
    try:
        n = d.readPipe(PIPE, buf, size)
    except Exception:
        r = d.readPipeEx(PIPE, size)
        if isinstance(r, dict):
            return bytes(r.get("bytes", b""))[: r.get("bytesTransferred", 0)]
        return bytes(r)
    return buf.raw[:n]


need = (2 * NFRAMES + 3) * (NBYTES + HDR)
data = bytearray()
t0 = time.time()
while len(data) < need and time.time() - t0 < 30.0:
    c = rd(1 << 20)
    if c:
        data += c
el = time.time() - t0
d.close()
print("read %.1f MB in %.2f s (%.1f MB/s)" % (len(data) / 1e6, el, len(data) / el / 1e6))

frames = []          # (slot, frame_idx, bytes)
offs = []
i = 0
while True:
    i = data.find(struct.pack("<I", MAGIC), i)
    if i < 0:
        break
    offs.append(i)
    i += 4
for o in offs:
    if o + HDR + NBYTES > len(data):
        break
    h = struct.unpack_from("<8I", data, o)
    if h[7] != (~MAGIC & 0xFFFFFFFF) or h[4] != NBYTES:
        continue
    frames.append((h[3], h[1], bytes(data[o + HDR: o + HDR + NBYTES])))

gaps = sorted({offs[k + 1] - offs[k] for k in range(len(offs) - 1)})
print("complete frames: %d   header spacing (expect %d): %s"
      % (len(frames), NBYTES + HDR, gaps))
print("slots seen     :", [f[0] for f in frames])
print("frame indices  :", [f[1] for f in frames])

bad_slot = [(s, ix) for s, ix, _ in frames if s != ix % NFRAMES]
print("slot == idx mod %d : %s" % (NFRAMES, "OK" if not bad_slot else bad_slot))

# --- same slot across passes must be byte-identical
by_slot = collections.defaultdict(list)
for s, ix, px in frames:
    by_slot[s].append((ix, px))

repeats = 0
corrupt = 0
for s in sorted(by_slot):
    seq = by_slot[s]
    for k in range(1, len(seq)):
        repeats += 1
        n = sum(1 for a, b in zip(seq[0][1], seq[k][1]) if a != b)
        if n:
            corrupt += n
            lanes = collections.Counter(
                j % 4 for j in range(NBYTES) if seq[0][1][j] != seq[k][1][j])
            print("  slot %d: idx %d vs %d -> %d MISMATCHES lanes=%s"
                  % (s, seq[0][0], seq[k][0], n, dict(lanes)))
print("same-slot repeats compared: %d   mismatching bytes: %d" % (repeats, corrupt))

# --- distinct slots must differ, or the burst captured one frame N times
print()
print("slot   mean   diff-vs-slot0 (%% of pixels)")
base = by_slot[0][0][1] if 0 in by_slot else None
for s in sorted(by_slot):
    px = by_slot[s][0][1]
    m = sum(px) / len(px)
    if base is None:
        print("  %d   %6.2f    n/a" % (s, m))
        continue
    diff = sum(1 for a, b in zip(px, base) if a != b)
    print("  %d   %6.2f    %5.1f%%" % (s, m, 100.0 * diff / NBYTES))

distinct = len({px[:4096] for _, _, px in frames})
print()
print("VERDICT bus     :", "BYTE-EXACT" if corrupt == 0 and repeats else
      ("CORRUPTION" if corrupt else "no repeats seen"))
print("VERDICT burst   :", "%d distinct frames" % min(distinct, len(by_slot)))

try:
    from PIL import Image
    for s in sorted(by_slot):
        Image.frombytes("L", (NCOL, NROW), by_slot[s][0][1]).save(
            OUT + r"\burst_%d.png" % s)
    sheet = Image.new("L", (NCOL // 4 * 4, NROW // 4 * 2))
    for s in sorted(by_slot):
        th = Image.frombytes("L", (NCOL, NROW), by_slot[s][0][1]).resize(
            (NCOL // 4, NROW // 4))
        sheet.paste(th, ((s % 4) * (NCOL // 4), (s // 4) * (NROW // 4)))
    sheet.save(OUT + r"\burst_sheet.png")
    print("wrote burst_0..%d.png and burst_sheet.png" % max(by_slot))
except Exception as e:
    print("PNG skipped:", e)

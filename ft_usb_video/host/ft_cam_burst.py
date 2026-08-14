"""Verify an N-frame burst captured into DDR and streamed over the FT601.

Pixels are 10-bit, carried as 16-bit little-endian with the value in the low 10
bits (header format = 2). One decoded 8-pixel kernel is exactly one 128-bit DDR
word, so there is no multi-read packing to get wrong.

Three checks, all on the same capture:

  * SAME SLOT, different pass -> must be BYTE-IDENTICAL. DDR contents never
    change, so any difference is the USB bus. The 2026-07-30 failure measured
    192 fps with zero dropped frames while ft_data[31:16] was wrong, so a rate
    number proves nothing and only byte comparison does.

  * DIFFERENT SLOTS -> must actually DIFFER, or the burst captured one frame N
    times, or frames after the first are offset by the sensor's surplus kernels.

  * NO KERNEL DUPLICATION. cfifo is first-word-fall-through, so a consumer that
    pops a cycle late reads the same kernel twice. That bug shipped: it made every
    frame 640 real columns stretched to 1280 in 8-pixel blocks, and it was
    invisible on a featureless lens-less scene. Test it numerically, every time.
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
NPIX = NCOL * NROW
FBYTES = NPIX * 2                 # 16 bpp
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


need = (2 * NFRAMES + 3) * (FBYTES + HDR)
data = bytearray()
t0 = time.time()
while len(data) < need and time.time() - t0 < 40.0:
    c = rd(1 << 20)
    if c:
        data += c
el = time.time() - t0
d.close()
print("read %.1f MB in %.2f s (%.1f MB/s)" % (len(data) / 1e6, el, len(data) / el / 1e6))

frames, offs = [], []
i = 0
while True:
    i = data.find(struct.pack("<I", MAGIC), i)
    if i < 0:
        break
    offs.append(i)
    i += 4
fmt = None
for o in offs:
    if o + HDR + FBYTES > len(data):
        break
    h = struct.unpack_from("<8I", data, o)
    if h[7] != (~MAGIC & 0xFFFFFFFF) or h[4] != FBYTES:
        continue
    fmt = h[6]
    frames.append((h[3], h[1], bytes(data[o + HDR: o + HDR + FBYTES])))

gaps = sorted({offs[k + 1] - offs[k] for k in range(len(offs) - 1)})
print("complete frames: %d   header spacing (expect %d): %s"
      % (len(frames), FBYTES + HDR, gaps))
print("format field   : %s (2 = 16-bit LE, 10 valid bits)" % fmt)
print("slots seen     :", [f[0] for f in frames])
bad = [(s, ix) for s, ix, _ in frames if s != ix % NFRAMES]
print("slot == idx mod %d : %s" % (NFRAMES, "OK" if not bad else bad))
if not frames:
    sys.exit("no complete frames")

# ---- 1. bus integrity: same slot, different pass
by_slot = collections.defaultdict(list)
for s, ix, px in frames:
    by_slot[s].append((ix, px))
repeats = corrupt = 0
for s in sorted(by_slot):
    seq = by_slot[s]
    for k in range(1, len(seq)):
        repeats += 1
        n = sum(1 for a, b in zip(seq[0][1], seq[k][1]) if a != b)
        corrupt += n
        if n:
            print("  slot %d: idx %d vs %d -> %d MISMATCHES" % (s, seq[0][0], seq[k][0], n))
print("same-slot repeats: %d   mismatching bytes: %d" % (repeats, corrupt))

# ---- 2. kernel duplication (16 bytes = 8 pixels per DDR word half)
ref = by_slot[min(by_slot)][0][1]
groups = FBYTES // 32
dup = sum(1 for g in range(groups) if ref[g * 32:g * 32 + 16] == ref[g * 32 + 16:g * 32 + 32])
ctrl = sum(1 for g in range(groups - 1)
           if ref[g * 32 + 8:g * 32 + 24] == ref[g * 32 + 24:g * 32 + 40])
print("duplicated 8-pixel kernels: %d / %d (%.2f%%)   control %.2f%%"
      % (dup, groups, 100.0 * dup / groups, 100.0 * ctrl / max(groups - 1, 1)))

# ---- 3. pixel depth actually present
vals = struct.unpack_from("<%dH" % NPIX, ref, 0)
lo, hi = min(vals), max(vals)
over = sum(1 for v in vals if v > 0x3FF)
odd = len({v & 3 for v in vals})
print("pixel range: %d .. %d   values >10 bits: %d   distinct low-2-bit patterns: %d"
      % (lo, hi, over, odd))
print("  (4 distinct low-2-bit patterns means the two extra bits carry real data)")

print()
print("VERDICT bus       :", "BYTE-EXACT" if corrupt == 0 and repeats else "CHECK")
print("VERDICT duplication:", "NONE" if dup * 20 < groups else "DUPLICATED")
print("VERDICT depth     :", "10-bit" if over == 0 and odd == 4 else "suspect")

# ---- save RAW 16-bit, and do it before any display conversion.
#
# The PNGs below are 8-bit with a PER-FRAME min/max stretch: display only. An
# earlier lit-vs-covered analysis was computed by inverting that stretch, which
# is a second quantisation on top of the 10-bit data. It happened to be harmless
# (row-mean error came out at 0.34 LSB against a 65 LSB effect) but quantitative
# work should read these .bin files, not the pictures.
for s in sorted(by_slot):
    with open(OUT + r"\raw_slot%d_u16.bin" % s, "wb") as f:
        f.write(by_slot[s][0][1])
print("wrote raw_slot0..%d_u16.bin (%d x %d, 16-bit LE, 10 valid bits)"
      % (max(by_slot), NCOL, NROW))

try:
    from PIL import Image
    for s in sorted(by_slot):
        v = struct.unpack_from("<%dH" % NPIX, by_slot[s][0][1], 0)
        lo8, hi8 = min(v), max(v)
        span = max(hi8 - lo8, 1)
        img = bytes(min(255, max(0, (x - lo8) * 255 // span)) for x in v)
        Image.frombytes("L", (NCOL, NROW), img).save(OUT + r"\b10_%d.png" % s)
    print("wrote b10_0..%d.png (contrast-stretched for display)" % max(by_slot))
except Exception as e:
    print("PNG skipped:", e)

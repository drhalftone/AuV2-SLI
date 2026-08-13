"""Byte-exactness check for the FT601 bus, plus a PNG of the frame.

The DDR holds ONE captured frame and the streamer re-reads it forever, so every
frame the host receives must be byte-identical. Differences between consecutive
frames can only come from the bus. This is the check that the 2026-07-30 failure
demanded: that run measured 192 fps with zero dropped frames while ft_data[31:16]
was wrong, so throughput proves nothing and only byte comparison does.

Errors are bucketed by (offset mod 4) -- the byte lane -- because that is what
localises a bus fault to a half or a quarter of the 32-bit word.
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
NFRAMES = 5

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


need = (NFRAMES + 2) * (NBYTES + HDR)
data = bytearray()
t0 = time.time()
while len(data) < need and time.time() - t0 < 20.0:
    c = rd(1 << 20)
    if c:
        data += c
el = time.time() - t0
print("read %d bytes in %.2f s (%.1f MB/s)" % (len(data), el, len(data) / el / 1e6))
d.close()

# locate every header and pull the frames that follow
offs = []
i = 0
while True:
    i = data.find(struct.pack("<I", MAGIC), i)
    if i < 0:
        break
    offs.append(i)
    i += 4
print("headers found:", len(offs))

frames = []
idxs = []
for o in offs:
    if o + HDR + NBYTES > len(data):
        break
    h = struct.unpack_from("<8I", data, o)
    if h[7] != (~MAGIC & 0xFFFFFFFF) or h[4] != NBYTES:
        print("  header at %d malformed: %s" % (o, h))
        continue
    idxs.append(h[1])
    frames.append(bytes(data[o + HDR: o + HDR + NBYTES]))

print("complete frames:", len(frames), "frame indices:", idxs)
if len(frames) < 2:
    sys.exit("need >=2 complete frames to compare")

# header spacing must be exactly one frame + header, or words were lost
gaps = [offs[k + 1] - offs[k] for k in range(len(offs) - 1)]
print("header spacing (expect %d):" % (NBYTES + HDR), sorted(set(gaps))[:5])

ref = frames[0]
bad_total = 0
for k, f in enumerate(frames[1:], 1):
    lanes = collections.Counter()
    n = 0
    for j in range(NBYTES):
        if f[j] != ref[j]:
            lanes[j % 4] += 1
            n += 1
    bad_total += n
    print("frame %d vs frame 0: %d mismatches  lanes=%s" % (k, n, dict(lanes)))

print()
print("VERDICT:", "BYTE-EXACT" if bad_total == 0 else "CORRUPTION (%d bytes)" % bad_total)

try:
    from PIL import Image
    im = Image.frombytes("L", (NCOL, NROW), ref)
    out = (r"C:\Users\dllau\AppData\Local\Temp\claude"
           r"\C--Users-dllau-Developer-AuV2-SLI"
           r"\38453fd4-3087-4efe-9b4a-ac6c0036de5c\scratchpad\ft_frame.png")
    im.save(out)
    print("wrote", out)
    print("min=%d max=%d mean=%.1f" % (min(ref), max(ref), sum(ref) / len(ref)))
except Exception as e:
    print("PNG skipped:", e)

"""Verify the concurrent DDR3 write+read loopback, byte by byte.

The FPGA writes a deterministic pattern into DDR3 with one process and streams it
back with another. Every 32-bit word must be exactly

    {2'b0, frame[5:0], index[23:0]}

so this checks each word against what it MUST be, rather than checking that two
frames agree. A fault that corrupted every frame identically -- a wrong base
address, a stuck read pointer, an off-by-one in the unpacker -- would pass the
weaker frame-vs-frame test and fail this one.
"""
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
FBYTES = NPIX * 2
HDR = 32
CH = 1 << 22
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

d = None
for _ in range(10):
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is not None:
        break
    time.sleep(0.3)
if d is None:
    sys.exit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try:
        getattr(d, fn)(PIPE)
    except Exception:
        pass
try:
    d.setPipeTimeout(PIPE, 2000)
except Exception:
    pass
try:
    d.setStreamPipe(PIPE, CH)
except Exception:
    pass

buf = ctypes.create_string_buffer(CH)
data = bytearray()
t0 = time.time()
while time.time() - t0 < SECONDS and len(data) < 6 * (FBYTES + HDR):
    try:
        n = d.readPipe(PIPE, buf, CH)
    except Exception as e:
        print("read error:", e)
        break
    if n:
        data += buf.raw[:n]
el = time.time() - t0
try:
    d.clearStreamPipe(PIPE)
except Exception:
    pass
d.close()

print("read %.1f MB in %.2f s (%.1f MB/s)" % (len(data) / 1e6, el, len(data) / el / 1e6))
if not data:
    sys.exit("NO DATA -- nothing streamed")

offs = []
i = 0
while True:
    i = data.find(struct.pack("<I", MAGIC), i)
    if i < 0:
        break
    offs.append(i)
    i += 4
gaps = sorted({offs[k + 1] - offs[k] for k in range(len(offs) - 1)})
print("headers: %d   spacing (expect %d): %s" % (len(offs), FBYTES + HDR, gaps))

checked = bad = 0
frames = 0
for o in offs:
    if o + HDR + FBYTES > len(data):
        break
    h = struct.unpack_from("<8I", data, o)
    if h[7] != (~MAGIC & 0xFFFFFFFF) or h[4] != FBYTES:
        continue
    slot = h[3] & 0x3F
    frames += 1
    # every 32-bit word must be {2'b0, slot[5:0], index[23:0]}
    words = struct.unpack_from("<%dI" % (FBYTES // 4), data, o + HDR)
    expect_hi = slot << 24
    first_bad = None
    for idx, w in enumerate(words):
        if w != (expect_hi | idx):
            bad += 1
            if first_bad is None:
                first_bad = (idx, w, expect_hi | idx)
        checked += 1
    if first_bad:
        idx, got, exp = first_bad
        print("  slot %2d: FIRST MISMATCH at word %d: got %08X expected %08X"
              % (slot, idx, got, exp))
    else:
        print("  slot %2d: all %d words correct" % (slot, len(words)))

print()
print("frames checked : %d" % frames)
print("words checked  : %d" % checked)
print("words wrong    : %d" % bad)
print()
print("VERDICT:", "PATTERN EXACT -- concurrent write+read works"
      if frames and bad == 0 else "FAILED")

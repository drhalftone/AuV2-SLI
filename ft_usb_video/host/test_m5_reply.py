"""M5: does the USB3 link answer back, without damaging the frame stream?

Sends opcode 5 (request status reply) on the OUT pipe and looks for the reply on
the IN pipe, interleaved between frames.

TWO THINGS MUST BOTH HOLD, and the second is the one that matters:

  1. A reply arrives, correctly framed, with the right marker.
  2. THE FRAMES ARE UNHARMED. Every frame still carries MAGIC and ~MAGIC, still
     states the expected length, and frame_idx still advances by one each time.

The second is not a formality. This project has already shipped a build that
streamed at full rate with zero dropped frames while silently corrupting half
the data bus -- throughput and frame counts could not see it. Mixing a second
packet type into the same pipe is exactly the change that could reintroduce
that, so the test checks structure rather than volume.

Replies are inserted ONLY at frame boundaries, so the host's fast path is
unchanged: read a 32-byte header, switch on the magic, consume the length the
header states.

usage:  python test_m5_reply.py [n_requests]
"""
import ctypes, struct, sys, time

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

MAGIC = 0x30494C53          # a video frame
RMAGIC = 0x31494C53         # a control reply
HDR = 32
NREQ = int(sys.argv[1]) if len(sys.argv) > 1 else 5

d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    raise SystemExit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try: getattr(d, fn)(0x82)
    except Exception: pass
d.setPipeTimeout(0x82, 1000)
buf = ctypes.create_string_buffer(1 << 22)


def request():
    w = ctypes.create_string_buffer(struct.pack("<I", 5 << 28))
    try:    d.writePipe(0x02, w, 4)
    except Exception: d.writePipeEx(0x02, w, 4)


print("M5 -- control reply on the frame pipe\n")
data = bytearray()
t0 = time.time()
sent = 0
while time.time() - t0 < 6.0:
    if sent < NREQ and (time.time() - t0) > 0.5 + sent * 0.6:
        request(); sent += 1
    n = d.readPipe(0x82, buf, 1 << 22)
    if n:
        data += buf.raw[:n]
d.close()
print("  sent %d requests, captured %.1f MB\n" % (sent, len(data) / 1e6))

# ---- walk the stream as a sequence of typed packets ----------------------
fmag, rmag = struct.pack("<I", MAGIC), struct.pack("<I", RMAGIC)
i = 0
frames, replies, bad = 0, 0, []
idxs, seqs, markers = [], [], []
# find the first packet boundary of either kind
starts = [p for p in (data.find(fmag), data.find(rmag)) if p >= 0]
i = min(starts) if starts else -1
while i >= 0 and i + HDR <= len(data):
    h = struct.unpack_from("<8I", data, i)
    if h[0] == MAGIC and h[7] == (~MAGIC & 0xFFFFFFFF):
        if i + HDR + h[4] > len(data):
            break
        if h[4] != campack.FBYTES:
            bad.append("frame at %d claims %d bytes, expected %d" % (i, h[4], campack.FBYTES))
        frames += 1
        idxs.append(h[1])
        i += HDR + h[4]
    elif h[0] == RMAGIC and h[7] == (~RMAGIC & 0xFFFFFFFF):
        if i + HDR + h[4] > len(data):
            break
        pay = struct.unpack_from("<4I", data, i + HDR)
        replies += 1
        seqs.append(h[1])
        markers.append(pay[2])
        i += HDR + h[4]
    else:
        bad.append("unrecognised packet at %d: magic=0x%08X" % (i, h[0]))
        nxt = [p for p in (data.find(fmag, i + 4), data.find(rmag, i + 4)) if p >= 0]
        if not nxt:
            break
        i = min(nxt)

print("  frames  : %d" % frames)
print("  replies : %d  seq=%s marker=%s" % (replies, seqs, [hex(m) for m in markers]))
print("  malformed packets: %d" % len(bad))
for b in bad[:5]:
    print("     " + b)

# frame_idx must still advance by exactly one per frame
gaps = [b - a for a, b in zip(idxs, idxs[1:])]
contiguous = all(g == 1 for g in gaps) if gaps else False
print("  frame_idx contiguous : %s" % contiguous)
if not contiguous and gaps:
    print("     gaps seen: %s" % sorted(set(gaps))[:8])

print()
ok = (replies >= 1 and frames > 20 and not bad and contiguous
      and all(m == 0x48 for m in markers))
if ok:
    print("PASS: replies arrive and the frame stream is structurally intact.")
else:
    print("FAIL: %s" % (
        "no reply arrived" if replies == 0 else
        "malformed packets" if bad else
        "frame_idx not contiguous -- a boundary was lost" if not contiguous else
        "wrong marker in the reply payload"))

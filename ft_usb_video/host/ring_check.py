"""Ring-buffer check: frames must be CLEAN, FRESH, and cost no lost kernels.

Clean = no zero padding, i.e. the frame was delivered whole.
Fresh = frame_idx keeps advancing, i.e. the reader is following the writer and
        not re-serving one stale slot.
Lost  = ldrop, from the frame header, counts frames the writer had to pad
        because kernels went missing. CONSTANT across a run is the positive
        result: not one kernel was lost while these frames were captured.

None of the three is sufficient alone. A torn frame from an overwritten slot
still looks like a photograph, a stale slot still looks clean, and a frame can
be both clean and fresh while the FIFO behind it is quietly overflowing -- which
is exactly the state this design was in when it "worked".
"""
import ctypes, time
import numpy as np
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    raise SystemExit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try: getattr(d, fn)(0x82)
    except Exception: pass
d.setPipeTimeout(0x82, 1000)
b = ctypes.create_string_buffer(1 << 22)
data = bytearray(); t0 = time.time()
while len(data) < 40_000_000 and time.time() - t0 < 6:
    n = d.readPipe(0x82, b, 1 << 22)
    if n: data += b.raw[:n]
el = time.time() - t0
d.close()

clean = pad = 0
idxs = []; slots = []; drops = []; fmts = set()
for h, frame in campack.iter_frames(data):
    idxs.append(h["frame_idx"]); slots.append(h["slot"]); drops.append(h["ldrop"])
    fmts.add(h["fmt"])
    if int((frame == 0).sum()) > 1000: pad += 1
    else: clean += 1

fps = (clean + pad) / el if el else 0
print("%.1f MB/s   %d CLEAN, %d padded   (%.1f frames/s through this script)"
      % (len(data) / el / 1e6, clean, pad, fps))
print("payload format: %s" % (", ".join(
    {campack.FMT_U16: "2 (10-bit in u16)",
     campack.FMT_PACK10: "3 (packed 10-bit)"}.get(f, str(f)) for f in fmts) or "none"))
if idxs:
    print("frame_idx %d..%d   advancing: %s" % (idxs[0], idxs[-1], idxs[-1] > idxs[0]))
    print("slots served:", slots[:12])
    print("ldrop %d..%d   grew by %d during this run"
          % (drops[0], drops[-1], drops[-1] - drops[0]))
print()
ok = clean and not pad and idxs and idxs[-1] > idxs[0] and drops[-1] == drops[0]
print("VERDICT:", "RING OK -- clean, fresh, and no kernels lost" if ok else "NOT OK")

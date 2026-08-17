"""Ring-buffer check: frames must be CLEAN, and they must be FRESH.

Clean = no zero padding, i.e. the frame was delivered whole.
Fresh = frame_idx keeps advancing, i.e. the reader is following the writer and
        not re-serving one stale slot.
A torn frame from an overwritten slot would still look like a photograph, so the
zero test is not sufficient on its own -- the slot number is reported too.
"""
import ctypes, struct, time
import numpy as np
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
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

mag = struct.pack("<I", 0x30494C53)
i = 0; clean = pad = 0; idxs = []; slots = []; drops = []
while True:
    i = data.find(mag, i)
    if i < 0 or i + 32 + 2621440 > len(data): break
    h = struct.unpack_from("<8I", data, i); i += 4
    if h[7] != (~0x30494C53 & 0xFFFFFFFF): continue
    a = np.frombuffer(data[i + 28:i + 28 + 2621440], dtype="<u2")
    idxs.append(h[1]); slots.append(h[3] & 0x3F)
    # header word 3: {2'b0, ldrop[15:0], nframes[5:0], 2'b0, slot[5:0]}
    drops.append((h[3] >> 14) & 0xFFFF)
    if int((a == 0).sum()) > 1000: pad += 1
    else: clean += 1

print("%.1f MB/s   %d CLEAN, %d padded" % (len(data) / el / 1e6, clean, pad))
if idxs:
    print("frame_idx %d..%d   advancing: %s" % (idxs[0], idxs[-1], idxs[-1] > idxs[0]))
    print("slots served:", slots[:12])
    # ldrop counts frames the writer had to pad because kernels went missing.
    # A CONSTANT ldrop across the run is the positive result: it means not one
    # kernel was lost while these frames were captured. A rising one says the
    # camera->DDR FIFO is still overflowing, which is the fault that put every
    # delivered frame at a different offset.
    print("ldrop %d..%d   grew by %d during this run"
          % (drops[0], drops[-1], drops[-1] - drops[0]))
print()
ok = clean and not pad and idxs[-1] > idxs[0] and drops[-1] == drops[0]
print("VERDICT:", "RING OK -- clean, fresh, and no kernels lost" if ok else "NOT OK")

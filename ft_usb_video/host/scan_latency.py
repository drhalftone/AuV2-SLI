"""Measure scan turnaround: re-arm, then time until a complete NEW scan arrives.

This is the test that decides whether capture and streaming actually OVERLAP.
Byte-exact data does not prove it -- a sequential design produces byte-exact data
too, just later. Only the wall-clock turnaround separates them:

    sequential : capture THEN download   ~200 ms + ~182 ms = ~380 ms
    concurrent : download trails capture ~205 ms

Method: send the re-arm command, then read until every slot 0..N-1 has been seen
with a frame_idx at or beyond the first one observed after the re-arm. Frames left
in the FT601's buffers from the previous scan are discarded by requiring the
frame index to advance, so the timer measures a genuinely new scan.

usage: scan_latency.py [repeats]
"""
import ctypes
import struct
import subprocess
import sys
import time

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

PIPE = 0x82
MAGIC = 0x30494C53
NCOL, NROW = 1280, 1024
FBYTES = NCOL * NROW * 2
HDR = 32
CH = 1 << 22
CTL = r"C:\Users\dllau\Developer\AuV2-SLI\ft_usb_video\host\cam_ctl.py"
REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def open_pipe():
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
        d.setPipeTimeout(PIPE, 3000)
    except Exception:
        pass
    try:
        d.setStreamPipe(PIPE, CH)
    except Exception:
        pass
    return d


results = []
for run in range(REPEATS):
    d = open_pipe()
    buf = ctypes.create_string_buffer(CH)

    # drain whatever is already buffered so the timer starts clean
    t_drain = time.time()
    while time.time() - t_drain < 0.5:
        try:
            d.readPipe(PIPE, buf, CH)
        except Exception:
            break

    subprocess.run([sys.executable, CTL, "--grab"], capture_output=True)
    t0 = time.time()

    seen = {}
    base_idx = None
    data = bytearray()
    nframes = None
    while time.time() - t0 < 5.0:
        try:
            n = d.readPipe(PIPE, buf, CH)
        except Exception:
            continue
        if not n:
            continue
        data += buf.raw[:n]
        i = 0
        while True:
            i = data.find(struct.pack("<I", MAGIC), i)
            if i < 0 or i + HDR + FBYTES > len(data):
                break
            h = struct.unpack_from("<8I", data, i)
            i += 4
            if h[7] != (~MAGIC & 0xFFFFFFFF) or h[4] != FBYTES:
                continue
            slot = h[3] & 0x3F
            nf = (h[3] >> 8) & 0x3F
            if nf:
                nframes = nf
            if base_idx is None:
                base_idx = h[1]
            if h[1] >= base_idx:
                seen[slot] = h[1]
        if nframes and len(seen) >= nframes:
            break

    el = time.time() - t0
    try:
        d.clearStreamPipe(PIPE)
    except Exception:
        pass
    d.close()

    ok = nframes is not None and len(seen) >= nframes
    results.append(el if ok else None)
    print("run %d: %s  (%d/%s slots, %.1f MB read)"
          % (run + 1, ("%.0f ms" % (el * 1000)) if ok else "INCOMPLETE",
             len(seen), nframes, len(data) / 1e6), flush=True)

good = [r for r in results if r]
print()
if good:
    print("scan turnaround: min %.0f ms   mean %.0f ms   max %.0f ms"
          % (min(good) * 1000, sum(good) / len(good) * 1000, max(good) * 1000))
    print("  sequential would be ~380 ms (capture then download)")
    print("  concurrent  should be ~205 ms (download trails capture)")
else:
    print("no complete scans captured")

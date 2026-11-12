"""Grab frames from a CAM_WIN bitstream and work out what the sensor really sent.

  1. Line length: the repeat period of the flat pixel stream near the header's ncol
     and near 1280. A period of 1280 means the column window was ignored.
  2. Black rows: per-row mean and spread of the first rows, laid out at the true
     line length.
  3. Saves PNGs laid out at the header width and at the measured line length.

Usage: crop_check.py OUTPREFIX [EXPO_US]
"""
import ctypes
import struct
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\ft_usb_video\host")
import campack
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

out = sys.argv[1]
expo_us = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0

dev = None
for _ in range(10):
    dev = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if dev is not None:
        break
    time.sleep(0.3)
if dev is None:
    sys.exit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try:
        getattr(dev, fn)(0x82)
    except Exception:
        pass
dev.setPipeTimeout(0x82, 1000)
# Same exposure cam_live.py pushes at startup (opcode 1, units of 0.375 us).
dev.writePipe(0x02, ctypes.create_string_buffer(
    struct.pack("<I", (1 << 28) | int(expo_us / 0.375))), 4)
time.sleep(0.5)

buf = ctypes.create_string_buffer(1 << 22)
data = bytearray()
t0 = time.time()
while len(data) < 30_000_000 and time.time() - t0 < 10:
    n = dev.readPipe(0x82, buf, len(buf))
    data += bytes(memoryview(buf)[:n])
dev.close()
dt = time.time() - t0

frames = list(campack.iter_frames(data))
if not frames:
    sys.exit("no frames parsed from %d bytes" % len(data))
h = frames[0][0]
ncol, nrow = h["ncol"], h["nrow"]
print("read %d bytes in %.1f s, %d frames, header %d x %d, fmt %d"
      % (len(data), dt, len(frames), ncol, nrow, h["fmt"]))
stack = np.stack([f[1] for f in frames[-8:]]).astype(np.float64)
img = stack.mean(axis=0)
np.save(out + "_mean.npy", img)
flat = img.ravel()
print("level: min %.1f  max %.1f  mean %.1f" % (flat.min(), flat.max(), flat.mean()))


def lagcorr(x, lag):
    a, b = x[:-lag] - x[:-lag].mean(), x[lag:] - x[lag:].mean()
    return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))


# Skip the first 32 rows' worth of pixels so black rows cannot steer the period.
body = flat[32 * ncol:]
cands = range(min(ncol, 1280) - 40, max(ncol, 1280) + 41)
scores = sorted(((lagcorr(body, L), L) for L in cands), reverse=True)
print("line-length candidates:", ", ".join("%d (%.4f)" % (L, c) for c, L in scores[:5]))
for L in sorted({ncol, 1280}):
    print("  period %4d  corr %.4f" % (L, lagcorr(body, L)))
line = scores[0][1]

rows = flat[: (flat.size // line) * line].reshape(-1, line)
print("first rows at line length %d (mean / std):" % line)
for r in range(12):
    print("  row %2d  %7.1f  %6.1f" % (r, rows[r].mean(), rows[r].std()))
print("  ...  body rows 32..: mean %.1f" % rows[32:].mean())


def save(a, name):
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    a = np.clip((a - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
    Image.fromarray(a).resize((a.shape[1] // 2, a.shape[0] // 2)).save(name)


save(img, out + "_as_header.png")
save(rows, out + "_as_%d.png" % line)
print("saved", out + "_as_header.png", out + "_as_%d.png" % line)

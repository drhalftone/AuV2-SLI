"""Measure frame-to-frame ALIGNMENT DRIFT, in KERNELS, without fooling ourselves.

WHY THE OBVIOUS VERSION DOES NOT WORK. Correlating the raw pixel streams of two
frames locks onto the 8-pixel KERNEL PATTERN, not onto the scene: the de-
interleave emits even kernels ascending and odd kernels descending, which is a
strong period-8 signal present in every frame regardless of alignment. The
correlator then peaks at an essentially arbitrary multiple of 8 and reports a
huge, confident, meaningless shift. Every shift measured that way came out an
exact multiple of 8 -- that was the tell.

WHAT THIS DOES INSTEAD. Reduce each 8-pixel kernel to its MEAN. That collapses
the period-8 pattern to a constant and leaves the scene, so the correlation is
driven by image content. The sequence is then one sample per kernel, so the
recovered shift is already in kernels -- the unit the writer actually counts in.

This sensor has no lens and the scene is close to featureless, so the answer is
only as good as the structure present. Two guards:
  * The scene profile is reported, so a flat field is visible as such.
  * Every shift carries a peak quality, calibrated against a control: each frame
    is first correlated WITH ITSELF. That autocorrelation is the known-perfect
    case, so it sets the scale a real match has to reach. A shift whose quality
    is far below the control is not evidence.
"""
import ctypes, time
import numpy as np
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

NCOL, NROW = campack.NCOL, campack.NROW
NPIX = campack.NPIX
NKERN = NPIX // 8
NWANT = 12

d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    raise SystemExit("no D3XX device -- is the viewer still holding it?")
for fn in ("abortPipe", "flushPipe"):
    try: getattr(d, fn)(0x82)
    except Exception: pass
d.setPipeTimeout(0x82, 1000)

b = ctypes.create_string_buffer(1 << 22)
data = bytearray(); t0 = time.time()
while len(data) < 90_000_000 and time.time() - t0 < 10:
    n = d.readPipe(0x82, b, 1 << 22)
    if n: data += b.raw[:n]
d.close()
print("captured %.1f MB in %.1f s" % (len(data) / 1e6, time.time() - t0))

frames = [(h["frame_idx"], h["slot"],
           f.astype(np.float32).reshape(NKERN, 8).mean(axis=1))
          for h, f in campack.iter_frames(data, want=NWANT)]
print("parsed %d frames (reduced to %d kernel means each)\n" % (len(frames), NKERN))
if len(frames) < 2:
    raise SystemExit("need at least 2 frames")

k0 = frames[0][2]
print("SCENE (kernel means): mean %.1f  std %.1f" % (k0.mean(), k0.std()))
print("       frame-to-frame difference std %.1f" % (frames[1][2] - k0).std())

N = 1 << int(np.ceil(np.log2(2 * NKERN)))

def corr(x, y):
    A = np.fft.rfft(x - x.mean(), N)
    B = np.fft.rfft(y - y.mean(), N)
    return np.fft.irfft(A * np.conj(B), N)

def shift_q(x, y):
    c = np.abs(corr(x, y))
    k = int(np.argmax(c))
    m = c.copy()
    w = 64                                   # kernels; excise the peak lobe
    lo, hi = max(0, k - w), min(N, k + w)
    m[lo:hi] = 0.0
    keep = m[m > 0]
    q = (c[k] - keep.mean()) / (keep.std() + 1e-9)
    return (k - N if k > N // 2 else k), q

# CONTROL: the known-perfect match. Sets the scale a real answer must reach.
_, q_self = shift_q(k0, k0)
print("\nCONTROL  frame 0 vs itself: quality %.0f  <- a true match scores about this" % q_self)
thresh = q_self * 0.25
print("         accepting shifts above %.0f (25%% of control)\n" % thresh)

print("--- vs FRAME 0 ---")
print("%-6s %-5s %10s %10s %9s  %s" % ("idx", "slot", "kernels", "pixels", "rows", "quality"))
for idx, slot, k in frames:
    s, q = shift_q(k0, k)
    ok = "" if q >= thresh else "   <-- below control, not evidence"
    print("%-6d %-5d %10d %10d %9.2f  %7.0f%s"
          % (idx, slot, s, s * 8, s * 8.0 / NCOL, q, ok))

print("\n--- vs PREVIOUS FRAME FROM THE SAME SLOT ---")
print("a per-frame kernel deficit that never resyncs shows up here as a small,")
print("CONSISTENT, same-signed shift that scales with the frame gap\n")
print("%-6s %-5s %8s %10s %10s  %s" % ("idx", "slot", "idx_gap", "kernels", "pixels", "quality"))
last = {}
for idx, slot, k in frames:
    if slot in last:
        pidx, pk = last[slot]
        s, q = shift_q(pk, k)
        ok = "" if q >= thresh else "   <-- below control, not evidence"
        print("%-6d %-5d %8d %10d %10d  %7.0f%s"
              % (idx, slot, idx - pidx, s, s * 8, q, ok))
    last[slot] = (idx, k)

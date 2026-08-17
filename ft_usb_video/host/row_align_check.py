"""Are delivered frames aligned WITH EACH OTHER? Decided on row profiles.

The scene here is horizontal banding -- row-mean spread ~80 counts, column-mean
spread ~1 -- so the 1024-sample ROW PROFILE is the strongest signature a frame
has, and it is 160x shorter than the pixel stream, which makes its correlation
enormously better conditioned than correlating the raw streams.

The test is deliberately one-sided and needs no threshold picked by hand:

    If every frame carries the same alignment, then the NORMALISED correlation
    of two frames' row profiles is MAXIMISED AT ZERO SHIFT. That holds under
    arbitrary noise, because noise is zero-mean and only the shared scene
    structure survives averaging -- at lag zero it adds coherently.

So r(0) is compared against the best r over all shifts. If r(0) IS the best,
the frames agree and there is no drift to fix. If some other shift wins
repeatedly and by a clear margin, the frames genuinely sit at different offsets.

Correlation is NORMALISED (Pearson) so the numbers are interpretable on their
own: 1.0 is a perfect match, 0 is nothing. Frame 0 against itself is included as
a control -- it must read exactly 1.000 at shift 0, and if it does not, the
measurement is broken and nothing below it means anything.
"""
import ctypes, time
import numpy as np
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

NROW = campack.NROW
NWANT = 10

d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
if d is None:
    raise SystemExit("no D3XX device")
for fn in ("abortPipe", "flushPipe"):
    try: getattr(d, fn)(0x82)
    except Exception: pass
d.setPipeTimeout(0x82, 1000)
b = ctypes.create_string_buffer(1 << 22)
data = bytearray(); t0 = time.time()
while len(data) < 80_000_000 and time.time() - t0 < 10:
    n = d.readPipe(0x82, b, 1 << 22)
    if n: data += b.raw[:n]
d.close()

frames = [(h["frame_idx"], h["slot"], f.astype(np.float32).mean(axis=1))
          for h, f in campack.iter_frames(data, want=NWANT)]
print("captured %.1f MB, parsed %d frames\n" % (len(data) / 1e6, len(frames)))
if len(frames) < 2:
    raise SystemExit("need at least 2 frames")


def ncc_all(x, y):
    """Pearson r at every circular shift of y against x."""
    xz = (x - x.mean()) / (x.std() + 1e-9)
    yz = (y - y.mean()) / (y.std() + 1e-9)
    n = len(x)
    F = np.fft.rfft(xz, n) * np.conj(np.fft.rfft(yz, n))
    return np.fft.irfft(F, n) / n


p0 = frames[0][2]
print("row-profile contrast: std %.1f counts over mean %.1f" % (p0.std(), p0.mean()))
print()
print("%-6s %-5s %8s %9s %9s   %s" %
      ("idx", "slot", "best_row", "r(best)", "r(0)", "verdict"))
agree = 0
for idx, slot, p in frames:
    r = ncc_all(p0, p)
    k = int(np.argmax(r))
    best = k - NROW if k > NROW // 2 else k
    if best == 0: agree += 1
    print("%-6d %-5d %8d %9.3f %9.3f   %s"
          % (idx, slot, best, r[k], r[0],
             "ALIGNED" if best == 0 else "shifted %+d rows" % best))

print()
print("%d of %d frames peak at zero shift." % (agree, len(frames)))
print("VERDICT:", "frames AGREE -- no inter-frame drift" if agree == len(frames)
      else "frames DISAGREE -- delivered frames sit at different offsets")

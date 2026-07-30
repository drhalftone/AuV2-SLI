#!/usr/bin/env python3
"""
Is the frame wrong because our model is wrong, or because bytes are corrupted?

sli_frame_gen.v resets acc0 at every line start, so all 1024 rows MUST be
identical. They aren't. Build a per-column consensus (modal byte across all
rows) -- sparse corruption cannot outvote 1024 rows -- then ask:

  1. does the CONSENSUS row match the RTL formula exactly?   -> model OK?
  2. how many bytes deviate from consensus, and WHERE?       -> error rate
  3. do the errors cluster by byte lane (x mod 4)?           -> ft_data timing
     ...or by position within the USB transfer?              -> host/link
"""
import collections, math, sys
from ft_video_grab import FtDevice, FrameAssembler

COS_AW, COS_N, FRAC, ACC_W = 12, 4096, 12, 24
ACC_MASK = (1 << ACC_W) - 1
mcos = [int(255.0 * (0.5 + 0.5 * math.cos(2.0 * math.pi * i / COS_N)) + 0.5)
        for i in range(COS_N)]


def expected_row(width, frq, frm):
    inc1 = (1 << ACC_W) // (288 * ((width + 287) // 288))
    inc = inc1 if frq == 0 else (6 * inc1 if frq == 1 else 36 * inc1)
    return bytes(mcos[((((x * inc) & ACC_MASK) >> FRAC) + (frm << 9)) & (COS_N - 1)]
                 for x in range(width))


dev = FtDevice(pipe=0x82, stream_size=1 << 22)
asm = FrameAssembler()
frame = None
while frame is None:
    d = dev.read(1 << 22)
    if not d:
        continue
    for idx, px in asm.feed(d):
        frame, w, h = px, asm.width, asm.height
        break
dev.close()
px, w, h = frame, asm.width, asm.height

rows = [px[y * w:(y + 1) * w] for y in range(h)]

# ---- consensus row: modal value per column ------------------------------
consensus = bytearray(w)
for x in range(w):
    consensus[x] = collections.Counter(rows[y][x] for y in range(h)).most_common(1)[0][0]
consensus = bytes(consensus)

# ---- 1. does consensus match the RTL model? -----------------------------
hit = None
for q in range(3):
    for m in range(8):
        if expected_row(w, q, m) == consensus:
            hit = (q, m)
            break
    if hit:
        break
print("=" * 66)
if hit:
    print(f"CONSENSUS ROW == RTL model exactly, at frq={hit[0]} frm={hit[1]}")
    print("  -> the generator/model is correct; deviations are CORRUPTION.")
else:
    best, bq, bm = None, None, None
    for q in range(3):
        for m in range(8):
            e = expected_row(w, q, m)
            n = sum(1 for a, b in zip(e, consensus) if a != b)
            if best is None or n < best:
                best, bq, bm, be = n, q, m, e
    print(f"consensus does NOT match any (frq,frm); closest frq={bq} frm={bm}, "
          f"{best}/{w} columns differ")
    print("  expected:", list(be[:24]))
    print("  consensus:", list(consensus[:24]))
print("=" * 66)

# ---- 2. deviation statistics --------------------------------------------
bad_pos = [(y, x) for y in range(h) for x in range(w) if rows[y][x] != consensus[x]]
total = w * h
print(f"bytes deviating from consensus: {len(bad_pos)} / {total} "
      f"({100.0*len(bad_pos)/total:.4f}%)")
bad_rows = len({y for y, _ in bad_pos})
print(f"rows containing >=1 bad byte  : {bad_rows} / {h}")
if bad_pos:
    per_row = collections.Counter(y for y, _ in bad_pos)
    print(f"bad bytes per affected row    : min={min(per_row.values())} "
          f"max={max(per_row.values())} mean={len(bad_pos)/len(per_row):.1f}")

    # ---- 3. where do errors land? ---------------------------------------
    lane = collections.Counter(x % 4 for _, x in bad_pos)
    print(f"by byte lane (x mod 4)        : "
          f"{ {k: lane.get(k,0) for k in range(4)} }   <- flat=random, skewed=ft_data timing")

    col = collections.Counter(x for _, x in bad_pos)
    print(f"distinct columns affected     : {len(col)} / {w}")
    print(f"top columns                   : {col.most_common(8)}")

    # absolute byte offset within the frame -> USB/transfer alignment
    offs = [32 + y * w + x for y, x in bad_pos]
    for m in (512, 1024, 4096, 16384, 1 << 20):
        c = collections.Counter(o % m for o in offs)
        top, n = c.most_common(1)[0]
        print(f"  offset mod {m:>7}: {len(c)} distinct residues, "
              f"top={top} ({n} hits)")

    # error magnitude: is the wrong byte a near-neighbour or wild?
    mag = [abs(rows[y][x] - consensus[x]) for y, x in bad_pos[:5000]]
    print(f"error magnitude               : min={min(mag)} max={max(mag)} "
          f"mean={sum(mag)/len(mag):.1f}")
    print(f"sample bad bytes (y,x,got,exp): "
          f"{[(y, x, rows[y][x], consensus[x]) for y, x in bad_pos[:6]]}")

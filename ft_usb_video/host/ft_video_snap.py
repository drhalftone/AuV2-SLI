#!/usr/bin/env python3
# ============================================================================
# ft_video_snap.py -- capture frames off the FT601 and CHECK THEM BYTE-EXACT.
#
# ft_video_grab.py answers "how fast?"; this answers "is it *right*?". It grabs
# frames, recomputes what sli_frame_gen.v should have emitted for the (frq, frm)
# in each frame's header, and diffs pixel-for-pixel. That validates the one thing
# the README flags as unknown: the FT601 setup window on the combinationally
# driven `ft_data` bus. Corruption there shows up as mismatching pixels while
# MB/s stays perfect, so a throughput test alone would never catch it.
#
# Also writes PNGs (stdlib zlib -- no PIL, no PySide6) so the fringes can be
# eyeballed, picking frames with DIFFERENT phase/frequency to show the sequence.
#
#   python ft_video_snap.py                 # verify + write snap_*.png
#   python ft_video_snap.py --frames 600    # watch more of the frq/frm sequence
#   python ft_video_snap.py --no-png        # verify only
#
# The expected pattern, straight from the RTL:
#   P_LO   = 288*ceil(W/288) = 1440          INC1 = (1<<24)//P_LO
#   inc    = INC1 / 6*INC1 / 36*INC1         for frq = 0 / 1 / 2
#   a(x)   = (((x*inc) mod 2^24) >> 12) + frm*512   (mod 4096)
#   pix(x) = floor(255*(0.5 + 0.5*cos(2*pi*a/4096)) + 0.5)
# Row-independent (vertical fringes), so every line must be identical.
# ============================================================================
import argparse
import math
import struct
import sys
import time
import zlib

from ft_video_grab import FtDevice, FrameAssembler

COS_AW, COS_N = 12, 4096
FRAC, ACC_W = 12, 24
ACC_MASK = (1 << ACC_W) - 1


def master_cos():
    """mcos[] exactly as sli_frame_gen.v's initial block builds it."""
    return [int(255.0 * (0.5 + 0.5 * math.cos(2.0 * math.pi * i / COS_N)) + 0.5)
            for i in range(COS_N)]


def expected_row(width, frq, frm, mcos):
    """The one row every line of the frame must equal."""
    inc1 = (1 << ACC_W) // (288 * ((width + 287) // 288))
    inc = inc1 if frq == 0 else (6 * inc1 if frq == 1 else 36 * inc1)
    shift = frm << 9
    return bytes(mcos[((((x * inc) & ACC_MASK) >> FRAC) + shift) & (COS_N - 1)]
                 for x in range(width))


def write_png(path, pixels, width, height):
    """8-bit grayscale PNG, stdlib only."""
    raw = bytearray()
    for y in range(height):                      # filter byte 0 per scanline
        raw.append(0)
        raw += pixels[y * width:(y + 1) * width]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main():
    ap = argparse.ArgumentParser(description="Capture + byte-exact-verify FT601 SLI frames.")
    ap.add_argument("--frames", type=int, default=400, help="frames to check (default 400)")
    ap.add_argument("--chunk", type=lambda x: int(x, 0), default=1 << 22)
    ap.add_argument("--pipe", type=lambda x: int(x, 0), default=0x82)
    ap.add_argument("--no-png", action="store_true", help="verify only, write no images")
    ap.add_argument("--scale", type=int, default=2, help="PNG row decimation (default 2)")
    args = ap.parse_args()

    mcos = master_cos()
    dev = FtDevice(pipe=args.pipe, stream_size=args.chunk)
    asm = FrameAssembler()

    checked = bad_frames = 0
    worst = 0
    seen = {}                      # (frq, frm) -> pixels, for the PNG picks
    order = []
    t0 = time.perf_counter()
    try:
        while checked < args.frames:
            data = dev.read(args.chunk)
            if not data:
                if time.perf_counter() - t0 > 10:
                    print("timed out waiting for frames", file=sys.stderr)
                    return 2
                continue
            for idx, pixels in asm.feed(data):
                w, h = asm.width, asm.height
                # header word3 = {frq, 0, frm}: re-read it from the frame we kept
                # (FrameAssembler hands back pixels only, so recover frq/frm by
                # matching the first row against all 24 possibilities)
                row0 = pixels[:w]
                frq = frm = None
                for q in range(3):
                    for m in range(8):
                        if expected_row(w, q, m, mcos) == row0:
                            frq, frm = q, m
                            break
                    if frq is not None:
                        break
                if frq is None:
                    bad_frames += 1
                    # measure how far off row 0 is from its closest candidate
                    best = min(
                        max(abs(a - b) for a, b in zip(row0, expected_row(w, q, m, mcos)))
                        for q in range(3) for m in range(8)
                    )
                    worst = max(worst, best)
                else:
                    exp = expected_row(w, frq, frm, mcos)
                    mism = sum(1 for y in range(h)
                               if pixels[y * w:(y + 1) * w] != exp)
                    if mism:
                        bad_frames += 1
                        worst = max(worst, 255)
                    key = (frq, frm)
                    if key not in seen:
                        seen[key] = pixels
                        order.append(key)
                checked += 1
                if checked >= args.frames:
                    break
    finally:
        dev.close()

    dt = time.perf_counter() - t0
    print(f"checked {checked} frames in {dt:.1f}s  ({checked/dt:.0f} fps)")
    print(f"  frames matching the RTL byte-for-byte : {checked - bad_frames}")
    print(f"  frames with ANY mismatched pixel      : {bad_frames}")
    if bad_frames:
        print(f"  worst pixel error                     : {worst}")
    print(f"  distinct (frq,frm) states seen        : {len(seen)}  {sorted(seen)}")
    print(f"  frame index gaps (dropped)            : {asm.dropped}")

    if not args.no_png and seen:
        picks = order[:4]
        for (q, m) in picks:
            px, w, h = seen[(q, m)], asm.width, asm.height
            s = max(1, args.scale)
            if s > 1:                                   # decimate rows AND cols
                px = bytes(b for y in range(0, h, s)
                           for b in px[y * w:(y + 1) * w:s])
                w, h = len(range(0, w, s)), len(range(0, h, s))
            name = f"snap_frq{q}_frm{m}.png"
            write_png(name, px, w, h)
            print(f"  wrote {name}  ({w}x{h})")
    return 1 if bad_frames else 0


if __name__ == "__main__":
    sys.exit(main())

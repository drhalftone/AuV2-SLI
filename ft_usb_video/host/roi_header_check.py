"""Verify the ROI mean in the frame header against the host's own mean.

WHAT THIS PROVES, AND WHY IT IS NOT THE SAME AS "THE NUMBER LOOKS RIGHT". The fabric
averages a 16x16 patch while the pixels stream past; this recomputes that average from
the pixels that arrived in THE SAME PACKET. Two independent routes over one set of
pixels, so:

  * delta == 0 on every frame  -> the ROI arithmetic AND its per-slot pairing are right
  * a constant non-zero delta  -> the two are averaging DIFFERENT PIXELS (placement)
  * a delta that comes and goes -> the pairing is slipping, which is the failure the
                                   per-slot roi_meta[] was added to remove

Both hardware means truncate (sum >> 8) and so does the host, so an exact match is the
expectation -- not a match to within rounding.

    python roi_header_check.py [--frames N] [--roi-col8 C] [--roi-row8 R]
"""
import argparse
import ctypes
import struct
import sys
import time

import numpy as np
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

import campack

IN_PIPE = 0x82
MAGIC = 0x30494C53
FBYTES = campack.FBYTES_PACK10
HDR = 32
CH = 1 << 22


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--roi-col8", type=int, default=campack.ROI_COL8_DEFAULT)
    ap.add_argument("--roi-row8", type=int, default=campack.ROI_ROW8_DEFAULT)
    ap.add_argument("--seconds", type=float, default=20.0, help="give up after this")
    a = ap.parse_args()

    dev = None
    for _ in range(10):
        dev = ftd3xx.create(0, FT_OPEN_BY_INDEX)
        if dev is not None:
            break
        time.sleep(0.3)
    if dev is None:
        sys.exit("no D3XX device -- is the Ft+ connected?")
    for fn in ("abortPipe", "flushPipe"):
        try:
            getattr(dev, fn)(IN_PIPE)
        except Exception:
            pass
    try:
        dev.setPipeTimeout(IN_PIPE, 1000)
    except Exception:
        pass
    try:
        dev.setStreamPipe(IN_PIPE, CH)
        streamed = True
    except Exception:
        streamed = False

    buf = ctypes.create_string_buffer(CH)
    acc = bytearray()
    magic = struct.pack("<I", MAGIC)

    n = agree = invalid = badnpx = 0
    deltas = []
    phases = {}
    t0 = time.time()
    print("ROI 16x16 at col %d row %d  (col8=%d row8=%d)"
          % (a.roi_col8 * 8, a.roi_row8 * 8, a.roi_col8, a.roi_row8))
    print("%-6s %-8s %6s %6s %7s %5s %5s %5s"
          % ("frame", "idx", "FPGA", "host", "delta", "npx", "blk", "phase"))

    try:
        while n < a.frames and (time.time() - t0) < a.seconds:
            try:
                got = dev.readPipe(IN_PIPE, buf, CH)
            except Exception:
                continue
            if not got:
                continue
            acc += bytes(memoryview(buf)[:got])

            # Walk FORWARD here, unlike the viewer: this is checking every frame it
            # can, not painting the newest one.
            pos = acc.find(magic)
            while pos >= 0 and pos + HDR + FBYTES <= len(acc) and n < a.frames:
                h = campack.parse_header(acc, pos)
                if h is None or h["fbytes"] != FBYTES:
                    pos = acc.find(magic, pos + 4)
                    continue
                img = campack.to_frame(
                    bytes(acc[pos + HDR: pos + HDR + FBYTES]), h["fmt"])
                hostm = campack.roi_mean_host(img, a.roi_col8, a.roi_row8)
                n += 1
                if not h["roi_valid"]:
                    invalid += 1
                    d = None
                else:
                    d = h["roi_mean"] - hostm
                    deltas.append(d)
                    if d == 0:
                        agree += 1
                    if h["roi_npx"] != 256:
                        badnpx += 1
                phases[h["roi_phase"]] = phases.get(h["roi_phase"], 0) + 1
                if n <= 12 or d not in (0, None):
                    print("%-6d %-8d %6d %6d %7s %5d %5d %5d"
                          % (n, h["frame_idx"], h["roi_mean"], hostm,
                             "--" if d is None else "%+d" % d,
                             h["roi_npx"], h["roi_blk"], h["roi_phase"]))
                pos = acc.find(magic, pos + HDR + FBYTES)
            if pos < 0:
                acc = bytearray()
            elif pos > 0:
                del acc[:pos]
    finally:
        try:
            if streamed:
                dev.clearStreamPipe(IN_PIPE)
        except Exception:
            pass
        dev.close()

    print("\n---- %d frames ----" % n)
    if n == 0:
        sys.exit("NO FRAMES. The camera is not delivering over the Ft+.")
    print("roi_valid=0        : %d" % invalid)
    print("npx != 256         : %d" % badnpx)
    print("phases seen        : %s" % sorted(phases))
    if deltas:
        u = sorted(set(deltas))
        print("FPGA-host delta    : %s" % (u if len(u) <= 6 else
                                           "%d distinct, %d..%d" % (len(u), u[0], u[-1])))
    print("EXACT AGREEMENT    : %d / %d" % (agree, n - invalid))
    ok = (agree == n - invalid) and n - invalid > 0 and badnpx == 0
    print("\n%s" % ("PASS -- the header ROI mean describes this packet's own pixels."
                   if ok else
                   "FAIL -- see above. A constant delta means the host and fabric are "
                   "averaging different pixels (check --roi-col8/--roi-row8 against "
                   "uart_ctrl.v); a varying delta means the pairing is slipping."))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

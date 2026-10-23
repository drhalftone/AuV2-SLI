"""Hardware check for the FRAME LABEL (format-5 header, opcode 8).

What it proves, on the live board:

  1. seq advances by exactly one per captured frame (no frame silently lost or
     doubled between the projector's vsync and the header);
  2. after an epoch reset, the frame labelled seq = 0 shows pattern 0, and the
     pattern of every later frame is the one the FPGA's sequence puts there --
     with the free-running sequence and the identity phase order that is
     pattern = seq mod 32 ({frq[1:0], phase[2:0]}, frq 3 being the flash block);
  3. the label agrees with an independent route: the transmitted top-left pixel
     (header tlp), which in SLI mode carries {frq, phase} written by pixel_pipe.
     That route is latched at a different instant, so it may differ from the
     label by a CONSTANT number of frames; the check reports that offset and
     fails only if it is not constant.

Setup it applies (and leaves applied): free-running sequence (reg 0x09 bit 0),
SLI patterns on all three channels (reg 0x13 = 0xEE), genlock on with the given
delay (opcode 7), label on (opcode 8 bit 27), then one epoch reset (bit 26).

    python host/frame_label_check.py [--delay-us 8800.65] [--seconds 2]
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "ft_usb_video", "host"))
import campack                      # noqa: E402
from ftlink import FtLink, MAGIC, RMAGIC, HDR   # noqa: E402


def read_frames(link, seconds):
    """Pull the IN pipe for `seconds`, return parsed frame headers in order."""
    acc = bytearray()
    out = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            n = link.d.readPipe(0x82, link._buf, link._chunk)
        except Exception:
            n = 0
        if n:
            acc += link._buf.raw[:n]
        i = 0
        while len(acc) - i >= campack.HDR_EXT:
            w0 = struct.unpack_from("<I", acc, i)[0]
            if w0 == MAGIC:
                h = campack.parse_header(acc, i)
                if h is None:
                    i += 4
                    continue
                if len(acc) - i < h["hdr_len"] + h["fbytes"]:
                    break
                out.append(h)
                i += h["hdr_len"] + h["fbytes"]
            elif w0 == RMAGIC:
                h = struct.unpack_from("<8I", acc, i)
                if len(acc) - i < HDR + h[4]:
                    break
                i += HDR + h[4]
            else:
                j = acc.find(struct.pack("<I", MAGIC), i + 4)
                i = j if j >= 0 else max(i, len(acc) - campack.HDR_EXT)
                if j < 0:
                    break
        del acc[:i]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay-us", type=float, default=8800.65)
    ap.add_argument("--seconds", type=float, default=2.0)
    a = ap.parse_args()

    link = FtLink()
    try:
        link.drain()
        link.write_reg(0x09, 0x01)                 # free-running fringe sequence (bit2 = epoch)
        link.write_reg(0x13, 0xEE)                 # USB switches, SLI on, RGB, rows
        dly = int(round(a.delay_us * 100))         # 10 ns ticks
        link.send_word((7 << 28) | (1 << 27) | (dly & 0xFFFFFF))
        link.frame_label(enable=True)
        time.sleep(0.3)
        link.frame_label(enable=True, epoch=True)
        time.sleep(0.2)
        hs = read_frames(link, a.seconds)
    finally:
        link.close()

    lab = [h for h in hs if h.get("fmt") == campack.FMT_LABEL]
    print("frames read %d, format-5 %d" % (len(hs), len(lab)))
    if not lab:
        print("FAIL: no format-5 headers -- is the new bitstream loaded?")
        return 1
    print("  idx      seq  pat frq ph  ok ep v   tlp")
    for h in lab[:40]:
        print("%5d %8d %4d %3d %2d %3d %2d %d  0x%02X" % (
            h["frame_idx"], h["seq"], h["pattern"], h["frq"], h["phase"],
            h["pat_ok"], h["epoch_valid"], h["lab_valid"], h["tlp"]))

    # A step of 0 over one frame_idx is a REPEATED FRAME: the DDR ring sent the same
    # slot twice (measured 2026-10-23: such pairs carry byte-identical pixels from the
    # same slot). The label is right to repeat, so it is reported, not failed.
    bad = 0
    rep = 0
    good = [h for h in lab if h["lab_valid"] and h["epoch_valid"]]
    for p, q in zip(good, good[1:]):
        dfi = (q["frame_idx"] - p["frame_idx"]) & 0xFFFFFFFF
        dsq = (q["seq"] - p["seq"]) & 0xFFFFFFFF
        if dsq == 0 and dfi == 1 and q["slot"] == p["slot"]:
            rep += 1
        elif dsq != dfi:
            bad += 1
            print("seq step %d over %d frames at frame_idx %d" % (dsq, dfi, q["frame_idx"]))
    print("repeated frames (same slot sent twice): %d" % rep)
    mism = [h for h in good if h["pat_ok"] and h["pattern"] != (h["seq"] % 32)]
    print("label pattern != seq mod 32: %d of %d" % (len(mism), len(good)))
    offs = sorted(set(((h["tlp"] & 0x1F) - h["pattern"]) % 32 for h in good))
    print("tlp - pattern offsets seen (mod 32): %s" % offs)
    ok = bad == 0 and not mism and len(offs) == 1 and good
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

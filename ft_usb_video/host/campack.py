"""Frame geometry, header parsing and 10-bit unpacking, shared by the host tools.

WHY PACKED. The sensor is 10-bit. Shipping it in 16-bit words wasted 37.5% of
every byte moved, through DDR and over USB alike, and at 120 Hz that padding is
the difference between fitting and not: 16 bpp needs 314.6 MB/s where the link
delivers about 232, while packed needs 196.6 MB/s. Packing is LOSSLESS.

THE LAYOUT is the conventional packed-10 tiling, four pixels in five bytes:

    byte 0..3  the high 8 bits of p0..p3
    byte 4     the low 2 bits of each, LEAST-SIGNIFICANT PIXEL FIRST
               bits [1:0]=p0  [3:2]=p1  [5:4]=p2  [7:6]=p3

It tiles exactly: 4 px / 5 B, so the FPGA's 8-kernel group (64 px) is 80 bytes
= five 128-bit DDR words with nothing left over.

CHECK THE FORMAT FIELD, DO NOT ASSUME. Header word 6 carries it: 2 = 10-bit
right-aligned in 16-bit words (the old layout), 3 = packed. Reading packed bytes
as uint16 does not fail loudly -- it produces a plausible-looking wrong image,
which is the kind of mistake this project keeps having to measure its way out of.
"""
import struct

import numpy as np

NCOL, NROW = 1280, 1024
NPIX = NCOL * NROW
HDR = 32
MAGIC = 0x30494C53

FMT_U16 = 2                  # 10-bit right-aligned in 16-bit little-endian
FMT_PACK10 = 3               # dense packed 10-bit, 4 px in 5 bytes

FBYTES_U16 = NPIX * 2        # 2,621,440
FBYTES_PACK10 = NPIX * 10 // 8   # 1,638,400
FBYTES = FBYTES_PACK10       # what the current bitstream emits


def parse_header(buf, off):
    """Return the 8 header words as a dict, or None if it is not a valid header."""
    h = struct.unpack_from("<8I", buf, off)
    if h[0] != MAGIC or h[7] != (~MAGIC & 0xFFFFFFFF):
        return None
    return {
        "frame_idx": h[1],
        "nrow": h[2] >> 16,
        "ncol": h[2] & 0xFFFF,
        "slot": h[3] & 0x3F,
        # spare bits carry ldrop: frames the writer had to pad because kernels
        # went missing. Constant across a run == not one kernel lost.
        "ldrop": (h[3] >> 14) & 0xFFFF,
        "fbytes": h[4],
        "fmt": h[6],
    }


def unpack10_flat(raw):
    """Packed-10 bytes -> flat uint16 pixels. Vectorised; no Python bit-crawl."""
    b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 5)
    hi = b[:, :4].astype(np.uint16) << 2
    lo = b[:, 4]
    out = np.empty((b.shape[0], 4), dtype=np.uint16)
    out[:, 0] = hi[:, 0] | (lo & 3)
    out[:, 1] = hi[:, 1] | ((lo >> 2) & 3)
    out[:, 2] = hi[:, 2] | ((lo >> 4) & 3)
    out[:, 3] = hi[:, 3] | ((lo >> 6) & 3)
    return out.reshape(-1)


def unpack10(raw):
    """Packed-10 bytes -> (NROW, NCOL) uint16."""
    return unpack10_flat(raw).reshape(NROW, NCOL)


def to_frame(raw, fmt):
    """Decode a payload of either format into (NROW, NCOL) uint16."""
    if fmt == FMT_PACK10:
        return unpack10(raw)
    if fmt == FMT_U16:
        return np.frombuffer(raw, dtype="<u2").reshape(NROW, NCOL)
    raise ValueError("unknown payload format %r -- refusing to guess" % (fmt,))


def iter_frames(data, want=None):
    """Yield (header, frame) for each complete frame found in `data`.

    Frames are located by magic and validated against the trailing ~MAGIC, so a
    resynchronising parser cannot mistake payload for a header.
    """
    mag = struct.pack("<I", MAGIC)
    i = 0
    n = 0
    while want is None or n < want:
        i = data.find(mag, i)
        if i < 0 or i + HDR > len(data):
            return
        h = parse_header(data, i)
        if h is None:
            i += 4
            continue
        if i + HDR + h["fbytes"] > len(data):
            return
        raw = bytes(data[i + HDR:i + HDR + h["fbytes"]])
        yield h, to_frame(raw, h["fmt"])
        i += HDR + h["fbytes"]
        n += 1

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
        # TOP-LEFT PIXEL of the HDMI frame this capture was triggered by, in the
        # top byte of word 5 (the rest of that word is FBYTES/4, which the host
        # can derive anyway). In pass-through mode the PC owns the pattern
        # sequence and a GPU may repeat or delay a frame, so this is what lets a
        # captured sequence be matched to the displayed one rather than assumed.
        # Reads 0 when nothing is driving the HDMI input, and on the standalone
        # camera build where the TLP wire does not exist.
        "tlp": (h[5] >> 24) & 0xFF,
        # ROI MEAN OF THIS FRAME'S OWN PIXELS, in the low 24 bits of word 5 (which
        # used to hold FBYTES/4 -- redundant, since FBYTES is word 4). The fabric
        # stores it per ring slot and reads it back with the slot being streamed, so
        # it describes the image in THIS packet rather than whichever frame the
        # camera happened to finish most recently.
        #
        # roi_valid == 0 means no measurement was paired with this frame -- reported
        # rather than faked, because a fabricated 0 plots as a real dark reading.
        # roi_npx MUST be 256; anything else means the ROI is off the sensor and the
        # mean beside it is meaningless however plausible it looks.
        "roi_mean": (h[5] >> 14) & 0x3FF,
        "roi_npx": (h[5] >> 5) & 0x1FF,
        "roi_blk": (h[5] >> 4) & 1,
        "roi_phase": (h[5] >> 1) & 7,
        "roi_valid": h[5] & 1,
    }


# The ROI the fabric averages: 16x16, placed by roi_col8/roi_row8 (each x8).
#
# THESE MUST TRACK uart_ctrl.v's RESET VALUES, NOT AN IDEA OF WHERE THE CENTRE IS.
# The fabric resets them to 80/64, which ANCHORS the patch at the centre pixel --
# columns 640..655, rows 512..527 -- rather than centring it on that pixel (which
# would be 79/63). The 8-pixel difference is optically nothing on a 1280-wide
# sensor, but it is everything to the cross-check: computing the host mean over a
# patch shifted by 8 px yields a plausible, permanently non-zero delta that reads
# as a fabric arithmetic bug. The host validates the fabric, so the host matches it.
#
# If the ROI is moved at runtime (UART regs 0x18/0x19), pass the new values through
# -- cam_live.py takes --roi-col8/--roi-row8 for exactly that.
ROI_N = 16
ROI_COL8_DEFAULT = 80        # uart_ctrl.v: roi_col8 <= 8'd80  -> first column 640
ROI_ROW8_DEFAULT = 64        # uart_ctrl.v: roi_row8 <= 8'd64  -> first row    512


def roi_mean_host(img, col8=ROI_COL8_DEFAULT, row8=ROI_ROW8_DEFAULT):
    """Mean of the same 16x16 patch the fabric averages, computed on the host.

    THE POINT OF THIS IS DISAGREEMENT. The fabric's mean and this one are computed
    from the same pixels by two independent routes, so if they differ the ROI logic
    is wrong -- and that is a question no amount of staring at a single number can
    settle. Truncates like the hardware (sum >> 8), so an exact match is expected,
    not a match to within rounding.
    """
    r0, c0 = row8 * 8, col8 * 8
    patch = img[r0:r0 + ROI_N, c0:c0 + ROI_N]
    if patch.shape != (ROI_N, ROI_N):
        return None
    return int(patch.astype(np.uint32).sum()) >> 8


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

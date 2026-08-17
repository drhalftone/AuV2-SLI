"""Round-trip the packed-10 layout against a model of the RTL packer.

Worth having because the failure mode is silent: a swapped low-bit field or a
reversed byte order still unpacks to a full-looking image with plausible
statistics, and would be blamed on the sensor. The model below is transcribed
directly from cam_frame_ft.v's kpk wires, so if the two ever disagree, one of
them changed.

Run: python test_campack.py
"""
import numpy as np

import campack


def rtl_kernel_bytes(p):
    """Model of kpk in cam_frame_ft.v for one 8-pixel kernel.

        pa0..pa3 = kp0..kp3 [9:2]
        pa4      = {kp3[1:0], kp2[1:0], kp1[1:0], kp0[1:0]}   (kp0 in the LOW bits)
        likewise pb* for kp4..kp7
        kpk = {pb4,pb3,pb2,pb1,pb0, pa4,pa3,pa2,pa1,pa0}      (pa0 = byte 0)
    """
    assert len(p) == 8
    out = []
    for q in (p[0:4], p[4:8]):
        out += [(x >> 2) & 0xFF for x in q]
        out.append(((q[3] & 3) << 6) | ((q[2] & 3) << 4)
                   | ((q[1] & 3) << 2) | (q[0] & 3))
    return bytes(out)


def main():
    rng = np.random.default_rng(12345)
    npix = 8 * 4096                       # 4096 kernels, several 5-word groups
    pix = rng.integers(0, 1024, size=npix, dtype=np.uint16)

    # The packer is BYTE-ALIGNED: 80 bits is 10 whole bytes, so pk_n is always a
    # multiple of 8 and the 128-bit words are just a regrouping of one byte
    # stream. That is why no bit-crawl is needed on either side.
    stream = b"".join(rtl_kernel_bytes(pix[i:i + 8]) for i in range(0, npix, 8))
    assert len(stream) == npix * 10 // 8, len(stream)
    assert len(stream) % 16 == 0, "must tile into whole 128-bit DDR words"

    back = campack.unpack10_flat(stream)
    if not np.array_equal(back, pix):
        bad = int(np.argmax(back != pix))
        raise SystemExit("MISMATCH at pixel %d: sent %d got %d"
                         % (bad, pix[bad], back[bad]))

    # Endpoints matter most -- an off-by-one in the tiling shows up there first.
    assert back[0] == pix[0] and back[-1] == pix[-1]
    # And full-scale values, which is where a dropped low bit hides.
    edge = np.array([0, 1, 2, 3, 511, 512, 1022, 1023], dtype=np.uint16)
    assert np.array_equal(campack.unpack10_flat(rtl_kernel_bytes(edge)), edge)

    print("packed-10 round-trip OK: %d pixels, %d bytes, %d DDR words"
          % (npix, len(stream), len(stream) // 16))
    print("full 10-bit range preserved (0..1023), byte-aligned tiling confirmed")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Dump and decode the display's EDID as captured by the FPGA.

    python host/dump_edid.py [COM6]

The FPGA reads the EDID off the HDMI-OUT DDC into a RAM inside edid_merge.
`rdtbl` target 0x03 streams those 256 bytes back over the USB control link:

    host -> A5 72 03 8B
    FPGA -> 03 D[0..255] CK        (03 + sum(D) + CK) == 0 mod 256

Replies share the UART with the ASCII status telemetry (a status line pauses
mid-line while a reply goes out, then resumes), so -- exactly as test_silicon.py
does -- we frame-scan the RX buffer for a window whose checksum closes rather
than assuming the reply starts at byte 0.
"""
import sys, time, serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SYNC, OP_LR, TGT_EDID, NBYTES = 0xA5, 0x72, 0x03, 256


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def read_edid(ser, window=2.0):
    need = NBYTES + 2                      # target echo + data + checksum
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_LR, TGT_EDID, ck(OP_LR + TGT_EDID)]))
    buf = bytearray()
    deadline = time.time() + window
    while time.time() < deadline:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - need + 1):
            if buf[i] == TGT_EDID and (sum(buf[i:i + need]) & 0xFF) == 0:
                return bytes(buf[i + 1:i + 1 + NBYTES])
    return None


def mfg_id(b):
    v = (b[8] << 8) | b[9]                 # 3 x 5-bit letters, big-endian
    return "".join(chr(ord("A") - 1 + ((v >> s) & 0x1F)) for s in (10, 5, 0))


def descriptors(e):
    for off in (54, 72, 90, 108):
        yield off, e[off:off + 18]


def detailed_timing(d):
    """Decode an 18-byte detailed timing descriptor -> dict, or None."""
    pixclk = ((d[1] << 8) | d[0]) * 10_000          # 10 kHz units -> Hz
    if pixclk == 0:
        return None
    hact = d[2] | ((d[4] >> 4) << 8)
    hbl  = d[3] | ((d[4] & 0x0F) << 8)
    vact = d[5] | ((d[7] >> 4) << 8)
    vbl  = d[6] | ((d[7] & 0x0F) << 8)
    htot, vtot = hact + hbl, vact + vbl
    if htot == 0 or vtot == 0:
        return None
    return {
        "h": hact, "v": vact, "htotal": htot, "vtotal": vtot,
        "pixclk": pixclk, "refresh": pixclk / (htot * vtot),
        "interlaced": bool(d[17] & 0x80),
    }


def main():
    with serial.Serial(PORT, 115200, timeout=0.2) as ser:
        e = read_edid(ser)

    if e is None:
        print(f"No EDID reply on {PORT}.")
        print("  - Is a display connected to the FPGA's HDMI OUTPUT?")
        print("  - Is the board running a bitstream with rdtbl TGT_EDID (0x03)?")
        return 1

    print(f"=== Raw EDID ({len(e)} bytes from {PORT}) ===")
    for row in range(0, len(e), 16):
        print(f"  {row:02X}: " + " ".join(f"{b:02X}" for b in e[row:row + 16]))

    hdr_ok = e[0:8] == bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
    sum_ok = (sum(e[0:128]) & 0xFF) == 0
    print("\n=== Validity ===")
    print(f"  header      : {'OK' if hdr_ok else 'BAD'}  ({e[0:8].hex(' ')})")
    print(f"  block-0 sum : {'OK' if sum_ok else 'BAD'}  (sum mod 256 = {sum(e[0:128]) & 0xFF})")
    if not (hdr_ok and sum_ok):
        print("\n  EDID did not validate -- not decoding further.")
        return 1

    print("\n=== Display ===")
    print(f"  manufacturer : {mfg_id(e)}")
    print(f"  product code : 0x{(e[11] << 8) | e[10]:04X}")
    print(f"  serial       : {int.from_bytes(e[12:16], 'little')}")
    if e[17]:
        print(f"  manufactured : week {e[16]}, {1990 + e[17]}")
    print(f"  EDID version : {e[18]}.{e[19]}")
    print(f"  extensions   : {e[126]}")

    for off, d in descriptors(e):
        if d[0:3] == b"\x00\x00\x00" and d[3] == 0xFC:
            name = d[5:18].split(b"\x0a")[0].decode("ascii", "replace").strip()
            print(f"  model name   : {name}")

    print("\n=== Detailed timings ===")
    first = True
    for off, d in descriptors(e):
        t = detailed_timing(d)
        if not t:
            continue
        tag = "PREFERRED" if first else "alternate"
        first = False
        print(f"  [{tag}] {t['h']}x{t['v']}{'i' if t['interlaced'] else ''} "
              f"@ {t['refresh']:.2f} Hz")
        print(f"      pixel clock {t['pixclk'] / 1e6:.3f} MHz   "
              f"total {t['htotal']}x{t['vtotal']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

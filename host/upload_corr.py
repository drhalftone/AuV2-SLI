#!/usr/bin/env python3
"""Upload the radiometric transfer LUT (`corr`, USB target 0x02) and prove it is live.

    python host/upload_corr.py [COM6] [identity|const:NN|invert|gamma:G]
    python host/upload_corr.py COM6 --selftest

pattern_gen presents its raw cosine value to a 256-entry transfer LUT and uses the
result for every fringe pixel (`out = corr[cos]`), so `corr` is the radiometric /
gamma linearisation curve. It powers up as identity (no correction).

FLASH frames bypass the LUT inside pattern_gen and stay true 0x00/0xFF, so a few
telemetry samples will always sit at the extremes regardless of the curve.

--selftest proves the LUT reaches the pixels: upload a CONSTANT curve and watch the
pipe-OUTPUT top-left pixel (`O=` in the telemetry) collapse onto that constant, while
the pipe-INPUT sample (`P=`) is unaffected. Then it restores identity.
"""
import sys, time, serial

SYNC, OP_W, OP_R, OP_L, OP_LR = 0xA5, 0x57, 0x52, 0x5B, 0x72
TGT_CORR, N = 0x02, 256
REG_SLICTRL = 0x13          # {7:sw_en, 6:mode_en, 5:mode_val, 3:R,2:G,1:B,0:orient}


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def write_reg(ser, addr, val):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.05)


def read_reg(ser, addr, window=0.6):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    buf = bytearray(); dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def set_sli_pattern(ser, on):
    """Force pattern_gen on/off over USB (0x13 bit6 mode_en, bit5 mode_val).

    Without this the camera 'mode' GPIO (C1_in[1]) decides, and it is pulled LOW in
    the XDC -- so with no camera board attached the fringe generator is OFF and the
    vga colour bars pass straight through. The corr LUT only shapes FRINGE pixels, so
    it does nothing at all until the pattern generator is actually running.
    """
    cur = read_reg(ser, REG_SLICTRL) or 0
    val = (cur | 0x40 | (0x20 if on else 0)) if on else (cur & ~0x60) & 0xFF
    write_reg(ser, REG_SLICTRL, val & 0xFF)
    return read_reg(ser, REG_SLICTRL)


def curve(spec):
    if spec == "identity":
        return bytes(range(256))
    if spec == "invert":
        return bytes(255 - i for i in range(256))
    if spec.startswith("const:"):
        v = int(spec.split(":", 1)[1], 0) & 0xFF
        return bytes([v] * 256)
    if spec.startswith("gamma:"):
        g = float(spec.split(":", 1)[1])
        return bytes(min(255, round(255 * ((i / 255) ** g))) for i in range(256))
    raise SystemExit(f"unknown curve '{spec}' (identity|const:NN|invert|gamma:G)")


def upload(ser, data):
    assert len(data) == N
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_L, TGT_CORR]) + data + bytes([ck(TGT_CORR + sum(data))]))
    time.sleep(0.15)


def readback(ser, window=2.0):
    need = N + 2
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_LR, TGT_CORR, ck(OP_LR + TGT_CORR)]))
    buf = bytearray()
    dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - need + 1):
            if buf[i] == TGT_CORR and (sum(buf[i:i + need]) & 0xFF) == 0:
                return bytes(buf[i + 1:i + 1 + N])
    return None


def sample_pixels(ser, secs=4.0):
    """Collect the P= (pipe input) and O= (pipe output) top-left samples."""
    ser.reset_input_buffer()
    time.sleep(secs)
    raw = ser.read(ser.in_waiting or 1).decode("latin1", "replace")
    P, O = [], []
    for line in raw.replace("\r", "").split("\n"):
        for f in line.split():
            if f.startswith("P="):
                try: P.append(int(f[2:], 16))
                except ValueError: pass
            elif f.startswith("O="):
                try: O.append(int(f[2:], 16))
                except ValueError: pass
    return P, O


def hist(vals):
    if not vals:
        return "(no samples)"
    from collections import Counter
    c = Counter(vals)
    return "  ".join(f"0x{v:02X}x{n}" for v, n in sorted(c.items(), key=lambda kv: -kv[1])[:6])


def selftest(ser):
    print("=== Is the corr LUT live in the pixel datapath? ===")
    print("Uploading a CONSTANT curve: every fringe pixel must become that constant.")
    print("(FLASH frames bypass the LUT by design and stay at 0x00/0xFF.)\n")

    ok = True

    # The LUT only shapes FRINGE pixels. With no camera board the 'mode' GPIO is pulled
    # low, pattern_gen is off, and the vga colour bars pass through untouched -- the LUT
    # would look dead even when wired correctly. Force the pattern on first.
    sc = set_sli_pattern(ser, True)
    print(f"  SLI pattern forced ON over USB (0x13 = 0x{sc:02X})\n" if sc is not None
          else "  WARNING: could not read back 0x13\n")

    for const in (0x40, 0xC0):
        upload(ser, curve(f"const:{const}"))
        rb = readback(ser)
        if rb is None or set(rb) != {const}:
            print(f"  const 0x{const:02X}: table readback FAILED"); ok = False; continue
        P, O = sample_pixels(ser)
        hits = sum(1 for v in O if v == const)
        print(f"  corr = 0x{const:02X} everywhere")
        print(f"    pipe INPUT  P= : {hist(P)}")
        print(f"    pipe OUTPUT O= : {hist(O)}")
        print(f"    -> {hits}/{len(O)} output samples == 0x{const:02X}")
        if hits == 0:
            print("    *** LUT is NOT reaching the pixels ***"); ok = False
        print()

    # ---- the LUT must NOT touch passthrough video ----------------------------
    # The correction curve is a property of the INTERNALLY generated SLI patterns.
    # Host video passed through the FPGA must reach the projector untouched. In
    # pattern_gen this is structural (show = enable & ...; out = show ? pat_out : r_d,
    # so with enable=0 the raw pixels bypass the LUT entirely) -- assert it anyway.
    print("Checking the LUT does NOT affect PASSTHROUGH video...")
    set_sli_pattern(ser, False)             # enable = 0 -> passthrough
    _, base = sample_pixels(ser, 3.0)
    upload(ser, curve("const:80"))          # a curve that would be glaring if applied
    _, after = sample_pixels(ser, 3.0)
    print(f"    passthrough O= before : {hist(base)}")
    print(f"    passthrough O= after  : {hist(after)}   (corr = 0x80 everywhere)")
    if after and 0x80 in set(after) and set(after) == {0x80}:
        print("    *** LUT LEAKED into passthrough -- host video is being altered ***")
        ok = False
    elif set(base) == set(after):
        print("    -> unchanged. Passthrough video is untouched by the LUT.\n")
    else:
        print("    -> output moved, but not to the curve; passthrough not LUT-driven.\n")

    print("Restoring identity (no correction) and releasing the pattern override.")
    upload(ser, curve("identity"))
    rb = readback(ser)
    if rb != bytes(range(256)):
        print("  *** identity restore FAILED ***"); ok = False
    else:
        print("  identity restored, verified by readback.")
    set_sli_pattern(ser, False)
    print("  SLI pattern override released (back to the camera 'mode' GPIO).")
    return 0 if ok else 1


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"
    arg = sys.argv[2] if len(sys.argv) > 2 else "--selftest"
    with serial.Serial(port, 115200, timeout=0.2) as ser:
        if arg == "--selftest":
            return selftest(ser)
        data = curve(arg)
        upload(ser, data)
        rb = readback(ser)
        if rb is None:
            print("no readback reply"); return 1
        if rb != data:
            print(f"readback MISMATCH ({sum(a != b for a, b in zip(rb, data))} bytes differ)")
            return 1
        print(f"uploaded '{arg}' to corr (256 B) — verified by readback.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

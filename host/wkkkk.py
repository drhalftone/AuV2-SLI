#!/usr/bin/env python3
"""Pick a projector mode from its own EDID and run the WHITE,K,K,K,K sequence on it.

    python host/wkkkk.py                  # show what the projector supports, and the state
    python host/wkkkk.py --on             # start flashing at whatever mode is selected
    python host/wkkkk.py --mode 0 --on    # force 800x600@120 and flash
    python host/wkkkk.py --auto --on      # let the EDID pick the mode, and flash
    python host/wkkkk.py --off            # stop flashing, restore the test pattern

HOW THE MODE GETS CHOSEN. i2c_master_edid reads the projector's EDID off the HDMI-OUT
DDC; mode_select parses it into a 14-bit mask of which curated modes the display
actually advertises (regs 0x29/0x2A) and picks the best one -- highest refresh first,
then highest pixel count. MODEFORCE (0x14) overrides that pick. The chosen index
drives BOTH the DRP pixel-clock generator and the timing geometry, so writing 0x14
retunes the MMCM and the video mode together.

IT REFUSES TO FORCE AN UNSUPPORTED MODE. The FPGA will happily generate a mode the
projector never advertised, and the result is a black screen that looks exactly like
a broken bitstream. The supported mask is right there; checking it costs nothing and
turns an afternoon of debugging into an error message. --anyway overrides, for the
case where you know the EDID is lying.
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
REG_MODEFORCE, REG_ROICTL, REG_IMPRGB, REG_IMPLVL = 0x14, 0x17, 0x1F, 0x12
COLOURS = {"white": 0x07, "red": 0x04, "green": 0x02, "blue": 0x01,
           "yellow": 0x06, "cyan": 0x03, "magenta": 0x05}

# mode_table.vh order -- index IS the wire value written to MODEFORCE
TABLE = ["800x600@120", "640x480@120", "1024x768@75", "800x600@75", "640x480@75",
         "1024x768@70", "800x600@72", "640x480@72", "1280x720@60", "1280x800@60",
         "1024x768@60", "800x600@60", "640x480@60", "1280x1024@60"]


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.05)


def rd(ser, addr, window=0.6):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    buf = bytearray()
    dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def state(ser):
    r = {a: rd(ser, a) for a in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25,
                                 0x26, 0x27, 0x28, 0x29, 0x2A, 0x14, 0x17, 0x1F, 0x12)}
    if r[0x20] is None:
        return None
    return dict(
        valid=(r[0x20] >> 7) & 1, edid_ok=(r[0x20] >> 6) & 1, idx=r[0x20] & 0x0F,
        refr=r[0x21], hact=r[0x22] | (r[0x23] << 8), vact=r[0x24] | (r[0x25] << 8),
        pclk=r[0x26] | (r[0x27] << 8) | (r[0x28] << 16),
        mask=r[0x29] | (r[0x2A] << 8), force=r[0x14], roictl=r[0x17],
        imprgb=r.get(0x1F) if r.get(0x1F) is not None else 0x07,
        implvl=r.get(0x12) if r.get(0x12) is not None else 0xFF)


def report(st):
    name = TABLE[st["idx"]] if st["idx"] < len(TABLE) else "?"
    src = f"FORCED (0x{st['force']:02X})" if st["force"] & 0x80 else "chosen from EDID"
    print(f"\n  generating : idx {st['idx']} ({name})  {st['hact']}x{st['vact']} @ "
          f"{st['refr']} Hz, {st['pclk']/1000:.3f} MHz")
    print(f"  mode source: {src}")
    print(f"  edid_ok    : {st['edid_ok']}   timing valid: {st['valid']}")
    rgb = st.get("imprgb", 0x07)
    cname = next((k for k, v in COLOURS.items() if v == (rgb & 7)), f"0x{rgb&7:X}")
    lvl = st.get('implvl', 0xFF)
    seq = (f"{cname.upper()}({lvl}),K,K,K,K RUNNING") if st['roictl'] & 0x80 else 'off'
    print(f"  sequence   : {seq}")
    if not st["edid_ok"]:
        print("  NOTE: edid_ok = 0 -- the projector's EDID was not read, so the mask "
              "below is not trustworthy.")


def show_modes(mask):
    print(f"\n  supported by this projector (mask 0x{mask:04X}):")
    for i, n in enumerate(TABLE):
        if mask & (1 << i):
            print(f"    --mode {i:<2d}  {n}")
    if mask == 0:
        print("    (none -- EDID unread or unparsable)")


def ser_cycle(ser, st, dwell, flash):
    """Walk every advertised mode, and CHECK EACH ONE ARRIVED.

    Writing MODEFORCE and assuming is the trap: the register accepts any index, the
    DRP retunes to it, and a mode the projector cannot display looks identical to a
    mode the FPGA failed to generate -- black either way. So each step reads the
    APPLIED index and geometry back and compares them against the curated table.
    A mismatch is reported per mode rather than aborting, because knowing WHICH modes
    fail is the whole point of a sweep.
    """
    idxs = [i for i in range(len(TABLE)) if st["mask"] & (1 << i)]
    if not idxs:
        print("no advertised modes to cycle -- EDID unread?")
        return
    if flash:
        wr(ser, REG_ROICTL, (st["roictl"] & 0x40) | 0x80)
    print(f"\ncycling {len(idxs)} advertised modes, {dwell:.1f} s each"
          f"{' with the sequence running' if flash else ''}\n")
    print(f"  {'idx':>3}  {'expected':<14} {'applied':<14} {'pixel clk':>10}  result")
    print("  " + "-" * 60)
    bad = []
    for i in idxs:
        wr(ser, REG_MODEFORCE, 0x80 | i)
        time.sleep(0.6)                       # MMCM retune + relock
        got = state(ser)
        if got is None:
            print(f"  {i:>3}  {TABLE[i]:<14} {'NO REPLY':<14} {'':>10}  FAIL")
            bad.append(i); continue
        applied = f"{got['hact']}x{got['vact']}@{got['refr']}"
        ok = (got["idx"] == i and got["valid"] == 1)
        if not ok:
            bad.append(i)
        print(f"  {i:>3}  {TABLE[i]:<14} {applied:<14} {got['pclk']/1000:>9.3f}M  "
              f"{'ok' if ok else 'MISMATCH (idx %d valid %d)' % (got['idx'], got['valid'])}")
        time.sleep(max(0.0, dwell - 0.6))
    print()
    if bad:
        print(f"  {len(bad)} mode(s) did not apply as asked: "
              + ", ".join(f"{i} ({TABLE[i]})" for i in bad))
    else:
        print(f"  all {len(idxs)} advertised modes applied correctly")
    print("  NOTE: this checks what the FPGA GENERATED, not what the projector "
          "displayed.\n        Watch the screen -- a mode can be generated correctly "
          "and still not be shown.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--mode", type=int, help="force a curated-table index")
    ap.add_argument("--auto", action="store_true", help="release the force; EDID picks")
    ap.add_argument("--on", action="store_true", help="start the WHITE,K,K,K,K sequence")
    ap.add_argument("--off", action="store_true", help="stop it, restore the test pattern")
    ap.add_argument("--colour", "--color", dest="colour", choices=sorted(COLOURS),
                    help="which primaries the bright frame drives (default: leave "
                         "as-is; the board resets to white)")
    ap.add_argument("--level", type=int, metavar="0..255",
                    help="code the bright frame drives (default 255). On a DLP this "
                         "is a PWM duty, so lowering it also changes the emission "
                         "TIMING -- good for getting off the sensor rail, not a "
                         "substitute for real attenuation.")
    ap.add_argument("--anyway", action="store_true",
                    help="force a mode even if the EDID does not advertise it")
    ap.add_argument("--cycle", nargs="?", type=float, const=4.0, metavar="SECONDS",
                    help="step through EVERY mode the projector advertises, dwelling "
                         "SECONDS at each (default 4)")
    a = ap.parse_args()

    if a.mode is not None and a.auto:
        sys.exit("--mode and --auto are opposites; pick one")

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.2)

    st = state(ser)
    if st is None:
        ser.close()
        sys.exit(f"no reply on {a.port} -- is a bitstream with the control plane loaded?")

    if a.cycle is not None:
        ser_cycle(ser, st, a.cycle, a.on)
        ser.close()
        return

    if a.mode is not None:
        if not (0 <= a.mode < len(TABLE)):
            ser.close(); sys.exit(f"mode index must be 0..{len(TABLE)-1}")
        if not (st["mask"] & (1 << a.mode)) and not a.anyway:
            show_modes(st["mask"])
            ser.close()
            sys.exit(f"\nREFUSING: this projector does not advertise idx {a.mode} "
                     f"({TABLE[a.mode]}).\nThe FPGA would generate it anyway and you would "
                     f"get a black screen that looks\nlike a broken bitstream. Use --anyway "
                     f"if you believe the EDID is wrong.")
        wr(ser, REG_MODEFORCE, 0x80 | a.mode)
        time.sleep(0.5)          # the MMCM retunes on this write
    elif a.auto:
        wr(ser, REG_MODEFORCE, 0x00)
        time.sleep(0.5)

    if a.colour:
        wr(ser, REG_IMPRGB, COLOURS[a.colour])
        print(f"flash colour set to {a.colour} (0x{COLOURS[a.colour]:02X})")
    if a.level is not None:
        if not (0 <= a.level <= 255):
            ser.close(); sys.exit("--level must be 0..255")
        wr(ser, REG_IMPLVL, a.level)
        print(f"flash level set to {a.level} (0x{a.level:02X})")
    if a.on:
        wr(ser, REG_ROICTL, (st["roictl"] & 0x40) | 0x80)
    elif a.off:
        wr(ser, REG_ROICTL, st["roictl"] & 0x40)

    st = state(ser)
    ser.close()
    if st is None:
        sys.exit("board stopped replying after the write")
    report(st)
    show_modes(st["mask"])
    if st["roictl"] & 0x80:
        period = 5.0 / st["refr"] if st["refr"] else 0
        print(f"\n  expect a white flash every 5 frames = {1/period:.1f} Hz flicker"
              if period else "")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Pin the offline output mode over USB (reg 0x14 MODEFORCE), overriding the EDID pick.

    python host/force_mode.py [COM6] <idx>     # force a curated-table index
    python host/force_mode.py [COM6] release   # hand control back to the EDID

Why this exists: mode_select picks by highest refresh, then highest pixel count. A
display that supports BOTH 1024x768@75 and 1280x1024@60 will therefore ALWAYS take the
75 Hz mode -- so a new, higher-pixel-clock mode can never be reached, and never tested,
on such a display without forcing it.

Writing 0x14 changes the applied index, which re-pulses the MMCM's DRP SEN, so the pixel
clock retunes along with the timing geometry. Reads back the resulting state so you can
see what the board is ACTUALLY generating (regs 0x20..0x2A), not what was requested.
"""
import sys, time, serial

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
REG_FORCE = 0x14

TABLE = [
    "800x600@120", "640x480@120", "1024x768@75", "800x600@75", "640x480@75",
    "1024x768@70", "800x600@72",  "640x480@72",  "1280x720@60", "1280x800@60",
    "1024x768@60", "800x600@60",  "640x480@60 (failsafe)", "1280x1024@60 (108 MHz)",
]


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


def report(ser):
    r = {a: read_reg(ser, a) for a in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28)}
    if any(v is None for v in r.values()):
        print("  (no reply)"); return
    idx  = r[0x20] & 0x0F
    hact = r[0x22] | (r[0x23] << 8)
    vact = r[0x24] | (r[0x25] << 8)
    pclk = r[0x26] | (r[0x27] << 8) | (r[0x28] << 16)
    name = TABLE[idx] if idx < len(TABLE) else "?"
    print(f"  generating: idx {idx} ({name})  {hact}x{vact} @ {r[0x21]} Hz, "
          f"{pclk/1000:.3f} MHz  (x5 = {5*pclk/1000:.1f} MHz)")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"
    arg  = sys.argv[2] if len(sys.argv) > 2 else "release"
    with serial.Serial(port, 115200, timeout=0.2) as ser:
        print("before:"); report(ser)
        if arg == "release":
            write_reg(ser, REG_FORCE, 0x00)
            print("\nMODEFORCE released — the EDID pick is back in charge.")
        else:
            idx = int(arg, 0)
            if not 0 <= idx <= 13:
                raise SystemExit("idx must be 0..13")
            write_reg(ser, REG_FORCE, 0x80 | idx)
            print(f"\nforced idx {idx} ({TABLE[idx]}) — MMCM retuned via DRP.")
        time.sleep(1.5)                      # let the MMCM re-lock
        rb = read_reg(ser, REG_FORCE)
        print(f"0x14 readback = 0x{rb:02X}\n" if rb is not None else "")
        print("after:"); report(ser)
    return 0


if __name__ == "__main__":
    sys.exit(main())

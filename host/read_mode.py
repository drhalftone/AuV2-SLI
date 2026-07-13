#!/usr/bin/env python3
"""Read back which offline mode the FPGA picked from the display's EDID.

    python host/read_mode.py [COM6]

mode_select parses the display's EDID, works out which of the 13 curated modes it
supports, and picks the best by highest refresh -> highest pixel count. That whole
decision used to be invisible: you could only infer it by decoding the EDID by hand
and measuring the frame rate off the telemetry. Registers 0x20..0x2A expose it.

    0x20 MODE  = {7:valid, 6:edid_ok, 3..0:mode_idx}
    0x21 REFR  = refresh (Hz)
    0x22/0x23  = h_active lo/hi      0x24/0x25 = v_active lo/hi
    0x26/27/28 = pixel clock (kHz)   0x29/0x2A = supported mask (13-bit)
"""
import sys, time, serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SYNC, OP_R = 0xA5, 0x52

# The curated table (mode_table.vh), for naming the mask bits. Index order here is
# the TABLE index, which is deliberately NOT the priority order -- see mode_select.v.
TABLE = [
    "800x600@120", "640x480@120", "1024x768@75", "800x600@75", "640x480@75",
    "1024x768@70", "800x600@72",  "640x480@72",  "1280x720@60", "1280x800@60",
    "1024x768@60", "800x600@60",  "640x480@60 (failsafe)",
]


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def read_reg(ser, addr, window=0.6):
    """Frame-scan past the ASCII telemetry sharing this UART."""
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    buf = bytearray()
    deadline = time.time() + window
    while time.time() < deadline:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def read_all(ser):
    regs = {}
    for a in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A):
        regs[a] = read_reg(ser, a)
        if regs[a] is None:
            return None
    return regs


def main():
    with serial.Serial(PORT, 115200, timeout=0.2) as ser:
        # After a board reset, edid_merge needs a few seconds to complete the I2C
        # EDID read off the display's DDC. Until it does, edid_ok is 0 and the
        # supported mask is empty while mode_idx still shows the power-up default --
        # a confusing half-state that looks like a failed pick. Wait it out.
        deadline = time.time() + 12.0
        while True:
            regs = read_all(ser)
            if regs is None:
                print(f"No reply on {PORT}.")
                print("  Is the board running a bitstream with the mode registers (0x20+)?")
                return 1
            if (regs[0x20] & 0x40) or time.time() > deadline:   # edid_ok, or give up
                break
            print("  waiting for the EDID read to complete...")
            time.sleep(1.0)

    valid   = bool(regs[0x20] & 0x80)
    edid_ok = bool(regs[0x20] & 0x40)
    idx     = regs[0x20] & 0x0F
    refr    = regs[0x21]
    hact    = regs[0x22] | (regs[0x23] << 8)
    vact    = regs[0x24] | (regs[0x25] << 8)
    pclk    = regs[0x26] | (regs[0x27] << 8) | (regs[0x28] << 16)   # kHz
    supp    = regs[0x29] | (regs[0x2A] << 8)                        # 13-bit mask

    print(f"=== Offline mode in use (from {PORT}) ===")
    print(f"  mode_valid   : {valid}")
    print(f"  edid_ok      : {edid_ok}   (display EDID block-0 checksum)")
    if not valid:
        print("\n  No mode picked yet -- is a display connected to the HDMI OUTPUT?")
        return 1

    name = TABLE[idx] if idx < len(TABLE) else "?"
    print(f"  mode_idx     : {idx}  ({name})")
    print(f"  resolution   : {hact}x{vact}")
    print(f"  refresh      : {refr} Hz")
    print(f"  pixel clock  : {pclk / 1000:.3f} MHz")

    print(f"\n=== What the display supports (mask 0x{supp:04X}) ===")
    n = 0
    for i, nm in enumerate(TABLE):
        if supp & (1 << i):
            n += 1
            mark = "  <-- PICKED" if i == idx else ""
            print(f"  [{i:2d}] {nm}{mark}")
    if n == 0:
        print("  none -- no curated mode matched; running the failsafe")
    print(f"\n  {n} of {len(TABLE)} curated modes supported by this display.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

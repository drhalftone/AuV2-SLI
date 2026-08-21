"""M6a: does the 0xA5 control plane work over the FT601 instead of the UART?

Sends a register WRITE over USB3 and reads the result back over the SERIAL link.

THE TEST IS DELIBERATELY ONE-DIRECTIONAL. Writing over USB3 and verifying over
serial isolates the command path completely: if the value changes, bytes reached
uart_ctrl and were executed, with no dependency on the reply path that M6b still
has to build. Doing both over USB3 would test two unbuilt things at once and
tell us nothing when it failed.

HOW BYTES RIDE THE OUT PIPE. The pipe carries 32-bit WORDS whose top nibble is
an opcode, so packing 0xA5 bytes raw would let the control stream ALIAS INTO
CAMERA COMMANDS -- a byte landing at 0x1? fires opcode 1 and silently rewrites
the exposure. The stream therefore rides opcode 0, three bytes at a time with an
explicit count:

    {4'd0, count[3:0], byte2, byte1, byte0}

usage:  python host/test_m6a_ctlpath.py [COM6]
"""
import ctypes, struct, sys, time

import serial
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd_serial(ser, addr, window=0.8):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, buf = time.time(), b""
    while time.time() - t0 < window:
        buf += ser.read(64)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def send_ctl_bytes(payload):
    """Push a 0xA5 frame down the FT601 OUT pipe, 3 bytes per opcode-0 word."""
    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        raise SystemExit("no D3XX device")
    words = b""
    for i in range(0, len(payload), 3):
        chunk = payload[i:i + 3]
        n = len(chunk)
        w = (0 << 28) | (n << 24)
        for j, b in enumerate(chunk):
            w |= b << (8 * j)
        words += struct.pack("<I", w)
    buf = ctypes.create_string_buffer(words)
    try:    d.writePipe(0x02, buf, len(words))
    except Exception: d.writePipeEx(0x02, buf, len(words))
    d.close()
    return len(words) // 4


print("M6a -- 0xA5 control plane over the FT601\n")
with serial.Serial(PORT, 115200, timeout=0.3) as s:
    time.sleep(0.4)
    if rd_serial(s, 0x00) != 0x48:
        raise SystemExit("control plane not answering on serial -- wrong bitstream?")
    print("  serial link healthy (ID = 0x48)\n")

    # EVERY VALUE MUST BE DISTINCT AND NON-ZERO. The first version of this test
    # alternated with 0x00, and writing 0x00 to a register already reading 0x00
    # "passes" without the write having happened at all -- it scored 2 of 4 on a
    # path that was delivering nothing.
    ok = 0
    vals = (0xAB, 0x5A, 0x33, 0xC7)
    for val in vals:
        before = rd_serial(s, 0x13)
        if before == val:
            raise SystemExit("0x13 already reads 0x%02X -- test value not distinct" % val)
        nw = send_ctl_bytes(bytes([SYNC, OP_W, 0x13, val, ck(OP_W + 0x13 + val)]))
        time.sleep(0.4)
        after = rd_serial(s, 0x13)
        # SLICTRL reads back with bit 7 set when the override is engaged, so
        # compare the bits the write actually controls.
        good = (after is not None) and ((after & 0x7F) == (val & 0x7F))
        ok += good
        print("  write 0x13=0x%02X over USB3 (%d words) -> serial reads 0x%s   %s"
              % (val, nw, ("%02X" % after) if after is not None else "--",
                 "ok" if good else "MISMATCH (was 0x%02X)" % (before or 0)))

    print("\n  restoring 0x13 = 0x00")
    send_ctl_bytes(bytes([SYNC, OP_W, 0x13, 0x00, ck(OP_W + 0x13)]))
    time.sleep(0.3)

print()
print("PASS: commands sent over USB3 are executed by the control plane."
      if ok == len(vals) else "FAIL: %d of %d writes took effect" % (ok, len(vals)))

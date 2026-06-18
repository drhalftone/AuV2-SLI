#!/usr/bin/env python3
"""Hardware bring-up test for the AuV2-SLI USB control link (Stage 1).

Run against a board freshly programmed with the Stage-1 bitstream:
    python host/test_silicon.py [COM3]

Confirms the FPGA's *receive* path (which the old TX-only bitstream lacked):
telemetry liveness, register read (ID=0x48), write->read round-trip, and table
upload->readback for all three targets. Replies share the UART with status
telemetry, so reads frame-scan past the ASCII telemetry (never a 0x00/0x01/0x02).
"""
import sys, time, serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM3"
SYNC, OP_W, OP_R, OP_L, OP_LR = 0xA5, 0x57, 0x52, 0x5B, 0x72
LEN = {0x00: 720, 0x01: 1280, 0x02: 256}
PASS = FAIL = 0


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def check(name, ok, extra=""):
    global PASS, FAIL
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{(' -- ' + extra) if extra else ''}")


def read_register(ser, addr, window=0.5):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    buf = bytearray(); dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def write_register(ser, addr, val):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.05)  # success is confirmed by reading it back, not by parsing the ack


def upload_table(ser, target, data):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_L, target]) + bytes(data) + bytes([ck(target + sum(data))]))
    time.sleep(0.1)


def read_table(ser, target, window=1.5):
    expect = LEN[target]; need = expect + 2
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_LR, target, ck(OP_LR + target)]))
    buf = bytearray(); dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - need + 1):
            if buf[i] == target and (sum(buf[i:i + need]) & 0xFF) == 0:
                return bytes(buf[i + 1:i + 1 + expect])
    return None


def main():
    print(f"opening {PORT} @115200 ...")
    ser = serial.Serial(PORT, 115200, timeout=0.05)
    try:
        # 1) telemetry liveness -- proves the (new) design is running & TX works
        time.sleep(1.5)
        tele = ser.read(ser.in_waiting or 1)
        line = tele.decode("latin1", "replace").strip().replace("\r\n", " | ")
        check("telemetry present on COM port", len(tele) > 0, line[:80])

        # 2) register reads -- the KEY proof that the FPGA RX path works now
        idv = read_register(ser, 0x00)
        check("read ID reg 0x00 == 0x48", idv == 0x48, f"got {idv!r}")
        check("read VERSION reg 0x01 == 0x01", read_register(ser, 0x01) == 0x01)
        st = read_register(ser, 0x02)
        check("read STATUS reg 0x02 (live byte)", st is not None, f"0x{st:02X}" if st is not None else "none")

        # 3) write -> read round-trip on SLICTRL 0x13
        write_register(ser, 0x13, 0xAB)
        check("write 0x13=0xAB -> read back 0xAB", read_register(ser, 0x13) == 0xAB)
        write_register(ser, 0x13, 0x00)  # restore
        check("write 0x13=0x00 -> read back 0x00", read_register(ser, 0x13) == 0x00)

        # 4) table upload -> readback, all three targets
        for name, tgt in (("corr", 0x02), ("lut", 0x00), ("lutv", 0x01)):
            n = LEN[tgt]
            patt = bytes((i * 7 + 3) & 0xFF for i in range(n))
            upload_table(ser, tgt, patt)
            got = read_table(ser, tgt)
            ok = got == patt
            extra = "match" if ok else (f"got {len(got) if got else 0}/{n} bytes" +
                                        ("" if not got else f", first diff @{next((j for j in range(n) if got[j] != patt[j]), -1)}"))
            check(f"{name}: upload {n}B -> readback equal", ok, extra)

        # 5) loaded flag visible in FLAGS 0x06
        fl = read_register(ser, 0x06)
        check("FLAGS 0x06 lut_loaded bit set after upload", fl is not None and (fl & 0x01), f"0x{fl:02X}" if fl is not None else "none")

        print(f"\n{PASS} passed, {FAIL} failed")
        return 1 if FAIL else 0
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())

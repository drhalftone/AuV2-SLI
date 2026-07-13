#!/usr/bin/env python3
"""
Pre-silicon protocol check for the AuV2-SLI USB control link.

The board is not reflashed yet, so this does NOT touch hardware. Instead it ports
the two sides of the 0xA5 protocol *independently* and runs them against each other:

  * FpgaCtrl  -- a byte-level behavioural model of sources_1/imports/RTL/uart_ctrl.v
                 (the FSM: framing, checksums, register file, table upload/readback).
  * Host      -- the encode/decode logic of host/lauauboard.cpp (writeRegister,
                 readRegister, uploadPatternTable, readTable + its frame-scan).

Because each side is transcribed from its own source file, a mismatch in checksum
math, frame layout, lengths, or the readback prologue is caught here. It does NOT
cover cycle-level timing (uart_rx/uart_tx, the TX arbiter) -- only a real sim or
hardware does. Run: python host/test_protocol.py
"""

SYNC = 0xA5
OP_W, OP_R, OP_L, OP_LR = 0x57, 0x52, 0x5B, 0x72
ACK_K, ACK_E, ACK_N = 0x4B, 0x45, 0x4E
TGT_LUT, TGT_LUTV, TGT_CORR = 0x00, 0x01, 0x02
TGT_LEN = {TGT_LUT: 720, TGT_LUTV: 1280, TGT_CORR: 256}


def lo(x):
    return x & 0xFF


# ---------------------------------------------------------------------------
# FPGA side -- transcribed from uart_ctrl.v (byte-level)
# ---------------------------------------------------------------------------
class FpgaCtrl:
    def __init__(self, led=0x00):
        self.led = led
        self.sli_ctrl = 0x00
        self.corr = bytearray(256)
        self.lut = bytearray(720)
        self.lutv = bytearray(1280)
        self.corr_ld = self.lut_ld = self.lutv_ld = False

    def regread(self, a):
        if a == 0x00:
            return 0x48
        if a == 0x01:
            return 0x01
        if a == 0x02:
            return self.led
        if a == 0x06:
            return (((1 if self.sli_ctrl & 0x80 else 0) << 1)
                    | (1 if (self.corr_ld or self.lut_ld or self.lutv_ld) else 0))
        if a == 0x13:
            return self.sli_ctrl
        return 0x00

    def _store(self, tgt, idx, val):
        (self.lut, self.lutv, self.corr)[tgt][idx] = val

    def _table(self, tgt):
        return (self.lut, self.lutv, self.corr)[tgt]

    def transact(self, frame):
        """Feed a full command frame (bytes); return the FPGA's response bytes.

        Mirrors the uart_ctrl FSM: hunts for SYNC, dispatches on the opcode,
        validates checksums, mutates state, and emits the reply.
        """
        b = list(frame)
        # S_SYNC: skip until 0xA5
        while b and b[0] != SYNC:
            b.pop(0)
        if not b:
            return b""
        b.pop(0)                       # consume SYNC
        if not b:
            return b""
        op = b.pop(0)

        if op == OP_W:                 # A5 57 ADDR DATA CK
            addr, data, ck = b[0], b[1], b[2]
            if lo(OP_W + addr + data + ck) != 0:
                return bytes([ACK_E])
            if addr == 0x13:
                self.sli_ctrl = data
                return bytes([ACK_K])
            return bytes([ACK_N])      # RO / undefined

        if op == OP_R:                 # A5 52 ADDR CK -> ADDR DATA CK2
            addr, ck = b[0], b[1]
            if lo(OP_R + addr + ck) != 0:
                return bytes([ACK_E])
            data = self.regread(addr)
            ck2 = lo(0 - addr - data)
            return bytes([addr, data, ck2])

        if op == OP_L:                 # A5 5B TGT D[..] CK -> K/E
            tgt = b[0]
            if tgt not in TGT_LEN:
                return bytes([ACK_E])
            n = TGT_LEN[tgt]
            data = b[1:1 + n]
            ck = b[1 + n]
            s = lo(tgt + sum(data))
            if lo(s + ck) != 0:
                return bytes([ACK_E])
            for i, v in enumerate(data):
                self._store(tgt, i, v)
            if tgt == TGT_LUT:
                self.lut_ld = True
            elif tgt == TGT_LUTV:
                self.lutv_ld = True
            else:
                self.corr_ld = True
            return bytes([ACK_K])

        if op == OP_LR:                # A5 72 TGT CK -> TGT D[..] CK2
            tgt, ck = b[0], b[1]
            if lo(OP_LR + tgt + ck) != 0 or tgt not in TGT_LEN:
                return bytes([ACK_E])
            data = bytes(self._table(tgt))
            ck2 = lo(0 - tgt - sum(data))
            return bytes([tgt]) + data + bytes([ck2])

        return b""                     # unknown opcode -> back to sync, no reply


# ---------------------------------------------------------------------------
# Host side -- transcribed from lauauboard.cpp
# ---------------------------------------------------------------------------
def checksum_byte(running_sum):
    return lo(256 - lo(running_sum))


class Host:
    """Talks to a `link(frame)->reply` callable that delivers the response bytes."""
    def __init__(self, link):
        self.link = link
        self.error = ""

    def write_register(self, addr, value):
        ck = checksum_byte(OP_W + addr + value)
        reply = self.link(bytes([SYNC, OP_W, addr, value, ck]))
        if not reply:
            self.error = "timeout"
            return False
        r = reply[0]
        self.error = {ACK_K: "", ACK_E: "checksum", ACK_N: "read-only/undef"}.get(r, "unexpected")
        return r == ACK_K

    def read_register(self, addr):
        ck = checksum_byte(OP_R + addr)
        reply = self.link(bytes([SYNC, OP_R, addr, ck]))
        if reply and reply[0] == ACK_E:
            self.error = "E"
            return -1
        if len(reply) < 3:
            self.error = "short"
            return -1
        a, d, c = reply[0], reply[1], reply[2]
        if a != addr or lo(a + d + c) != 0:
            self.error = "echo/checksum"
            return -1
        return d

    def upload_table(self, data, target):
        if len(data) != TGT_LEN.get(target, -1):
            self.error = "length"
            return False
        s = target + sum(data)
        ck = checksum_byte(s)
        frame = bytes([SYNC, OP_L, target]) + bytes(data) + bytes([ck])
        reply = self.link(frame)
        if reply and reply[0] == ACK_K:
            return True
        self.error = "E" if (reply and reply[0] == ACK_E) else "timeout"
        return False

    def read_table(self, target):
        expect = TGT_LEN.get(target, 0)
        if expect == 0:
            self.error = "target"
            return None
        ck = checksum_byte(OP_LR + target)
        reply = self.link(bytes([SYNC, OP_LR, target, ck]))
        need = expect + 2
        buf = list(reply)
        # frame-scan: first TARGET byte that begins a checksum-valid frame
        for i in range(0, len(buf) - need + 1):
            if buf[i] != target:
                continue
            if lo(sum(buf[i:i + need])) == 0:
                return bytes(buf[i + 1:i + 1 + expect])
        self.error = "no valid reply"
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def main():
    fpga = FpgaCtrl(led=0xC3)
    host = Host(fpga.transact)

    print("register access")
    check("ID reg 0x00 == 0x48 (verifyIdentity)", host.read_register(0x00) == 0x48)
    check("VERSION reg 0x01 == 0x01", host.read_register(0x01) == 0x01)
    check("STATUS reg 0x02 == live led (0xC3)", host.read_register(0x02) == 0xC3)
    check("write SLICTRL 0x13 = 0xAB -> K", host.write_register(0x13, 0xAB))
    check("read back SLICTRL 0x13 == 0xAB (round-trip)", host.read_register(0x13) == 0xAB)
    check("FLAGS 0x06 bit1 usb_sw_en set (0xAB has bit7)", host.read_register(0x06) & 0x02 != 0)
    check("write read-only reg 0x00 -> N (False)", host.write_register(0x00, 0x55) is False and host.error == "read-only/undef")

    print("bad checksums")
    bad_w = host.link(bytes([SYNC, OP_W, 0x13, 0x10, 0x00]))      # wrong CK
    check("write bad CK -> 'E'", bad_w == bytes([ACK_E]))
    bad_r = host.link(bytes([SYNC, OP_R, 0x00, 0x00]))            # wrong CK
    check("read bad CK -> 'E'", bad_r == bytes([ACK_E]))

    print("table upload + readback (correction 256)")
    ident = bytes(range(256))
    check("upload identity corr -> K", host.upload_table(ident, TGT_CORR))
    check("FLAGS 0x06 bit0 lut_loaded set after upload", host.read_register(0x06) & 0x01 != 0)
    check("read back corr == identity", host.read_table(TGT_CORR) == ident)

    ramp2 = bytes((255 - i) for i in range(256))
    check("upload inverse corr -> K", host.upload_table(ramp2, TGT_CORR))
    check("read back corr == inverse (overwrote)", host.read_table(TGT_CORR) == ramp2)

    print("table upload + readback (LUT 720, LUT_V 1280)")
    lut = bytes((i * 7) & 0xFF for i in range(720))
    lutv = bytes((i * 3 + 5) & 0xFF for i in range(1280))
    check("upload LUT 720 -> K", host.upload_table(lut, TGT_LUT))
    check("read back LUT == sent", host.read_table(TGT_LUT) == lut)
    check("upload LUT_V 1280 -> K", host.upload_table(lutv, TGT_LUTV))
    check("read back LUT_V == sent", host.read_table(TGT_LUTV) == lutv)

    print("error paths")
    bad_up = bytes([SYNC, OP_L, TGT_CORR]) + bytes(256) + bytes([0x01])  # wrong CK
    check("upload bad CK -> 'E'", host.link(bad_up) == bytes([ACK_E]))
    bad_rt = host.link(bytes([SYNC, OP_LR, 0x03, checksum_byte(OP_LR + 0x03)]))  # unknown target
    check("read-table unknown target -> 'E'", bad_rt == bytes([ACK_E]))

    print("telemetry-interleave robustness (host frame-scan)")
    # A status line is all printable ASCII + CR/LF, never a target byte. Surround the
    # readback reply with a partial status line and confirm the scan still locks on.
    noise_pre = b"S=1 V=0 N=003C L=C3 D=0E\r\n"
    noise_post = b"S=1 V=0 N=003D"
    real_reply = fpga.transact(bytes([SYNC, OP_LR, TGT_CORR, checksum_byte(OP_LR + TGT_CORR)]))
    polluted = Host(lambda _f: noise_pre + real_reply + noise_post)
    check("readback survives leading+trailing status bytes", polluted.read_table(TGT_CORR) == ramp2)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

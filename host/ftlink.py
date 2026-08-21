"""M6b: the 0xA5 control plane over the Ft+ alone -- commands AND replies.

This is the transport the delivered system uses. The Pt's COM6 UART is bring-up
scaffolding; once this works nothing needs it.

TWO DIRECTIONS, TWO SHAPES.

Commands (host -> FPGA) ride the OUT pipe as opcode-0 words, three bytes at a
time with an explicit count: {4'd0, count[3:0], byte2, byte1, byte0}. They cannot
be packed raw because the top nibble of every word is a camera opcode, and a
protocol byte landing at 0x1? would fire opcode 1 and silently rewrite the
exposure.

Replies (FPGA -> host) come back INTERLEAVED WITH VIDEO on the IN pipe, as
packets with the same 32-byte header shape as a frame so one parser handles
both. Reply packets carry RMAGIC ("SLI1") and format 4; frames carry MAGIC
("SLI0"). Header layout for a reply:

    w0 = RMAGIC        w4 = padded byte count (what to skip)
    w1 = sequence      w5 = w4/4
    w2 = TRUE count    w6 = 4 (format)
    w3 = 0             w7 = ~RMAGIC

w4 is padded up to a whole 128-bit word because the pipe moves 128-bit words and
the parser must be able to skip a packet by byte count alone. w2 is the count
that matters. The padding is zeros, which are never SYNC, so a host that used w4
by mistake would not corrupt the parse -- it just would not find the reply.

A REPLY IS A TRANSPORT CHUNK, NOT A MESSAGE. The FPGA emits whatever bytes are
queued when it reaches a frame boundary, so one protocol reply can arrive split
across two packets, and two replies can share one. That is why this class
reassembles a BYTE STREAM and lets the 0xA5 framing do its own work, exactly as
it would over a serial port.

REPLIES ONLY LEAVE AT FRAME BOUNDARIES. At 120 Hz that is up to ~8.3 ms of
latency, by construction -- the reply is injected in the reader's idle state so a
frame is never interrupted. Size timeouts accordingly: a register read is
milliseconds, not microseconds.
"""
import ctypes, struct, time

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

SYNC = 0xA5
OP_W, OP_R, OP_L, OP_LR = 0x57, 0x52, 0x5B, 0x72
MAGIC, RMAGIC = 0x30494C53, 0x31494C53
HDR = 32
FMT_STATUS, FMT_CTRL = 1, 4


def ck(total):
    return (256 - (total & 0xFF)) & 0xFF


class FtLink:
    """0xA5 control plane over the FT601, with video sharing the same IN pipe."""

    def __init__(self, index=0, read_chunk=1 << 20):
        self.d = ftd3xx.create(index, FT_OPEN_BY_INDEX)
        if self.d is None:
            raise RuntimeError("no D3XX device")
        for fn in ("abortPipe", "flushPipe"):
            try: getattr(self.d, fn)(0x82)
            except Exception: pass
        self.d.setPipeTimeout(0x82, 200)
        self._buf = ctypes.create_string_buffer(read_chunk)
        self._chunk = read_chunk
        self._acc = bytearray()      # raw IN-pipe bytes, packet framing
        self._rx = bytearray()       # reassembled 0xA5 reply byte stream
        self.frames = 0
        self.replies = 0
        self.badpk = 0
        self._synced = False

    def close(self):
        try: self.d.close()
        except Exception: pass

    # ---------------- command direction ----------------
    def send_bytes(self, payload):
        """Push raw 0xA5 protocol bytes down the OUT pipe as opcode-0 words."""
        words = b""
        for i in range(0, len(payload), 3):
            chunk = payload[i:i + 3]
            w = (0 << 28) | (len(chunk) << 24)
            for j, b in enumerate(chunk):
                w |= b << (8 * j)
            words += struct.pack("<I", w)
        buf = ctypes.create_string_buffer(words)
        try:    self.d.writePipe(0x02, buf, len(words))
        except Exception: self.d.writePipeEx(0x02, buf, len(words))
        return len(words) // 4

    # ---------------- reply direction ----------------
    def pump(self):
        """Read once from the IN pipe and walk out whole packets."""
        try:
            n = self.d.readPipe(0x82, self._buf, self._chunk)
        except Exception:
            n = 0
        if n:
            self._acc += self._buf.raw[:n]

        fmag, rmag = struct.pack("<I", MAGIC), struct.pack("<I", RMAGIC)
        i = 0
        while True:
            if len(self._acc) - i < HDR:
                break
            h = struct.unpack_from("<8I", self._acc, i)
            if h[0] == MAGIC and h[7] == (~MAGIC & 0xFFFFFFFF):
                if len(self._acc) - i < HDR + h[4]:
                    break
                self._synced = True
                self.frames += 1
                i += HDR + h[4]
            elif h[0] == RMAGIC and h[7] == (~RMAGIC & 0xFFFFFFFF):
                if len(self._acc) - i < HDR + h[4]:
                    break
                self._synced = True
                if h[6] == FMT_CTRL:
                    # h[2] is the TRUE byte count; the rest of h[4] is zero pad.
                    nvalid = min(h[2], h[4])
                    self._rx += self._acc[i + HDR: i + HDR + nvalid]
                    self.replies += 1
                i += HDR + h[4]
            else:
                nxt = [p for p in (self._acc.find(fmag, i + 4),
                                   self._acc.find(rmag, i + 4)) if p >= 0]
                if not nxt:
                    i = max(0, len(self._acc) - HDR)
                    break
                # never score the first partial packet: the pipe is joined
                # mid-stream, so the opening bytes are mid-packet by definition
                if self._synced:
                    self.badpk += 1
                i = min(nxt)
        del self._acc[:i]
        return n

    def _recv(self, nbytes, timeout):
        t0 = time.time()
        while len(self._rx) < nbytes and time.time() - t0 < timeout:
            self.pump()
        if len(self._rx) < nbytes:
            return None
        out = bytes(self._rx[:nbytes])
        del self._rx[:nbytes]
        return out

    def flush_rx(self):
        self._rx.clear()

    # ---------------- protocol operations ----------------
    def read_reg(self, addr, timeout=1.0):
        """Register read. Reply is addr, value, checksum."""
        self.flush_rx()
        self.send_bytes(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
        r = self._recv(3, timeout)
        if r is None:
            return None
        if r[0] != addr or ((r[0] + r[1] + r[2]) & 0xFF) != 0:
            raise RuntimeError("bad read reply for 0x%02X: %s" % (addr, r.hex()))
        return r[1]

    def write_reg(self, addr, val, timeout=1.0):
        """Register write. Reply is a single ACK byte ('K' good, 'E'/'N' bad)."""
        self.flush_rx()
        self.send_bytes(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
        r = self._recv(1, timeout)
        return None if r is None else r[0]

    def read_table(self, tgt, length, timeout=3.0):
        """Table readback: reply is the echoed target, `length` bytes, checksum."""
        self.flush_rx()
        self.send_bytes(bytes([SYNC, OP_LR, tgt, ck(OP_LR + tgt)]))
        r = self._recv(length + 2, timeout)
        if r is None:
            return None
        if r[0] != tgt:
            raise RuntimeError("table reply target %02X != %02X" % (r[0], tgt))
        if (sum(r) & 0xFF) != 0:
            raise RuntimeError("table reply checksum bad")
        return r[1:1 + length]

    def write_table(self, tgt, data, timeout=3.0):
        """Table upload: target, data, checksum. Reply is one ACK byte."""
        self.flush_rx()
        payload = bytes([SYNC, OP_L, tgt]) + bytes(data)
        payload += bytes([ck(tgt + sum(data))])
        self.send_bytes(payload)
        r = self._recv(1, timeout)
        return None if r is None else r[0]

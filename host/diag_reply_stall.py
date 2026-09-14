"""Why does the Ft+ reply path go silent when the frame stream stops?

    python host/diag_reply_stall.py [COM6]

FTPLUS_API.md "Known limitation" and MERGE_MILESTONES.md M3/3b: with the camera
idled, commands still arrive and the FPGA builds the reply, but no bytes reach the
host. The reader FSM was ruled out (it emits the reply correctly), so the stall is
at or below the FT601 handoff. This run separates the remaining explanations
instead of guessing between them.

THE WITNESS IS PORT A. The link under test is Port B; measuring its stall over
itself cannot distinguish "the FPGA is holding the bytes" from "the host is losing
them". Reg 0x3B, read over serial, says which side of the FT601 the bytes are on:

    0x3B = {cfifo_ovf, ufifo_ovf, ufifo_empty, txe, 0000}

    ufifo_empty  0 = bytes still queued in the FPGA's FT601 FIFO
    txe          1 = FT601 TXE# high: the chip is refusing new data

WHAT EACH OUTCOME MEANS

  replies arrive       The host-side fix in ftlink._read_in() cured it. `partial`
                       > 0 says the mechanism was a timed-out read whose bytes the
                       old wrapper call threw away.
  silent, empty=1      Bytes LEFT the FPGA and are stuck in the FT601 or driver:
                       the chip is not closing the transfer. Check the chip-config
                       bits printed at the top -- an underrun/session option that
                       holds a short transfer open would do exactly this.
  silent, empty=0 txe=1  The FT601 will not take data. Same chip-config suspicion,
                       one step earlier: its buffer holds a transfer the host never
                       completed.
  silent, empty=0 txe=0  The FT601 would take data and the FPGA is not writing:
                       an RTL stall in the unpack/skid/ft601_sync_tx path.

Camera idle is SELF-TIMED (opcode 6, 20 s), so a crash here cannot strand the
camera off.
"""
import os, sys, time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ftlink import FtLink

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SYNC, OP_R = 0xA5, 0x52
IDLE_MS = 20000


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd_a(ser, addr, window=0.6):
    """Port A register read, frame-scanning past the ASCII telemetry."""
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, buf = time.time(), b""
    while time.time() - t0 < window:
        buf += ser.read(64)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def fifo_state(ser):
    v = rd_a(ser, 0x3B)
    if v is None:
        return None
    return {"ufifo_empty": (v >> 5) & 1, "txe": (v >> 4) & 1,
            "ufifo_ovf": (v >> 6) & 1, "raw": v}


def chip_config(link):
    """Print the FT601 options that decide when a short IN transfer is sent.

    Bit meanings follow ftd3xx.defines where the installed package names them; the
    fallback values are FTDI's D3XX header values as recalled -- trust the named
    ones, and confirm the fallback against the D3XX programmer's guide.
    """
    try:
        cfg = link.d.getChipConfiguration()
    except Exception as e:
        print("  chip config: unreadable (%s)" % e)
        return
    opt = getattr(cfg, "OptionalFeatureSupport", None)
    print("  chip config: FIFOMode=%s ChannelConfig=%s OptionalFeatureSupport=%s"
          % (getattr(cfg, "FIFOMode", "?"), getattr(cfg, "ChannelConfig", "?"),
             "0x%04X" % opt if opt is not None else "?"))
    if opt is None:
        return
    try:
        import ftd3xx.defines as D
    except Exception:
        D = None
    fallback = {
        "DISABLECANCELSESSIONUNDERRUN": 0x0002,
        "DISABLEUNDERRUN_INCH1":        0x0040,
    }
    for name, val in fallback.items():
        full = "CONFIGURATION_OPTIONAL_FEATURE_" + name
        bit = getattr(D, full, None) if D else None
        src = "ftd3xx.defines" if bit is not None else "fallback, verify"
        bit = val if bit is None else bit
        print("    %-30s %s   (%s)" % (name, "SET" if opt & bit else "clear", src))


def replies(link, n=5, timeout=2.0):
    ok = 0
    for _ in range(n):
        try:
            if link.read_reg(0x00, timeout) == 0x48:
                ok += 1
        except Exception:
            pass
    return ok


def frames_in(link, secs):
    n0, t0 = link.frames, time.time()
    while time.time() - t0 < secs:
        link.pump()
    return link.frames - n0


link = FtLink()
ser = serial.Serial(PORT, 115200, timeout=0.2)
time.sleep(0.4)
link.drain()

print("Ft+ reply stall -- which side of the FT601 holds the bytes?\n")
chip_config(link)
print("  low-level D3XX read path: %s" % ("yes" if link._ft else
      "NO -- wrapper fallback, partial reads cannot be recovered"))

fps = frames_in(link, 2.0) / 2.0
base = replies(link)
print("\n  baseline   camera %.1f fps   Port B replies %d/5   0x3B %s"
      % (fps, base, fifo_state(ser)))
if fps < 50 or base < 5:
    sys.exit("not a valid baseline -- streaming and replies must both work first")

print("\n  -> opcode 6: idle camera %d ms (self-releasing)" % IDLE_MS)
link.cam_idle(IDLE_MS)
time.sleep(1.0)
idle_fps = frames_in(link, 1.0)
if idle_fps > 5:
    sys.exit("camera did not stop (%.1f fps) -- the stall cannot be reproduced" % idle_fps)

before = fifo_state(ser)
t0, p0 = link.timeouts, link.partial
got = replies(link)
after = fifo_state(ser)
print("  idle       Port B replies %d/5   read timeouts %d (partial %d)"
      % (got, link.timeouts - t0, link.partial - p0))
print("             0x3B before %s" % before)
print("             0x3B after  %s" % after)

print("\n  -> release camera")
link.cam_resume()
time.sleep(4.0)
link.drain()
rec_fps = frames_in(link, 2.0) / 2.0
rec = replies(link)
print("  recovered  camera %.1f fps   Port B replies %d/5" % (rec_fps, rec))

print()
if got == 5:
    print("VERDICT: replies arrive with the camera idle -- the host-side read fix works.")
    if link.partial - p0:
        print("         Mechanism: timed-out reads carried the reply; the old call dropped them.")
elif after is None:
    print("VERDICT: inconclusive -- Port A did not answer the 0x3B read.")
elif after["ufifo_empty"]:
    print("VERDICT: bytes LEFT the FPGA and did not reach the host -> FT601 / driver.")
    print("         Check the underrun/session bits above.")
elif after["txe"]:
    print("VERDICT: bytes queued in the FPGA, FT601 refusing them (TXE# high) -> chip config.")
else:
    print("VERDICT: bytes queued in the FPGA and FT601 READY -> RTL stall in the FT601 write path.")
link.close()
ser.close()

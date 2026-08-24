"""M6c: the whole control plane over the Ft+ -- test_silicon.py with no serial port.

M6a moved COMMANDS onto the FT601, M6b moved REPLIES. This closes M6: everything
test_silicon.py proves over the UART is proved over D3XX instead, plus the EDID
readback the UART version never covered. If this passes, Port A is TX-only and
nothing in the delivered system needs it.

    python host/test_m6c_silicon.py

WHAT IS ACTUALLY NEW HERE, AND WHY IT IS THE RISKY PART.

M6a and M6b both moved SHORT messages: a 4-byte command, a 3-byte reply. This
milestone is the first to move LONG ones in both directions:

  * OUT: a 1,280-byte table upload is 1,283 protocol bytes = 428 opcode-0 words.
    Every earlier command fitted in two.
  * IN: the readback is 1,282 bytes, against a reply FIFO sized at 2,048 -- which
    is what that sizing was FOR. It also spans several frame boundaries, so the
    reply necessarily arrives split across packets and the byte-stream
    reassembly in FtLink is load-bearing for the first time.

WHY READBACK-EQUALS-UPLOAD IS NOT ENOUGH ON ONE TRANSPORT.

test_silicon.py uploads a table and reads it back over the same link, which is
sound over a UART where the FPGA has nowhere to hide 1,280 bytes. It is weaker
here, and M6b is the reason to care: that bug returned a perfectly-shaped reply
carrying the WRONG bytes, and it passed every structural check. A design that
echoed the command buffer instead of reading the table would pass an
upload-then-readback test too.

So each table is checked three ways instead:

  1. read it BEFORE uploading and confirm it does not already hold the pattern
     -- otherwise the test can pass against a stale table from a previous run
  2. upload pattern A, read back, then upload a DIFFERENT pattern B and read
     back -- an echo of the last thing written passes (1), but the readback has
     to TRACK the change to pass this
  3. after all three targets are loaded, re-read all three -- a shared buffer
     or a mis-decoded target byte shows up as cross-contamination, which
     per-target testing in isolation cannot see

Point 3 is why the three targets get DISTINCT patterns rather than the one
formula test_silicon.py reuses for all of them.

NO SERIAL PORT IS OPENED ANYWHERE IN THIS FILE. That is the milestone.
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ftlink import FtLink

# Table targets and their exact lengths, from the 0xA5 protocol.
TGT_LUT, TGT_LUTV, TGT_CORR, TGT_EDID = 0x00, 0x01, 0x02, 0x03
LEN = {TGT_LUT: 720, TGT_LUTV: 1280, TGT_CORR: 256, TGT_EDID: 256}
ACK_K = 0x4B

PASS = FAIL = 0
fails = []


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        fails.append(name)
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name,
                         ("  -- " + extra) if extra else ""))


def patt(tgt, salt):
    """Distinct per target AND per round, so nothing passes by coincidence."""
    n = LEN[tgt]
    return bytes(((i * 7 + 3) ^ (tgt * 0x5B) ^ salt) & 0xFF for i in range(n))


def rdtbl(tgt, n, timeout=8.0):
    """read_table that REPORTS a bad reply instead of raising.

    FtLink.read_table raises on a wrong target echo or a failed checksum --
    correct for a library, wrong for this test. Those two are precisely the
    symptoms a long-reply bug produces (M6b's rotation failed the checksum and
    looked like a wire fault), so they have to arrive as a readable line, not a
    traceback that ends the run before the remaining targets are tried.
    """
    try:
        return link.read_table(tgt, n, timeout=timeout)
    except RuntimeError as e:
        print("       ! target 0x%02X readback rejected: %s" % (tgt, e))
        return None


def first_diff(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return -1 if len(a) == len(b) else min(len(a), len(b))


link = FtLink()
print("M6c -- test_silicon over the Ft+, no serial port\n")

# Replies carry no framing of their own: one stale byte left in the FPGA shifts
# every subsequent reply by one, permanently. Same reason M6b needed this.
stale = link.drain()
print("  drained stale replies (%d reply packets seen while draining)\n" % stale)

# ---- 1. the IN pipe is alive ----------------------------------------------
# The UART version checks for telemetry bytes. The equivalent liveness proof
# here is that the video stream itself is arriving -- same question, and it also
# confirms the pipe replies will share is actually running.
f0 = link.frames
t0 = time.time()
while link.frames - f0 < 5 and time.time() - t0 < 3.0:
    link.pump()
check("IN pipe alive (frames arriving)", link.frames - f0 >= 5,
      "%d frames in %.2f s" % (link.frames - f0, time.time() - t0))

# ---- 2-4. register reads ---------------------------------------------------
idv = link.read_reg(0x00, timeout=3.0)
check("read ID reg 0x00 == 0x48", idv == 0x48,
      ("got 0x%02X" % idv) if idv is not None else "TIMEOUT")
ver = link.read_reg(0x01, timeout=3.0)
check("read VERSION reg 0x01 == 0x01", ver == 0x01,
      ("got 0x%02X" % ver) if ver is not None else "TIMEOUT")
st = link.read_reg(0x02, timeout=3.0)
check("read STATUS reg 0x02 (live byte)", st is not None,
      ("0x%02X" % st) if st is not None else "TIMEOUT")

# ---- 5-6. write -> read round-trip on SLICTRL 0x13 -------------------------
ack = link.write_reg(0x13, 0xAB, timeout=3.0)
rb = link.read_reg(0x13, timeout=3.0)
check("write 0x13=0xAB -> read back 0xAB", ack == ACK_K and rb == 0xAB,
      "ack=%s val=%s" % (ack, ("0x%02X" % rb) if rb is not None else "--"))
ack = link.write_reg(0x13, 0x00, timeout=3.0)
rb = link.read_reg(0x13, timeout=3.0)
check("write 0x13=0x00 -> read back 0x00", ack == ACK_K and rb == 0x00,
      "ack=%s val=%s" % (ack, ("0x%02X" % rb) if rb is not None else "--"))

# ---- 7-9. tables: upload -> readback, all three targets --------------------
print()
loaded = {}
for name, tgt in (("corr", TGT_CORR), ("lut", TGT_LUT), ("lutv", TGT_LUTV)):
    n = LEN[tgt]
    a, b = patt(tgt, 0x00), patt(tgt, 0x9E)

    # (1) it must not already hold what we are about to send
    pre = rdtbl(tgt, n, timeout=6.0)
    if pre == a:
        check("%s: pre-state distinct from pattern A" % name, False,
              "table already held A -- readback cannot prove anything")
    ok_pre = pre is not None and pre != a

    # (2) upload A, read back; then B, read back -- the readback must TRACK
    t0 = time.time()
    ack_a = link.write_table(tgt, a, timeout=8.0)
    got_a = rdtbl(tgt, n)
    ack_b = link.write_table(tgt, b, timeout=8.0)
    got_b = rdtbl(tgt, n)
    dt = time.time() - t0

    ok = (ack_a == ACK_K and ack_b == ACK_K and got_a == a and got_b == b and ok_pre)
    if got_a == a and got_b == a:
        extra = "readback did NOT track the second upload -- echo, not storage"
    elif ok:
        extra = "%dB x2 round-trips in %.2f s" % (n, dt)
    else:
        d = first_diff(got_b or b"", b)
        extra = ("A %s, B %s%s"
                 % ("ok" if got_a == a else "bad",
                    "ok" if got_b == b else "bad",
                    "" if d < 0 else ", first diff @%d" % d))
    check("%s: upload %dB -> readback equal (x2 patterns)" % (name, n), ok, extra)
    loaded[tgt] = b

# ---- 10. no cross-contamination between targets ---------------------------
bad_tgt = [t for t, want in loaded.items()
           if rdtbl(t, LEN[t]) != want]
check("all three tables still hold their own data", not bad_tgt,
      "clobbered: %s" % [hex(t) for t in bad_tgt] if bad_tgt else "no cross-talk")

# ---- 11. loaded flag -------------------------------------------------------
fl = link.read_reg(0x06, timeout=3.0)
check("FLAGS 0x06 lut_loaded bit set after upload",
      fl is not None and (fl & 0x01),
      ("0x%02X" % fl) if fl is not None else "TIMEOUT")

# ---- 12-15. 0x13 override of the physical switch pins, seen via 0x10 ------
# 0x10 = {eff_sw[3:0], phys_sw[3:0]}; eff_sw is literally pixel_pipe's sw input.
print()
p0 = link.read_reg(0x10, timeout=3.0)
phys0, eff0 = (p0 & 0x0F, (p0 >> 4) & 0x0F) if p0 is not None else (None, None)
check("PINS 0x10 readable; override off -> eff == phys",
      p0 is not None and eff0 == phys0,
      ("phys=0x%X eff=0x%X" % (phys0, eff0)) if p0 is not None else "TIMEOUT")

ov = (phys0 ^ 0x0F) & 0x0F if p0 is not None else 0x5   # differ from the switches
link.write_reg(0x13, 0x80 | ov, timeout=3.0)            # sw_en=1 + override nibble
rb = link.read_reg(0x13, timeout=3.0)
check("read 0x13 == 0x80|ov", rb == (0x80 | ov), "ov=0x%X" % ov)

p1 = link.read_reg(0x10, timeout=3.0)
phys1, eff1 = (p1 & 0x0F, (p1 >> 4) & 0x0F) if p1 is not None else (None, None)
check("override ON: eff_sw (pixel_pipe input) follows USB", eff1 == ov,
      ("eff=0x%X want 0x%X" % (eff1, ov)) if p1 is not None else "TIMEOUT")
check("override ON: physical switches unchanged", phys1 == phys0,
      ("phys=0x%X" % phys1) if p1 is not None else "TIMEOUT")

link.write_reg(0x13, 0x00, timeout=3.0)                 # restore: override off
p2 = link.read_reg(0x10, timeout=3.0)
check("override OFF: eff_sw reverts to physical",
      p2 is not None and ((p2 >> 4) & 0xF) == (p2 & 0xF) == phys0,
      ("0x%02X" % p2) if p2 is not None else "TIMEOUT")

# ---- EDID readback ---------------------------------------------------------
# Not in the UART test_silicon.py at all; the milestone asks for it because it is
# the one table the HOST cannot fabricate -- it is read off the HDMI-OUT DDC into
# edid_merge's RAM, so real content proves the readback path reaches CAPTURED
# state rather than something this script put there.
#
# WHICH MEANS IT NEEDS A DISPLAY ON HDMI-OUT, and must report honestly when there
# is not one. DO NOT "validate" an EDID by its block-0 checksum alone: that sum is
# mod-256 over 128 bytes, so an ALL-ZERO buffer passes it trivially. An empty RAM
# therefore reads as "checksum OK" and looks like a captured EDID. The first run
# of this test printed exactly that, and it was meaningless -- 0 of 256 bytes were
# non-zero, confirmed identical over the UART as an independent witness. Content
# is only claimed when the header is right AND the buffer is not empty.
print()
skipped = []
e = rdtbl(TGT_EDID, LEN[TGT_EDID])
if e is None:
    check("EDID readback: transport delivers 256 bytes", False, "TIMEOUT")
else:
    # The TRANSPORT half is proven regardless of content: the reply arrived, it
    # echoed the right target, and its protocol checksum closed -- rdtbl would
    # have rejected it otherwise.
    check("EDID readback: transport delivers 256 bytes", True,
          "target echo + checksum ok")
    nz = sum(1 for b in e if b)
    hdr_ok = e[0:8] == bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
    if nz == 0:
        skipped.append("EDID CONTENT -- edid_merge's RAM is empty (0 of 256 bytes"
                       " non-zero), so nothing is attached to HDMI-OUT and there is"
                       " no EDID to capture. Re-run with a display connected to"
                       " close this half of the milestone.")
    elif hdr_ok and (sum(e[0:128]) & 0xFF) == 0:
        v = (e[8] << 8) | e[9]
        mfg = "".join(chr(ord("A") - 1 + ((v >> s) & 0x1F)) for s in (10, 5, 0))
        check("EDID content: header + block-0 checksum valid", True, "mfg %s" % mfg)
    else:
        check("EDID content: header + block-0 checksum valid", False,
              "header %s, %d/256 non-zero -- data present but not a valid EDID"
              % ("ok" if hdr_ok else "BAD", nz))

# ---- the stream must have survived all of it ------------------------------
print()
print("  stream: %d frames seen, %d malformed packets, %d reply packets"
      % (link.frames, link.badpk, link.replies))
if link.badpk:
    check("frame stream unharmed (no malformed packets)", False,
          "%d malformed" % link.badpk)
else:
    check("frame stream unharmed (no malformed packets)", True)

link.close()

print("\n%d passed, %d failed, %d not proven" % (PASS, FAIL, len(skipped)))
if FAIL:
    print("FAIL:")
    for f in fails:
        print("  - " + f)
for item in skipped:
    print("NOT PROVEN: " + item)
if skipped and not FAIL:
    print("\nEverything testable on this bench passes. The milestone's listed proof"
          "\nis NOT complete until the item above is re-run with a display attached.")
raise SystemExit(1 if FAIL else 0)

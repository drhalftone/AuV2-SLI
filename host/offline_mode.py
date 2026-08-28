#!/usr/bin/env python3
"""Inspect both EDIDs and drive the OFFLINE display mode.

    python host/offline_mode.py                 # show everything, change nothing
    python host/offline_mode.py --list          # just the mode table
    python host/offline_mode.py --set 1024x768@75
    python host/offline_mode.py --set 11        # by index, if you prefer
    python host/offline_mode.py --release       # back to the EDID's own pick
    python host/offline_mode.py --dump-display edid_display.bin
    python host/offline_mode.py --dump-served  edid_served.bin

THREE THINGS THAT ARE EASY TO CONFUSE, so this prints them side by side.

  1. THE DISPLAY'S EDID (rdtbl 0x03) -- what the monitor on HDMI-OUT told us.
  2. THE SERVED EDID (rdtbl 0x05)    -- the merged block we present to the PC on
     the input DDC. It is the display's modes INTERSECTED with the pass-through
     clock window, so it is deliberately SHORTER than the display's. Until this
     target existed the served copy could only be inspected through Windows'
     registry cache, which is the host's copy and can be stale.
  3. THE OFFLINE MODE (regs 0x20..0x28) -- what the FPGA generates when no source
     is connected. Nothing to do with either EDID's *timings*; the EDID only
     decides which entry of the curated table gets picked.

SETTING A MODE IS A FORCE, AND IT IS STICKY. --set writes MODEFORCE (reg 0x14)
with force_en set, which OVERRIDES the EDID pick until you --release it or the
FPGA is reloaded. That is the point -- it is how you test a mode the attached
display would never ask for -- but it means "why is my offline mode wrong" has a
second answer besides the EDID.

WHAT IT VERIFIES. After writing, it reads 0x20..0x28 back and reports what the
FPGA actually applied, rather than assuming the write took. The applied index,
the geometry AND the pixel clock all come from the fabric.
"""
import argparse, os, re, sys, time

SYNC, OP_W, OP_R, OP_LR = 0xA5, 0x57, 0x52, 0x72
TGT_DISPLAY, TGT_SERVED = 0x03, 0x05
REG_FORCE = 0x14

ck = lambda s: (256 - (s & 0xFF)) & 0xFF

# ---------------------------------------------------------------- mode table
# Parsed from the RTL itself so it cannot drift from what the fabric will do.
# mode_table.vh is the single source of truth for both the DRP clock words and
# the timing ROM, so re-deriving it here by hand would be a second copy to rot.
MROW = re.compile(r"MROW\(\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),")


def mode_table(rtl_dir):
    p = os.path.join(rtl_dir, "mode_table.vh")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p):
        m = MROW.search(line)
        if m:
            i, w, h, hz, khz = (int(x) for x in m.groups())
            out.append(dict(idx=i, w=w, h=h, hz=hz, khz=khz))
    return sorted(out, key=lambda r: r["idx"])


class Link:
    def __init__(self, port, prefer_uart=False):
        self.f = None
        if not prefer_uart:
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from ftlink import FtLink
                f = FtLink()
                f.drain(quiet=0.3, limit=2.0)
                if f.read_reg(0x00) is not None:
                    self.f, self.name = f, "Ft+ USB 3"
                    return
                f.close()
            except Exception:
                pass
        import serial
        self.s = serial.Serial(port, 115200, timeout=0.2)
        self.name = "%s UART" % port
        time.sleep(0.25)

    def rd(self, a):
        if self.f is not None:
            try:
                return self.f.read_reg(a)
            except Exception:
                return None
        self.s.reset_input_buffer()
        self.s.write(bytes([SYNC, OP_R, a, ck(OP_R + a)]))
        buf, dl = bytearray(), time.time() + 0.35
        while time.time() < dl:
            buf += self.s.read(self.s.in_waiting or 1)
            for i in range(len(buf) - 2):
                if buf[i] == a and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                    return buf[i + 1]
        return None

    def wr(self, a, v):
        if self.f is not None:
            self.f.write_reg(a, v)
            return
        self.s.write(bytes([SYNC, OP_W, a, v, ck(OP_W + a + v)]))
        time.sleep(0.3)

    def table(self, tgt, n=256):
        """Frame-scan for a reply whose checksum closes -- the reply shares the
        UART with ASCII telemetry, so it will not start at byte 0."""
        if self.f is not None:
            try:
                return bytes(self.f.read_table(tgt, n))
            except Exception:
                return None
        self.s.reset_input_buffer()
        self.s.write(bytes([SYNC, OP_LR, tgt, ck(OP_LR + tgt)]))
        need, buf, dl = n + 2, bytearray(), time.time() + 4.0
        while time.time() < dl:
            buf += self.s.read(self.s.in_waiting or 1)
            for i in range(len(buf) - need + 1):
                if buf[i] != tgt:
                    continue
                w = buf[i:i + need]
                if (sum(w) & 0xFF) == 0:
                    return bytes(w[1:1 + n])
        return None

    def close(self):
        (self.f or self.s).close()


def rd16(link, a):
    lo, hi = link.rd(a), link.rd(a + 1)
    return None if lo is None or hi is None else lo | (hi << 8)


def applied(link):
    b20 = link.rd(0x20)
    if b20 is None:
        return None
    p0, p1, p2 = link.rd(0x26), link.rd(0x27), link.rd(0x28)
    pk = 0 if None in (p0, p1, p2) else p0 | (p1 << 8) | ((p2 & 1) << 16)
    return dict(valid=(b20 >> 7) & 1, edid_ok=(b20 >> 6) & 1, idx=b20 & 0xF,
                hz=link.rd(0x21), w=rd16(link, 0x22), h=rd16(link, 0x24), khz=pk)


def show_applied(a, tag="offline mode now"):
    if a is None:
        print("  %s: (no reply)" % tag)
        return
    print("  %-18s idx %-2d  %sx%s @ %s Hz  pclk %d kHz   valid=%d edid_ok=%d"
          % (tag, a["idx"], a["w"], a["h"], a["hz"], a["khz"], a["valid"], a["edid_ok"]))
    if not a["edid_ok"]:
        print("       edid_ok=0 -> this is the FAILSAFE pick, not a choice made "
              "from the display's EDID.")


def est_timings(b):
    """Established-timing bitmaps, EDID bytes 35/36. The compact part of an EDID
    and the part the merge actually filters, so it is what is worth diffing."""
    B35 = [(7, "720x400@70"), (6, "720x400@88"), (5, "640x480@60"),
           (4, "640x480@67"), (3, "640x480@72"), (2, "640x480@75"),
           (1, "800x600@56"), (0, "800x600@60")]
    B36 = [(7, "800x600@72"), (6, "800x600@75"), (5, "832x624@75"),
           (4, "1024x768@87i"), (3, "1024x768@60"), (2, "1024x768@70"),
           (1, "1024x768@75"), (0, "1280x1024@75")]
    if b is None or len(b) < 38:
        return None
    out = [n for bit, n in B35 if (b[35] >> bit) & 1]
    out += [n for bit, n in B36 if (b[36] >> bit) & 1]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--uart", action="store_true", help="force the Port A UART")
    p.add_argument("--list", action="store_true", help="print the mode table and exit")
    p.add_argument("--set", metavar="MODE", help="WxH@Hz, or a bare table index")
    p.add_argument("--release", action="store_true", help="clear MODEFORCE")
    p.add_argument("--sweep", action="store_true",
                   help="step through every table mode so you can watch the display")
    p.add_argument("--dwell", type=float, default=8.0,
                   help="seconds to hold each mode during --sweep (default 8)")
    p.add_argument("--only", default=None,
                   help="--sweep subset, e.g. 2,3,10,11 (table indices)")
    p.add_argument("--dump-display", metavar="FILE")
    p.add_argument("--dump-served", metavar="FILE")
    a = p.parse_args()

    rtl = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "sources_1", "imports", "RTL")
    tbl = mode_table(rtl)

    if a.list:
        print("offline mode table (mode_table.vh):")
        for r in tbl:
            print("   %2d   %4dx%-4d @ %3d Hz   %6.3f MHz"
                  % (r["idx"], r["w"], r["h"], r["hz"], r["khz"] / 1000.0))
        return

    link = Link(a.port, a.uart)
    print("link: %s\n" % link.name)
    try:
        if a.release:
            link.wr(REG_FORCE, 0x00)
            time.sleep(2.5)
            print("MODEFORCE cleared -- the EDID's own pick is in charge again.")
            show_applied(applied(link))
            return

        if a.set:
            want = None
            if a.set.isdigit():
                want = next((r for r in tbl if r["idx"] == int(a.set)), None)
            else:
                m = re.match(r"(\d+)x(\d+)@(\d+)$", a.set.strip())
                if m:
                    w, h, hz = (int(x) for x in m.groups())
                    want = next((r for r in tbl
                                 if (r["w"], r["h"], r["hz"]) == (w, h, hz)), None)
            if want is None:
                print("no such mode: %s   (--list to see them)" % a.set)
                return
            before = applied(link)
            show_applied(before, "before")
            print("\n  setting idx %d = %dx%d@%d (%.3f MHz) ..."
                  % (want["idx"], want["w"], want["h"], want["hz"], want["khz"] / 1000.0))
            # The DRP retune fires on a CHANGE of the applied index. Forcing the
            # index it is already on would therefore be a no-op at the clock even
            # though the register took, so step via a neighbour to guarantee the
            # edge. (This is the same failure that left offline at its 108 MHz
            # power-up default when the failsafe pick matched the reset value.)
            if before and before["idx"] == want["idx"]:
                other = 0 if want["idx"] != 0 else 1
                link.wr(REG_FORCE, 0x80 | other)
                time.sleep(1.5)
            link.wr(REG_FORCE, 0x80 | want["idx"])
            time.sleep(2.5)
            got = applied(link)
            show_applied(got, "after")
            if got and got["idx"] == want["idx"] and got["khz"] == want["khz"]:
                print("\n  OK -- index and pixel clock both match the table.")
            else:
                print("\n  MISMATCH -- the fabric did not land where the table says."
                      "\n  A wrong pclk with a right index means the DRP retune did "
                      "not run.")
            return

        if a.sweep:
            # THE FPGA MUST BE OFFLINE. MODEFORCE steers the OFFLINE generator; with
            # a source connected the output follows pass-through instead and every
            # mode would look identical -- a sweep that "passes" while proving
            # nothing. pll_locked is the honest "there is a real source" bit (the
            # floating RX input self-oscillates and fools symbol_sync, so sym alone
            # is not trustworthy here).
            d6b = link.rd(0x6B)
            if d6b is not None and ((d6b >> 1) & 1):
                print("REFUSING TO SWEEP: pll_locked=1, so a source is connected and")
                print("the output is following PASS-THROUGH, not the offline generator.")
                print("Unplug the PC's HDMI first -- otherwise every mode looks the same.")
                return
            want = tbl
            if a.only:
                keep = {int(x) for x in a.only.replace(" ", "").split(",") if x != ""}
                want = [r for r in tbl if r["idx"] in keep]
            print("Stepping %d modes, %.0f s each. WATCH THE DISPLAY and note which"
                  % (len(want), a.dwell))
            print("ones fail -- the FPGA cannot see its own output, so your eyes are")
            print("the measurement here. 'applied OK' below only means the fabric")
            print("programmed the clock it intended, NOT that the display liked it.\n")
            rows = []
            prev = None
            for r in want:
                # Guarantee the index edge that triggers the DRP retune.
                if prev is not None and prev == r["idx"]:
                    link.wr(REG_FORCE, 0x80 | (0 if r["idx"] != 0 else 1))
                    time.sleep(1.2)
                link.wr(REG_FORCE, 0x80 | r["idx"])
                time.sleep(2.0)
                got = applied(link)
                ok = bool(got and got["idx"] == r["idx"] and got["khz"] == r["khz"])
                rows.append((r, got, ok))
                print("  [%2d/%2d] idx %-2d  %4dx%-4d @ %3d Hz  %7.3f MHz   applied %s"
                      % (len(rows), len(want), r["idx"], r["w"], r["h"], r["hz"],
                         r["khz"] / 1000.0, "OK" if ok else "MISMATCH"))
                prev = r["idx"]
                time.sleep(max(0.0, a.dwell - 2.0))
            link.wr(REG_FORCE, 0x00)
            time.sleep(2.0)
            print("\nMODEFORCE released; back to the EDID's own pick.")
            show_applied(applied(link))
            print("\n  idx   mode              pclk       fabric")
            for r, got, ok in rows:
                print("   %2d   %4dx%-4d @ %3d   %7.3f MHz   %s"
                      % (r["idx"], r["w"], r["h"], r["hz"], r["khz"] / 1000.0,
                         "applied OK" if ok else "MISMATCH"))
            print("\n  Now say which of those the display actually showed correctly.")
            return

        if a.dump_display or a.dump_served:
            for tgt, path, tag in ((TGT_DISPLAY, a.dump_display, "display"),
                                   (TGT_SERVED, a.dump_served, "served")):
                if not path:
                    continue
                b = link.table(tgt)
                if b is None:
                    print("  %s EDID: no reply (target 0x%02X)" % (tag, tgt))
                else:
                    open(path, "wb").write(b)
                    print("  %s EDID -> %s (%d bytes)" % (tag, path, len(b)))
            return

        # ---- default: show everything ------------------------------------
        show_applied(applied(link))
        print()
        for tgt, tag in ((TGT_DISPLAY, "DISPLAY says it supports"),
                         (TGT_SERVED, "we SERVE to the PC")):
            b = link.table(tgt)
            if b is None:
                extra = ("  (target 0x05 needs the build that adds it)"
                         if tgt == TGT_SERVED else "")
                print("  %-26s: no reply%s" % (tag, extra))
                continue
            ok = (sum(b[:128]) & 0xFF) == 0
            print("  %s:  byte35=0x%02X byte36=0x%02X  block-0 sum %s"
                  % (tag, b[35], b[36], "OK" if ok else "BAD"))
            for n in est_timings(b) or []:
                print("        %s" % n)
        print("\n  The served list is deliberately shorter: it is the display's "
              "modes\n  INTERSECTED with the pass-through clock window.")
    finally:
        link.close()


if __name__ == "__main__":
    main()

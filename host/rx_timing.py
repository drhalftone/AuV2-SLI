#!/usr/bin/env python3
"""What is the source actually sending? Read the measured pass-through timing.

    python host/rx_timing.py            # over the Ft+ (USB 3), falling back to COM6
    python host/rx_timing.py --uart     # force the Port A UART
    python host/rx_timing.py --watch    # keep polling

Registers 0x60..0x67 come from video_meas, which counts the RECOVERED timing on
the recovered pixel clock. They answer "what am I being sent".

DO NOT CONFUSE THESE WITH 0x22..0x25. Those come from mode_timing_rom and report
the OFFLINE mode that mode_select chose out of the display's EDID -- "what could
I send". The two disagree whenever the source picks a mode other than that one,
which is the normal case: with a PC sending 1280x720 the offline registers read
1280x800. This tool prints both, side by side, precisely so the difference
cannot be mistaken for a discrepancy in the measurement.

0x67 IS READ FIRST AND IS NOT ADVISORY. meas_ok = 0 means the FPGA is refusing
to answer -- the link is not decoding, or no vsync arrived inside the counter's
168 ms range -- and the other six registers read 0 rather than holding the last
good value. A stale resolution that looks live is the failure this avoids.

USB READBACK NEEDS FRAMES. The Ft+ reply path rides on the video frame stream,
so with the camera idle there are no replies at all and this falls back to the
UART. That is a limitation of the transport, not of the measurement.
"""
import argparse
import sys
import time

SYNC, OP_R = 0xA5, 0x52


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


class Uart:
    name = "COM6 UART"

    def __init__(self, port):
        import serial
        self.s = serial.Serial(port, 115200, timeout=0.2)
        self.name = "%s UART" % port
        time.sleep(0.25)

    def read_reg(self, a, window=0.6):
        self.s.reset_input_buffer()
        self.s.write(bytes([SYNC, OP_R, a, ck(OP_R + a)]))
        buf, deadline = bytearray(), time.time() + window
        while time.time() < deadline:
            buf += self.s.read(self.s.in_waiting or 1)
            for i in range(len(buf) - 2):
                if buf[i] == a and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                    return buf[i + 1]
        return None

    def close(self):
        self.s.close()


def open_link(prefer_uart, port):
    if not prefer_uart:
        try:
            sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
            from ftlink import FtLink
            f = FtLink()
            f.drain(quiet=0.3, limit=2.0)
            if f.read_reg(0x00) is not None:
                f.name = "Ft+ USB 3"
                return f
            f.close()
            print("  (Ft+ open but silent -- no frames streaming, so no replies; "
                  "falling back to the UART)")
        except Exception as e:
            print("  (Ft+ unavailable: %s -- falling back to the UART)" % e)
    return Uart(port)


def rd_n(link, addr, n):
    out = 0
    for i in range(n):
        v = link.read_reg(addr + i)
        if v is None:
            return None
        out |= v << (8 * i)
    return out


def report(link):
    st = link.read_reg(0x67)
    if st is None:
        print("  no reply from 0x67 -- is a control bitstream loaded?")
        return False
    meas_ok, vid_valid = st & 1, (st >> 1) & 1
    print("  link            : %s" % getattr(link, "name", "?"))
    print("  vid_valid       : %d  (symbol_sync & pll_locked)" % vid_valid)
    print("  meas_ok         : %d" % meas_ok)
    if not meas_ok:
        print("\n  THE FPGA IS REFUSING TO ANSWER, not defaulting. Either the HDMI")
        print("  input is not decoding, or no vsync arrived within 168 ms.")
        return False

    hact = rd_n(link, 0x60, 2)
    vact = rd_n(link, 0x62, 2)
    per = rd_n(link, 0x64, 3)
    if None in (hact, vact, per) or per == 0:
        print("  incomplete reply")
        return False
    hz = 1e8 / per
    print("\n=== INCOMING -- what the source is sending (0x60..0x67) ===")
    print("  resolution      : %d x %d active" % (hact, vact))
    print("  frame period    : %d x 10 ns = %.3f ms" % (per, per / 1e5))
    print("  frame rate      : %.3f Hz" % hz)

    ohact = rd_n(link, 0x22, 2)
    ovact = rd_n(link, 0x24, 2)
    orefr = link.read_reg(0x21)
    mode = link.read_reg(0x20)
    if None not in (ohact, ovact, orefr, mode):
        print("\n=== OFFLINE mode chosen from the display's EDID (0x20..0x25) ===")
        print("  (a DIFFERENT question -- what this FPGA would send, not what it gets)")
        print("  mode_idx %-2d      : %d x %d @ %d Hz   valid=%d edid_ok=%d"
              % (mode & 0x0F, ohact, ovact, orefr,
                 (mode >> 7) & 1, (mode >> 6) & 1))
        if (ohact, ovact) != (hact, vact):
            print("  -> differs from the input, which is normal: the source picked its")
            print("     own mode. Neither number is wrong; they answer different things.")
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uart", action="store_true", help="force the Port A UART")
    p.add_argument("--port", default="COM6")
    p.add_argument("--watch", action="store_true", help="poll until interrupted")
    a = p.parse_args()

    link = open_link(a.uart, a.port)
    try:
        while True:
            report(link)
            if not a.watch:
                break
            time.sleep(1.0)
            print("-" * 60)
    except KeyboardInterrupt:
        pass
    finally:
        link.close()


if __name__ == "__main__":
    main()

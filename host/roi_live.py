#!/usr/bin/env python3
"""Live ROI-mean scope over the Pt's USB 2.0 UART -- the projector-profiling front end.

    python host/roi_live.py                      # COM6, impulse on, stream on
    python host/roi_live.py COM6 --col 640 --row 512
    python host/roi_live.py --no-impulse         # leave the video alone, just watch
    python host/roi_live.py --log roi.csv        # also write every sample

WHAT THIS DOES
    1. forces the offline output mode to 800x600@120 (curated index 0)
    2. turns on the WHITE,K,K,K,K frame sequence            (reg 0x17 bit 7)
    3. places the 16x16 ROI                                  (regs 0x18 / 0x19)
    4. turns on the per-frame ROI line                       (reg 0x17 bit 6)
    5. plots the mean as it arrives

WHY THIS RUNS OVER USB 2.0 AT ALL. The FPGA averages the ROI in fabric and sends one
18-byte line per camera frame, so a sample costs 18 bytes instead of a 2.6 MB frame.
At 120 fps that is ~2.2 kB/s against 11.5 kB/s at 115200 -- the Ft+ does not need to
be cabled, and this whole experiment runs on the port that is already plugged in.

THE STREAM REPLACES THE STATUS LINE, IT DOES NOT JOIN IT. Both producers share one
arbiter slot in usb_link. While bit 6 is set the 0.5 s "S=... V=..." telemetry stops.
Clearing it (or --no-stream on exit, which this does automatically) brings it back.

READ npx BEFORE READING THE MEAN. It counts the pixels actually accumulated and must
be 256. An ROI placed off the sensor, past the last row, or on a column no kernel
covers still reports a perfectly plausible small mean -- npx is the only thing that
tells you the number is meaningless. This tool prints a loud warning and colours the
trace when npx != 256, because a quiet wrong number is the failure mode that costs
days.
"""
import argparse
import collections
import csv
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
REG_MODEFORCE, REG_ROICTL, REG_ROICOL8, REG_ROIROW8 = 0x14, 0x17, 0x18, 0x19
MODE_800x600_120 = 0                      # curated index, see mode_table.vh

# The trailing \r\n is REQUIRED, not decoration. Without it the fixed-width fields
# alone will match a line that is still arriving, and the same record is then read
# again once the rest of it lands -- which shows up as a frame-counter step of 0 and
# is easily mistaken for the FPGA repeating itself.
# R=mmm,ffff,nnn,b,p,tt,cc<CR><LF>  -- 26 bytes.
#   tt = transmitted top-left pixel, cc = TRIGGER ORDINAL.
# This regex had drifted: it still described the 5-field line from before tt
# was added, so it matched NOTHING and the viewer drew an empty plot rather
# than reporting an error. Requiring the CRLF is what stops a half-arrived
# line from matching on its fixed-width fields.
# R=mmm,nnn,tt,cc<CR><LF> -- 17 bytes. The fcnt, blk and phase fields are
# gone: phase was measured racing its clock crossing, and the other two
# duplicated what npx and a gap in cc already say. See roi_line.v.
LINE = re.compile(rb"R=([0-9A-F]{3}),([0-9A-F]{3}),([0-9A-F]{2}),([0-9A-F]{2})"
                  + bytes([13, 10]))


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def write_reg(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.03)


def read_reg(ser, addr, window=0.6):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    buf = bytearray()
    dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--col", type=int, default=640, help="ROI first column (rounded to /8)")
    ap.add_argument("--row", type=int, default=512, help="ROI first row (rounded to /8)")
    ap.add_argument("--depth", type=int, default=600, help="samples visible in the plot")
    ap.add_argument("--no-impulse", dest="impulse", action="store_false",
                    help="do not touch the video; just stream the ROI mean")
    ap.add_argument("--no-mode", dest="setmode", action="store_false",
                    help="leave the output mode alone (default forces 800x600@120)")
    ap.add_argument("--log", help="append every sample to this CSV")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N s (0 = forever)")
    ap.add_argument("--bars", nargs="?", type=int, const=5, metavar="N",
                    help="fold onto an N-frame cycle (default 5) and show a live bar "
                         "chart instead of the trace")
    ap.add_argument("--no-align", dest="align", action="store_false",
                    help="with --bars, leave the phases in raw fcnt order instead of "
                         "rotating the brightest to the left")
    a = ap.parse_args()

    col8, row8 = a.col // 8, a.row // 8
    if not (0 <= col8 <= 255 and 0 <= row8 <= 255):
        sys.exit("ROI out of range: col/8 and row/8 must both fit in a byte")

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.2)

    if a.setmode:
        write_reg(ser, REG_MODEFORCE, 0x80 | MODE_800x600_120)
        print(f"forced offline mode idx {MODE_800x600_120} (800x600@120)")
        time.sleep(0.4)                      # the MMCM retunes on this write

    write_reg(ser, REG_ROICOL8, col8)
    write_reg(ser, REG_ROIROW8, row8)
    # READ-MODIFY-WRITE, and remember what was there. --no-impulse means "leave the
    # video alone", so it must PRESERVE bit 7, not clear it. Writing a whole byte here
    # switched off a sequence someone else had started (wkkkk.py --on), which looks
    # exactly like the bitstream dying mid-run.
    prev_ctl = read_reg(ser, REG_ROICTL)
    if prev_ctl is None:
        prev_ctl = 0x00
    ctl = (prev_ctl | 0x80) if a.impulse else prev_ctl      # bit 7 untouched if not asked
    ctl |= 0x40                                             # bit 6: the stream is ours
    write_reg(ser, REG_ROICTL, ctl)
    print(f"ROI at column {col8*8}, row {row8*8}, 16x16 = 256 px")
    print(f"impulse (WHITE,K,K,K,K): "
          f"{'ON (set here)' if a.impulse else ('already on' if prev_ctl & 0x80 else 'off')}"
          f"   stream: ON")
    print("status telemetry is suspended while streaming\n")

    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt

    xs = collections.deque(maxlen=a.depth)
    ys = collections.deque(maxlen=a.depth)
    NP = a.bars or 0
    phase_q = [collections.deque(maxlen=max(a.depth // max(NP, 1), 20))
               for _ in range(max(NP, 1))]

    fig, ax = plt.subplots(figsize=(11, 5))
    if NP:
        bars = ax.bar(range(NP), [0] * NP, color="#c1322c", width=0.62)
        labels = [ax.text(i, 0, "", ha="center", va="bottom", fontsize=10)
                  for i in range(NP)]
        ax.set_xticks(range(NP))
        ax.set_xlabel(f"frames after the WHITE frame's vsync "
                      f"(bar 0 = triggered at it)")
        ax.axhline(1023, color="#9a6614", lw=1, ls="--")
        ax.text(NP - 0.5, 1023, " 10-bit rail", color="#9a6614", fontsize=9,
                va="bottom", ha="right")
        ln = None
    else:
        (ln,) = ax.plot([], [], lw=1.3, color="#c1322c")
        ax.set_xlabel("camera frame")
    ax.set_ylabel("ROI mean (10-bit ADU)")
    ax.set_title("ROI mean -- live")
    ax.grid(alpha=0.3, axis="y")
    ax.set_ylim(0, 1023)
    fig.canvas.manager.set_window_title("roi_live")
    plt.tight_layout()
    plt.show(block=False)

    logf = writer = None
    if a.log:
        logf = open(a.log, "a", newline="")
        writer = csv.writer(logf)
        writer.writerow(["host_time", "fcnt", "mean", "npx", "blk", "phase"])

    buf = bytearray()
    n = 0
    bad_npx = 0
    last_fcnt = None
    gaps = 0        # breaks in the frame counter -- frames genuinely missing
    lost = 0        # how many frames those breaks account for
    dups = 0        # the same frame counter seen twice -- a HOST parsing fault
    unpaired = 0    # phase 7: the frame had no trigger recorded
    t0 = time.time()
    t_draw = 0.0

    try:
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf += chunk
                consumed = 0
                for m in LINE.finditer(buf):
                    mean = int(m.group(1), 16)
                    npx = int(m.group(2), 16)
                    tlp = int(m.group(3), 16)
                    tcnt = int(m.group(4), 16)
                    # Derived, not transmitted: the tag IS {seq, position}.
                    phase = tlp & 7
                    fcnt = tcnt
                    blk = 0
                    consumed = m.end()
                    n += 1
                    if npx != 256:
                        bad_npx += 1
                    if last_fcnt is not None:
                        # Report what actually happened rather than lumping every
                        # non-1 step together: a repeat and a break are different
                        # faults with different causes, and calling a duplicate a
                        # "gap" sends you looking for dropped bytes that never were.
                        d = (fcnt - last_fcnt) & 0xFFFF
                        if d == 0:
                            dups += 1
                        elif d != 1:
                            gaps += 1
                            lost += d - 1
                    last_fcnt = fcnt
                    xs.append(fcnt)
                    ys.append(mean)
                    if NP:
                        # The FPGA's own phase, not fcnt mod N: it is the sequence
                        # phase the frame was TRIGGERED at, so bar 0 really is the
                        # frame triggered on the white frame's vsync. 7 = unpaired.
                        if phase < NP:
                            phase_q[phase].append(mean)
                        else:
                            unpaired += 1
                    if writer:
                        writer.writerow([f"{time.time()-t0:.4f}", fcnt, mean, npx,
                                         blk, phase])
                # Consume EXACTLY what was matched. Trimming on the last newline
                # instead leaves any already-matched bytes of a partial line in the
                # buffer to be matched a second time; trimming to a fixed tail is
                # worse still, because it keeps whole processed lines.
                if consumed:
                    buf = buf[consumed:]
                elif len(buf) > 4096:
                    # nothing matched in 4 kB: keep only enough for a straddling
                    # record so genuine garbage cannot grow without bound
                    buf = buf[-64:]

            now = time.time()
            if now - t_draw > 0.10 and xs:
                t_draw = now
                rate = n / max(now - t0, 1e-6)
                warn = ((f"   npx BAD x{bad_npx}" if bad_npx else "")
                        + (f"   gaps {gaps}" if gaps else "")
                        + (f"   dups {dups}" if dups else ""))
                if NP:
                    means = [ (sum(q) / len(q)) if q else 0.0 for q in phase_q ]
                    # ALIGNMENT IS INFERRED, NOT KNOWN. The FPGA holds the impulse
                    # phase; the host only sees a camera frame counter, so which bin
                    # is the white frame has to come from the data. Rotating the
                    # brightest bin to the left is a convention, not a measurement --
                    # and it CANNOT show projector latency, because latency is
                    # exactly the offset being rotated away. Use --no-align to see
                    # the raw bins.
                    rot = 0   # phase 0 IS the white frame; nothing to infer
                    vals = [means[(rot + i) % NP] for i in range(NP)]
                    cnts = [len(phase_q[(rot + i) % NP]) for i in range(NP)]
                    for b, lab, v, c in zip(bars, labels, vals, cnts):
                        b.set_height(v)
                        b.set_color("#9a6614" if v >= 1015 else "#c1322c")
                        lab.set_y(v)
                        lab.set_text(f"{v:.0f}" + ("  RAIL" if v >= 1015 else ""))
                    hi = max(vals) if vals else 1
                    ax.set_ylim(0, max(hi * 1.18, 10))
                    spread = (max(vals) - min(vals)) / max(max(vals), 1) * 100
                    ax.set_title(f"{NP}-frame fold -- {n} samples, {rate:5.1f}/s, "
                                 f"contrast {spread:.1f}%" + warn)
                else:
                    ln.set_data(range(len(ys)), list(ys))
                    ax.set_xlim(0, max(len(ys), 10))
                    lo, hi = min(ys), max(ys)
                    pad = max(8, (hi - lo) * 0.15)
                    ax.set_ylim(max(0, lo - pad), min(1023, hi + pad))
                    ax.set_title(f"ROI mean -- {n} samples, {rate:5.1f}/s, "
                                 f"last {ys[-1]}, span {lo}-{hi}" + warn)
                fig.canvas.draw_idle()
                fig.canvas.flush_events()

            if a.seconds and now - t0 > a.seconds:
                break
            if not plt.fignum_exists(fig.number):
                break
    except KeyboardInterrupt:
        pass
    finally:
        # Restore what we found: drop OUR bit (the stream) and leave the impulse
        # exactly as it was. Zeroing the byte here is what made the flashing stop
        # when this tool exited.
        write_reg(ser, REG_ROICTL, prev_ctl & 0x80)
        ser.close()
        if logf:
            logf.close()
        el = time.time() - t0
        print(f"\n{n} samples in {el:.1f} s  ({n/max(el,1e-6):.1f}/s)")
        if bad_npx:
            print(f"WARNING: {bad_npx} samples had npx != 256 -- the ROI is off the "
                  f"sensor or past the last row. Those means are meaningless.")
        if gaps:
            print(f"WARNING: {gaps} break(s) in the frame counter, {lost} frame(s) "
                  f"missing ({100.0*lost/max(n+lost,1):.2f}%) -- the plot has "
                  f"invisible holes in it.")
        if unpaired:
            print(f"NOTE: {unpaired} frame(s) reported phase 7 -- no trigger paired to "
                  f"them. Expected while genlock is settling; persistent means the "
                  f"camera is free-running, not locked.")
        if dups:
            print(f"WARNING: {dups} DUPLICATE record(s) -- the same frame counter "
                  f"arrived twice. That is a host-side parsing fault, not the FPGA "
                  f"repeating itself.")
        if n == 0:
            print("No samples. Check: is the camera board attached and out of reset, "
                  "and did the bitstream with roi_mean actually get loaded?")
        print(f"stream stopped; ROICTL restored to 0x{prev_ctl & 0x80:02X} "
              f"({'sequence still running' if prev_ctl & 0x80 else 'sequence off'}); "
              f"status telemetry back")


if __name__ == "__main__":
    main()

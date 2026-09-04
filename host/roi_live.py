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

LINE = re.compile(rb"R=([0-9A-F]{3}),([0-9A-F]{4}),([0-9A-F]{3}),([01])")


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
    ctl = (0x80 if a.impulse else 0x00) | 0x40
    write_reg(ser, REG_ROICTL, ctl)
    print(f"ROI at column {col8*8}, row {row8*8}, 16x16 = 256 px")
    print(f"impulse (WHITE,K,K,K,K): {'ON' if a.impulse else 'off'}   stream: ON")
    print("status telemetry is suspended while streaming\n")

    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt

    xs = collections.deque(maxlen=a.depth)
    ys = collections.deque(maxlen=a.depth)
    fig, ax = plt.subplots(figsize=(11, 5))
    (ln,) = ax.plot([], [], lw=1.3, color="#c1322c")
    ax.set_xlabel("camera frame")
    ax.set_ylabel("ROI mean (10-bit ADU)")
    ax.set_title("ROI mean -- live")
    ax.grid(alpha=0.3)
    ax.set_ylim(0, 1023)
    fig.canvas.manager.set_window_title("roi_live")
    plt.tight_layout()
    plt.show(block=False)

    logf = writer = None
    if a.log:
        logf = open(a.log, "a", newline="")
        writer = csv.writer(logf)
        writer.writerow(["host_time", "fcnt", "mean", "npx", "blk"])

    buf = bytearray()
    n = 0
    bad_npx = 0
    last_fcnt = None
    gaps = 0
    t0 = time.time()
    t_draw = 0.0

    try:
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf += chunk
                for m in LINE.finditer(buf):
                    mean = int(m.group(1), 16)
                    fcnt = int(m.group(2), 16)
                    npx = int(m.group(3), 16)
                    blk = int(m.group(4))
                    n += 1
                    if npx != 256:
                        bad_npx += 1
                    if last_fcnt is not None:
                        d = (fcnt - last_fcnt) & 0xFFFF
                        if d != 1:
                            gaps += 1
                    last_fcnt = fcnt
                    xs.append(fcnt)
                    ys.append(mean)
                    if writer:
                        writer.writerow([f"{time.time()-t0:.4f}", fcnt, mean, npx, blk])
                if len(buf) > 4096:
                    buf = buf[-256:]
                else:
                    cut = buf.rfind(b"\n")
                    if cut >= 0:
                        buf = buf[cut + 1:]

            now = time.time()
            if now - t_draw > 0.05 and xs:
                t_draw = now
                ln.set_data(range(len(ys)), list(ys))
                ax.set_xlim(0, max(len(ys), 10))
                lo, hi = min(ys), max(ys)
                pad = max(8, (hi - lo) * 0.15)
                ax.set_ylim(max(0, lo - pad), min(1023, hi + pad))
                rate = n / max(now - t0, 1e-6)
                ax.set_title(f"ROI mean -- {n} samples, {rate:5.1f}/s, "
                             f"last {ys[-1]}, span {lo}-{hi}"
                             + (f"   npx BAD x{bad_npx}" if bad_npx else "")
                             + (f"   gaps {gaps}" if gaps else ""))
                fig.canvas.draw_idle()
                fig.canvas.flush_events()

            if a.seconds and now - t0 > a.seconds:
                break
            if not plt.fignum_exists(fig.number):
                break
    except KeyboardInterrupt:
        pass
    finally:
        write_reg(ser, REG_ROICTL, 0x00)     # stream off, impulse off, telemetry back
        ser.close()
        if logf:
            logf.close()
        el = time.time() - t0
        print(f"\n{n} samples in {el:.1f} s  ({n/max(el,1e-6):.1f}/s)")
        if bad_npx:
            print(f"WARNING: {bad_npx} samples had npx != 256 -- the ROI is off the "
                  f"sensor or past the last row. Those means are meaningless.")
        if gaps:
            print(f"WARNING: {gaps} breaks in the frame counter -- lines were dropped "
                  f"on the UART, so the plot has invisible holes in it.")
        if n == 0:
            print("No samples. Check: is the camera board attached and out of reset, "
                  "and did the bitstream with roi_mean actually get loaded?")
        print("registers cleared; status telemetry restored")


if __name__ == "__main__":
    main()

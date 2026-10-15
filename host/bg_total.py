#!/usr/bin/env python3
"""Total light in the BACKGROUND PERIOD, one exposure per frame, live.

    python -u host/bg_total.py --profile profile.sliprof [--octet 2] [--order bayer]

TERMS. The FOREGROUND period is the exposure window set in the profiling app (the
profile's lead/trail edges). The BACKGROUND period is the rest of the frame: from the
trailing edge to the next frame's leading edge. Every instant of the frame is in one
or the other.

ONE EXPOSURE. Triggers come once per frame and an exposure may run across the vsync;
the only limit is that it ends at least the sensor gap (MAXEXP) before the next
trigger. So the whole background period is a single exposure:

    delay    = trail mod T
    exposure = T - (trail - lead)

which ends exactly where the next foreground begins.

WHICH PATTERN. The FPGA sequencer free-runs one fringe per frame (the seq_cfg
bitstream), and every reading carries the tag of the frame its trigger fell in. The
foreground of frame k shows the pattern transmitted in frame k-1, so a background
reading tagged k sits between that foreground and the next one, which shows pattern k:
it is labelled "into phase p", p being frame k's phase.
"""
import argparse
import collections
import csv
import json
import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import lag_core as lc                                                   # noqa: E402
from frame_sweep import LINE, R_ROICTL, R_EXPO_LO, R_EXPO_HI            # noqa: E402
from sli_background import edge, decode_tag, spin, R_SLICTL, R_CAMSIM   # noqa: E402

R_SEQCTL, R_ORDER, R_WIN = 0x09, (0x0A, 0x0B, 0x0C), (0x0D, 0x0E, 0x0F, 0x2B)
ORDERS = {"seq": [0, 1, 2, 3, 4, 5, 6, 7], "bayer": [0, 4, 2, 6, 1, 5, 3, 7]}
IDENTITY = 0xFAC688


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default=None)
    ap.add_argument("--profile", required=True, help="the .sliprof holding the exposure edges")
    ap.add_argument("--display", default=None)
    ap.add_argument("--octet", type=int, default=2, choices=(0, 1, 2))
    ap.add_argument("--order", default="seq", choices=sorted(ORDERS))
    ap.add_argument("--window", type=int, default=None,
                    help="fringes only in a W x W square around --center, rest black")
    ap.add_argument("--center", default="424,400")
    ap.add_argument("--rgb", default="rgb")
    ap.add_argument("--span", type=int, default=480, help="readings in the rolling trace")
    ap.add_argument("--out", default="bg_total_%s.csv" % time.strftime("%Y-%m-%d_%H%M%S"))
    a = ap.parse_args()

    ed = json.load(open(a.profile)).get("edges")
    if not ed:
        sys.exit("%s has no exposure edges -- set them in the profiling app" % a.profile)
    import serial.tools.list_ports as lp
    port = a.port or ([p.device for p in lp.comports()] + ["COM6"])[0]
    display = a.display or lc.projector_display()
    ser, T, mx = lc.preflight(port, display)
    fg = ed["trail_us"] - ed["lead_us"]
    delay = ed["trail_us"] % T
    u = int((T - fg) / lc.EXPO_UNIT_US)              # round DOWN: never into the foreground
    if u > mx:
        sys.exit("background period %.1f us is over the %.1f us exposure limit"
                 % (u * lc.EXPO_UNIT_US, mx * lc.EXPO_UNIT_US))
    e = u * lc.EXPO_UNIT_US
    order = ORDERS[a.order]
    tags = [8 * a.octet + ph for ph in order]
    print("frame %.2f us   foreground %.2f us (%.2f .. %.2f)   background %.2f us"
          % (T, fg, ed["lead_us"] % T, delay, T - fg))
    print("one exposure: delay %.2f us, %d units = %.3f us, ends %.2f us into the next frame"
          % (delay, u, e, delay + e - T))

    # ---- live plot ------------------------------------------------------------------
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    plt.ion()
    fig, (axt, axb) = plt.subplots(2, 1, figsize=(13, 7.5),
                                   gridspec_kw={"height_ratios": [2, 1.3]})
    fig.subplots_adjust(left=0.07, right=0.985, top=0.89, bottom=0.08, hspace=0.38)
    col = {ph: plt.get_cmap("turbo")(0.06 + 0.88 * ph / 7) for ph in range(8)}
    ring = collections.deque(maxlen=a.span)
    trail, = axt.plot([], [], lw=0.6, color="#b8bfc7", zorder=1)
    dots = {ph: axt.plot([], [], ls="none", marker="o", ms=4, color=col[ph],
                         label="into phase %d" % ph, zorder=2)[0] for ph in range(8)}
    axt.set_ylabel("ROI mean (ADU)")
    axt.set_xlabel("last %d frames, newest at the right" % a.span)
    axt.grid(alpha=0.3)
    axt.legend(loc="upper left", fontsize=8, ncol=8, framealpha=0.9)
    xs = list(range(8))
    bars = axb.bar(xs, [0] * 8, width=0.8, color=[col[ph] for ph in order])
    errs = axb.errorbar(xs, [0] * 8, yerr=[0] * 8, fmt="none", ecolor="#c0392b", lw=1)
    axb.set_xticks(xs)
    axb.set_xticklabels(["phase %d" % ph for ph in order])
    axb.set_xlabel("background period leading into each phase's foreground, in display "
                   "order (%s) -- running mean +- sd" % a.order)
    axb.set_ylabel("ADU")
    axb.grid(axis="y", alpha=0.3)
    hdr = fig.text(0.07, 0.985, "", va="top", family="monospace", fontsize=9,
                   linespacing=1.6)

    # ---- FPGA: exposure, delay, octet, free-run ---------------------------------------
    sw = (0x8 if "r" in a.rgb else 0) | (0x4 if "g" in a.rgb else 0) \
        | (0x2 if "b" in a.rgb else 0)
    lc.wr(ser, R_EXPO_LO, u & 0xFF)
    lc.wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    want = int(round(delay / lc.TICK_US))
    lc.set_delay(ser, want)
    lc.wait_delay(ser, want)
    lc.wr(ser, R_CAMSIM, 0x80)
    lc.wr(ser, R_SLICTL, 0xC0 | sw)                  # homes the sequencer
    time.sleep(0.2)
    lc.wr(ser, R_SLICTL, 0xE0 | sw)
    lc.wr(ser, R_ROICTL, 0x40)
    time.sleep(0.5)
    for _ in range(8 * a.octet):
        edge(ser)
        time.sleep(T / 1e6 * 1.5)
    lc.wr(ser, R_SLICTL, 0xF0 | sw)                  # + frq_hold
    ow = sum(ph << (3 * k) for k, ph in enumerate(order))
    for k, r in enumerate(R_ORDER):
        lc.wr(ser, r, (ow >> (8 * k)) & 0xFF)
    ctl = 0x01
    where = "full screen"
    if a.window:
        cx, cy = (int(v) for v in a.center.split(","))
        x0, y0 = (cx - a.window // 2) // 4, (cy - a.window // 2) // 4
        x1, y1 = x0 + -(-a.window // 4), y0 + -(-a.window // 4)
        for r, v in zip(R_WIN, (x0, x1, y0, y1)):
            lc.wr(ser, r, max(0, min(255, v)))
        ctl |= 0x02
        where = "window (%d,%d)-(%d,%d)" % (4 * x0, 4 * y0, 4 * x1, 4 * y1)
    lc.wr(ser, R_SEQCTL, ctl)
    if lc.reg(ser, R_SEQCTL) != ctl:
        raise SystemExit("SEQCTL did not take -- is the seq_cfg bitstream loaded?")
    time.sleep(0.3)

    n = [0] * 8
    s = [0.0] * 8
    q = [0.0] * 8
    hi = [0] * 8
    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["reading", "slot", "phase", "tag", "mean", "nhi", "delay_us", "expo_us",
                "T_us", "order"])
    nread = nbad = 0
    buf, prev = b"", None
    last_draw, t0 = 0.0, time.time()
    ser.timeout = 0
    ser.reset_input_buffer()
    try:
        while plt.fignum_exists(fig.number):
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                ms = list(LINE.finditer(buf))
                for m in ms:
                    mean, npx = int(m.group(1), 16), int(m.group(2), 16)
                    tag = decode_tag(int(m.group(3), 16))
                    nhi = int(m.group(5), 16) if m.group(5) else 0
                    ok = (tag in tags and prev in tags
                          and tags.index(tag) == (tags.index(prev) + 1) % 8 and npx == 256)
                    prev = tag
                    if not ok:
                        nbad += 1
                        continue
                    k = tags.index(tag)
                    nread += 1
                    n[k] += 1
                    s[k] += mean
                    q[k] += mean * mean
                    hi[k] = max(hi[k], nhi)
                    ring.append((nread, mean, order[k]))
                    w.writerow([nread, k, order[k], tag, mean, nhi, round(delay, 2),
                                round(e, 3), round(T, 2), a.order])
                if ms:
                    buf = buf[ms[-1].end():]
                elif len(buf) > 8192:
                    buf = buf[-64:]
            else:
                spin(0.0005)
            if time.time() - last_draw > 0.15 and ring:
                last_draw = time.time()
                pts = list(ring)
                trail.set_data([p[0] for p in pts], [p[1] for p in pts])
                for ph in range(8):
                    d = [p for p in pts if p[2] == ph]
                    dots[ph].set_data([p[0] for p in d], [p[1] for p in d])
                ys = [p[1] for p in pts]
                pad = max(2.0, 0.1 * (max(ys) - min(ys)))
                axt.set_xlim(pts[0][0] - 2, max(pts[-1][0], pts[0][0] + 10) + 2)
                axt.set_ylim(min(ys) - pad, max(ys) + pad)
                mu = [s[k] / n[k] if n[k] else 0.0 for k in range(8)]
                sd = [np.sqrt(max(0.0, q[k] / n[k] - mu[k] ** 2)) if n[k] > 1 else 0.0
                      for k in range(8)]
                for b, m_ in zip(bars, mu):
                    b.set_height(m_)
                errs.lines[2][0].set_segments(
                    [[(k, mu[k] - sd[k]), (k, mu[k] + sd[k])] for k in range(8)])
                axb.set_ylim(0, max(mu + [1.0]) * 1.15 + max(sd))
                fh.flush()
                hdr.set_text(
                    "BACKGROUND PERIOD, one exposure per frame   octet %d   %s   %s order\n"
                    "delay %.2f us   exposure %.3f us (%d units)   %d readings   "
                    "%d dropped   saturated px max %d   %.1f min"
                    % (a.octet, where, a.order, delay, e, u, nread, nbad, max(hi),
                       (time.time() - t0) / 60))
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
    except KeyboardInterrupt:
        print("interrupted")
    except Exception as ex:
        if "invalid command name" not in str(ex) and "application has been destroyed" not in str(ex):
            raise
    finally:
        fh.close()
        ser.timeout = 0.05
        lc.wr(ser, R_SEQCTL, 0x00)
        for k, r in enumerate(R_ORDER):
            lc.wr(ser, r, (IDENTITY >> (8 * k)) & 0xFF)
        lc.wr(ser, R_SLICTL, 0x00)
        lc.wr(ser, R_CAMSIM, 0x00)
        lc.wr(ser, R_ROICTL, 0x00)
        lc.set_delay(ser, 0)
        ser.close()
    print("wrote %s   %d readings, %d dropped" % (a.out, nread, nbad))
    for k in range(8):
        if n[k]:
            m_ = s[k] / n[k]
            print("   into phase %d: %7.2f ADU  sd %5.2f  n %d  saturated px max %d"
                  % (order[k], m_, np.sqrt(max(0.0, q[k] / n[k] - m_ ** 2)), n[k], hi[k]))


if __name__ == "__main__":
    main()

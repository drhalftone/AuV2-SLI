#!/usr/bin/env python3
"""Delay sweep over the 8 frames of one fringe octet, plotted live.

    python -u host/octet_sweep.py [COM5] --octet 2 --step 10 --expo 5

lag_sweep.py does this for the 5-frame impulse cycle; this does it for the eight
phase-shifted patterns of a real scan. The sequencer is stepped to the octet and
frozen there (as sli_background.py --octet does), so the eight patterns repeat
forever. At every genlock delay d in one frame, each camera line carries the tag of
the pattern its frame transmitted, so one delay fills all eight positions at once:
position p is plotted at t = p*T + d.

TAGS. A trigger before the first active pixel (d < --active-us) latches the PREVIOUS
frame's tag, so it is counted one position later -- the same fix as lag_core.

ONLY CONSECUTIVE FRAMES COUNT. The host advances the pattern once per frame over the
serial line, and lines are lost while it does. A reading is kept only when the line
before it carried the previous pattern's tag, so a late or doubled advance never
puts a reading on the wrong position.

--window W: THE SAME FRINGES, ONLY AROUND THE ROI. The FPGA cannot mask its own
generator, so the PC draws the frames instead and the FPGA passes HDMI through. Each
frame is pattern_gen's exact pixel values (same cosine ROM, same DDS step, same
frm*512 shift) inside a W x W square centred on --center, black everywhere else. The
PC writes the tag into the first active pixel itself, so a frame Windows shows twice
or skips is caught the same way as above.

--pc draws the whole screen that way (no window). --order bayer shows the eight phases
in the 1-D Bayer (bit-reversed) order 0 4 2 6 1 5 3 7 instead of 0..7, so consecutive
frames are never neighbouring phases; it needs the PC (the FPGA's sequencer only counts
up). Positions are then DISPLAY SLOTS, and the CSV's phase column says which phase
each slot showed.

--fpga: ALL OF THE ABOVE FROM THE FPGA (bitstream with the seq_cfg registers). The
sequencer free-runs one pattern per frame off its own vsync (SEQCTL bit 0), takes the
slot->phase order from ORDER (0x0A..0x0C), and blacks out everything outside WIN
(0x0D..0x0F, 0x2B; 4-pixel units) when --window is given (SEQCTL bit 1). The host only
steps to the octet at the start; after that it sends nothing but delay changes.
"""
import argparse
import csv
import json
import sys
import time

import math

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import lag_core as lc                                                   # noqa: E402
from frame_sweep import LINE, R_ROICTL, R_EXPO_LO, R_EXPO_HI            # noqa: E402

R_SEQCTL, R_ORDER, R_WIN = 0x09, (0x0A, 0x0B, 0x0C), (0x0D, 0x0E, 0x0F, 0x2B)
from sli_background import (edge, decode_tag, spin, R_SLICTL, R_CAMSIM,  # noqa: E402
                            N_PHASE)


def fringe_rows(F, frq, frm):
    """pattern_gen.v's value for each of F rows (orient 0), frequency frq, phase frm."""
    mc = [int(255.0 * (0.5 + 0.5 * math.cos(2 * math.pi * i / 4096)) + 0.5) for i in range(4096)]
    b = -(-F // 288)
    inc = (1 << 24) // (288 * b) * (1, 6, 36)[frq]
    return np.array([mc[((((r * inc) & 0xFFFFFF) >> 12) - frm * 512) & 4095] for r in range(F)],
                    np.uint8)


ORDERS = {"seq": [0, 1, 2, 3, 4, 5, 6, 7], "bayer": [0, 4, 2, 6, 1, 5, 3, 7]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default=None)
    ap.add_argument("--display", default=None, help=r"projector display, e.g. \\.\DISPLAY5")
    ap.add_argument("--octet", type=int, default=2, choices=(0, 1, 2))
    ap.add_argument("--step", type=float, default=10.0, help="delay step, us")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--per", type=int, default=3, help="readings per position per delay")
    ap.add_argument("--dmin", type=float, default=0.0)
    ap.add_argument("--dmax", type=float, default=None)
    ap.add_argument("--active-us", type=float, default=lc.ACTIVE_US)
    ap.add_argument("--rgb", default="rgb")
    ap.add_argument("--orient", type=int, default=0, choices=(0, 1))
    ap.add_argument("--window", type=int, default=None,
                    help="PC-drawn fringes only in a W x W square around --center, rest black")
    ap.add_argument("--pc", action="store_true", help="PC-drawn fringes over the whole screen")
    ap.add_argument("--order", default="seq", choices=sorted(ORDERS),
                    help="phase order on screen (bayer needs --fpga, --pc or --window)")
    ap.add_argument("--fpga", action="store_true",
                    help="FPGA free-running sequencer with order table and window "
                         "(needs the seq_cfg bitstream)")
    ap.add_argument("--center", default="424,400", help="x,y of the window centre (align_roi)")
    ap.add_argument("--profile", help="a .sliprof: mark its exposure edges and background delay")
    ap.add_argument("--out", default="octet_sweep_%s.csv" % time.strftime("%Y-%m-%d_%H%M%S"))
    a = ap.parse_args()

    import serial.tools.list_ports as lp
    port = a.port or ([p.device for p in lp.comports()] + ["COM6"])[0]
    display = a.display or lc.projector_display()
    ser, T, mx = lc.preflight(port, display)
    u = max(1, int(round(a.expo / lc.EXPO_UNIT_US)))
    e = u * lc.EXPO_UNIT_US
    delays = lc.delays_for(T, e, a.step, a.dmin, a.dmax)
    sw = (0x8 if "r" in a.rgb else 0) | (0x4 if "g" in a.rgb else 0) \
        | (0x2 if "b" in a.rgb else 0) | (a.orient & 1)
    order = ORDERS[a.order]
    if a.order != "seq" and not (a.pc or a.window or a.fpga):
        sys.exit("--order %s needs --fpga, --pc or --window" % a.order)
    tags = [8 * a.octet + ph for ph in order]       # tag shown in each display slot
    print("frame %.1f us   exposure %d units = %.3f us   octet %d (tags %d..%d)   "
          "%d delays, %.1f us apart" % (T, u, e, a.octet, tags[0], tags[-1], len(delays), a.step))

    # ---- live plot ------------------------------------------------------------------
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    plt.ion()
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.subplots_adjust(left=0.06, right=0.99, top=0.88, bottom=0.1)
    for k in range(9):
        ax.axvline(k * T / 1000, color="#999999", lw=0.8)
    for k in range(8):
        ax.text((k + 0.5) * T / 1000, 1.01, "phase %d" % order[k], transform=ax.get_xaxis_transform(),
                ha="center", fontsize=9)
    if a.profile:
        j = json.load(open(a.profile))
        ed, bg = j.get("edges"), j.get("bg_delay_us")
        for k in range(8):
            if ed:
                lo = ed["lead_us"] % T
                ax.axvspan((k * T + lo) / 1000, (k * T + lo + ed["trail_us"] - ed["lead_us"]) / 1000,
                           color="#2e9e4f", alpha=0.06, lw=0)
            if bg is not None:
                ax.axvline((k * T + bg % T) / 1000, color="#e08a00", lw=1, ls="--")
    line, = ax.plot([], [], ".", ms=2.5, color="#1f5fd0")
    ax.set_xlim(0, 8 * T / 1000)
    ax.set_xlabel("time from the start of the first frame in the order (ms)   "
                  "order: %s" % " ".join(map(str, order)))
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=0.25)
    hdr = fig.text(0.06, 0.975, "", va="top", family="monospace", fontsize=9)
    pts = []

    lc.wr(ser, R_EXPO_LO, u & 0xFF)
    lc.wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    scr = None
    if (a.window or a.pc) and not a.fpga:
        # ---- PC frames: fringes in a window, black elsewhere, FPGA passing through --
        from PIL import Image, ImageTk
        from screen import Screen
        if a.orient != 0:
            sys.exit("--window draws horizontal stripes (orient 0) only")
        lc.wr(ser, R_SLICTL, 0x00)                   # pass HDMI through
        lc.wr(ser, R_ROICTL, 0x40)
        scr = Screen(device=display)
        if a.window:
            cx, cy = (int(v) for v in a.center.split(","))
            x0, y0 = max(0, cx - a.window // 2), max(0, cy - a.window // 2)
            x1, y1 = min(scr.w, x0 + a.window), min(scr.h, y0 + a.window)
        else:
            x0, y0, x1, y1 = 0, 0, scr.w, scr.h
        chans = [c in a.rgb for c in "rgb"]
        imgs = []
        for k in range(8):
            buf = scr.blank()
            col = fringe_rows(scr.h, a.octet, order[k])[y0:y1]
            for ch in range(3):
                if chans[ch]:
                    buf[y0:y1, x0:x1, ch] = col[:, None]
            buf[0, 0] = (tags[k], 0, 0)              # the tag, where pixel_pipe reads it
            imgs.append(ImageTk.PhotoImage(Image.fromarray(buf, "RGB")))
        print("PC frames: fringes in (%d,%d)-(%d,%d), black elsewhere, order %s"
              % (x0, y0, x1, y1, order))
        ax.set_title(("fringes only in %dx%d px around (%d,%d), rest black"
                      % (x1 - x0, y1 - y0, cx, cy) if a.window else "PC-drawn full-screen fringes")
                     + ", %s order" % a.order, fontsize=10, pad=16)
        shown = [0]

        def advance():
            scr.label.configure(image=imgs[shown[0] % 8])
            scr.root.update()
            shown[0] += 1
        advance()
        time.sleep(0.5)
    else:
        # ---- freeze the octet -------------------------------------------------------
        lc.wr(ser, R_CAMSIM, 0x80)                   # host owns the ready line
        lc.wr(ser, R_SLICTL, 0xC0 | sw)              # homes the sequencer
        time.sleep(0.2)
        lc.wr(ser, R_SLICTL, 0xE0 | sw)              # fringes on screen
        lc.wr(ser, R_ROICTL, 0x40)                   # per-frame stream, no impulse
        time.sleep(0.5)
        for _ in range(8 * a.octet):
            edge(ser)
            time.sleep(T / 1e6 * 1.5)
        lc.wr(ser, R_SLICTL, 0xF0 | sw)              # + frq_hold
        time.sleep(0.3)

        def advance():
            edge(ser)

        if a.fpga:
            ow = sum(ph << (3 * k) for k, ph in enumerate(order))
            for k, r in enumerate(R_ORDER):
                lc.wr(ser, r, (ow >> (8 * k)) & 0xFF)
            ctl = 0x01                               # free-run
            if a.window:
                cx, cy = (int(v) for v in a.center.split(","))
                x0, y0 = (cx - a.window // 2) // 4, (cy - a.window // 2) // 4
                x1, y1 = x0 + -(-a.window // 4), y0 + -(-a.window // 4)
                for r, v in zip(R_WIN, (x0, x1, y0, y1)):
                    lc.wr(ser, r, max(0, min(255, v)))
                ctl |= 0x02
                print("FPGA window (%d,%d)-(%d,%d) px" % (4 * x0, 4 * y0, 4 * x1, 4 * y1))
                ax.set_title("FPGA fringes only in (%d,%d)-(%d,%d), rest black, %s order"
                             % (4 * x0, 4 * y0, 4 * x1, 4 * y1, a.order), fontsize=10, pad=16)
            else:
                ax.set_title("FPGA full-screen fringes, free-running, %s order" % a.order,
                             fontsize=10, pad=16)
            lc.wr(ser, R_SEQCTL, ctl)
            if lc.reg(ser, R_SEQCTL) != ctl:
                raise SystemExit("SEQCTL did not take -- is the seq_cfg bitstream loaded?")
            print("FPGA sequencer: free-run, order %s (0x%06X)" % (order, ow))

            def advance():
                pass

    t_frame = T / 1e6
    t0 = time.time()
    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["T_us", "expo_us", "delay_us", "position", "t_us", "mean", "sd", "n",
                "tag_fix_us", "phase"])
    closed = False
    try:
        for i, d in enumerate(delays):
            if not plt.fignum_exists(fig.number):
                closed = True
                break
            want = int(round(d / lc.TICK_US))
            lc.set_delay(ser, want)
            if i % 25 == 0:
                lc.wait_delay(ser, want)
            fix = d < a.active_us
            by = {p: [] for p in range(N_PHASE)}
            ser.timeout = 0
            ser.reset_input_buffer()
            buf, prev, skip = b"", None, 3           # first lines may be at the old delay
            last_adv = time.perf_counter()
            tend = time.time() + 4.0
            while min(len(v) for v in by.values()) < a.per and time.time() < tend:
                chunk = ser.read(4096)
                if chunk:
                    buf += chunk
                    ms = list(LINE.finditer(buf))
                    for m in ms:
                        mean, npx = int(m.group(1), 16), int(m.group(2), 16)
                        tag = decode_tag(int(m.group(3), 16))
                        ok = (tag in tags and prev in tags
                              and tags.index(tag) == (tags.index(prev) + 1) % 8)
                        prev = tag
                        if skip:
                            skip -= 1
                            continue
                        if ok and npx == 256:
                            by[(tags.index(tag) + fix) % 8].append(mean)
                    if ms:
                        buf = buf[ms[-1].end():]
                    elif len(buf) > 8192:
                        buf = buf[-64:]
                now = time.perf_counter()
                if scr is not None:
                    if now - last_adv >= t_frame:    # PC frames: on the frame clock
                        advance()
                        last_adv = now if now - last_adv > 2 * t_frame else last_adv + t_frame
                elif (chunk and now - last_adv >= t_frame * 0.9) or now - last_adv > t_frame * 3:
                    advance()                        # advance right after a line
                    last_adv = now
                if not chunk:
                    spin(0.0002)
            ser.timeout = 0.05
            for p in range(N_PHASE):
                if by[p]:
                    m = float(np.mean(by[p]))
                    w.writerow([round(T, 2), round(e, 3), round(d, 2), p, round(p * T + d, 2),
                                round(m, 3), round(float(np.std(by[p])), 3), len(by[p]),
                                a.active_us, order[p]])
                    pts.append(((p * T + d) / 1000, m))
            missing = [p for p in range(N_PHASE) if not by[p]]
            if missing:
                print("  delay %.1f: no readings for positions %s" % (d, missing))
            if i % 10 == 0:
                fh.flush()
            if i % 3 == 0 or i == len(delays) - 1:
                line.set_data([q[0] for q in pts], [q[1] for q in pts])
                ys = [q[1] for q in pts]
                if ys:
                    pad = max(2.0, 0.05 * (max(ys) - min(ys)))
                    ax.set_ylim(min(ys) - pad, max(ys) + pad)
                el = time.time() - t0
                hdr.set_text("octet %d   %s fringes   exposure %.3f us   step %.1f us   "
                             "delay %d/%d  %.1f us   %.1f min, ~%.1f min left"
                             % (a.octet, a.rgb, e, a.step, i + 1, len(delays), d, el / 60,
                                el / (i + 1) * (len(delays) - i - 1) / 60))
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
    except Exception as ex:
        # The projector window is a Toplevel of the plot's Tk root, so closing the
        # plot destroys it too: treat that as the window being closed, not a crash.
        if "invalid command name" not in str(ex):
            raise
        closed = True
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        fh.close()
        if scr is not None:
            scr.close()
        ser.timeout = 0.05
        if a.fpga:
            lc.wr(ser, R_SEQCTL, 0x00)
            for k, r in enumerate(R_ORDER):
                lc.wr(ser, r, (0xFAC688 >> (8 * k)) & 0xFF)
        lc.wr(ser, R_SLICTL, 0x00)
        lc.wr(ser, R_CAMSIM, 0x00)
        lc.wr(ser, R_ROICTL, 0x00)
        lc.set_delay(ser, 0)
        ser.close()
    print("wrote %s (%d readings)%s" % (a.out, len(pts), "   -- window closed early" if closed else ""))
    if not closed:
        png = a.out.rsplit(".", 1)[0] + ".png"
        fig.savefig(png, dpi=130)
        print("wrote %s" % png)
        print("plot left open -- close the window to exit")
        plt.ioff()
        plt.show()


if __name__ == "__main__":
    main()

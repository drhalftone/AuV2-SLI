#!/usr/bin/env python3
"""The whole frame at 1 us, but lit by a running SLI scan instead of a solid field.

    python -u host/sli_frame_sweep.py COM6 --rgb b --octet 2 --orient 1 --live

WHAT THIS IS FOR. fine1us_blue.csv profiled one frame period at 1 us with a SOLID
blue field: where the light comes out, and when. A scan does not project solid
fields, it projects fringes -- so does the emission profile of a scanning
projector look like the solid-field profile at all? Same axis, same exposure,
same 1 us step; the only thing changed is what is on the screen.

TREATING EIGHT PATTERNS AS ONE FIELD. At each delay the eight patterns of an
octet are cycling at the frame rate, so the reading depends on which one was up.
This averages the eight -- but by taking the mean of each pattern's OWN mean
rather than of all frames pooled, so an unequal number of frames per pattern
cannot tilt the result.

That is only honest if the eight are actually interchangeable, and there is a
measurement that says when they are: with VERTICAL stripes at 5300 us the eight
read 239.2 .. 240.9, a spread of 1.7 ADU. Rotated to horizontal the same eight
spread 41.5 ADU while their mean did not move (239.8 vs 240.0) -- fringe geometry
landing on the ROI, not the LED drive. So --orient 1 is the default here: it is
the orientation in which "pretend it is a solid field" costs almost nothing. The
per-pattern spread is recorded at every delay so the assumption is visible in the
data rather than assumed in the analysis.

LABELS ARE ROTATED BY ONE. Frames are binned by their own transmitted tag, not
the previous frame's, so no sample needs an intact neighbour and none is thrown
away -- at the cost of every pattern label being one position out. The average
over the eight, which is what this plots, is identical either way.
"""
import argparse
import collections
import csv
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from frame_sweep import (wr, rd, set_delay, wait_delay, ck, LINE,       # noqa: E402
                         SYNC, OP_W, EXPO_UNIT_US, TICK_US, LEDCOL,
                         R_ROICTL, R_EXPO_LO, R_EXPO_HI)
from sli_background import spin, edge, decode_tag, R_SLICTL, R_CAMSIM   # noqa: E402

N_PHASE = 8


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--rgb", default="b", help="primaries the fringes drive")
    ap.add_argument("--octet", type=int, default=2, choices=(0, 1, 2, 3),
                    help="which set of eight to cycle (2 = finest fringes)")
    ap.add_argument("--orient", type=int, default=1, choices=(0, 1),
                    help="1 = vertical stripes, the orientation in which the eight "
                         "patterns read alike (see the module docstring)")
    ap.add_argument("--step", type=float, default=1.0, help="delay step, us")
    ap.add_argument("--expo", type=float, default=1.0,
                    help="exposure, us -- 1.0 commands 3 units = 1.12 us, matching "
                         "the solid-field sweeps this is compared against")
    ap.add_argument("--per", type=int, default=2,
                    help="frames wanted for EACH of the eight patterns per delay")
    ap.add_argument("--verify-every", type=int, default=50, metavar="N",
                    help="read the delay register back every Nth point; the poll is "
                         "~0.1 s and would otherwise dominate the run")
    ap.add_argument("--ref", default=None,
                    help="a solid-field CSV (delay_us, mean) to draw underneath")
    ap.add_argument("--out", default="sli_frame_sweep.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--close", action="store_true",
                    help="save the PNG and exit instead of leaving the window up. "
                         "The window holds the serial port open, so colours run "
                         "back to back need this or the next one cannot open COM6.")
    a = ap.parse_args()

    sw = (0x8 if "r" in a.rgb else 0) | (0x4 if "g" in a.rgb else 0) \
       | (0x2 if "b" in a.rgb else 0) | (a.orient & 1)
    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))
    tags = list(range(8 * a.octet, 8 * a.octet + 8))
    hue = LEDCOL.get({"r": "red", "g": "green", "b": "blue"}.get(a.rgb, ""), "#444444")

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    if not (1000 < T_us < 60000) or (gl & 3) != 3:
        ser.close(); sys.exit(f"period {T_us} us / genlock 0x{gl:02X} -- not ready")

    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)
    wr(ser, R_CAMSIM, 0x80)
    wr(ser, R_SLICTL, 0xC0 | sw); time.sleep(0.2)     # home the sequencer
    wr(ser, R_SLICTL, 0xE0 | sw)
    wr(ser, R_ROICTL, 0x40); time.sleep(0.4)
    t_frame = T_us / 1e6
    for _ in range(8 * a.octet):                      # walk to the octet
        edge(ser); time.sleep(t_frame * 1.5)
    wr(ser, R_SLICTL, 0xF0 | sw); time.sleep(0.3)     # and freeze there

    npts = int(T_us / a.step) + 1
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"projecting     octet {a.octet}, tags {tags[0]}..{tags[-1]}, {a.rgb} "
          f"{'vertical' if a.orient else 'horizontal'} fringes, cycling at "
          f"{1e6/T_us:.0f} Hz")
    print(f"delay sweep    0 .. {(npts-1)*a.step:.0f} us in {a.step:.0f} us "
          f"-> {npts} points")
    print(f"per point      >= {a.per} frames for each of the 8 patterns")
    print(f"estimated      ~{npts*0.23/60:.0f} min\n")

    ref = []
    if a.ref:
        try:
            ref = [(float(r["delay_us"]), float(r["mean"]))
                   for r in csv.DictReader(open(a.ref))]
            print(f"reference      {a.ref}, {len(ref)} points\n")
        except Exception as e:
            print(f"reference {a.ref} not usable: {e}\n")

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, ax = plt.subplots(figsize=(13, 6))
        if ref:
            ax.plot([p[0] for p in ref], [p[1] for p in ref], lw=1.0,
                    color="#9aa3ac", label="solid field (reference)")
        band = [None]
        ln, = ax.plot([], [], lw=1.2, color=hue,
                      label=f"8 patterns, averaged")
        ax.set_xlim(-T_us * 0.01, T_us * 1.01)
        ax.set_ylim(0, 1100)
        ax.set_xlabel("genlock delay within the projected frame (us)")
        ax.set_ylabel("ROI mean (ADU)")
        ax.grid(alpha=0.3)
        ax.axhline(1023, color="#9a5b00", lw=1, ls="--")
        ax.legend(loc="lower right", fontsize=9)
        fig.subplots_adjust(left=0.07, right=0.985, top=0.90, bottom=0.11)
        hdr = fig.text(0.07, 0.985, "", va="top", ha="left",
                       family="monospace", fontsize=9, linespacing=1.6)
        live = (plt, fig, ax, ln, hdr, band)

    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["delay_us", "mean", "spread", "ntags"]
               + [f"p{t % N_PHASE}" for t in tags])
    fh.flush()

    xs, ys, los, his = [], [], [], []
    nskip, nlost = 0, 0
    t0 = time.time()
    try:
        for i in range(npts):
            d = i * a.step
            tick = int(round(d / TICK_US))
            set_delay(ser, tick)
            if i % max(1, a.verify_every) == 0:
                got, ok = wait_delay(ser, tick)
                if not ok:
                    print(f"  delay {d:.0f} did not land (holds {got}) -- skipped")
                    nskip += 1
                    continue

            # collect with the sequence running until every pattern has enough
            bucket = collections.defaultdict(list)
            ser.reset_input_buffer()
            buf = b""
            ser.timeout = 0
            last_adv = time.perf_counter()
            prev_tcnt = None
            deadline = time.time() + 1.4
            while time.time() < deadline:
                chunk = ser.read(4096)
                if chunk:
                    buf += chunk
                    ms = list(LINE.finditer(buf))
                    if ms:
                        for m in ms:
                            mean = int(m.group(1), 16)
                            npx = int(m.group(2), 16)
                            tag = decode_tag(int(m.group(3), 16))
                            tc = int(m.group(4), 16)
                            if prev_tcnt is not None:
                                nlost += ((tc - prev_tcnt) % 256) - 1
                            prev_tcnt = tc
                            if tag in bucket or tag in tags:
                                if npx == 256:
                                    bucket[tag].append(mean)
                        buf = buf[ms[-1].end():]
                    elif len(buf) > 8192:
                        buf = buf[-64:]
                now = time.perf_counter()
                if (chunk and now - last_adv >= t_frame * 0.9) \
                        or now - last_adv > t_frame * 3:
                    edge(ser)
                    last_adv = now
                if len(bucket) == 8 and min(len(v) for v in bucket.values()) >= a.per:
                    break
                if not chunk:
                    spin(0.0002)
            ser.timeout = 0.05
            if not bucket:
                continue
            pm = {t: sum(v) / len(v) for t, v in bucket.items()}
            vals = list(pm.values())
            point = sum(vals) / len(vals)
            xs.append(d); ys.append(point)
            los.append(min(vals)); his.append(max(vals))
            w.writerow([d, round(point, 2), round(max(vals) - min(vals), 2), len(vals)]
                       + [round(pm[t], 2) if t in pm else "" for t in tags])
            fh.flush()

            if live and (i % 12 == 0 or i == npts - 1):
                plt, fig, ax, ln, hdr, band = live
                ln.set_data(xs, ys)
                if band[0] is not None:
                    band[0].remove()
                band[0] = ax.fill_between(xs, los, his, color=hue, alpha=0.25, lw=0)
                hdr.set_text(
                    f"octet {a.octet} {a.rgb} fringes cycling at {1e6/T_us:.0f} Hz, "
                    f"exposure {expo_units*EXPO_UNIT_US:.2f} us   band = spread "
                    f"across the 8 patterns" + chr(10)
                    + f"delay {d:6.0f} us ({i+1}/{npts})   mean {point:7.2f}   "
                      f"pattern spread {max(vals)-min(vals):6.2f}   "
                      f"triggers lost {nlost}   {(time.time()-t0)/60:.1f} min")
                try:
                    if not plt.fignum_exists(fig.number):
                        raise RuntimeError
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                except Exception:
                    print("plot gone -- continuing, CSV still written")
                    live = None
            if i % 200 == 0 or i == npts - 1:
                print(f"  {i+1:5d}/{npts}  delay {d:8.0f} us   mean {point:7.2f}   "
                      f"spread {max(vals)-min(vals):6.2f}   "
                      f"{(time.time()-t0)/60:.1f} min")
    except KeyboardInterrupt:
        print("\ninterrupted -- everything captured is written")
    finally:
        wr(ser, R_SLICTL, 0x00)
        wr(ser, R_CAMSIM, 0x00)
        wr(ser, R_ROICTL, 0x80)
        set_delay(ser, 0)
        ser.close()
        fh.close()

    print(f"\nwrote {a.out}   ({len(xs)} delays)")
    if xs:
        print(f"   mean range        {min(ys):.1f} .. {max(ys):.1f}")
        print(f"   at the ceiling    {sum(1 for v in ys if v >= 1023.0)}")
        sp = [h - l for h, l in zip(his, los)]
        print(f"   pattern spread    {min(sp):.2f} .. {max(sp):.2f}  "
              f"(median {sorted(sp)[len(sp)//2]:.2f})")
        print(f"   delays not landed {nskip}")
        print(f"   triggers lost     {nlost}")
    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")
    if live and not a.close:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()
    elif live:
        live[0].close("all")
        print("window closed (--close); the PNG and CSV are on disk")


if __name__ == "__main__":
    main()

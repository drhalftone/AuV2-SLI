#!/usr/bin/env python3
"""Does the LED drive hold still across a 24-frame SLI scan at 120 Hz?

    python -u host/sli_background.py COM6 --live
    python -u host/sli_background.py COM6 --passes 300 --live

THE QUESTION. At 120 Hz the projector runs in 3D mode, which turns on its
content-adaptive LED drive. If that drive follows the projected content then
every fringe pattern in a scan is lit by a different amount of light, and the
phase-shift arithmetic -- which assumes one constant illumination term for the
whole set -- is being fed patterns that were never comparable.

THE PROBE. At a genlock delay of 8000 us the exposure lands BETWEEN sub-fields:
the mirrors are not delivering the image, so what reaches the sensor is the LED's
own output rather than the pattern. One number per projected frame, and the scan
either gives 24 equal numbers or it does not.

SCALE. The static level sweep at this same delay reads 155 ADU with everything
black and 785 with blue at full, so about 630 ADU spans the drive range and 6 ADU
is one percent of it.

THE FRAMES LABEL THEMSELVES. pixel_pipe writes {frq,fra} -- 0..23 for the three
fringe octets, 24..31 for the flash block -- into the FIRST ACTIVE PIXEL of every
frame it generates, on all three channels. The camera already reports that pixel
with every ROI mean. So a reading arrives knowing which pattern produced it, and
nothing here depends on a 115200 serial line keeping step with a 120 Hz
projector: the advances are paced at the frame rate, and if one lands late the
tag says so instead of silently rotating the whole result.

THE LIGHT LAGS THE PIXEL BY ONE FRAME. That is the display latency measured
earlier -- a dark frame commanded at position 2 emerges at position 3. So a mean
is paired with the tag of the PREVIOUS frame, not its own. It matters only at the
flash/fringe boundary: if the 24 fringe readings agree, they agree under either
alignment, and --shift 0 prints the other one for comparison.

By default the sequence free-runs through its natural 32-frame cycle, so the
flash block is present exactly as it is in a real scan; its 8 frames are reported
separately and are not in the bar chart.

    python -u host/sli_background.py COM6 --octet 2 --live

--octet loops ONE set of eight instead. The sequencer is stepped to that octet
and frozen there, so those eight patterns repeat forever and each one is preceded
by the same seven -- the whole content history is identical for every bar, which
it is not when the coarse octets and the flash block are also in the cycle.
"""
import argparse
import csv
import math
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from frame_sweep import (wr, rd, set_delay, wait_delay, ck, LINE,       # noqa: E402
                         SYNC, OP_W, EXPO_UNIT_US, TICK_US,
                         R_ROICTL, R_EXPO_LO, R_EXPO_HI)

R_SLICTL, R_CAMSIM = 0x13, 0x16
N_TAG, N_FRINGE, N_PHASE, N_FREQ = 32, 24, 8, 3
TONE = ["#9fb6d4", "#5d84b8", "#27547f"]
BLACK_FLOOR = 155.0          # all-black baseline at this delay, measured
DRIVE_SPAN = 630.0           # 155 -> 785 (blue at 255): the full drive range


def spin(dt):
    """Busy-wait dt seconds.

    NOT time.sleep(). This paces a 120 Hz sequence, and Windows' sleep quantises
    to the system timer tick -- a requested 0.5 ms can return 15 ms later. The
    first run of this tool advanced every ~15 ms instead of every 8.3 ms and held
    each pattern for 1.75 frames; the tags made that visible rather than letting
    it pass as data.
    """
    end = time.perf_counter() + dt
    while time.perf_counter() < end:
        pass


def edge(ser):
    """One frame advance: a clean low->high->low on the host-owned ready line.

    pixel_pipe de-glitches rdy over 10000 pixel clocks (135-250 us depending on
    the output timing) and takes ONE advance per rising edge, 1-deep. A 5-byte
    command is 434 us on the wire, and the spin adds more, so the high level is
    believed well before it is taken away.
    """
    ser.write(bytes([SYNC, OP_W, R_CAMSIM, 0x81, ck(OP_W + R_CAMSIM + 0x81)]))
    spin(0.0006)
    ser.write(bytes([SYNC, OP_W, R_CAMSIM, 0x80, ck(OP_W + R_CAMSIM + 0x80)]))


def decode_tag(tlp):
    """A valid tag is three equal bytes below 32. Anything else is not a tag."""
    r, g, b = (tlp >> 16) & 0xFF, (tlp >> 8) & 0xFF, tlp & 0xFF
    return r if (r == g == b and r < N_TAG) else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--delay", type=float, default=8000.0,
                    help="genlock delay, us -- must land between sub-fields")
    ap.add_argument("--expo", type=float, default=5.0,
                    help="exposure, us. 5 matches the static level sweep this is "
                         "calibrated against.")
    ap.add_argument("--passes", type=int, default=150,
                    help="complete 32-frame cycles to fold together (0 = until "
                         "the plot window is closed)")
    ap.add_argument("--shift", type=int, default=1, choices=(0, 1),
                    help="frames of display latency between the transmitted tag "
                         "and the light it produces")
    ap.add_argument("--octet", type=int, default=None, choices=(0, 1, 2, 3),
                    help="loop ONE octet of eight patterns instead of walking the "
                         "whole 32-frame cycle: 0-2 are the three fringe "
                         "frequencies, 3 the flash block. The sequencer is stepped "
                         "to that octet and then frozen there (SLICTL bit 4), so "
                         "the eight patterns repeat forever and every pattern in "
                         "the set sees the same history.")
    ap.add_argument("--orient", type=int, default=0, choices=(0, 1),
                    help="fringe orientation: 0 horizontal stripes, 1 vertical")
    ap.add_argument("--rgb", default="rgb",
                    help="which primaries the fringes drive (default all three, "
                         "i.e. the white fringes a real scan projects)")
    ap.add_argument("--out", default="sli_background.csv")
    ap.add_argument("--raw", default=None, help="also write every frame")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--scope", action="store_true",
                    help="show every reading as it arrives instead of a running "
                         "average: a rolling trace of the last --span frames, and "
                         "bars holding the LATEST value for each pattern. Averaging "
                         "hides exactly what this shows -- whether a pattern sits "
                         "where it sits every time, or wanders.")
    ap.add_argument("--span", type=int, default=480,
                    help="frames visible in the rolling trace (4 s at 120 Hz)")
    a = ap.parse_args()

    sw = (0x8 if "r" in a.rgb else 0) | (0x4 if "g" in a.rgb else 0) \
       | (0x2 if "b" in a.rgb else 0) | (a.orient & 1)
    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))
    tick = int(round(a.delay / TICK_US))

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    if not (1000 < T_us < 60000) or (gl & 3) != 3:
        ser.close(); sys.exit(f"period {T_us} us / genlock 0x{gl:02X} -- not ready")
    if a.delay >= T_us:
        ser.close(); sys.exit(f"delay {a.delay} us is past the {T_us:.0f} us frame")

    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)
    set_delay(ser, tick)
    got, ok = wait_delay(ser, tick)
    if not ok:
        ser.close(); sys.exit(f"delay did not land: asked {tick} ticks, holds {got}")

    wr(ser, R_CAMSIM, 0x80)                 # host owns the ready line, held low
    wr(ser, R_SLICTL, 0xC0 | sw)            # sw_en + mode_en, mode_val = 0 ...
    time.sleep(0.2)                         # ... which homes the sequencer
    wr(ser, R_SLICTL, 0xE0 | sw)            # mode_val = 1: fringes on screen
    wr(ser, R_ROICTL, 0x40)                 # imp_en OFF, ROI stream ON
    time.sleep(0.5)

    t_frame = T_us / 1e6
    # WALK TO THE OCTET, THEN FREEZE. The sequencer homes to frq=0,fra=0, so
    # 8*octet advances land on that octet's first pattern; SLICTL bit 4 then stops
    # the wrap from carrying frq forward. Stepped slower than a frame here because
    # the pending advance is 1-deep and two edges inside one frame lose one.
    if a.octet is not None:
        for _ in range(8 * a.octet):
            edge(ser)
            time.sleep(t_frame * 1.5)
        wr(ser, R_SLICTL, 0xF0 | sw)        # + frq_hold
        time.sleep(0.3)
    tags = list(range(N_FRINGE)) if a.octet is None \
        else list(range(8 * a.octet, 8 * a.octet + 8))
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"delay          {a.delay:.0f} us   (register holds {got} ticks)")
    print(f"fringes        sw=0x{sw:X}  ({a.rgb}, "
          f"{'horizontal' if a.orient == 0 else 'vertical'} stripes)")
    print(f"advancing      one pattern per frame, {1e6/T_us:.1f} Hz")
    if a.octet is None:
        print(f"patterns       the whole 32-frame cycle, tags 0..31")
    else:
        print(f"patterns       octet {a.octet} ONLY, tags {tags[0]}..{tags[-1]}, "
              f"frozen and repeating")
    print(f"target         {a.passes} cycles"
          f"  (~{a.passes*N_TAG*t_frame:.0f} s)" if a.passes else "until closed")
    print()

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        xs = [i + (i // N_PHASE) * 0.9 for i in range(len(tags))]
        if a.scope:
            import collections as _c
            fig, (axt, axb) = plt.subplots(2, 1, figsize=(13, 7.5),
                                           gridspec_kw={"height_ratios": [2, 1]})
            hue = plt.get_cmap("turbo")
            col = {t: hue(0.06 + 0.88 * i / max(1, len(tags) - 1))
                   for i, t in enumerate(tags)}
            ring = _c.deque(maxlen=a.span)
            trail, = axt.plot([], [], lw=0.6, color="#b8bfc7", zorder=1)
            dots = {t: axt.plot([], [], ls="none", marker="o", ms=4,
                                color=col[t], label=f"phase {t % N_PHASE}",
                                zorder=2)[0] for t in tags}
            axt.set_ylabel("ROI mean (ADU)")
            axt.set_xlabel(f"last {a.span} frames, newest at the right")
            axt.grid(alpha=0.3)
            axt.legend(loc="upper left", fontsize=8, ncol=8, framealpha=0.9)
            bars = axb.bar(xs, [0] * len(tags), width=0.82,
                           color=[col[t] for t in tags])
            axb.set_ylim(0, 1024)
            axb.set_xlim(min(xs) - 1, max(xs) + 1)
            axb.set_xticks(xs)
            axb.set_xticklabels([str(t % N_PHASE) for t in tags], fontsize=9)
            axb.set_xlabel("phase step -- MOST RECENT reading, not an average")
            axb.set_ylabel("ADU")
            axb.grid(axis="y", alpha=0.3)
            axb.axhline(BLACK_FLOOR, color="#444444", lw=1, ls="--")
            fig.subplots_adjust(left=0.07, right=0.985, top=0.90, bottom=0.09,
                                hspace=0.32)
            hdr = fig.text(0.07, 0.985, "", va="top", ha="left",
                           family="monospace", fontsize=9, linespacing=1.6)
            live = (plt, fig, axt, axb, hdr, ring, dots, trail, bars, col)
        else:
            fig, ax = plt.subplots(figsize=(13, 6))
            fig.subplots_adjust(left=0.07, right=0.985, top=0.90, bottom=0.11)
            hdr = fig.text(0.07, 0.985, "", va="top", ha="left",
                           family="monospace", fontsize=9, linespacing=1.6)
            live = (plt, fig, ax, xs, hdr)

    fh_raw = w_raw = None
    if a.raw:
        fh_raw = open(a.raw, "w", newline="")
        w_raw = csv.writer(fh_raw)
        w_raw.writerow(["frame", "tag_tx", "tag_light", "mean", "npx", "tcnt"])

    n = [0] * N_TAG
    s = [0.0] * N_TAG
    q = [0.0] * N_TAG
    prev_tag, nframes, nbadpx, nbadtag, cycles = None, 0, 0, 0, 0
    prev_lit, prev_tcnt, nlost, nlag = None, None, 0, 0
    buf = b""
    ser.timeout = 0
    ser.reset_input_buffer()
    last_adv = time.perf_counter()
    last_draw = 0.0
    t0 = time.time()

    try:
        while True:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                ms = list(LINE.finditer(buf))
                if ms:
                    for m in ms:
                        mean = int(m.group(1), 16)
                        npx = int(m.group(2), 16)
                        tlp = int(m.group(3), 16)
                        tcnt = int(m.group(4), 16)
                        nframes += 1
                        tag = decode_tag(tlp)
                        # THE ORDINAL SAYS WHETHER THE PREVIOUS LINE WAS THE
                        # PREVIOUS FRAME. It is not, whenever a line was lost --
                        # and lines ARE lost while this tool writes: usb_link's
                        # UART arbiter switches owner between BYTES, so the ACK
                        # for an advance command lands inside an R=... line and
                        # the line fails to match. Measured 80% loss at one
                        # advance per frame. Surviving lines are intact, but a
                        # shift-1 label taken across a gap names the wrong
                        # pattern, so those samples are dropped rather than
                        # averaged in.
                        consec = (prev_tcnt is not None
                                  and (tcnt - prev_tcnt) % 256 == 1)
                        if prev_tcnt is not None:
                            nlost += ((tcnt - prev_tcnt) % 256) - 1
                        prev_tcnt = tcnt
                        if tag is None:
                            nbadtag += 1
                            prev_tag = None
                            continue
                        lit = prev_tag if a.shift else tag
                        prev_tag = tag
                        if a.shift and not consec:
                            nlag += 1
                            continue
                        if lit is None:
                            continue
                        # a wrap to tag 0 completes one cycle of the sequence
                        if (prev_lit is not None and lit == tags[0]
                                and prev_lit != tags[0]):
                            cycles += 1
                        prev_lit = lit
                        if npx != 256:
                            nbadpx += 1
                            continue
                        n[lit] += 1
                        s[lit] += mean
                        q[lit] += mean * mean
                        if live and a.scope:
                            live[5].append((nframes, mean, lit))
                        if w_raw:
                            w_raw.writerow([nframes, tag, lit, mean, npx, tcnt])
                    buf = buf[ms[-1].end():]
                elif len(buf) > 8192:
                    buf = buf[-64:]

            # ADVANCE RIGHT AFTER A LINE, NOT ON A FREE-RUNNING CLOCK. The ACK
            # for each advance command can seize the UART mid-line and destroy
            # it (usb_link's arbiter switches owner between BYTES). Whether it
            # does depends on where the line sits in the frame, which follows
            # the genlock delay: 0.5 % of frames lost at 875 us, 80 % at 8000.
            # Issuing the writes immediately after a line has arrived puts them
            # in the gap the line just vacated, and one line per frame still
            # means one advance per frame.
            now = time.perf_counter()
            if (chunk and now - last_adv >= t_frame * 0.9)                     or now - last_adv > t_frame * 3:
                edge(ser)
                last_adv = now
            if not chunk:
                spin(0.0002)     # see spin(): sleeping here would miss the frame

            if a.passes and cycles >= a.passes:
                break

            if live and a.scope and time.time() - last_draw > 0.12:
                last_draw = time.time()
                plt, fig, axt, axb, hdr, ring, dots, trail, bars, col = live
                if ring:
                    pts = list(ring)
                    trail.set_data([p[0] for p in pts], [p[1] for p in pts])
                    for t in tags:
                        d = [p for p in pts if p[2] == t]
                        dots[t].set_data([p[0] for p in d], [p[1] for p in d])
                    ys = [p[1] for p in pts]
                    lo, hi = min(ys), max(ys)
                    pad = max(2.0, (hi - lo) * 0.15)
                    axt.set_xlim(pts[0][0] - 2, max(pts[-1][0], pts[0][0] + 10) + 2)
                    axt.set_ylim(lo - pad, hi + pad)
                    latest = {}
                    for f, m, t in pts:
                        latest[t] = m
                    for b, t in zip(bars, tags):
                        b.set_height(latest.get(t, 0))
                    hdr.set_text(
                        f"LIVE, not averaged   octet {a.octet}   {a.rgb} fringes   "
                        f"delay {a.delay:.0f} us   exposure "
                        f"{expo_units*EXPO_UNIT_US:.2f} us" + chr(10)
                        + f"visible window {lo:7.2f} .. {hi:7.2f} ADU  "
                          f"(swing {hi-lo:6.2f})   {nframes} frames   "
                          f"{cycles} cycles   lost {nlost}   "
                          f"{(time.time()-t0)/60:.1f} min")
                try:
                    if not plt.fignum_exists(fig.number):
                        break
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                except Exception:
                    print("plot gone -- continuing, CSV still written")
                    live = None
            elif live and not a.scope and time.time() - last_draw > 0.4:
                last_draw = time.time()
                plt, fig, ax, xs, hdr = live
                mean = [s[i] / n[i] if n[i] else 0.0 for i in range(N_TAG)]
                sd = [math.sqrt(max(0.0, q[i] / n[i] - (s[i] / n[i]) ** 2))
                      if n[i] > 1 else 0.0 for i in range(N_TAG)]
                fr = [mean[t] for t in tags if n[t]]
                ax.clear()
                ax.bar(xs, [mean[t] for t in tags], width=0.82,
                       yerr=[sd[t] for t in tags], capsize=2,
                       color=[TONE[(t // N_PHASE) % len(TONE)] for t in tags],
                       error_kw={"ecolor": "#c0392b", "lw": 1})
                ax.set_ylim(0, 1024)
                ax.set_xlim(min(xs) - 1, max(xs) + 1)
                ax.set_xticks(xs)
                ax.set_xticklabels([str(t % N_PHASE) for t in tags], fontsize=9)
                ax.set_xlabel("phase step within each fringe frequency")
                ax.set_ylabel("ROI mean at 8000 us delay (ADU)  --  LED drive proxy")
                ax.grid(axis="y", alpha=0.3)
                ax.axhline(BLACK_FLOOR, color="#444444", lw=1, ls="--")
                ax.text(min(xs) - 0.85, BLACK_FLOOR + 14, "all-black floor 155",
                        fontsize=9, color="#444444")
                for k in range(len(tags) // N_PHASE):
                    lbl = ("flash block" if tags[k*N_PHASE] >= N_FRINGE
                           else f"frequency {tags[k*N_PHASE] // N_PHASE}")
                    ax.text(k * (N_PHASE + 0.9) + (N_PHASE - 1) / 2.0, 980, lbl,
                            ha="center", fontsize=10,
                            color=TONE[(tags[k*N_PHASE] // N_PHASE) % len(TONE)])
                if fr:
                    ax.axhline(sum(fr) / len(fr), color="#c0392b", lw=1, ls=":")
                    hdr.set_text(
                        f"{len(tags)}-frame SLI scan at {1e6/T_us:.0f} Hz, LED drive probed "
                        f"at {a.delay:.0f} us delay, {expo_units*EXPO_UNIT_US:.2f} "
                        f"us exposure" + chr(10)
                        + f"{cycles} cycles   {nframes} frames   mean "
                          f"{sum(fr)/len(fr):7.2f}   spread {max(fr)-min(fr):6.2f} ADU"
                          f" = {100*(max(fr)-min(fr))/DRIVE_SPAN:5.2f} % of the drive"
                          f" range   {(time.time()-t0)/60:.1f} min")
                try:
                    if not plt.fignum_exists(fig.number):
                        break
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                except Exception:
                    print("plot gone -- continuing, CSV still written")
                    live = None
    except KeyboardInterrupt:
        print("\ninterrupted -- everything captured is written")
    finally:
        ser.timeout = 0.05
        wr(ser, R_SLICTL, 0x00)          # release the switches and the mode pin
        wr(ser, R_CAMSIM, 0x00)          # give the ready line back to the pin
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()
        if fh_raw:
            fh_raw.close()

    mean = [s[i] / n[i] if n[i] else float("nan") for i in range(N_TAG)]
    sd = [math.sqrt(max(0.0, q[i] / n[i] - (s[i] / n[i]) ** 2)) if n[i] > 1 else 0.0
          for i in range(N_TAG)]
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tag", "kind", "freq", "phase", "n", "mean", "sd"])
        for i in range(N_TAG):
            kind = "fringe" if i < N_FRINGE else "flash"
            w.writerow([i, kind, i // N_PHASE if i < N_FRINGE else "",
                        i % N_PHASE, n[i],
                        "" if n[i] == 0 else round(mean[i], 3), round(sd[i], 3)])
    print(f"\nwrote {a.out}")
    print(f"   cycles folded        {cycles}")
    print(f"   frames seen          {nframes}")
    print(f"   frames npx != 256    {nbadpx}")
    print(f"   frames with no tag   {nbadtag}")
    print(f"   triggers never seen  {nlost}   "
          f"({100.0*nlost/max(1, nlost+nframes):.1f} % of the stream, lost to "
          f"ACK bytes landing inside a line)")
    if a.shift:
        print(f"   dropped, gap before  {nlag}   "
              f"(shift 1 needs the previous frame; --shift 0 needs no neighbour "
              f"and only rotates the labels by one position)")
    lit = [t for t in tags if n[t]]
    if not lit:
        sys.exit("no tagged frames in the set -- is the pattern generator running?")
    if len(lit) < len(tags):
        print(f"   *** only {len(lit)} of {len(tags)} positions were seen ***")
    stray = [i for i in range(N_TAG) if n[i] and i not in tags]
    if stray:
        print(f"   *** frames arrived tagged {stray} -- outside the set asked for; "
              f"the freeze did not hold ***")
    fr = [mean[i] for i in lit]
    avg = sum(fr) / len(fr)
    print(f"   repeats per position {min(n[i] for i in lit)}"
          f" .. {max(n[i] for i in lit)}")
    print(f"   mean across the set  {avg:.2f} ADU")
    print(f"   spread               {max(fr)-min(fr):.2f} ADU  "
          f"({100*(max(fr)-min(fr))/DRIVE_SPAN:.2f} % of the drive range, "
          f"{100*(max(fr)-min(fr))/avg:.2f} % of the reading)")
    for k in range(len(tags) // N_PHASE):
        seg = [mean[t] for t in tags[k*N_PHASE:(k+1)*N_PHASE] if n[t]]
        if seg:
            print(f"   octet {tags[k*N_PHASE] // N_PHASE}: {min(seg):7.2f} .. "
                  f"{max(seg):7.2f}   spread {max(seg)-min(seg):6.2f}")
    fl = [mean[i] for i in range(N_FRINGE, N_TAG) if n[i] and i not in tags]
    if fl:
        print(f"   flash block (24-31): {min(fl):7.2f} .. {max(fl):7.2f}"
              f"   -- not in the chart, shown for context")

    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")
    if live:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()


if __name__ == "__main__":
    main()

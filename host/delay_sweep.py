#!/usr/bin/env python3
"""Sweep the genlock delay and record ROI intensity across the whole 5-frame sequence.

    python host/delay_sweep.py                       # white, 50 us, full 5 frames
    python host/delay_sweep.py --colour red
    python host/delay_sweep.py --step 10 --frames 120   # finer, slower
    python host/delay_sweep.py --plot sweep.png

WHAT IT MEASURES. The exposure is pinned at one step width and walked across the
projected timeline; each point is the light collected in its own window. Because the
exposure EQUALS the step, the windows are contiguous and non-overlapping -- they tile
the sequence exactly once, with no gaps and no double counting, so each point is the
true energy in its own bin.

WHY IT ONLY SWEEPS ONE FRAME PERIOD. Genlock is 1:1 and every sample carries the
sequence phase it was TRIGGERED at, so a delay d on a phase-p frame samples absolute
time p*T + d. Sweeping d over one frame period and binning by phase therefore covers
all five frames -- 167 steps instead of 833, for the same coverage and five times the
speed. The redundancy is not lost either: --overlap sweeps past T so the same absolute
time is reached by two different (phase, delay) pairs, which is a direct check that
the phase model is right rather than an assumption.

THE TIME AXIS IS NOT THE FRAME START. Genlock fires on the RISING edge of ext_sync.
At a negative-vsync mode (800x600@120 is VPOL=0) that is the END of the vsync pulse,
so delay 0 already sits ~78.6 us into the frame. The offset is constant and is
reported in the header; subtract it before quoting a latency.

READ npx. Every sample must show 256 accumulated pixels or the mean means nothing.
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
R_MODEFORCE, R_ROICTL, R_EXPO_LO, R_EXPO_HI = 0x14, 0x17, 0x1A, 0x1B
R_DLY0, R_DLY1, R_DLY2, R_IMPRGB, R_IMPLVL = 0x1C, 0x1D, 0x1E, 0x1F, 0x12
R_IMPCYC = 0x11
COLOURS = {"white": 0x07, "red": 0x04, "green": 0x02, "blue": 0x01}
# 23-byte line: R=mmm,ffff,nnn,b,p,tt<CR><LF>. The trailing CRLF is REQUIRED in the
# pattern -- without it a line still arriving matches on its fixed-width fields and is
# read again once the rest lands, which shows up as a frame counter that does not
# advance and looks exactly like the FPGA repeating itself.
LINE = re.compile(rb"R=([0-9A-F]{3}),([0-9A-F]{4}),([0-9A-F]{3}),([01]),([0-9A-F]),([0-9A-F]{2})\r\n")

EXPO_UNIT_US = 0.375        # exposure register LSB
TICK_US = 0.01              # genlock delay register LSB (10 ns)


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.02)


def rd(ser, addr, window=0.6):
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


def set_delay(ser, ticks):
    wr(ser, R_DLY0, ticks & 0xFF)
    wr(ser, R_DLY1, (ticks >> 8) & 0xFF)
    wr(ser, R_DLY2, (ticks >> 16) & 0xFF)     # high byte commits


def collect(ser, nframes, timeout=4.0):
    """Read until nframes complete records arrive. Returns [(mean,npx,phase,tlp)]."""
    out = []
    buf = bytearray()
    dl = time.time() + timeout
    while len(out) < nframes and time.time() < dl:
        chunk = ser.read(ser.in_waiting or 1)
        if not chunk:
            continue
        buf += chunk
        consumed = 0
        for m in LINE.finditer(buf):
            out.append((int(m.group(1), 16), int(m.group(3), 16),
                        int(m.group(5), 16), int(m.group(6), 16)))
            consumed = m.end()
        if consumed:
            buf = buf[consumed:]
        elif len(buf) > 4096:
            buf = buf[-64:]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--step", type=float, default=50.0, help="delay step, us (default 50)")
    ap.add_argument("--expo", type=float, default=None,
                    help="exposure, us (default: equal to --step, so bins tile exactly)")
    ap.add_argument("--colour", "--color", dest="colour", default="white",
                    choices=sorted(COLOURS))
    ap.add_argument("--level", type=int, default=255)
    ap.add_argument("--cycle", type=int, default=None, metavar="N",
                    help="frames per sequence (2..7). 2 = BRIGHT,K -- sweeps ~6x "
                         "faster than 5, because the span is N*T and the per-cycle "
                         "trigger rate is fps/N.")
    ap.add_argument("--frames", type=int, default=60,
                    help="frames captured per delay point (spread over 5 phases)")
    ap.add_argument("--settle", type=int, default=8,
                    help="frames discarded after each delay change")
    ap.add_argument("--overlap", type=float, default=0.0,
                    help="sweep this many EXTRA us past one frame period, so the same "
                         "absolute time is reached twice -- a check on the phase model")
    ap.add_argument("--out", default="delay_sweep.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--percycle", action="store_true",
                    help="trigger ONCE per 5-frame sequence, on the bright frame, and "
                         "sweep the delay across all five frames (ROICTL bit 5). No "
                         "phase binning, no trigger/frame pairing -- one reference edge.")
    ap.add_argument("--endtoend", action="store_true",
                    help="live plot: lay the frames END TO END on one absolute time "
                         "axis (phase*T + delay) instead of overlaying them on 0..T")
    ap.add_argument("--live", action="store_true",
                    help="plot the five per-frame curves as they stream in")
    a = ap.parse_args()

    expo_us = a.expo if a.expo is not None else a.step
    expo_units = max(1, int(round(expo_us / EXPO_UNIT_US)))

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)

    per_ticks = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
                 | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per_ticks * TICK_US
    if not (1000 < T_us < 60000):
        ser.close(); sys.exit(f"implausible frame period {T_us} us -- is video running?")
    gl = rd(ser, 0x5D) or 0
    if not (gl & 1):
        ser.close(); sys.exit("genlock is not enabled (0x5D bit0 = 0)")

    if a.cycle is not None:
        if not (2 <= a.cycle <= 7):
            ser.close(); sys.exit('--cycle must be 2..7')
        wr(ser, R_IMPCYC, a.cycle)
        time.sleep(0.2)
    NCYC = rd(ser, R_IMPCYC)
    if NCYC is None or not (2 <= NCYC <= 7):
        NCYC = 5   # bitstream predates the register
    wr(ser, R_IMPRGB, COLOURS[a.colour])
    wr(ser, R_IMPLVL, a.level & 0xFF)
    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)      # commits
    wr(ser, R_ROICTL, 0xE0 if a.percycle else 0xC0)    # impulse + stream (+div5)
    time.sleep(0.3)

    if a.percycle:
        # One trigger per sequence: the delay itself must cover all five frames,
        # because there is no longer a phase bin to fold them into.
        span_us = NCYC * T_us + a.overlap
    else:
        span_us = T_us + a.overlap
    npts = int(span_us / a.step) + 1
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"delay sweep    0 .. {span_us:.0f} us in {a.step:.0f} us  ->  {npts} points")
    if a.percycle:
        print(f"sequence       {NCYC} frames per cycle")
        print(f"trigger        ONCE per sequence, on the bright frame "
              f"({1e6/(NCYC*T_us):.1f} Hz)")
        print(f"coverage       {npts} bins across {NCYC*T_us/1000:.1f} ms, "
              f"all from ONE reference edge")
    else:
        print(f"phase binning  x{NCYC}  ->  {npts*NCYC} bins covering {NCYC*T_us/1000:.1f} ms")
    print(f"flash          {a.colour} at level {a.level}")
    print(f"estimated      {npts*(a.frames+a.settle)/ (1e6/T_us) / 60:.1f} min\n")

    rows = []
    bad_npx = 0
    t0 = time.time()
    # THE FRAME INDEX IS COUNTED, NOT INFERRED.
    #
    # Two earlier schemes are gone from here, and both failed the same way. Each read
    # imp_phase -- a free-running counter latched at the sensor's frame_start -- and
    # tried to rotate it so the bright frame sat at 0. But frame_start is trigger +
    # exposure + readout latency, so it crosses a projected-frame boundary at
    # delay = T - exposure - L0 (MEASURED: 7200 us at 4.9 us exposure, 6800 at 500 us,
    # 5700 at 1500 us). Around that delay the latched phase flickers between two frames
    # and every bin becomes a mixture; per-delay rotation then swung whole traces by a
    # frame period, and a global rotation mislabelled everything past the crossing.
    #
    # Counting from the tlp marker sidesteps all of it: the frames arrive in order, so
    # frame 0 is the line carrying 0xFF and the rest follow. Nothing to anchor, nothing
    # to vote on, and a slip costs at most one cycle instead of the whole run.

    slips = 0

    def rot(fidx, _d=None):
        """Identity. The index is COUNTED from the tlp marker as the lines arrive, so
        frame 0 is already the transmitted bright frame. Kept as a function only so the
        call sites below read the same as before."""
        return fidx

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        cols = ["#c1322c", "#b5761a", "#1d7a44", "#1f5fb0", "#7a3fa0"]
        fig, ax = plt.subplots(figsize=(13, 6))
        if a.percycle:
            lines = [ax.plot([], [], lw=1.0, color=cols[0],
                             label="one trigger per sequence")[0]]
            for k in range(1, NCYC):
                ax.axvline(k * T_us, color="#8892a0", lw=1, ls="--")
                ax.text(k * T_us + 80, 1040, f"frame {k}", fontsize=8,
                        color="#5c6672", va="top")
        else:
            lines = [ax.plot([], [], lw=1.2, color=cols[k],
                             label=f"frame {k}" + ("  (bright)" if k == 0 else ""))[0]
                     for k in range(5)]
            if a.endtoend:
                # END TO END. Each phase is drawn at phase*T + delay, so the five
                # frames tile ONE absolute timeline instead of being stacked on the
                # same 0..T axis. Same data, same colours -- only the x offset moves.
                # It is the more honest view when the light crosses a frame boundary:
                # overlaid, a run that ends in frame 3 and continues in frame 4 looks
                # like two unrelated features at opposite ends of the axis.
                for k in range(1, NCYC):
                    ax.axvline(k * T_us, color="#8892a0", lw=1, ls="--")
                    ax.text(k * T_us + 80, 1040, f"frame {k}", fontsize=8,
                            color="#5c6672", va="top")
        ax.axhline(1023, color="#9a6614", lw=1, ls="--")
        ax.text(T_us, 1023, " 10-bit rail", color="#9a6614", fontsize=9,
                ha="right", va="bottom")
        ax.set_xlim(0, (NCYC * T_us + a.overlap) if (a.endtoend and not a.percycle)
                    else span_us)
        ax.set_ylim(0, 1060)
        ax.set_xlabel("time after the bright frame's genlock trigger (us)"
                      if (a.endtoend or a.percycle)
                      else "genlock delay after that frame's own vsync (us)")
        ax.set_ylabel("ROI mean (10-bit ADU)")
        ax.grid(alpha=0.3)
        fig.canvas.manager.set_window_title("delay_sweep -- live")
        # SET A TITLE BEFORE tight_layout, NOT ONLY INSIDE THE UPDATE LOOP.
        # tight_layout reserves space for what is on the axes AT THE MOMENT IT RUNS.
        # The live title is written per point, further down, so the layout was computed
        # for an untitled axes and every later title was drawn off the top of the
        # window. A placeholder with the same single line reserves the row; the loop
        # then only replaces the text, which does not re-run the layout.
        ax.set_title(f"{a.colour} flash, {expo_units*EXPO_UNIT_US:.1f} us exposure "
                     f"-- starting")
        fig.tight_layout()
        plt.show(block=False)
        acc = [dict() for _ in range(1 if a.percycle else 5)]
        live = (plt, fig, ax, lines, acc)
    try:
        for i in range(npts):
            d_us = i * a.step
            set_delay(ser, int(round(d_us / TICK_US)))
            ser.reset_input_buffer()
            collect(ser, a.settle, timeout=8.0)        # discard the transition
            # ---- FRAME INDEX FROM THE TRANSMITTED PIXEL, COUNTED IN ARRIVAL ORDER --
            #
            # Genlock is 1:1, so consecutive lines ARE consecutive projected frames.
            # tlp == 0xFF marks the bright frame; counting up from it names the four
            # dark ones, which tlp alone cannot distinguish because they all transmit
            # (0,0,0). No phase counter, no rotation, no anchor, no majority vote.
            #
            # SELF-RESYNCHRONISING, which is the property that matters. A dropped line
            # mislabels frames only until the next 0xFF -- five frames at worst -- where
            # every scheme built on imp_phase stayed wrong for the rest of the run.
            #
            # Samples before the first 0xFF are discarded rather than guessed: the index
            # is genuinely unknown until the marker arrives.
            fidx = None
            for mean, npx, ph, tlp in collect(ser, a.frames, timeout=12.0):
                if npx != 256:
                    bad_npx += 1
                    fidx = None          # a gap breaks the count until the next marker
                    continue
                if tlp == 0xFF:
                    if fidx is not None and fidx != NCYC - 1:
                        slips += 1       # marker arrived off-cycle
                    fidx = 0
                elif fidx is not None:
                    fidx = fidx + 1
                    if fidx >= NCYC:     # a 0xFF should have appeared by now
                        slips += 1
                        fidx = None
                if fidx is None:
                    continue
                # ANCHOR THE SEQUENCE ON THE TRANSMITTED PIXEL, NOT THE PHASE COUNTER.
                # tlp is the top-left pixel AS SENT, so tlp == 0xFF marks the bright
                # frame by CONTENT. The phase counter cannot do this: it is sampled at
                # the sensor's own frame_start, a constant but unknown number of frames
                # after the trigger, so which phase carries the flash is a calibration
                # rather than a given -- and a projector delayed by more than the
                # sequence length makes it alias outright. Latched on the FIRST sighting
                # and then held: a value that could change mid-sweep would rotate the
                # axis under the data already plotted.

                rows.append((d_us, 0 if a.percycle else fidx, tlp, mean))
            if live:
                plt, fig, ax, lines, acc = live
                # REBIN EVERYTHING, DO NOT APPEND. The rotation comes from a majority
                # vote that is still forming during the first few delay points -- the
                # earliest votes come from the back-porch region, where tlp reads one
                # frame early, so a0() can start at the wrong phase and settle later.
                # Appending under the rotation current at arrival left those first
                # points permanently in the wrong trace, which put a ~190 ADU step at
                # the start of every frame and made the transmitted frame look lit when
                # the CSV said it was dark. Rebinning is O(rows) on a few thousand
                # samples and removes the failure mode entirely.
                for k in range(len(acc)):
                    acc[k].clear()
                for dd, ph, _t, mean in rows:
                    acc[0 if a.percycle else rot(ph, dd)].setdefault(dd, []).append(mean)
                nrail = 0
                ntot = 0
                for k in range(len(lines)):
                    xs = sorted(acc[k])
                    ys = [sum(acc[k][x]) / len(acc[k][x]) for x in xs]
                    # The stored key is the DELAY; the absolute time is phase*T + delay.
                    off = (k * T_us) if (a.endtoend and not a.percycle) else 0.0
                    lines[k].set_data([x + off for x in xs], ys)
                    nrail += sum(1 for y in ys if y >= 1015)
                    ntot += len(ys)
                ax.set_title(f"{a.colour} flash, {expo_units*EXPO_UNIT_US:.1f} us "
                             f"exposure -- point {i+1}/{npts}, "
                             f"{100.0*nrail/max(ntot,1):.0f}% of bins AT THE RAIL")
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
                if not plt.fignum_exists(fig.number):
                    print("\nplot closed -- stopping")
                    break
            if i % 20 == 0 or i == npts - 1:
                el = time.time() - t0
                print(f"  {i+1:4d}/{npts}  delay {d_us:8.0f} us   "
                      f"{len(rows):6d} samples   {el/60:.1f} min")
    except KeyboardInterrupt:
        print("\ninterrupted -- writing what was captured")
    finally:
        wr(ser, R_ROICTL, 0x80)
        set_delay(ser, 0)
        ser.close()

    # SORTED SO THE BRIGHT FRAME IS FIRST. seq is the raw phase rotated so the frame
    # transmitted with tlp == 0xFF sits at 0, and abs_us is built from seq -- so t = 0
    # is the white frame in every run, whatever the phase counter happened to start on.
    # Both columns are written: raw `phase` keeps the file honest about what the
    # hardware reported, `seq` is what the plot and the analysis use.
    # THE ONE DELAY THAT MUST BE DROPPED. Near the top of the range the sensor's
    # frame_start crosses into the NEXT projected frame, so every label really does
    # rotate by one -- MEASURED: at delay 8300 each phase reports the value phase-1 had
    # at 8200. That is a genuine relabel, not the back-porch artefact, and a fixed
    # rotation cannot describe both regimes. Excluded rather than plotted wrong: it is
    # one point in eighty-four, and leaving it in puts a full frame-period step in the
    # trace exactly at the boundary the eye is drawn to.
    # ---- REJECT DELAYS WHERE THE FRAME LABEL IS AMBIGUOUS ---------------------
    #
    # At certain delays the sensor's frame_start lands exactly on a projected-frame
    # boundary, so the latched label flickers between p and p+1 from frame to frame and
    # every bin becomes a MIXTURE of two frames. MEASURED at delay 7200: frames 3, 4
    # and 5 -- dark throughout, and stable to +/-2 ADU at every neighbouring delay --
    # each returned samples spanning [174..429], i.e. some of their samples carried
    # frame 1's brightness. The mean then lands between the two and draws a spike that
    # looks like a real emission.
    #
    # THE QUIETEST BIN IS THE TEST, not the loudest. Label shuffling corrupts EVERY bin
    # at that delay; genuine fast signal (the PWM inside the flash) widens only the LIT
    # bins. So a delay is rejected when even its most stable frame is wide -- which
    # cannot happen from light alone.
    spread = collections.defaultdict(dict)
    for d_us, ph, _t, m in rows:
        spread[d_us].setdefault(rot(ph, d_us), []).append(m)
    AMBIG = 20.0
    ambiguous = sorted(x for x, byseq in spread.items()
                       if byseq and min(max(v) - min(v) for v in byseq.values()) > AMBIG)
    if ambiguous:
        print("")
        print("rejecting %d delay(s) where the frame label is ambiguous"
              " (every bin mixed, quietest spread > %.0f ADU): %s us"
              % (len(ambiguous), AMBIG, [int(x) for x in ambiguous]))
        rows = [r for r in rows if r[0] not in set(ambiguous)]
        # AND TAKE THEM OFF THE FIGURE. The PNG is the live figure, so filtering only
        # the CSV left the artefact on screen and in the saved image while the data
        # file was clean -- the plot and its own CSV disagreeing, which is worse than
        # either being wrong on its own.
        if live:
            _plt, _fig, _ax, _lines, _acc = live
            for k in range(len(_acc)):
                _acc[k].clear()
            for dd, ph, _t, mean in rows:
                _acc[0 if a.percycle else rot(ph, dd)].setdefault(dd, []).append(mean)
            for k in range(len(_lines)):
                xs2 = sorted(_acc[k])
                ys2 = [sum(_acc[k][x]) / len(_acc[k][x]) for x in xs2]
                off = (k * T_us) if (a.endtoend and not a.percycle) else 0.0
                _lines[k].set_data([x + off for x in xs2], ys2)
            _fig.canvas.draw_idle()

    # WHERE THE AMBIGUITY WILL BE, PREDICTED not discovered. The label is latched at
    # the sensor's frame_start = trigger + exposure + L0, so the bins mix when
    #     delay + exposure + L0  ==  0  (mod T)
    # MEASURED by sweeping the exposure and watching the artefact move with it:
    #     expo    4.88 us -> delay 7200      expo  499.88 us -> delay 6800
    #     expo 1500.00 us -> delay 5700
    # which fits L0 ~ 1021 us to within one step at every point. Printed so the
    # rejected delays can be checked against theory instead of trusted.
    L0_US = 1021.0
    pred = (T_us - expo_units * EXPO_UNIT_US - L0_US) % T_us
    print("   label ambiguity predicted at delay %.0f us (T - exposure - L0, L0=%.0f us)"
          % (pred, L0_US))

    # NOTHING TO ANCHOR ANY MORE. The frame index was counted from the tlp marker in
    # arrival order, so index 0 IS the transmitted bright frame by construction. The
    # majority vote, the per-delay anchor and the wrap-point drop are all gone with the
    # phase field they were compensating for.
    if not rows:
        print("")
        print("WARNING: no usable samples -- no line ever carried tlp == 0xFF, so the"
              " frame index could not be established. Is the impulse sequence running?")
    elif slips:
        print("")
        print("%d marker slip(s): a 0xFF arrived off-cycle or a cycle ran long."
              " Those samples were dropped; the count re-syncs on the next marker."
              % slips)

    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["delay_us", "phase", "seq", "tlp", "abs_us", "mean"])
        for d_us, ph, tlp, m in rows:
            sq = 0 if a.percycle else rot(ph, d_us)
            ab = d_us if a.percycle else (sq * T_us + d_us)
            w.writerow([d_us, ph, sq, tlp, ab, m])
    print(f"wrote {a.out}  ({len(rows)} samples, {bad_npx} rejected for npx != 256)")

    # fold to one value per absolute-time bin
    agg = {}
    for d_us, ph, _tlp, m in rows:
        t = d_us if a.percycle else (rot(ph, d_us) * T_us + d_us)
        agg.setdefault(round(t, 3), []).append(m)
    xs = sorted(agg)
    ys = [sum(agg[t]) / len(agg[t]) for t in xs]
    if not xs:
        return
    pk = max(range(len(ys)), key=lambda i: ys[i])
    print(f"peak  {ys[pk]:.1f} at {xs[pk]:.0f} us   floor {min(ys):.1f}   "
          f"contrast {(max(ys)-min(ys))/max(ys)*100:.1f}%")
    # THE OLD 78.6 us WARNING IS GONE, AND DELETING IT MATTERS. ext_sync used to be the
    # RAW out_vsync, whose RISING edge at a negative-polarity mode is the END of the
    # pulse -- 6 lines late. It is now driven from vsync_Pos, so delay 0 IS the vsync
    # leading edge. Subtracting 78.6 us today would introduce the very error the note
    # once corrected.
    print("NOTE: t=0 is the vsync LEADING edge of the frame the white pixel was"
          " transmitted in (tlp=0xFF), not the first white pixel -- the frame a vsync"
          " announces does not start emitting for ~354 us at 800x600@120.")
    print("      The zero carries a CONSTANT uncalibrated offset: tlp is latched at the"
          " sensor's frame_start, which trails the projected vsync by exposure plus"
          " readout. Quote differences, not absolutes.")

    if a.plot and live:
        # SAVE THE FIGURE THAT IS ALREADY ON SCREEN. Building a second one here would
        # switch the backend (matplotlib.use is process-wide, and calling it mid-run is
        # what used to tear down the live window), pop a second window the user then has
        # to close, and risk the PNG disagreeing with what they were just looking at.
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}  (the window you are looking at)")
    elif a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(xs, ys, lw=0.9, color="#c1322c")
        for k in range(1, 5):
            ax.axvline(k * T_us, color="#8892a0", lw=1, ls="--")
            ax.text(k * T_us, max(ys), f" frame {k}", fontsize=8, color="#5c6672",
                    va="top")
        ax.set_xlabel("time after the bright frame's genlock trigger (us)")
        ax.set_ylabel("ROI mean (10-bit ADU)")
        ax.set_title(f"{a.colour} flash, {expo_units*EXPO_UNIT_US:.1f} us exposure, "
                     f"{a.step:.0f} us steps")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")

    # HAND THE WINDOW BACK TO THE USER. Everything above is already written to disk, so
    # blocking here cannot lose data -- it only stops the process exiting and taking the
    # figure with it. A sweep you cannot look at afterwards is a sweep you have to run
    # twice.
    if live:
        plt_l = live[0]
        print("")
        print("plot left open -- CLOSE THE WINDOW to exit.")
        plt_l.ioff()
        plt_l.show()


if __name__ == "__main__":
    main()

# Concurrent capture + streaming — staged plan

**Goal:** grab camera frames into DDR3 while simultaneously streaming already-captured
frames to the PC, so a scan costs capture instead of capture *plus* download
(~205 ms instead of ~380 ms for 24 frames).

## Where this stands

`cam_frame_ft.v` restructured into two concurrent FSMs **builds clean and does not
work**: setup +0.082, hold +0.059, MIG instantiation diffed identical to the working
build, `app_en`/`app_wdf_wren` both low during calibration, no meaningful synthesis
warnings — and `init_calib_complete` **never asserts**, reproducibly, for 60 s.
Preserved on branch `wip/concurrent-capture` (commit `b2da729`).

`ddr_loop_ft.v` proves the concurrency itself is sound: two processes, one writing a
deterministic pattern to DDR3 and one streaming it over the Ft+, **3,932,160 words
checked against an absolute pattern, zero wrong**, at 275–294 MB/s. So the fault is
something `cam_frame_ft` *adds*, not concurrent DDR access.

## Principle

**Build up from the design that works, do not subtract from the one that doesn't.**
Keep the absolute-pattern check (`every 32-bit word == {frame[5:0], index[23:0]}`) as
deep into the stack as possible: a wrong base address, a stuck read pointer or an
off-by-one in the unpacker corrupts every frame *identically* and sails through a
frame-vs-frame comparison. Only switch to camera pixels when there is no alternative.

Each milestone is one bitstream (~15 min) plus minutes of test.

---

## M0 — concurrent DDR write+read, synthetic data, TX-only ✅ DONE

`ddr_loop_ft.v`, `ddr_loop_check.py`, commit `34ea691`.

**Pass:** 3.9 M words exact, 275–294 MB/s, setup +0.082 / hold +0.059.

## M1 — add the FT601 read path

Adds `ft601_sync_rx`, dynamic `bus_oe`, the tri-state multicycle, host commands.

**Pass:** pattern still exact; `cmd_count` increments on `cam_ctl.py`; throughput unchanged.
**If it fails:** the bidirectional bus is implicated — it is the part of the camera design
that most disturbs placement in the FT601 corner.

## M2 — add the camera front end, still writing synthetic data

Adds LVDS RX, `IDELAYCTRL`, eye scan, align, `wordclk` (BUFIO/BUFR). The camera runs but
DDR is still fed by the generator.

**Pass:** pattern exact **and** `aligned = 1` **and** `init_calib_complete = 1`.
**If it fails:** prime suspect. Two `IDELAYCTRL`s and extra clocking competing with the
MIG's own delay calibration would explain a failure invisible in both RTL and timing
reports — which is exactly the signature we have.

## M3 — add the clock-domain crossing, still synthetic data

Move the generator into `wordclk` so the pattern crosses `cam_async_fifo` exactly as
camera pixels will.

**Pass:** pattern exact, `cfifo_ovf = 0`.
Last milestone where a dropped word or wrong address cannot hide.

## M4 — real camera pixels, sequential

Reader held disabled until capture completes; reproduces today's shipping behaviour.

**Pass:** byte-exact frames, header spacing exactly 2,621,472, no kernel duplication,
120.000 Hz.
Worth keeping even though it "should just work" — skipping the known-good checkpoint is
how a new bug becomes indistinguishable from an old one.

## M5 — real camera pixels, concurrent ← the goal

Release the reader at `rf < wf_done`.

**Pass:** byte-exact, no duplication, `cfifo_ovf = 0`, 120.000 Hz unchanged, and **scan
turnaround ~205 ms instead of ~380 ms**.
The pass criterion is deliberately a *latency* measurement. Byte-exact data proves
nothing about whether the two processes actually overlapped — that trap caught two
loopback runs, where the host read after capture had already finished and the result
looked like a success.

---

## RESULT of the shortcut (2026-08-14) — M1..M3 are SUPERSEDED

`CONCURRENT=0` (two-FSM structure, reader waits for `stw == W_DONE`) **calibrates and
streams byte-exact**: init_calib=1 held over 32 s, 50 frames, spacing exactly 2,621,472,
0 mismatching bytes, 0 duplicated kernels, 216.6 MB/s, WNS +0.059 / WHS +0.059.

So the FSM split is fine and **only concurrency breaks it**. M1-M3 were designed to find
which *added component* broke calibration; there is no such component, so they are
skipped. `CONCURRENT=0` is effectively M4 and has passed.

**Puzzle worth keeping in view:** the two builds should be IDENTICAL until calibration
completes -- the reader cannot leave `R_IDLE` while `wf_done` is 0, whatever `CONCURRENT`
is, and `ui_rst` holds both FSMs until the MIG releases it. So the difference is in what
synthesis *builds*, not what the FSM *does*. That makes a marginal placement outcome a
live possibility, and means the A/B below may simply not reproduce.

## Shortcut (run first) -- DONE

Take `wip/concurrent-capture` and hold the reader until `stw == W_DONE`, i.e. the new
two-FSM structure with the *old* sequential behaviour.

- **calibrates and streams** → the FSM split is fine; the arbiter / concurrent access is
  the culprit, and M1–M3 can be skipped in favour of attacking the arbiter directly
- **still no calibration** → the split itself is implicated, independently of concurrency

One build to halve the search space.

## Standing rules

- Gate on **setup and hold**. The camera build only ever checked setup; hold violations
  produce exactly this signature — logically identical designs where one calibrates and
  one does not.
- Never load a bitstream that fails timing. Negative slack on the FT601 bus is what
  silently corrupted `ft_data[31:16]` in July.
- Verify **bytes**, never throughput. That failure measured 192 fps with zero dropped
  frames while half the bus was wrong.

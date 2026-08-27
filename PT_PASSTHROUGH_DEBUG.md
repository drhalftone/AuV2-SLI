# HDMI pass-through on the Pt — what is eliminated, and what is left

_State as of 2026-08-27. Open._

**Pass-through does not work on the Pt.** Offline does. This file records what has been
ruled out **by measurement on hardware**, so the next session does not re-run these.

## The one-line summary

The board receives the PC's HDMI perfectly and drives a display perfectly, but cannot
turn the received signal back into a valid raster. A display on the output shows **no
signal** — not a bad picture, nothing.

## What was never true

**This is not a regression.** `M1 — RESULT: PASS` records its hardware proof as
`mode_idx 2 (1024x768@75), edid_ok False — correct failsafe, no display`: M1 was
verified with **no display attached**, so pass-through was never exercised on the Pt at
any mode. The README's verified pass-through list — 800×600, 1024×768@60/70/75,
1280×720@60, 1280×800@60 — is from the **Au V2**, which was thoroughly tested. The Pt
was not. So this is an unfinished port, not something that broke.

The HDMI RX RTL is unchanged since M2. `ae8fa1e` was wrongly suspected here; its diff
touches only `rdy_buf`/CAMSIM and cannot affect the raster.

## Proven healthy (do not re-test)

| stage | evidence |
|---|---|
| Recovered pixel clock | Tracks the source **exactly**: 74250 / 83500 / 65000 / 40000 kHz measured against a clk100 gate |
| TMDS decode, per lane | `symbol_sync=1`, **zero invalid symbols on all three channels**, at failing modes too |
| IDELAY calibration | `IDELAYCTRL RDY = 1` (was `RDY => open`; nothing had ever checked it) |
| `video_phase_fifo` | `primed=1`, re-prime count **static** over 6 s in pass-through |
| Inter-channel alignment | At 720p all three lanes are in a control period together **99.7%** of the time — *better* than the mode that worked |
| HDMI timing closure | `pixel_clk_raw_i` WNS **+6.198 ns**. (The oft-quoted +0.082 ns WNS is `ft_clk_13`, the **FT601** clock — unrelated) |
| Output path | Offline drives a real display correctly (4×3 test pattern, 1024×768@75) |
| Camera independence | Six PC mode changes with the camera streaming: `ldrop` never left 0, 120.003 fps unchanged. **M3d generalised — passes** |

## The remaining gap

At **1280×720**: control periods are detected, all three lanes agree, the clock is exact —
and the extracted raster is still garbage (`h_active` saturates at 4095, `v_active` = 0,
vsync ~400–840 Hz instead of 60).

At **1024×768**: different failure — channel 0 is **never** in a control period
(count = 0). A total decode failure, not a misassembly.

So the fault sits between "valid control symbols arrive, aligned" and
"`raw_hsync`/`raw_vsync`/`raw_blank` form a sane raster" — i.e. inside
`hdmi_input.vhd`'s `hdmi_section_decode` process, or the CTL decode feeding it.

**Next probe:** histogram the `ch0_ctl` **values** over a window. If the hsync/vsync bits
are stuck while `ctl_valid` is healthy, the fault is the CTL decode; if they toggle
correctly, it is the state machine that turns them into blank/hsync/vsync.

## Two traps that cost time here

**The EDID source changed mid-investigation.** An EDID fob was swapped for a real display.
That changes the EDID read, therefore the merged EDID served to the PC, therefore the exact
timings Windows uses. **Measurements taken before and after that swap are not comparable** —
a "1280×800 works / 1280×800 fails" pair spanning it proves nothing.

**The diagnostics were polarity-naive.** `N=` and `video_meas` both tap the raw sync before
`VPolarity` correction, so at a negative-polarity mode (1024×768) they read the line rate
and look broken even if video were fine. Offline reading 75 Hz correctly on the same
counter is what proves pass-through is genuinely broken rather than merely mis-measured.

## Instrumentation added (this is what makes the above measurable)

Before this, the design could not report any of it.

| reg | meaning |
|---|---|
| `0x60`–`0x63` | measured **incoming** h_active / v_active |
| `0x64`–`0x66` | incoming frame period, 10 ns units (Hz = 1e8/period) |
| `0x67` | `{vid_valid, meas_ok}` |
| `0x68`–`0x6A` | **recovered pixel clock, kHz** |
| `0x6B` | `[7]fifo_primed [6]idelay_rdy [5:3]ch2/1/0 invalid [2]dvid [1]pll_locked [0]symbol_sync` |
| `0x6C` | `video_phase_fifo` re-prime count (static = healthy) |
| `0x6D`–`0x70` | ch0 control cycles / all-three-coincident, per 65536-pixel window |

> **`0x22`–`0x25` are NOT the input.** They are the offline mode `mode_select` chose from
> the EDID. With a PC sending 1280×720 they read 1280×800. Reading them as "what am I
> receiving" is wrong by a whole mode — which is why `0x60`+ exist.

Host tools: `host/rx_timing.py` (decode the above), `host/test_modecycle.py` (cycle the
PC's modes; `--camera-only` judges just the camera half).

## Build environment

Roughly **half of all Vivado runs died for reasons unrelated to the design** — `EXIT=127`
mid-synthesis with no error logged, a bitgen `[Designutils 12-1097]` that vanished on
retry, and CPython segfaults. Two Vivado instances sharing the out-of-tree IP dir cause a
real `upgrade_ip` failure; only run one. **Always check for the `BUILD DONE` marker, not
the exit code** — a stale bitstream flashed after a failed build reads as a hardware
result. See [[step-write-corruption]] in the session memory: this host also corrupts ~20%
of large file writes. Windows Defender exclusions for `C:\AMDDesignTools` are the first
thing to try; `build_merged.tcl` already documents an AV-scan race.

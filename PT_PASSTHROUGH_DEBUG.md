# HDMI pass-through on the Pt — SOLVED

_2026-08-27. Pass-through works: `1281x720 @ 60.00 Hz`, `meas_ok=1`, video confirmed
on the display. Commit `9e1bc80`._

## The fault, in one paragraph

The receiver decoded **sync perfectly and passed zero pixels**. `vdp_prefix_seen`
defaults to `'0'` every cycle and only sets while control symbols are valid, so it
fell at the **first** guard-band character. But only the **second** of the two guard
characters is ever detected on this hardware — 40 per 65536-pixel window, exactly one
per line at 720p. The flag and the detection could therefore never coincide, so
`vdp_guardband_detect` never latched and the video data period was never entered.
`pfx_hold` now keeps the flag alive across both guard characters.

| | before | after |
|---|---|---|
| guard band detected | 40 | 40 |
| `vdp_guardband_detect` | **0** | **40** |
| `in_vdp` duty | **0** | **50815** (77.5%; ~74.5% expected) |
| frame period | 263 / 428 / 1911 Hz (noise) | **60.00 Hz** |
| raster | `4095x0`, `meas_ok=0` | **`1281x720`, `meas_ok=1`** |

## It was two faults, not one

**Sync half** — three copy-paste guard bugs, each reading a third channel's *value*
while confirming only two channels' validity:

* VDP preamble (`:551`) tested `ch1_ctl_valid` twice, never `ch2_ctl_valid`
* ADP preamble (`:573`) same
* ADP guard band (`:588`) tested `ch1_guardband_valid` twice, never `ch2`

Fixing these took the frame period from noise to exactly 60.00 Hz. (`:512` also
tested `ch2_invalid_symbol` three times — fixed, but it only gates substitute pixel
values and was never the fault.)

**Pixel half** — the `vdp_prefix_seen` expiry above. Both halves had to be fixed
before a single pixel appeared, which is why partial fixes looked like no progress.

> A comment in this code records that this same class of bug was fixed once before by
> widening the preamble pulse. **That fix was not wide enough.** If pass-through ever
> breaks again, suspect this flag's lifetime before anything else.

## Eliminated by measurement — do not re-investigate

| stage | evidence |
|---|---|
| Recovered pixel clock | 74250 kHz, exact |
| TMDS decode, per lane | zero invalid symbols, all three channels |
| IDELAY calibration | `RDY = 1` |
| `video_phase_fifo` | primed; re-prime count static |
| Inter-channel alignment | 98% coincidence |
| **ch1/ch2 lane swap** | preamble counts **14680 as-coded vs 0 swapped** — lane order is correct |
| HDMI timing closure | `pixel_clk_raw_i` WNS +6.198 ns (the +0.082 ns figure is `ft_clk_13`, the FT601 — unrelated) |
| Camera independence | camera streamed throughout; `ldrop` never left 0 |

Two of the above — a stuck video-period latch and the lane swap — were working
theories that measurement **refuted**. Both cost a build. Prefer instrumenting the
one signal between two known-good stages over reasoning about which stage is wrong.

## Instrumentation (this is what made it findable)

All counted over one 65536-pixel window, readable over the Ft+ or COM6:

| reg | meaning |
|---|---|
| `0x60`–`0x63` | measured incoming h/v active |
| `0x64`–`0x66` | frame period, 10 ns units (Hz = 1e8/period) |
| `0x67` | `{vid_valid, meas_ok}` |
| `0x68`–`0x6A` | recovered pixel clock, kHz |
| `0x6B` | `[7]primed [6]idelay_rdy [5:3]ch2/1/0 invalid [2]dvid [1]pll [0]sym` |
| `0x6C` | phase-FIFO re-primes (static = healthy) |
| `0x6D`–`0x70` | ch0 control cycles / all-three coincident |
| `0x71`–`0x74` | **`in_vdp` duty / `in_adp` duty** |
| `0x75`–`0x78` | **vsync rises / hsync rises** per window (NOT preamble counts) |
| `0x79`–`0x7C` | **VDP guard band detected / `vdp_guardband_detect`** |

> `0x22`–`0x25` are **NOT** the input — they are the offline mode `mode_select` chose
> from the EDID. With a PC sending 1280×720 they read 1280×800.

Host: `host/rx_timing.py` (refuses to interpret when `meas_ok=0`),
`host/test_modecycle.py` (mode sweep; `--camera-only`).

## The pass-through pixel-clock floor is 40 MHz (Pt, and Au V2 alike)

> **SUPERSEDED 2026-08-31 -- THIS SECTION IS HISTORY, NOT CURRENT BEHAVIOUR.**
> The fixed `CLKFBOUT_MULT_F = 15.0` it describes is gone; `rx_freq_band` +
> `rx_drp_recfg` now retune the recovery MMCM per band. MEASURED on hardware:
> 640x480@75 locks at exactly 31500 kHz and 640x480@60 at 25156 kHz, both with
> `pll=1 sym=1` and zero invalid symbols -- so the "broken (below minimum)" rows
> in the table below are NO LONGER TRUE. Those modes still fail, but on SYNC
> DECODE, not on clocking: see "Known defects still open" section A.
> Keep this section for the arithmetic and for why the floor once existed.

`hdmi_input.vhd` fixes `CLKFBOUT_MULT_F = 15.0`, so VCO = pixel x 15. The Artix-7
`-2` MMCM VCO minimum is 600 MHz, giving a hard floor of **600 / 15 = 40.0 MHz**.
Measured behaviour matches that arithmetic exactly:

| mode | pixel clk | VCO | result |
|---|---|---|---|
| 1280x720@60 | 74.25 | 1114 | solid |
| 1280x800@60 | 71.00 | 1065 | solid |
| 1024x768@60 | 65.00 | 975 | solid |
| 800x600@75 | 49.50 | 743 | **TWITCHY — never worked, see below** |
| 800x600@60 | 40.00 | 600 | **TWITCHY — never worked, see below** |
| 640x480@75 | 31.50 | 473 | broken (below minimum) |
| 640x480@60 | 25.175 | 378 | broken (below minimum) |

**This is not a part limitation and moving boards will not fix it.** Au V2 is
`xc7a35tftg256-2`, Pt V2 is `xc7a100tfgg484-2` — same speed grade, same 600 MHz VCO
floor. A previous attempt on the Au V2 was abandoned; the Pt offers no advantage.

`edid_builder.v` already excludes 640x480 as `"<40, below floor"` — that floor came
from this same calculation.

> **CORRECTED TWICE. Read this before trusting any 800x600 claim.**
>
> First correction (wrong): this file blamed the VCO floor for 800x600's black
> sequences. The floor is real arithmetic but only excludes 640x480, so that was an
> overstatement.
>
> Second correction (the one that matters): the replacement text claimed 800x600
> "PASSES at both 60 and 75 Hz". **It does not.** That claim came from
> `test_modecycle.py`, which only checks the FPGA's measurement of the INCOMING
> raster -- it says nothing about the picture. The user reported 800x600 twitchy with
> black screens at both rates, before AND after every fix attempted on 2026-08-28.
>
> **800x600 PASS-THROUGH HAS NEVER WORKED, ON EITHER BOARD.** Before `8790569`,
> `edid_builder.v` read `Excluded: all 640x480/720x400/800x600 (<60, below floor)` --
> the x10 recovery MMCM could not reach it. `8790569` changed x10 to x15, widening the
> window to 40-90 MHz, and ADDED 800x600@60/72/75 **because the arithmetic now
> admitted it**. That commit's hardware verification was `1024x768@60 and @75 rock
> solid` -- 1024x768 only. 800x600 went from excluded-by-calculation to
> advertised-by-calculation without ever being looked at.
>
> On the Pt it was never exercised at all: M1's hardware record is
> `mode_idx 2 (1024x768@75), edid_ok False -- correct failsafe, no display`, i.e.
> verified with NO DISPLAY ATTACHED.
>
> The thing that IS solid at 800x600 is the OFFLINE path -- all 14 table modes were
> eyes-on verified on 2026-08-28. Offline generates its own clean clock and emits no
> data islands. That contrast is the evidence, not a contradiction.

**There is also nothing to fix in the EDID.** Verified against Windows' own cached
copy of the EDID we serve: byte35 = `0x01`, so bit5 (640x480@60) and bit2
(640x480@75) are both **zero** -- we already do not advertise them. The merge is
provably correct: the attached Dell offers `0xA5`, our mask is `0x01`, Windows
received `0xA5 & 0x01 = 0x01`; byte36 likewise `0x4B & 0xCE = 0x4A`. 640x480 shows
up in Windows' mode list because the OS exposes it as a legacy fallback regardless
of EDID (CEA-861 requires VIC 1 of HDMI sinks generally), and Windows substitutes a
different mode when asked to actually set it.
**Alternative (expensive, deliberately not done):** DRP-reconfigure `CLKFBOUT_MULT`,
all three `CLKOUT` dividers and the filter/lock registers per XAPP888 to raise the
multiplier at low pixel clocks (x30 at 25.175 MHz = 755 MHz, in range), re-locking
cleanly on every mode change.

**Left unmeasured:** `CLKIN1_PERIOD` is hard-coded at 13.000 ns (76.9 MHz) while the
real input varies per mode; it programs the lock filter and is a second, weaker
effect on top of the VCO floor. A 20.000 ns diagnostic build was started to separate
the two and abandoned before finishing. Do not assume it is or is not a contributor.
It is the obvious first probe if a low-clock mode is ever chased again -- though
800x600@75 no longer needs chasing.

## Known defects still open

_Register rebuilt 2026-08-31 from a full audit of the pass-through path plus that
day's measurements. Grouped by ROOT CAUSE / WORKAROUND / INSTRUMENT, because most of
the list exists only because the first group was never fixed._

### A. Decoder root causes

All of this is INHERITED code -- `hdmi_input`, `tmds_decoder`, `tmds_encoder`,
`input_channel`, `alignment_detect`, `deserialiser_1_to_10`, `hdmi_io` all arrived
whole as "Add files via upload" (2025-03-24 / 2025-04-17, `Engineer: Qihsi Hu`) and
were never audited. They showed a picture at 1280x720 and 1024x768, and that was
taken as sufficient.

1. **THE CONTROL-PERIOD DETECTOR IS SYMBOL-LOCAL.** It declares a control period
   whenever three symbols pattern-match control codes, ANYWHERE in the line, with no
   knowledge of where it is in the raster. A correct receiver brackets the video data
   period with the preamble -> guard-band -> video SEQUENCE. MEASURED per line
   (65536-px windows, normalised):

   | mode | ctl_call/line | real hblank | vdp/line | real active | display |
   |---|---|---|---|---|---|
   | 1280x720@60 | 363.6 | 370 | 1249 | 1280 | works |
   | 640x480@75 | **232.9** | **200** | 600 | 640 | 95% black |

   A control period CANNOT outlast the blanking interval. At 640x480 it does, by 33
   cycles per line -- real VIDEO data decoding as control on all three lanes at once.
   Each of those cycles feeds `ch0_ctl(1)` into vsync, i.e. pushes PIXEL DATA into the
   sync signal, and steals the same ~40 px from the video period.
   The false rate per line is roughly CONSTANT; only the denominator changes --
   33/370 = 9% at 720p, 33/200 = 16.5%, 33/160 = 21% at 640x480@60. That is the whole
   ">=256 px blanking works / <=200 fails" split, and it is why a majority vote cannot
   close it: the false samples are IN the vote.

2. **The preamble gate does no real work.** 14680 hits per window -- essentially every
   control cycle -- where a real 8-character preamble gives ~320. `ch1_ctl` is
   effectively stuck at `"01"`, so `vdp_prefix_seen` is asserted almost continuously.
   Logged earlier as "permissive rather than blocking [...] may bite later". IT BIT.

3. **Only one of the two guard characters is detected.** Worked around, not explained.

4. **`alignment_detect` IS STRUCTURALLY BLIND TO THE ERROR THAT MATTERS.** Its only
   feedback is `invalid_symbol`. A corrupted control symbol is still a LEGAL control
   code, so the flag never fires, the delay is never advanced, bitslip never asserts --
   and every instrument reports a perfect link while sync rots. This is why
   `inv ch2/ch1/ch0 = 0/0/0`, `sym=1`, `pll=1` coexisted with a black screen all day.
   **"Zero invalid symbols" is NOT evidence the link is clean.**

5. **The video data period runs ~40 px/line short and is unstable** -- 600 detected vs
   640 real at 640x480@75, drifting 600 <-> 641 between reads. Consequence of (1).

### B. Workarounds stacked on the above (2026-08-31, UNCOMMITTED)

Every one of these exists only because A1-A4 are unfixed. If A is repaired these
should be DELETED, not left layered on a correct decoder.

6. Sync HELD through the VDP instead of forced low. Spec-correct and a proven no-op for
   positive-polarity modes. **Did not fix the target bug.**
7. ADP sync overwrite removed (two sites). Defensible independently -- `edid_merge`
   forwards no CEA extension, so we present a DVI sink and every data island is
   spurious by construction. **Did not fix the target bug.**
8. 16-cycle persistence debounce. **MANUFACTURED A DEFECT** -- control cycles exist only
   inside blanking, so an edge arriving late deferred into the next line, giving +/-1
   line jitter in the transmitted frame. Removed.
9. Per-line vsync majority vote committed at the hsync edge. Jitter span 2 -> 1.
   OFFLINE at the same mode is span 0. **Partial.**
10. `CTL_MIN = 12` qualified-control-period gate on the sync latch only. Gates sync,
    NOT `in_vdp`/`raw_blank`/pixels, so working-mode geometry cannot shift -- which also
    means the ~40 stolen pixels are NOT recovered. Built 2026-08-31, result pending.

### C. Instruments that misreported

11. **`pre_diag`'s header comment is WRONG.** It claims `[15:0]` = video preamble
    as-coded and `[31:16]` = the same with ch1/ch2 swapped. The live code counts
    **vsync rises** and **hsync rises**. Reading the header instead of the code yields a
    bogus "the channels are swapped" conclusion -- it produced exactly that today.
12. `vdp_diag[31:16]` is labelled `in_dvid` but counts `in_adp`.
13. `video_meas` `vs_hi` overflow: 22-bit counter, 2^21 window, and the ">= half" test
    reads bit 20. At 100.0% duty the count reaches 2^21, bit 20 CLEARS, and the vote
    comes out INVERTED. Latent, not today's cause.
14. `vs_pol` reads 0 even at 1024x768@60, a negative-vsync mode that passes through
    perfectly. The polarity detector does not do what its comments claim.
15. **Saturating 8-bit counters read 255 and look STALE while actively climbing.** This
    hid `edid_fall` for hours -- it reads 255 static, and only a bitstream reload (which
    zeroes it) reveals it is incrementing ~3/sec.
16. `video_phase_fifo` freezes `rdata` when unprimed, so a stalled FIFO emits a FROZEN
    raster rather than blanking -- it presents as valid signal, not as "no signal".

### D. Open and unexplained

17. ~~`edid_fall` climbs ~3/sec~~ **NOT A DEFECT -- RESOLVED 2026-08-31.** It is the
    designed monitor-presence heartbeat. `edid_merge` has no working output HPD sense,
    so it re-reads the display EDID over DDC on a `probe_cnt == 50_000_000` timer at
    clk100 = **exactly 0.5 s = 2.0 Hz**, and `chk0_ok` deasserts during each read and
    re-asserts on a good checksum -> one falling edge per probe. Measured ~2/sec, an
    exact match. A HEALTHY board sits at 2/sec forever; the 8-bit counter saturating at
    255 just means it has been up two minutes. The NAME reads like an error counter and
    it is not one -- that misled this investigation twice, first as "stale", then as a
    "live unexplained fault".
18. 640x480@60 (160 px blanking, the worst case in the table) is untested against any
    2026-08-31 change.
19. 1280x800@60 is claimed working at hblank 160, which would BREAK the blanking
    correlation. The attached display does not offer that mode, so the load-bearing
    counter-example cannot be checked here. Treat the correlation as unconfirmed until
    it is.

### E. Theories KILLED by measurement (do not revive without new evidence)

* **Negative sync polarity.** 1024x768@60 is `-hsync -vsync` and passes through
  perfectly. Both the vsync and the hsync variants of this theory are dead.
* **Low pixel clock / IDELAY range.** OFFLINE drives the SAME 31500 kHz to the SAME
  sink flawlessly (`v_active` span 0). 315 Mbps is not too slow for this board, cable
  or display.
* **Single-bit errors from an uncentred eye.** The implied BER (~4e-4) would visibly
  destroy pixel data; all three lanes report ZERO invalid symbols.
* **Blanking budget as a spec limit.** 160 px is far above the ~22-character HDMI
  minimum. Short blanking is the STRESSOR that exposes A1, not the cause.
* The raster measured `1281x720` -- one extra pixel per line -- because `in_vdp` rose a
  cycle early. Fixed in `c1b00c6`; re-measure for exactly 1280 if it recurs.

## Two traps that cost time here

**An HDMI-only isolation build is a dead end as specified.** `cam_frame_ft` owns every
`ft_*` pin *and* `usb_tx`, so `WITH_CAM=0` deletes the register readback path along
with the camera — leaving no way to read the diagnostics it was built to expose. The
Ft+ reply path also rides the video frame stream, so it needs the camera streaming.

**The EDID source changed mid-investigation** (fob → real display), which changes the
merged EDID served to the PC and therefore the timings Windows uses. Measurements
spanning that swap are not comparable.

## Build environment

Roughly half of all Vivado runs died for reasons unrelated to the design: `EXIT=127`
mid-synthesis, `EXIT=139` (SIGSEGV), a bitgen `[Designutils 12-1097]` that vanished on
retry, CPython segfaults, and corrupt git objects. **Always check for the `BUILD DONE`
marker and a fresh bitfile timestamp, not the exit code** — flashing a stale bitstream
reads as a hardware result. Two Vivado instances sharing the out-of-tree IP dir cause a
real `upgrade_ip` failure; run one. See `[[step-write-corruption]]`.

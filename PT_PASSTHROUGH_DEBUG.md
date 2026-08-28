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
| `0x75`–`0x78` | **video preamble as-coded / with ch1,ch2 swapped** |
| `0x79`–`0x7C` | **VDP guard band detected / `vdp_guardband_detect`** |

> `0x22`–`0x25` are **NOT** the input — they are the offline mode `mode_select` chose
> from the EDID. With a PC sending 1280×720 they read 1280×800.

Host: `host/rx_timing.py` (refuses to interpret when `meas_ok=0`),
`host/test_modecycle.py` (mode sweep; `--camera-only`).

## The pass-through pixel-clock floor is 40 MHz (Pt, and Au V2 alike)

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

* **The preamble gate does no real work.** It counts 14680 hits per window —
  essentially every control cycle — where a real 8-character preamble should give
  ~320. `ch1_ctl` is effectively stuck at `"01"`, so `vdp_prefix_seen` is asserted
  almost continuously. It is permissive rather than blocking, so it does not stop
  video, but the gate is not discriminating and may bite later.
* **Only one of the two guard characters is detected.** Worked around, not explained.
* The raster measured `1281x720` — one extra pixel per line — because `in_vdp` rose a
  cycle early. A follow-up removes the early entry path; re-measure for exactly 1280.

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

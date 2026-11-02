# Sensor readout window, the runway and burst sampling: measurements of 2026-11-02

Notes for the TPAMI manuscript. All measurements were taken on 2026-11-02 with the
PYTHON 1300 camera on the Alchitry Pt V2 and the Optoma ML750ST projector, running
firmware from branch `feat/sensor-window-and-sat-fix` (worktree
`C:\Users\dllau\Developer\AuV2-SLI-sensorwin`, bitstreams loaded to FPGA RAM).
Data, figures and analysis scripts are in `sensor_window_2026-11-02/`.

**Data provenance.** Sections 1–5 and the burst sweeps in section 6.2 were taken with
scripts (`sensor_window_2026-11-02/scripts/` and `host/burst_sweep.py`), not with
`host/sli_ui.py`. Under the manuscript's rule that reported data come from the UI,
each of those results has to be reproduced in `sli_ui.py` before it is cited as data.
The three RGB-filter sweeps in section 6.3 were taken with `sli_ui.py`.

Conventions used throughout:
- ROI = the 16 × 16 pixel patch at columns 640–655 and rows 512–527, counted from
  frame start with black reference rows included (the convention of `roi_mean.v`).
- "Full frame" = the sensor reads out all 1280 × 1024 pixels.
- "Window" = the sensor reads out only a 32 × 24 window (columns 632–663, rows
  504–527) around the ROI, using the sensor's ROI 1 (registers 259–261, selected by
  register 195).
- ADU values are 10-bit. "Above dark" values have the dark level of the same mode
  subtracted.

---

## 1. Purpose of the sensor window

A full-frame readout takes 5659.4 µs, so one picture fits in each 8.33 ms projector
frame at 120 Hz. Reading out only a window around the ROI shortens the readout and
allows several pictures per projector frame, which would shorten a profiling sweep
by the same factor.

Measured with `host/window_check.py` on the profiling bitstream (`build_roimin.tcl`):

| Quantity | Full frame | Window |
|---|---|---|
| Readout, first line to frame end | 5659.4 µs | 27.6 µs |
| Trigger to first line out, 4.875 µs exposure | 78.2 µs | 78.2 µs |
| Trigger to first line out, 250.125 µs exposure | 323.4 µs | 323.4 µs |
| One picture at a 250 µs exposure (trigger to frame end) | ≈ 5983 µs | ≈ 351 µs |

The planned 6 pictures per 8.33 ms frame require ≤ 1389 µs per picture; the window
takes ≈ 351 µs at a 250 µs exposure. The ~1.1 ms trigger-to-first-line figure in the
branch's hand-off document was the full frame at a longer exposure, not a fixed
overhead.

## 2. Firmware defects found and fixed

Four defects were found while testing the branch. Fixes 2.1–2.3 are in the worktree,
uncommitted.

### 2.1 Build-time window written before the sequencer is enabled is not retained

Symptom: a merged build with `CAM_WIN=608,480,64,64` showed three diagonal bands
instead of a 64 × 64 image. The frame's pixel stream repeated with a period of 1280
pixels (correlation 0.913 at lag 1280, 0.78 at lag 64): the sensor was still
sending full 1280-pixel lines, and the FPGA was folding them into 64-pixel rows.

A build with `CAM_WIN=8,8,1264,1008` confirmed it. The line length was 1280
(correlation 0.9956, against 0.9677 at 1264), and the data started at sensor row 0
(correlation 0.9960, against 0.9787 at row 8). Neither the column nor the row window
took effect.

A debug build read sensor registers 256, 257 and 195 back over SPI every 0.5 s:

| | Reg 256 (x) | Reg 257 (y start) | Reg 195 | Completed window writes |
|---|---|---|---|---|
| Written by `cam_boot_stage1` before stream start | 0x9F00 (default) | 0x0000 (default) | 0x0001 | 1 |
| Rewritten once after streaming started | 0x9E01 (as written) | 0x0008 (as written) | 0x0001 | 2 |

The writes issued after the boot ROM, while the sequencer was off (register 192
bit 0 = 0), were not retained. The same writes issued after the sequencer was
enabled were retained, and the window then took effect.

Fix: `cam_boot_stage1.v` writes the build-time window when `streaming` is set,
instead of before the deferred stream start.

### 2.2 Kernel mirroring for windows that start on an odd kernel

The PYTHON 1300 sends even 8-pixel kernels in ascending pixel order and odd kernels
in descending order, where "even" and "odd" refer to the kernel's position on the
sensor array. `cam_sync_decode.v` reset its parity to even at the start of every
line, so a window starting on an odd kernel (x_start = 1 for the 1264 × 1008 crop)
had every kernel reversed. This was visible as vertical banding every 8 pixels.

Test: the mean absolute difference between horizontally adjacent pixels, with and
without reversing every kernel:

| Image | As decoded | Every kernel reversed |
|---|---|---|
| Full frame (x_start = 0) | 1.505 | 1.568 |
| Crop, before fix | 1.606 | 1.540 |
| Crop, after fix | 2.588 | 3.238 |

Fix: `cam_sync_decode.v` takes a parameter `KPAR0`, the parity of a line's first
kernel. `cam_frame_ft.v` sets it to `(WIN_X / 8) % 2`. The profiling build's runtime
window starts at kernel `col8 − 1` and still has this defect. It does not change
the ROI mean, which averages two whole kernels.

### 2.3 Viewer read size

`cam_live.py` requested 4 MiB per USB read with a 1 s timeout. A 64 × 64 frame is
5120 bytes, so each read waited out the full timeout while holding the device lock
that the controls also use. The read size now follows the frame size: 3 frames,
rounded up to 4 KiB, at least 16 KiB.

### 2.4 Not fixed on the branch

- The branch's `build_merged.tcl` lacks the `cdc_max` constraint for the `clk` to
  `ui_clk` crossing that the main tree has (uncommitted there). The full-frame build
  reported a hold violation of −0.108 ns on `u_rplfifo` (`sys_clk_pin` to
  `clk_pll_i`).
- `TESTING_SENSOR_WINDOW.md` says the viewer prints `frame size from the FPGA:
  1280 x 1024` for a full-frame build. It prints that line only when the size
  differs from 1280 × 1024.

## 3. The window reads the same pixels as the full-frame ROI

A debug build of the merged bitstream (`CAM_ALT=1`) alternates the sensor between a
full-frame readout and the 32 × 24 window on consecutive pictures, and marks window
pictures in header word 3 bit 31. Averages of 300 full-frame and 300 window pictures
(100 µs exposure, 3.35 ms delay, a small projected spot in the ROI) were compared:

| Check | Result |
|---|---|
| Best offset of the window against the full frame | 0 rows, 0 columns |
| Correlation at that offset (24 rows × 32 columns) | 0.9997, mean \|difference\| 4.2 ADU |
| Rows in a window picture | 24; the rows before the ROI number the same as in a full frame (offset 0) |
| ROI mean, full frame | 178.01 ADU |
| ROI mean, window | 177.49 ADU (−0.3 %) |
| Correlation without un-mirroring window kernels | 0.89 (the window starts at kernel 79, odd) |

Figure: `figures/align100_side_by_side.png` (left: full frame around the ROI;
right: window picture).

## 4. Full frame and window disagree because of the runway

### 4.1 Observation

At projected level 255 (every frame white), 4.875 µs exposure, both modes from the
same run on the `CAM_ALT` build:

| Delay after vsync | Full frame ROI mean | Window ROI mean |
|---|---|---|
| 0.5 ms | 297.0 (28 pixels clipped) | 28.2 |
| 3.0 ms | 828.7 (55 pixels clipped) | 780.3 (74 pixels clipped) |
| 7.0 ms | 117.7 | 24.4 |

At projected level 64 (three short flashes per frame), a delay sweep in 50 µs steps
(`figures/wsweep_L64.png`) showed the flashes at the same delays in both modes
(best shift 0 µs, correlation 0.98), with peaks of +4.9 ADU (full frame) and
+5.2 ADU (window) above each mode's baseline.

Dark levels (projector black): full frame 18.77 ADU, window 22.85 ADU. The window's
dark level is 4.1 ADU higher.

### 4.2 Interpretation

In a full-frame readout the sensor reads rows in order over 5659.4 µs, so the ROI's
first row (512) is read 512 × 5659.4 / 1024 = 2830 µs after readout starts, plus
78 µs from trigger to first line: ≈ 2908 µs after the exposure. Until it is read, a
pixel continues to collect light at a low rate (parasitic light sensitivity). In the
window readout the ROI is read within ≈ 30 µs, so the window reading contains only
the exposure. This matches the 2026-10-27 findings that a power meter sees no light
during the runway and that the camera reading follows the light in the ≈ 2.9 ms
after the exposure ends.

### 4.3 Impulse measurement and kernel fit

Method (`scripts/alt_wkkkk.py`): the projector showed one white frame (level 255)
and four black frames (WKKKK, a 41.67 ms cycle). Every projector frame triggers the
camera at the set delay, and the frame header's `roi_phase` gives the frame's
position in the cycle, so a delay sweep over one frame (166 delays, 50.2 µs apart)
covers the 41.67 ms cycle: t = phase × 8333.3 µs + delay. Full-frame and window
pictures alternate, and because 2 and 5 are coprime both modes cover all five
phases. At least 4 pictures of each mode were averaged at every (delay, phase).

Result at 1.875 µs exposure (`figures/wkkkk_e1875.png`):
- The window reading is non-zero only during the white frame (≈ 9–15 ms in the
  cycle).
- The full-frame reading shows the same flashes, rises from ≈ 6 ms (≈ 3 ms before
  the first flash), and returns to the dark level immediately after the last flash
  at ≈ 15 ms. No signal remains in the following black frames.

Model, fitted over T on the points where the full-frame reading is below 600 ADU:

  full(t) = g0 · window(t) + k · Σ window(t′) dt′ over t′ ∈ (t, t + T]

| Parameter | Value |
|---|---|
| T | 2962 µs (2860–3060 µs within 0.002 of the best R²) |
| Predicted T for row 512 | 2908 µs |
| k | 2.563 × 10⁻⁴ per µs |
| k × exposure (collection rate during the wait relative to the exposure) | ≈ 1/2100 |
| Runway total, k × T | 0.76 |
| g0 | 0.65 |
| R² | 0.949, rms residual 20.3 ADU |

The datasheet specifies parasitic light sensitivity better than 1/8000; the
measured value is ≈ 4× higher, for this projector's light.

g0 should be 1: unclipped comparisons gave a window/full-frame ratio of 0.997
(section 3) and matching level-64 peaks (section 4.1). The 0.65 is attributed to
clipping at the flash tops (58,798 clipped ROI pixels over the run). With g0 fixed
at 1, the fit gives k = 2.442 × 10⁻⁴ per µs, T = 2962 µs, R² 0.927.

Figure: `figures/kernel_view.png`.

A non-parametric FFT deconvolution (H = F̂·Ŵ\* / (|Ŵ|² + λ max|Ŵ|²), λ = 10⁻³)
reproduced the full-frame curve (R² 0.993) but gave a noisy kernel, because the
window signal is sparse and clipped. A free-form least-squares kernel over 0–6 ms
was unstable (R² 0.33). The parametric box model is used instead.

### 4.4 Deconvolution of the full-frame measurement

The fitted kernel (spike g0 plus a box of height k and length T, anti-causal in the
delay) was inverted by FFT over the 41.67 ms cycle. The minimum of |H| over
frequency is 0.507 (fitted g0) or 0.858 (g0 = 1), so the inversion is stable.

| | Measured full frame | Deconvolved, fitted g0 | Deconvolved, g0 = 1 |
|---|---|---|---|
| Mean error where the window is dark | +30.8 ADU | −0.1 ADU | +5.3 ADU |
| RMS error where the window is dark | — | 26.0 ADU | 23.1 ADU |
| Flash tops | close to the window | 1000–1130 ADU (overshoot) | 530–670 ADU (undershoot) |

The deconvolution removes the inter-flash floors (≈ 300, 150 and 75 ADU in the
measured full frame) and most of the 6–9 ms ramp. The flash tops are not recovered
in either variant, because the measured tops are clipped. One bad sample at 8.27 ms
becomes a −360 ADU artifact after deconvolution. Measured data would need outlier
rejection before deconvolution.

Figure: `figures/wkkkk_deconv.png`.

## 5. Short exposures have a fixed overhead (to be confirmed)

The WKKKK sweep repeated at 0.75 µs instead of 1.875 µs changed unclipped window
readings by a factor of 0.93–0.96, where 0.40 was expected. The full-frame runway
floors changed by a factor of 1.01. One explanation is a fixed overhead of ≈ 20 µs
added to the programmed exposure: (20 + 0.75) / (20 + 1.875) ≈ 0.95. If confirmed,
the exposures reported as 4.875 µs in earlier sweeps were ≈ 25 µs long. This has
not been measured directly. An exposure series in the window mode (no runway)
would measure it.

Consequence: at level 255 the flash tops clip at any programmed exposure. Removing
the clipping requires less light (a lower projected level, a single colour, or an
ND filter), not a shorter exposure.

## 6. Several window pictures per projector frame (burst sampling)

### 6.1 Method

The profiling bitstream fires N triggers after each projector vsync instead of one, at

  t_k = d + k·Δ,  k = 0 … N − 1,  Δ = T_v / N,

where d is the genlock delay and T_v is the vsync period measured by the FPGA
(Δ = (T_v × round(2²⁰/N)) >> 20 in 10 ns ticks). N is set by ROICTL bits 3:1 (N − 1, up
to N = 8). Bursts run only with the sensor in window readout: a window picture takes the
exposure plus ≈ 110 µs from trigger to frame end, against ≈ 5.98 ms for a full-frame
picture at a 250 µs exposure (section 1). One picture is in flight at a time, so each
trigger's labels (trigger ordinal, transmitted top-left pixel, cycle phase) are paired with
its own ROI result as in the single-trigger design, and the burst index k is carried with
them.

A delay sweep over d ∈ [0, Δ) then places a picture at every delay in [0, T_v): the
result for (d, k) is the reading at delay d + k·Δ. A sweep needs 1/N of the delay steps.

Each ROI result is sent as one ASCII line over the board's serial link. Burst lines are
29 bytes (`R=mmm,nnn,rrggbb,cc,hh,ll,k` plus CR LF); single-trigger lines keep the
27-byte format. At 115200 baud a line takes 2.5 ms, which limits N to 3 at 120 Hz;
a bitstream built with `UART_BAUD=921600` carries N = 8.

### 6.2 Measurements

All with a projected white frame every frame (WWWWW), 4.875 µs programmed exposure,
the window ROI, and T_v = 8325.8 µs.

| N | Δ (µs) | Level | Step (µs) | Delay steps | Sweep time | Lines received | Lines lost |
|---|---|---|---|---|---|---|---|
| 3 | 2775.3 | 64 | 50 | 56 | 22 s | 2483 | 0 |
| 6 | 1387.6 | 64 | 50 | 28 | 11 s | 2511 | 0 |
| 6 | 1387.6 | 255 | 50 | 28 | 11 s | 2501 | 0 |
| 6 | 1387.6 | 255 | 5 | 278 | 109 s | 25 091 | 0 |
| 8 | 1040.7 | 255 | 5 | 209 | 84 s | 25 035 | 0 |

Every line had npx = 256. Lines with clipped ROI pixels: 1008 (N = 6, level 255, 50 µs),
10 124 (N = 6, 5 µs) and 10 065 (N = 8, 5 µs), at the flash peaks; the projected spot in
the ROI is bright enough to clip at level 255.

Consistency checks:

| Comparison | Correlation | Median \|difference\| |
|---|---|---|
| N = 8 vs N = 6, both level 255, 5 µs steps | 0.9999 | 0.28 ADU |
| N = 6 vs N = 3, level 64 (at the N = 3 delays) | 0.9311 | 0.28 ADU |
| N = 6, level 255, 50 µs vs the `CAM_ALT` window sweep of section 4.1 (one picture per frame, 167 delays) | 0.9910 | 2.4 ADU |

At level 64 the three short flashes appear at the same delays in the N = 3 burst sweep
(1950/2000, 3375, 3975 µs) as in the single-picture window sweep (1950, 3350, 3950 µs),
and the burst segments k join without a step in the baseline.

The time per delay step is ≈ 0.3–0.4 s at every N (writing and confirming the delay,
discarding the frames in flight, collecting the lines). That fixed cost, not the
number of pictures per frame, now sets the sweep time.

Figures: `figures/burst_vs_single.png`, `figures/burst_N6_vs_N3.png`,
`figures/burst_N6_L255_5us.png`, `figures/burst_N8_lines.png`.

### 6.3 In the UI

`sli_ui.py` detects the board's serial rate (115200 or 921600) and runs burst sweeps
(Sweep > Pictures per frame: 1 full-frame readout, or 3, 6, 8 window readout; automatic
= 8 on a 921600 board). Each picture is stored as an ordinary sweep row at its own delay
d + k·Δ, and the sweep's settings record N, Δ and the readout.

Three WWWWW sweeps through the red, green and blue lens filters were run in the UI at
N = 8: 1670 delays each (0–8315 µs in 5 µs steps), all five cycle positions (8350 rows),
none at or above 1000 ADU. Each took 66 s from its log's first write to its last
(`data/ui_wwwww_{r,g,b}_N8.csv`). A single-picture sweep over the same 1666 delays at
≈ 0.3 s per step takes ≈ 8 min.

These sweeps use window readout, so they contain no runway (section 4).

### 6.4 Limitation: placement-sensitive camera bring-up

One build of the burst bitstream at `UART_BAUD=921600` never brought the camera up:
status 0x3A read 0 (not aligned, not streaming) after repeated loads. The same source at
115200 baud came up normally, and a second 921600 build with only status wires added came
up normally. The UART has no logical connection to the camera's bring-up, so the failure
is attributed to placement; the marginal path has not been identified. Status 0x3A bits
7..1 now report the bring-up stages in order (MMCM locked, IDELAYCTRL ready, eye scan
done, boot ROM done, aligned, streaming), so a failing build is identified on loading.

## 7. Open items

1. Repeat the WKKKK kernel measurement without clipping, using less light, to obtain
   g0, k and T cleanly.
2. Move the ROI to other rows (for example 100 and 900) to test T ≈ r × 5.53 µs + 78 µs.
   The runway maps need this to deconvolve each pixel with its own T.
3. Measure the exposure overhead (section 5) with an exposure series in window mode.
4. Reproduce the script-based results above in `sli_ui.py` before citing them as data.
5. Add the `cdc_max` constraint to the branch's `build_merged.tcl`; fix `KPAR0` for the
   profiling build's runtime window. (The fixes of sections 2.1–2.3 are committed:
   `33b277f`, `6bb5e0a`, `3c1f84b` on `feat/sensor-window-and-sat-fix`.)
6. Find the placement-sensitive path in camera bring-up (section 6.4).
7. **Two-stage linearization.** (a) Linearize the projector per colour using the window
   ROI, which has no runway; this needs one correction table per colour in the FPGA
   (today one 256-entry table is applied to all three channels). (b) Linearize the
   full-sensor image with a per-row runway model, F_r = E + k · (light over the T_r after
   the exposure), which in an SLI capture is a row-dependent linear mix of two consecutive
   patterns; this needs T_r and k measured at several rows (item 2).

## Files in `sensor_window_2026-11-02/`

| File | Contents |
|---|---|
| `data/wsweep_L64.csv` | Delay sweep, level 64, profiling bitstream, full frame and window interleaved |
| `data/altsweep_L255.csv` | Delay sweep, level 255 every frame, `CAM_ALT` bitstream, with clipped-pixel counts |
| `data/wkkkk_e1875.csv`, `data/wkkkk_e075.csv` | WKKKK timelines (41.67 ms, 830 points), above dark, at 1.875 and 0.75 µs |
| `paper/notes/sensor_window_2026-11-02_npy/align100_{full,win}.npy` | 300-picture averages for the alignment check (section 3); 10 MB, kept local (gitignored) |
| `figures/win64_as64x64.png`, `figures/win64_as1280wide.png` | 64 × 64 build before fix 2.1, as decoded and re-laid at 1280 |
| `figures/crop_as_header.png`, `figures/crop_as_1280.png` | 1264 × 1008 build before fix 2.1 |
| `figures/fixed2_as_header.png` | 1264 × 1008 build after fixes 2.1 and 2.2 |
| `figures/align100_side_by_side.png` | Section 3 |
| `figures/wsweep_L64.png`, `figures/altsweep_L255.png` | Section 4.1 |
| `figures/wkkkk_e1875.png`, `figures/wkkkk_e1875_box.png` | Section 4.3 |
| `figures/kernel_view.png`, `figures/wkkkk_deconv.png` | Sections 4.3 and 4.4 |
| `data/burst_N3_L64b.csv`, `data/burst_N6_L64.csv` | Burst sweeps, level 64, 50 µs steps (section 6.2) |
| `data/burst_N6_L255.csv`, `data/burst_N6_L255_5us.csv`, `data/burst_N8_L255_5us.csv` | Burst sweeps, level 255 (section 6.2) |
| `data/ui_wwwww_{r,g,b}_N8.csv` | `sli_ui.py` sweep logs, RGB filters, N = 8 (section 6.3) |
| `figures/burst_vs_single.png`, `figures/burst_N6_vs_N3.png` | Burst against single-picture and N = 3 sweeps, level 64 |
| `figures/burst_N6_L255_5us.png`, `figures/burst_N8_lines.png` | N = 6 and N = 8 at 5 µs steps, level 255 |
| `scripts/` | Capture and analysis scripts as run (paths inside point at the worktree); the burst sweep script is `host/burst_sweep.py` on `feat/sensor-window-and-sat-fix` |

# Sensor readout window: what to build and how to test it

Branch `feat/sensor-window-and-sat-fix`. Written 2026-11-01 as the hand-off for building and
testing on any machine. **Built and run on the Pt V2 on 2026-11-02**: results in section 6, the
burst stages in section 7, and the next work (two-stage linearization) in section 8.

```
git fetch
git switch feat/sensor-window-and-sat-fix
```

---

## 1. Why this branch exists

**The goal is several camera exposures per projector frame**, so that a profiling sweep samples
each frame along the time axis several times instead of once. The target is **6 per frame at
120 Hz**: one exposure every 1389 µs.

Today the sensor reads out all 1280 × 1024 pixels for every picture, which takes about 6 ms, so
only one picture fits in an 8.33 ms frame. The PYTHON 1300 can read out only a window instead.
The datasheet's frame-rate table, fitted to a simple timing model, gives:

| | Time |
|---|---|
| Fixed overhead per picture | ~171 µs |
| Per row | ~1.2 µs |
| Per pixel | ~3.6 ns |
| **A 16 × 16 window** | **~190 µs, about 5,000 pictures/s** |

That is a ceiling. On this board we measured about 1.1 ms from trigger to the first line out,
far more than the datasheet implies, and it isn't yet known whether that delay blocks the next
picture.

**6 per frame works in either case.** Each picture finishes in about 1.14 ms (exposure + 1.1 ms +
readout), inside the 1389 µs spacing, so only one picture is ever in flight. That keeps the
existing frame labelling unchanged, with no label queue.

**Light leakage is not the reason.** Leakage during the readout wait could not be detected in the
2026-10-16 WKKKK data. Exposures taken just before the light arrives read the same as dark frames,
within ±0.1 ADU. The datasheet's worst case (PLS < 1/8000) would predict about 3 ADU. The runway's
light is real projector light, so windowing will not shrink the runway.

---

## 2. What is on the branch

| Commit | What | Build |
|---|---|---|
| `58190de` | **Clipped-pixel count fix.** The merged build always sent 0 saturated pixels. A fully clipped ROI read 1023 with "00 saturated", so the iris dialog could pass a clipped image. | merged |
| `abe0d90` | **Runtime window, profiling build.** ROICTL (0x17) bit 0 switches the sensor between the full frame and a 32 × 24 window around the 16 × 16 ROI. Adds two timings to the status registers. Adds `host/window_check.py`. | `build_roimin.tcl` |
| `7828ba4` | **Build-time window, merged build.** `CAM_WIN=x,y,w,h` builds a bitstream that reads only that window and streams it as video. The viewer takes the frame size from each frame header. | `build_merged.tcl` |

Registers added on the profiling build (also in `host/SLI_UI.md` §14):

| Reg | Meaning |
|---|---|
| 0x17 bit 0 | 1 = read out only the window. Every existing tool writes 0x00 / 0x40 / 0xC0, all window-off |
| 0x42–0x44 | trigger → first line out, last frame, 10 ns ticks |
| 0x45–0x47 | readout length (first line → frame end), 10 ns ticks |
| 0x48 bit 0 | the sensor is reading the window now |

---

## 3. Before you start

- **Vivado 2026.1**, at `C:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat` (not on `PATH`).
- **Python 3** with `numpy`, `pyserial`, `matplotlib`, `pillow`, plus `ftd3xx` and FTDI's D3XX
  driver for the Ft+ viewer.
- The board's COM port, usually COM5 or COM6. **Close `sli_ui.py`** before running any other
  script, or the port will be busy.

---

## 4. Test steps, in order

Each step needs the one before it to pass.

### Step 1: full-frame merged build

```
vivado -mode batch -source build_merged.tcl     # CAM_WIN must be UNSET
```

Output: `build_merged/Au2_SLI_merged.bin`. Flash it, then:

| Check | Pass |
|---|---|
| `ft_usb_video/host/cam_live.py` | normal video. (It prints `frame size from the FPGA: ...` only when the size differs from 1280 x 1024, so a full-frame build prints nothing.) |
| Iris dialog in `host/sli_ui.py`, camera aimed at something bright enough to clip | "saturated px" goes **above 0** (it was always 0 before) |

Then aim the camera at a real scene, for example out of an office window, and **Save TIFF** in
`cam_live.py`. This is the reference for step 2. **Do not move the camera or change the exposure
until step 2 is done.**

### Step 2: 64 × 64 merged build (visual crop test)

```powershell
$env:CAM_WIN = "608,480,64,64"                  # the 64x64 at the centre of the sensor
vivado -mode batch -source build_merged.tcl
Remove-Item Env:CAM_WIN                         # DO THIS, see below
```

Output: `build_merged/Au2_SLI_merged_win64x64.bin`. Flash it, open `cam_live.py` (it should print
`64 x 64`), and Save TIFF again.

> **`CAM_WIN` stays set for the rest of that PowerShell session.** Forget to clear it, and the
> next "full-frame" build is silently a 64 × 64 one. The build log prints
> `### CAM_WIN = ...` near the start, so check it there.

**Pass:** the 64 × 64 picture matches rows 480–543, columns 608–671 of step 1's TIFF. The two are
different exposures, so they match to within noise, not pixel for pixel:

```python
import numpy as np
from PIL import Image
full = np.asarray(Image.open("full.tif"), dtype=np.int32) >> 6     # TIFFs are MSB-aligned
win  = np.asarray(Image.open("win64.tif"), dtype=np.int32) >> 6
crop = full[480:544, 608:672]
print("mean |diff| %.1f ADU, corr %.4f" % (np.abs(crop - win).mean(),
      np.corrcoef(crop.ravel(), win.ravel())[0, 1]))
for dy in range(-8, 9):                                             # is it shifted?
    c = full[480 + dy:544 + dy, 608:672]
    print(dy, round(np.corrcoef(c.ravel(), win.ravel())[0, 1], 4))
```

The correlation should be highest at `dy = 0`. A peak somewhere else means the window is that many
rows off.

| If you see | It means |
|---|---|
| **Garbled rows at the top** of the 64 × 64 image | The sensor's black reference rows (5, set by reg 197 in `cam_boot_seq.v`) are captured inside the frame. You can't see that on a full frame, but it's obvious on a small one. The capture in `cam_frame_ft.v` would have to skip them. |
| The viewer never shows a frame | Check the build log's `CAM_WIN` line, then the header: `campack.parse_header` must report `ncol 64, nrow 64, fbytes 5120`. |
| `npx 0` / ROI invalid in the viewer | **Expected.** The fabric ROI's default position (80, 64 → pixel 640, 512) is outside a 64 × 64 frame. The picture is unaffected. |

### Step 3: profiling build (runtime window, for the 6-per-frame plan)

```
vivado -mode batch -source build_roimin.tcl
```

Output: `build_roimin/Au2_SLI_roimin.bin`. Flash it. Turn on genlock and offline video (Connect
in `sli_ui.py` does both), close `sli_ui.py`, then:

```
python host/window_check.py COM5
```

| Column | Pass |
|---|---|
| npx | 100 % in both modes |
| window − full | within about ±1 ADU |
| gaps | 0 |
| readout | about 6 ms full frame; about 40–60 µs with the window |
| trig→fs | about 1.1 ms full frame; record what the window gives |

**For 6 per frame, trig→fs + readout must stay under about 1.38 ms**, also at a 250 µs exposure
(`--expo-units 667`).

| If you see | It means |
|---|---|
| npx below 100 % with the window on | The pixel decoder (`cam_sync_decode.v`) mishandles very short lines, which it has never seen |
| Means differ but npx is 100 % | The averaging patch is on the wrong rows inside the window (`cam_roi_min.v`, "THE ROI MEAN") |
| "the sensor never reported window ON" | The sensor ignored the window register writes (`cam_boot_stage1.v`, `P_WIN`) |

---

## 5. Open questions these tests answer

1. **What is the 1.1 ms?** Step 3's trig→fs with the window on. If it stays near 1.1 ms, it's a
   fixed delay: fine for 6 per frame, but it caps how far beyond 6 we can go.
2. **Are black reference rows inside the captured frame?** Step 2. The profiling build's window is
   built to tolerate 0–8 black rows either way.
3. **Does the window crop where we think?** Steps 2 and 3.

---

## 6. Results, 2026-11-02

Steps 1–3 were run on the Pt V2. Measurements and figures:
`paper/sensor_window_2026-11-02.md` on `feat/frame-label`.

- **Two firmware defects found and fixed.** The build-time window was written before the
  sequencer was enabled and not retained (SPI readback: 256 = 9F00, 257 = 0000); it is now
  written after `streaming`. Windows starting on an odd kernel came out mirrored in 8-pixel
  groups; `cam_sync_decode` now takes the line's first-kernel parity (`KPAR0`).
- **The window reads the full-frame ROI's pixels** (offset 0 rows / 0 columns, corr 0.9997,
  ROI means 178.01 vs 177.49), checked with the `CAM_ALT=1` build.
- **Answers to section 5.** (1) The 1.1 ms was the full frame at a longer exposure: trig→fs is
  78.2 µs at 4.875 µs and 323.4 µs at 250 µs, the same with the window; the window's readout
  is 27.6 µs against 5659.4 µs. (2) The window picture has 24 rows, with the same row count
  before the ROI as a full frame. (3) Yes.
- **The full frame and the window disagree because of the runway.** A full-frame reading of row
  512 also collects light for ~2.9 ms after the exposure, until that row is read out (WKKKK fit:
  T = 2962 µs against 2908 µs predicted, ~1/2100 of in-exposure sensitivity). The window has
  no runway.

## 7. Stages 2 and 3: done

- **Burst triggers** (`cam_roi_min`, ROICTL bits 3:1 = N − 1): up to **8** pictures per projected
  frame, window only. Lines end in `,k`; non-burst lines are unchanged. `build_roimin.tcl
  UART_BAUD=921600` carries N = 8 (N ≤ 3 at 115200). Measured: N = 8, 5 µs steps, 209 delays in
  84 s, 0 of ~25 000 lines lost.
- **The app.** `sli_ui.py` detects the board's serial rate and runs burst sweeps (Sweep >
  Pictures per frame), **6 per frame by default** on a 921600 board (1 otherwise). Six ~27-byte
  lines per frame are ~21% of 921600 baud at 120 Hz; the camera lines travel on the serial port,
  not the Ft+. `host/burst_sweep.py` does the same from the command line.
- **Known issue: placement-sensitive camera bring-up.** One 921600 build never aligned; the same
  logic plus status wires came up normally. After every build, check status 0x3A: bit 2
  (aligned) and bit 1 (streaming) must both be 1. Bits 7..4 show where bring-up stopped
  (MMCM, IDELAYCTRL, eye scan, boot ROM).

## 8. Next: two-stage linearization

The runway changes how linearization has to be done.

**Stage 1: linearize the projector with the window ROI.** The window reading is the light
during the exposure only, so it measures the projector's output without the runway. Measure
each colour's response (R, G, B independently) and build one correction table per colour.

*The goal (2026-11-12):* `pattern_gen` makes ONE white cosine (0..255). The `corr` table
maps each value c to (`R[c]`, `G[c]`, `B[c]`), so that each channel's light is a clean
sinusoid. All three channels keep the same phase and frequency; only the shape differs per
channel, and `pattern_gen` does not change.

*Decided and built (2026-11-11):*
- **Hardware.** Three `corr` tables in the FPGA: upload target 0x06. IMPRGB bit 3 sends the
  impulse level through them, so the sweep reads the same tables the fringes index.
- **Measurement.** Lens colour filters with all three channels on, matching fringe use.
- **Exposure.** One long window exposure per level, at the placed exposure (6.2 ms).
- **Levels.** All 256.
- **Method.** The White level tab's Linearize R/G/B loop (`host/SLI_UI.md` §9).

*Still open:*
- **Dark end.** The projector is flat below about level 50, so sinusoid troughs are built from
  a few coarse levels. Accept that, or narrow the cosine's range.
- **Per-channel amplitude.** Equalise it, or leave it, which is fine if each channel is
  decoded on its own.
- **Persistence.** The tables are lost at power-off: upload them at startup, then flash them.
- **Final check.** Fit a sinusoid per filter on real fringes: amplitude, offset and harmonics.

**Stage 2: linearize the full-sensor image, with the runway.** A full-frame pixel in row r
reads E + k · (light over the T_r after the exposure), with T_r ≈ 78 µs + r × 5.53 µs
(predicted; measured only at row 512). In an SLI capture that light is mostly the next
pattern, so each row is a row-dependent linear mix of two consecutive patterns. With the
projector made linear by Stage 1, the mix can be undone per row. Needs T_r and k measured at
several rows and columns: WKKKK on the `CAM_ALT=1` build with the ROI moved, without clipping.

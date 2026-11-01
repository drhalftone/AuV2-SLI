# Sensor readout window: what to build and how to test it

Branch `feat/sensor-window-and-sat-fix`. Written 2026-11-01. **None of this has been compiled or
run on hardware yet**; this file is the hand-off for doing that, on any machine.

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

Registers added on the profiling build (also in `host/SLI_UI.md` §12):

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
| `ft_usb_video/host/cam_live.py` | normal video; the console prints `frame size from the FPGA: 1280 x 1024` |
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

## 6. What comes next (not written yet)

- **Stage 2: burst triggers.** The FPGA fires N triggers per vsync at delay d + k·Δ (N up to 6,
  Δ = T/6), with a burst index in each camera line. The COM port moves from 115200 to 921600
  baud: 720 lines/s × 27 bytes needs about 195 kbaud. Exposures in burst mode are limited to
  about 250 µs; runway maps stay at 1 per frame.
- **Stage 3: the app.** `sli_ui.py` sweeps the delay from 0 to T/6 instead of 0 to T, so 278 steps
  instead of 1666, and a sweep drops from about 8 minutes to under 2.

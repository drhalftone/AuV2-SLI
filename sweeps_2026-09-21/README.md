# 5 us sweeps, 21 September 2026

Every file is a delay sweep across one 8325.8 us frame (120 Hz) read from the camera's 16x16 ROI.
Delay and mean are in the first two columns. Dark level is the median of 7000-7900 us.

| File | Scene | Filter | Notes |
|---|---|---|---|
| `sli5us_red.csv` | high-frequency SLI scan (octet 2, white), FPGA offline mode | red 620 nm (10 nm) | 8-pattern mean, 5 us slices, `host/sli_frame_sweep.py` |
| `sli5us_blue460.csv` | same | blue 460 nm | "triggers lost" 6622 in the run summary |
| `sli5us_green.csv` | same | green, **wavelength not recorded** | a first attempt crashed part-way and was rerun |
| `white_sweep_red620_5us_cutout102.csv` | white + 102 px black cutout at (384, 592), W = 0 | red 620 nm | cutout sized so the 5 us reading at 8000 us matches the SLI level (158.22); `hole_red.json` |
| `white_sweep_blue460_5us_cutout104.csv` | white + 104 px cutout | blue 460 nm | target 161.95; `hole_blue.json` |
| `white_sweep_GREENFILTER_cutout180_5us.csv` | white + 180 px cutout | **green** (a green filter was in by mistake; the cutout was sized for the blue target) | valid green data, wrong cutout |
| `white_sweep_blue_check.csv` | solid white, 30 us slices | blue 460 nm | filter check; clips at 1023 |
| `hole_*.json`, `hole_red_FIRSTTRY_median.json` | cutout results from `host/hole_match.py` | | the FIRSTTRY file is the median-based result that was not trustworthy (120 px; true level 157.56) |
| `led_windows_plan_centred.csv` | single 900 us exposure, 8 trigger delays + background | | from `host/led_windows.py --centre --background-delay 7000` on the three SLI sweeps |
| `align_roi_run10.csv` | projector-ROI alignment | blue filter | ROI seen at (376, 584)-(392, 600), centre (384, 592) |

Caveats: the blue LED's level between pulses is raised by ~11-13 ADU before each pulse and ~0 after
it, in both the SLI and the cutout scene (see the report); red's is flat within +-6 ADU.

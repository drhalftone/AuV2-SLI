# lag_search_tile_closeup_ml750st.csv

Figure: `report_figures/fig_background_led_tiling_closeup.png`

- **Date:** 29 September 2026
- **Projector:** Optoma ML750ST, 800x600 @ 120 Hz (8335.1 us frame)
- **Camera:** PYTHON 1300 bare (no lens), mounted on the projector lens; 16x16 ROI; no filter
- **Video:** FPGA impulse mode, 5-frame cycle, black except one white (255) frame at position 0
- **Command:** `python -u host/lag_search.py COM5 --start 100 --tile 2 --out lag_search_tile.csv`
  (coarse 100 us windows tiling the frame, then the whole lit span re-tiled at 50 us and 25 us;
  40 frames per reading, 10 discarded after each change)

Columns: `stage` (coarse, tile1, tile2), `expo_us`, `delay_us` (within the frame), `position`
(frame 0-4 after the white frame was sent), `t_us` = position*T + delay, `mean` (ROI mean of the
frames at that position), `sd`, `n`, `ref` (median over the five positions at that delay, the dark
reference), `lit` (above 2 ADU/us over `ref`).

What it shows: the white frame's light fills frame 1 (one frame of lag); a glow starts ~6200 us
into frame 0 and a tail runs to ~2100 us into frame 2 (weak to ~6000 us). Each halving of the
exposure roughly doubles the apparent rate, so most of each reading near the lit frame is a fixed
per-reading offset, not light in the window. A same-day exposure-scaling check agrees: at frame 1
+7200 us the ROI read ~855 ADU above dark at every exposure from 5 to 80 us.

The first/last-light markers on the figure come from the absolute 2 ADU/us threshold and are
not LED edges in this run.

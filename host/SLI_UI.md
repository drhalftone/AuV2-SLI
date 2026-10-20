# `sli_ui.py` — the projector-profiling app

`sli_ui.py` is a Tk desktop app that measures **when, within each video frame, a DLP
projector actually puts out light**. It uses that measurement to place the camera's exposure
window. It drives the AuV2-SLI board over its COM port and reads back the 16×16 ROI mean
from every camera frame. It sweeps the camera trigger's genlock delay across five
consecutive projector frames, and shows the result as a timeline you place the exposure on.

Everything a session measures is saved in one JSON document, a **`.sliprof`**.

```
python host/sli_ui.py [profile.sliprof]
```

Any Python 3 that has the packages in [section 1](#1-self-contained-what-it-needs) works.
A `.sliprof` given on the command line is opened at start-up.

The background (why profile, which traps the measurement has to avoid) is in
[`../PROJECTOR_PROFILING_PLAN.md`](../PROJECTOR_PROFILING_PLAN.md).

---

## Contents

1. [Self-contained: what it needs](#1-self-contained-what-it-needs)
2. [Hardware setup](#2-hardware-setup)
3. [Concepts](#3-concepts)
4. [Workflow, step by step](#4-workflow-step-by-step)
5. [Profile tab](#5-profile-tab)
6. [Adjust iris dialog](#6-adjust-iris-dialog)
7. [RGB filters tab](#7-rgb-filters-tab)
8. [Runway maps tab](#8-runway-maps-tab)
9. [Menus and keys](#9-menus-and-keys)
10. [Files it reads and writes](#10-files-it-reads-and-writes)
11. [How a measurement works](#11-how-a-measurement-works)
12. [Board registers used](#12-board-registers-used)
13. [The embedded mode table](#13-the-embedded-mode-table)
14. [Troubleshooting](#14-troubleshooting)
15. [Code map](#15-code-map)

---

## 1. Self-contained: what it needs

The app is **one file**. Copy `sli_ui.py` anywhere and run it; it imports nothing else from
`host/`.

| Needs | Why | When |
|---|---|---|
| Python 3 with Tk (the python.org Windows installer includes it) | the UI | always |
| `numpy` | sweeps, fits, maps | always |
| `pyserial` | the board's COM port: every register read/write and every camera line | always |
| `matplotlib` | the plots (embedded in Tk) | always |
| `ftd3xx` + FTDI's D3XX driver | turning genlock on over the Ft+ (USB 3) | **only if genlock is off** when you connect |

```
pip install numpy pyserial matplotlib
pip install ftd3xx            # only needed if genlock has to be turned on
```

`ftd3xx` is imported only inside `genlock_on()`. If the board's genlock is already on, the
app never touches the Ft+. If genlock is off and `ftd3xx` is missing, Connect fails with a
message saying so.

**Where the code came from.** Until 2026-10-19 the app imported `lag_core`, `frame_sweep`,
`gray_pair`, `sli_background`, `ftlink` and `align_roi`. The functions the three tabs call
were copied into the top of `sli_ui.py`. `align_roi` was dropped because nothing in the app
used it: it only set the PC's display mode, and the app always runs offline. The original
modules are unchanged and still back the command-line tools. **A protocol fix made there has
to be made here as well.**

The copy was checked against the originals with a simulated board. Both versions gave
byte-identical results for:
- the 5-frame sweep (with and without the frame-ordinal tag);
- the grey-pair runway measurement;
- every register write, in the same order;
- the mode table;
- the EDID name, exposure limit and runway split.

---

## 2. Hardware setup

- **The AuV2-SLI board**, running a bitstream with the impulse generator, genlock and the
  per-frame ROI stream. Either the merged build or the profiling bitstream works (see
  [frame tags](#frame-tags-and-the-two-bitstreams)).
- **The board's COM port** (USB UART), at 115200 baud. Everything the app does goes over
  this port. It is usually COM5 or COM6; the Connect dialog lists every port.
- **The projector** on the board's HDMI-OUT. The app reads the projector's EDID through the
  board, for its name and the modes it supports.
- **No PC video on HDMI-IN.** The app runs **offline only**: the board generates the video
  itself. If a PC is plugged into HDMI-IN, the board is in pass-through, and Connect refuses
  with *"unplug the PC's HDMI from the board"*.
- **The Ft+ on USB 3**, only to turn genlock on. Genlock resets to off on the merged build.
- **The camera** pointed at the projector's lens. The RGB filters tab also needs a red, a
  green and a blue filter that can be mounted on the lens.

---

## 3. Concepts

### The 5-frame cycle

The board's impulse generator repeats a 5-frame pattern. Frame 0 is the **odd frame**
(level `IMPLVL2`); frames 1–4 share one level (`IMPLVL`). The app uses two patterns:

| Sweep | Frame 0 | Frames 1–4 | What it shows |
|---|---|---|---|
| **WWWWW** | 255 | 254 | Always white. When the projector emits light, period after period. Frames 1–4 are 254 rather than 255 only so that frame 0's tag still marks where the cycle starts. |
| **WKKKK** | 255 | 0 | One white frame, then four black. The light of a **single frame**, including any that spills into the frames after it. |

All three LEDs are on (`IMPRGB = 0x07`). The RGB filters tab colours the light with filters
on the lens, not by switching LEDs.

### The timeline

Every plot's x-axis is **time after the vsync of frame 0**, from 0 to 5T, where T is the
measured frame period (8333.3 µs at 120 Hz). Grey vertical lines separate the frames.

A reading taken at genlock delay *d* in frame *k* is plotted at `k·T + d`. Each delay
therefore gives five points, one per frame.

### Units

| Quantity | Unit | Notes |
|---|---|---|
| Genlock delay | 0.01 µs (one tick, 100 MHz) | 24-bit register, 0x1C–0x1E |
| Exposure | 0.375 µs | camera opcode 1's unit. A requested 5 µs becomes 13 units = 4.875 µs |
| Frame period | 0.01 µs | measured by the FPGA; regs 0x4A–0x4C |
| ROI mean | ADU, 10-bit (0–1023) | the mean of the 16×16 ROI; 256 pixels |

### The exposure limit (120 fps)

The FPGA computes the longest exposure that keeps the camera at the video rate:
`MAXEXP = (period ticks − 9000) × 27962 >> 20`. That is T − 80 µs sensor gap − 10 µs margin,
in whole 0.375 µs units. At 120 Hz it is **21982 units = 8243.25 µs**. A longer exposure
drops the camera to 60 fps.

The app reads the board's own value (regs 0x53/0x54) when connected. Otherwise it computes
the same formula from the document's frame period. The exposure edges cannot be dragged
wider than this limit.

### Vsync to first active line

The time from vsync to the first active pixel depends on the mode: `T × (VSYNC + VBP) /
VTOTAL`. For 800×600@120 that is 33 of 636 lines, **432.4 µs** at T = 8333.3 µs. It is
recomputed for the mode in use at every connect. Before a connect, and when a CSV is
imported, the app uses the fixed 800×600@120 value `ACTIVE_US = 432.5`.

The sweep uses it to correct the frame tag. A trigger before the first active pixel still
carries the previous frame's tag, so its reading belongs one position later.

---

## 4. Workflow, step by step

This is the same sequence as **Help → Instructions** (F1) in the app.

1. **Connect.** Unplug the PC's HDMI from the board. **File → New…** opens the Connect
   dialog.
   - Pick the COM port.
   - Pick the projector mode: 800×600 @ 120 Hz for 120 fps work.

   **OK** does the rest: switches the board to that mode, turns genlock on, checks the frame
   period, and opens an empty document.
2. **Run WWWWW**, then **Adjust iris…**. Turn the lens iris until the live reading sits in
   the green band with *no* saturated pixels, then press Done. If the iris changed, run
   WWWWW again.
3. **Run WKKKK.** Where the WWWWW and WKKKK traces agree, you are looking at one frame's
   light. Where WKKKK has light that WWWWW's period does not explain, light is spilling from
   a neighbouring frame.
4. **Place the exposure.** Until you move the edges, they sit on the best window: the
   longest allowed exposure that holds the most of the white frame's light. Drag them, or
   type values.
5. **Mark the runway**: where projector light may start *before* the exposure (cyan).
6. *(Optional)* **RGB filters tab**: mount each filter in turn and scan it.
7. *(Optional)* **Runway maps tab**: measure the runway as grey-pair maps.
8. **File → Save** writes the `.sliprof`. **Sweep → Export exposure window JSON…** writes
   the window for other tools to use.

Only **one measurement runs at a time** in the whole app: any sweep, the iris dialog, or the
runway maps. The other Run buttons are disabled while one is running.

---

## 5. Profile tab

**WWWWW (blue) and WKKKK (red)** on one 5-frame timeline, with the exposure edges on top.

### Controls

| Control | Does |
|---|---|
| **Run WWWWW** / **Run WKKKK** | Runs that sweep, replacing the one in the document (it asks first). Connects if needed. |
| **Stop** | Ends the sweep after the current delay. Readings so far are kept; the sweep is marked INCOMPLETE. |
| **Adjust iris…** | The [iris dialog](#6-adjust-iris-dialog). Needs a WWWWW sweep. |
| **runway / leading edge / trailing edge (µs)** | Type a value; it applies on Enter or when the field loses focus. The same rules as dragging apply. |
| **Best window** | Keeps the current width (capped at the limit) and moves it to the most white-frame light. Needs WKKKK. |
| **Whole view** | Back to all five frames. |

### Sweep parameters (fixed in code: `SETTINGS`)

| | Value |
|---|---|
| exposure | 5 µs requested → 13 units = 4.875 µs |
| delay step | 5 µs, from 0 to T − exposure − 1 µs (1666 delays at 120 Hz) |
| frames per delay | 20 kept, after 4 discarded (`settle`) |
| vsync → active | from the mode, at connect |

Each delay gives five readings, one per frame position. A delay where any of the five
positions is missing is skipped and logged to the console.

### The three edges

| Line | Colour | Meaning |
|---|---|---|
| **Leading edge** | green, solid, box at 93 % height | when the exposure **opens**: the genlock delay after frame 0's vsync |
| **Trailing edge** | purple, solid, box at 93 % | when the exposure **closes** |
| **Runway start** | cyan, dash-dot, box at 85 % | where projector light may start **before** the exposure. The runway is the stretch from here to the leading edge |

The rules:
- **Dragging green** moves purple and the runway with it. The width and the runway's
  distance before green are kept.
- **Dragging purple** keeps green fixed. Purple is held between green + 0.375 µs and
  green + the exposure limit.
- **Dragging cyan** keeps it at or before green, and no more than one frame before it. It
  can reach back into the K frame before the W.
- The leading edge is clamped to 0 … 5T. The trailing edge goes wherever the width puts it,
  so it can run past 5T.

**Defaults, until you touch them:**
- The edges sit on the best window: the start that puts the most WKKKK light (dark level
  subtracted) inside the longest allowed exposure. This is a circular box filter over the
  five frames. With no WKKKK sweep, green sits at the first active line.
- The runway starts where the WKKKK light, walking back from green, falls below **2 % of
  its peak**. It goes back at most one frame.

Once you move any edge, all three are stored in the document (`edges`) and stop following
the data.

### Shading

- **Green**: the exposure (lead → trail).
- **Cyan**: the runway (runway → lead).
- **Orange hatching with dashed ends**: the **background exposure**. That is the rest of
  each frame, from the trailing edge to the next leading edge (trail → lead + T), drawn in
  all five frames and wrapping at 5T.

### Readout lines

1. **Foreground.** The exposure's delay (as frame + offset), its length in µs and in
   0.375 µs units, and the share of the white frame's light inside it. Then the limit, and
   **OVER THE LIMIT** if it is exceeded, which also turns all three readout lines red. The
   unit count here is truncated to whole units.
2. **Background.** Where the background exposure starts and ends within the frame, and its
   length.
3. **Runway.** Where it starts (frame + offset), how far before the exposure, and the share
   of the white frame's light on it.
4. **Cursor.** The mouse's time (frame + offset) and ADU.

"White-frame light" is the WKKKK sweep with its dark level subtracted. The dark level is the
median of frames 2–4, which the white frame never reaches. It is clipped at zero and
resampled onto a uniform grid. Without a WKKKK sweep these shares read "- (needs WKKKK)".

### Mouse

- **Wheel**: zoom the time axis around the cursor. No closer than 2 µs across.
- **Drag empty plot**: pan in time. Let go while moving and it glides to a stop.
- **Double-click**: back to all five frames.
- **Drag a coloured box**: move that line. The boxes are a fixed size on screen at a fixed
  height, so zooming never loses them.
- **Only time zooms and pans.** The ADU axis always spans every value the sweeps have read,
  and grows as a sweep runs. The time axis never goes past 0 or 5T.

### The status line

- **While a sweep runs**: the delay count, elapsed time, time left, the number of saturated
  readings (≥ 1000 ADU), and the temp-folder CSV that every reading is also written to.
- **Otherwise**: a summary of each sweep (readings, saturated, measured or imported, when,
  and INCOMPLETE if stopped).

---

## 6. Adjust iris dialog

The dialog holds a short exposure on the **brightest delay of the WWWWW sweep** and shows
every reading live, so you can turn the lens iris until the peak stops clipping.

- **The delay it uses.** The WWWWW sweep is folded onto one frame and averaged per delay.
  It takes the middle of the longest run of delays within 0.5 ADU of the top. When the top
  clips at 1023 there is no single maximum, and this picks the delay most surely inside the
  bright slot.
- **What it runs.** The same white cycle as WWWWW (255/254) at 13 units (4.875 µs).
- **Top chart**: ROI mean for the last 300 readings. The green band is the target,
  **750–900 ADU**, and the red dashed line is 1023.
- **Bottom chart**: **saturated pixels** in the ROI. This is the number that matters: the
  mean can sit below 1023 while the brightest pixels already clip. Older bitstreams don't
  send this count; it then shows "-".
- **Hints**:
  - *SATURATED, close the iris*: any saturated pixel, or a mean of 1022 or more.
  - *below the target band*.
  - *above the band, not clipping*.
  - *in the target band, nothing clipping, Done*.
- **delay (µs)**: type a different delay and press Enter to move the hold point.
- **Done** stops the camera's impulse mode. It records `iris` in the document: the delay,
  the exposure, the median of the last 30 readings, and the worst saturated count among
  them. If the document already holds sweeps, it says they were measured at the old setting
  and must be re-run.

---

## 7. RGB filters tab

The WWWWW sweep repeated through a **red, a green and a blue filter on the lens**, one sweep
per filter, all on one plot.

- **Scan R / G / B filter (WWWWW).** Mount the filter, then press its button. Each filter is
  stored under its own key (`wwwww_r`, `wwwww_g`, `wwwww_b`), so re-scanning one leaves the
  others alone.
- **Show** picks one view:
  - **Raw**: the sweeps as measured.
  - **Normalized**: each sweep minus the camera's dark level, divided by its peak. Use this
    to compare shapes.
  - **Fit to white**: WWWWW minus dark, fitted by least squares as
    `cR·R + cG·G + cB·B + offset`. The filter sweeps are interpolated onto WWWWW's delays,
    also minus dark. Each filter is drawn scaled by its weight, over WWWWW (a wide grey
    band), with the sum as a black dashed line. The line under the buttons gives the three
    weights, the offset, the dark level, the residual RMS (overall, and where WWWWW is above
    5 % of its peak), the largest residual and where it is, and R².

    This view needs WWWWW and all three filter sweeps, each with at least 10 readings.
- **The camera's dark level.** WWWWW never goes dark, so the dark level is taken from the
  Profile tab's WKKKK sweep (median of frames 2–4). Without one, it falls back to each
  sweep's minimum.
- The Profile tab's **edges, runway and shading** are drawn here too. They can be dragged
  here; the drag goes through the Profile tab's rules, so both tabs always agree.
- Mouse: wheel zoom, drag pan (no glide), double-click or **Whole view** for all five frames.

Documents from before 2026-10-19 may hold **WKKKK** filter sweeps (`wkkkk_r/g/b`). The tab no
longer shows them, but **Sweep → Export** can still write them out.

---

## 8. Runway maps tab

This tab measures the **runway** (the Profile tab's cyan region) as **grey-pair maps**. They
show how the reading in the runway depends on the levels of the frames around it.

### How it measures

The impulse generator switches to a **2-frame cycle, A B A B**, with two full-field levels
and all LEDs. A reading in the runway's frame then sees that frame's level (x), and frames
k−1 and k+1 are both the other level (y). That means "the frame before" and "the exposure
frame after" are the same level.

For each unordered pair {A, B}, both orders (A→B and B→A) are collected; A = B gives the
diagonal. Each map pixel is the ROI mean for one (y, x) cell.

**Waiting for the new levels.** After switching levels, the app waits until the frame tag
shows the new level. It then drops two more frames, so the image on screen (one frame
behind) is new too. If the change never shows within 1 s, the pair is counted as **level
change not seen**.

**The runway is split** where the previous frame's exposure ends (trail − T), giving up to
two maps:

| Map | From → to |
|---|---|
| **runway inside the previous exposure** | runway start → min(trail − T, lead), if trail − T is after the runway start |
| **runway between exposures** | max(runway start, trail − T) → lead, if that is a positive span |

Each map is measured with that part's own exposure, in whole 0.375 µs units, at that part's
delay within its frame. If either part is longer than the exposure limit, it refuses to
start.

### Controls

| Control | Does |
|---|---|
| **Run runway maps** | Measures both parts, replacing the document's maps (it asks first). Disabled until a runway exists. |
| **Stop** | Ends after the current pair. Cells so far are kept; the maps are marked INCOMPLETE. |
| **Levels** | every 8th (33 levels), every 4th (65), every 2nd (129), or all 256 |
| **samples per cell** | readings averaged per (prev, this) cell, 2–100 (default 10) |
| **Auto colour** | puts every map's top colour back on its largest cell (undoes the max sliders) |

**Time.** The info line shows an estimate. For two windows at 120 Hz with 10 samples per
cell, the formula gives:

| Levels | Pairs | Estimate |
|---|---|---|
| every 8th (33) | 561 | ~7 min |
| every 4th (65) | 2 145 | ~27 min |
| every 2nd (129) | 8 385 | ~1.7 h |
| all 256 | 32 896 | ~6.9 h |

These come from `estimate()`, `windows × pairs × (4·per + 5) frames × T`. They are not
measured run times.

### The maps

The tab is one or two 2-D colour maps side by side, one per runway part, each with its own
colour bar. They are `imshow` images, one square cell per level pair, with no interpolation
between cells.

| | |
|---|---|
| **x** (columns) | the level of the runway's frame (`this_level`) |
| **y** (rows) | the level of the frames before and after it (`prev_level`); A B A B makes them the same |
| **Origin** | bottom left: levels increase to the right and upwards, so the diagonal runs from bottom left to top right |
| **Cells** | one per level in the chosen set (33, 65, 129 or 256 a side), stretched to fill the axes. The tick labels show about every eighth level |
| **Colour** | ROI mean, magma, **log scale**. The colour bar reads *ROI mean (ADU, log scale)* |
| **Scale** | each map on its own: from its smallest positive cell (at least 1 ADU) to its largest, re-fitted as cells arrive |
| **max slider** | the vertical slider right of each colour bar sets that map's top colour, in ADU (log travel, value shown below it). Brighter cells clip to the top colour, so dim regions get the whole colour range. Each map keeps its own setting while cells arrive, until **Auto colour** |
| **Title** | the part's name, its span in µs into the frame, and its length |

How the cells fill:
- Each pair {A, B} fills two cells that mirror each other across the diagonal: (y = B,
  x = A) and (y = A, x = B). A pair with A = B fills one diagonal cell.
- A cell not yet measured is blank. So is a cell that read 0 ADU, which a log scale cannot
  show.
- The maps fill in while the run proceeds, redrawn every 300 ms.

What is on screen:
- **If the document holds maps**, the tab shows them, with the windows and levels they were
  measured with. Moving the runway or changing Levels does not redraw them.
- **Otherwise** it shows empty grids for what Run would measure now (the current runway's
  parts and the Levels setting).
- **With no runway**, it shows *"No runway yet: set the edges and the runway in the Profile
  tab."*

The maps have no zoom, pan or cursor readout. To read values, take them from the
document's `runway_maps.rows` or the temp-folder CSV.

**The two text lines above the maps:**
1. **Status.** While running: the window, the current pair, pairs done of total, elapsed
   and remaining minutes, the *level change not seen* count, and the log file. Otherwise:
   the saved maps' cell count, when they were measured, their levels and samples per cell
   (or INCOMPLETE).
2. **Info.** The runway as it is now: each part's span, the Levels choice, and the time
   estimate.

---

## 9. Menus and keys

| Menu | Item | Key |
|---|---|---|
| File | **New…**: the Connect dialog, then an empty connected document | Ctrl+N |
| | **Open…**: a saved `.sliprof`, *not* connected; the first Run reconnects | Ctrl+O |
| | **Save** / **Save As…** | Ctrl+S / Ctrl+Shift+S |
| | **Close** (asks about unsaved changes, and about a running sweep) | Ctrl+W |
| | **Exit** | |
| Sweep | **Import … CSV**, one per sweep key: a `lag_sweep.py` CSV | |
| | **Export … CSV**, one per sweep key | |
| | **Export exposure window JSON…** | |
| Help | **Instructions**: a scrolling window that can stay open beside the plot | F1 |

Saving is refused while a measurement runs.

### The Connect dialog

- **Camera COM port.**
  - **Refresh** re-lists the ports.
  - **Check** opens the port and reports the frame period and genlock state (live, enabled
    but not live, or off).
- **Projector.** The projector's EDID name, and the modes its EDID supports that the board
  can generate. The mode in use is marked. **Ask the board** re-reads them.
- **When reopening a document**, the document's saved mode is preselected. The dialog says
  if that mode is not supported, or if a different projector is attached.
- **OK**:
  1. switches the board's offline mode (MODEFORCE), if it isn't already in that mode;
  2. checks the board is offline with a valid mode;
  3. turns genlock on if it is off;
  4. checks the frame period is plausible and genlock is enabled *and* live.

  Any failure is shown in the dialog, and it stays open.

### Reconnecting a saved document

When a reopened document is first asked to measure, the app tries its saved COM port. It
proceeds without the dialog only if three things match: the projector EDID name, the board
still supports the saved mode, and the connection succeeds. Otherwise it shows the Connect
dialog.

After connecting, silently or through the dialog, it checks two more things. Both apply
only when the document already holds sweeps:
- If the projector name or mode differs, it asks before continuing.
- If the frame period differs by more than 1 µs, it refuses, and says to start a new
  document.

---

## 10. Files it reads and writes

### `.sliprof`, the document (JSON)

It is written atomically (`.tmp`, then renamed), so a crash never leaves a half-written
document.

```jsonc
{
  "format": "auv2-sli-profile",   // checked on open
  "version": 1,
  "created": "2026-10-19 10:02:11",
  "saved":   "2026-10-19 11:40:03",
  "port": "COM5",
  "display": "PROJ (ABC), 800x600 @ 120 Hz, offline",
  "T_us": 8333.3,                 // measured frame period
  "max_expo_units": 21982,        // the board's MAXEXP
  "projector": {"name": "PROJ (ABC)", "idx": 0, "w": 800, "h": 600, "hz": 120, "khz": 73270},
  "edges": {"lead_us": ..., "trail_us": ..., "runway_us": ...},   // null until an edge is moved
  "iris":  {"delay_us": ..., "expo_us": 4.875, "peak_mean": ..., "saturated_px": ..., "when": ...},
  "csv_head": ["T_us","expo_us","delay_us","position","t_us","mean","sd","n","ref","tag_fix_us"],
  "sweeps": {
    "wwwww":   {"settings": {...}, "rows": [[...csv_head row...], ...], "when": ..., "complete": true},
    "wkkkk":   {...},
    "wwwww_r": {...}, "wwwww_g": {...}, "wwwww_b": {...}
  },
  "runway_maps": {
    "settings": {"windows": [[start, end, name], ...], "levels": [...], "per": 10, "skip": 2, "edges": {...}},
    "rows": [[delay_us, expo_us, prev_level, this_level, mean, sd, n, nhi_max, T_us], ...],
    "when": ..., "complete": true
  }
}
```

The sweep keys are `wwwww`, `wkkkk`, `wwwww_r/g/b`, and in older documents `wkkkk_r/g/b`.

A sweep's `settings` records:
- the SETTINGS in force: `expo_us`, `step_us`, `frames`, `settle`, `active_us`;
- `level`, `bg`, `expo_real_us`, `port` and `display`;
- or, for an imported sweep, `imported_from` and `tag_fixed_on_import`.

### Sweep CSV (import / export, and the temp log)

One row per (delay, position):

| Column | Meaning |
|---|---|
| `T_us` | frame period |
| `expo_us` | the real exposure (whole units) |
| `delay_us` | genlock delay after vsync, within the frame |
| `position` | frame position 0–4 in the cycle |
| `t_us` | `position · T + delay`, the timeline x |
| `mean`, `sd`, `n` | ROI mean over the frames kept at this delay and position, its SD, how many |
| `ref` | 0 when frames 1–4 are lit; else the median of the five means |
| `tag_fix_us` | the vsync→active offset the tag correction used |

This is `lag_sweep.py`'s format. **Importing** a CSV that has no `tag_fix_us` column (written
before the vblank-tag fix) moves every reading with a delay before the first active line one
position later, the same correction the live sweep applies. The import uses the fixed
800×600@120 offset (432.5 µs) for this, whatever mode the document is in. Importing also refuses a CSV
whose frame period differs from the document's by more than 1 µs.

### Exposure window JSON (Sweep → Export exposure window JSON…)

```json
{
  "T_us": 8333.3,
  "runway_us": 8000.0,
  "lead_us": 8700.0,
  "trail_us": 16943.25,
  "delay_ticks": 870000,
  "expo_units": 21982,
  "expo_us": 8243.25,
  "white_frame_light_inside": 0.9731,
  "max_expo_us": 8243.25
}
```

`delay_ticks` (0.01 µs) and `expo_units` (0.375 µs) are the values to program into the
board. Three things to know about them:
- `lead_us`, and so `delay_ticks`, is measured from **frame 0's** vsync. A leading edge in
  frame 1 or later, as in the example, gives a delay longer than one frame period.
- `expo_units` is **rounded** to the nearest unit, but the Profile readout **truncates**.
  The two can differ by one unit.
- `white_frame_light_inside` is `null` without a WKKKK sweep.

### Temp-folder logs

While a measurement runs, every reading is also written to a CSV in the system temp folder
(`%TEMP%`), so a crash loses nothing. The file is named on the status line.

| File | Contents |
|---|---|
| `auv2sli_<key>_<date>_<time>.csv` | sweep rows, flushed every 10 delays |
| `auv2sli_runway_<date>_<time>.csv` | runway-map cells |

---

## 11. How a measurement works

### The serial protocol

- **Write**: `A5 57 addr val ck`, where `ck = (256 − (0x57 + addr + val)) & 0xFF`. The app
  waits 20 ms after each write, except for the fast level writes in the runway maps.
- **Read**: `A5 52 addr ck`. The reply is **three bytes**, `addr val ck`, with no sync
  prefix, mixed in with the camera's ASCII lines. The reader scans for a triple whose
  checksum closes.
- **EDID**: `A5 72 03 ck` (table read, target 0x03). The reply is the target, 256 bytes, and
  a checksum, found the same way.

### The camera line

With the per-frame stream on (ROICTL bit 6), the board sends one line per camera frame:

```
R=mmm,nnn,rrggbb,cc[,hh,ll]\r\n
```

| Field | Meaning |
|---|---|
| `mmm` | ROI mean, 10-bit hex |
| `nnn` | ROI pixel count. **Must be 0x100 (256)**; any other line is discarded |
| `rrggbb` | the **frame tag**: the top-left pixel transmitted on the frame that was on screen when this exposure was triggered |
| `cc` | trigger ordinal, +1 per trigger (profiling bitstream; 0 on the merged build) |
| `hh`, `ll` | saturated (≥ 1020) and clamped-low (≤ 3) ROI pixels, newer bitstreams only |

The trailing CRLF is required, so a half-arrived line can't match on its fixed-width fields.

### One delay of a sweep

1. Write the delay (three bytes, 0x1C–0x1E). Every 25th delay, **poll the readback**
   (0x5A–0x5C) until it shows the new value. The readback comes from a snapshot republished
   at about 10 Hz, so a single read straight after the write returns the old delay.
2. Flush the input. Discard 4 frames (`settle`), then collect 20.
3. **Assign each frame a position 0–4** (see the next section).
4. If all five positions have readings, keep their means, SDs and counts. Otherwise skip the
   delay and log it.

### Frame tags and the two bitstreams

The white frame (frame 0) is recognised by its tag.
- **Profiling bitstream**: the tag is the full pixel, so white reads `FFFFFF`. The `cc`
  ordinal numbers the frames, so a dropped frame shows up as a gap.
- **Merged build**: the tag latches only red, so white reads `FF0000`. `cc` is always 0, so
  the arrival order of the lines numbers the frames.

The app accepts either form and detects which numbering to use from the data.

**Anchoring.** Positions are counted from the **most recent** white frame. Any cycle whose
two white frames are not exactly 5 apart is dropped, so a missed line costs one cycle rather
than shifting every later reading.

**The vblank correction.** A trigger before the first active line still carries the previous
frame's tag, so it is moved one position later (4 → 0 of the next cycle).

### Runway pairs

The 2-frame cycle uses `IMPCYC = 2`: frame 0 takes `IMPLVL2 = A`, frame 1 takes
`IMPLVL = B`. The SLI pattern engine is turned off (`0x13 = 0`) so nothing is underneath.

Lines are read without the 20 ms write delay and parsed one at a time. The idle wait uses a
busy-spin instead of `time.sleep()`, because Windows sleep rounds up to its ~15 ms timer
tick, which is longer than a 120 Hz frame.

When the run ends, `pairs_off` restores the 5-frame cycle, white levels, no stream, and
delay 0.

### Cleaning up

Every measurement takes the camera back out of impulse mode when it ends, whether it
finished, was stopped or failed: ROICTL = 0 and delay = 0. Closing a document or exiting
stops any running measurement first, and waits up to 15 s for it to return.

---

## 12. Board registers used

| Reg | Name | Use |
|---|---|---|
| 0x02 | STATUS | bit 4 = pass-through (PC video on HDMI-IN), refused |
| 0x07 / 0x08 | IMPLVL2 / IMPRGB2 | the odd frame's level and LED mask |
| 0x11 | IMPCYC | cycle length: 5 for sweeps, 2 for runway pairs |
| 0x12 | IMPLVL | level of the other frames |
| 0x13 | SLICTL | 0 = SLI patterns off (runway maps) |
| 0x14 | MODEFORCE | `0x80 \| idx` pins the offline mode (sticky until the FPGA reloads) |
| 0x17 | ROICTL | `0xC0` = impulse enable + per-frame stream; `0x00` = off |
| 0x1A / 0x1B | EXPO lo / hi | exposure in 0.375 µs units (the hi write commits) |
| 0x1C–0x1E | DLY | genlock delay, 24-bit, 0.01 µs ticks |
| 0x1F | IMPRGB | LED mask of the other frames (0x07 = all) |
| 0x20 | MODE | bit 7 = valid; low nibble = mode index in use |
| 0x29 / 0x2A | mode mask | bit *i* = mode *i* is supported by the projector's EDID |
| 0x4A–0x4C | period | measured frame period, 0.01 µs ticks |
| 0x53 / 0x54 | MAXEXP | the exposure limit at this frame rate, 0.375 µs units |
| 0x5A–0x5C | DLY readback | the delay in force (lags writes by up to ~100 ms) |
| 0x5D | genlock | bit 0 = enabled, bit 1 = live (projector vsync reaching the camera) |
| table 0x03 | EDID | the projector's EDID, as the board read it |
| Ft+ opcode 7 | genlock enable | `(7 << 28) \| (1 << 27)`: gl_en = 1, delay 0. One raw word down D3XX OUT pipe 0x02 |

The full register reference is in [`../FTPLUS_API.md`](../FTPLUS_API.md) and
[`../USB_COMMAND_REFERENCE.md`](../USB_COMMAND_REFERENCE.md).

---

## 13. The embedded mode table

The board's offline video modes are defined in
[`../sources_1/imports/RTL/mode_table.vh`](../sources_1/imports/RTL/mode_table.vh). The app
needs them for three things:
- the mode names in the Connect dialog;
- the vsync → first-active-line offset;
- mapping the board's mode index and EDID mask to modes.

They used to be parsed out of that Verilog file at run time. They are now **embedded** in
`sli_ui.py` as `MODE_ROWS`, copied on 2026-10-19:

| idx | Mode | Timing | Vsync→active (lines / total) |
|---|---|---|---|
| 0 | 800×600 @ 120 Hz | CVT-RB | 33 / 636 |
| 1 | 640×480 @ 120 Hz | CVT-RB | 63 / 546 |
| 2 | 1024×768 @ 75 Hz | DMT | 31 / 800 |
| 3 | 800×600 @ 75 Hz | DMT | 24 / 625 |
| 4 | 640×480 @ 75 Hz | DMT | 19 / 500 |
| 5 | 1024×768 @ 70 Hz | DMT | 35 / 806 |
| 6 | 800×600 @ 72 Hz | DMT | 29 / 666 |
| 7 | 640×480 @ 72 Hz | DMT | 31 / 520 |
| 8 | 1280×720 @ 60 Hz | CEA/DMT | 25 / 750 |
| 9 | 1280×800 @ 60 Hz | CVT-RB | 20 / 823 |
| 10 | 1024×768 @ 60 Hz | DMT | 35 / 806 |
| 11 | 800×600 @ 60 Hz | DMT | 27 / 628 |
| 12 | 640×480 @ 60 Hz | DMT (failsafe) | 35 / 525 |
| 13 | 1280×1024 @ 60 Hz | DMT, 108 MHz | 41 / 1066 |

> **Keep it in step with the RTL.** The index is what the FPGA reports in MODE (0x20), what
> its EDID mask is keyed by, and what MODEFORCE takes. If `mode_table.vh` is reordered or
> extended and the bitstream rebuilt, update `MODE_ROWS` to match. Otherwise the app will
> name modes wrongly, force the wrong one, and use the wrong vsync→active offset. Each row
> holds the same twelve fields as the Verilog `MROW`, in the same order.

The embedded table was checked identical to the parsed `mode_table.vh` when it was copied, and checked again row by row against `mode_table.vh` on 2026-10-19 (all 14 rows match).

---

## 14. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| *"the board is in pass-through: unplug the PC's HDMI"* | A PC is on HDMI-IN. The app is offline-only. |
| *"the board has no valid offline mode"* | MODE bit 7 is clear: no projector EDID read, or no supported mode. Check the projector is on HDMI-OUT and powered. |
| *"frame … us, genlock 0x.. -- is video running?"* | The period is outside 1–60 ms, or genlock is not both enabled and live. Live means the projector's vsync is reaching the camera. |
| *"genlock is off, and turning it on needs the Ft+ driver"* | `pip install ftd3xx` and install FTDI's D3XX driver, or turn genlock on with another tool first. |
| *"genlock is off and no Ft+ … was found"* | The Ft+ is not enumerated. Check the USB 3 cable. |
| Connect dialog: *"no reply -- not the camera board?"* | Wrong COM port, or the board isn't running. |
| Connect dialog: *"COMn: cannot open (SerialException)"* | Another program (a terminal, a command-line tool) has the port open. Close it. |
| *"the board did not switch to mode N"* | MODEFORCE didn't take within 5 s. |
| A sweep skips many delays (*"positions seen … -- skipped"* on the console) | Frames lost, or the white frame's tag isn't recognised (wrong level, or LUT not identity). |
| *"X us is over the exposure limit"* | The requested exposure exceeds MAXEXP for this frame rate. |
| Runway maps: *"A runway part is longer than the … exposure limit"* | Shorten the runway in the Profile tab. |
| Runway maps: *"delay … did not land"* | The delay readback never matched the write. |
| *"Different frame period"* on reconnect | The board runs another mode. Start a new document for it. |
| Sweeps flagged as needing a re-run | The iris changed after they were measured; they are no longer comparable. |

---

## 15. Code map

| Section / class | Role |
|---|---|
| **Board layer** (top of file) | serial protocol (`wr`, `rd`, `collect`, delay set/poll); mode table, EDID, mode switching, `genlock_on`, `preflight`; the impulse sweep (`start_impulse`, `sweep`, `positions`, `stop_impulse`); runway pairs (`Stream`, `pairs_on`, `measure_window`, `pairs_off`, `runway_windows`) |
| `Document` | one session: save/load `.sliprof`, CSV import/export, the open serial port |
| `SetupDialog`, `connect_board`, `reconnect` | the Connect dialog, and silent reconnection of a saved document |
| `timeline_axes`, `fit_margins`, `runway_and_background` | shared plot pieces: the 5-frame axes, pixel-fixed margins, and the runway and background shading |
| `SweepRunner` | one sweep in a worker thread; results come back through a queue |
| `SweepHost` | Run / poll / Stop / finish, shared by the Profile and RGB filters tabs |
| `ProfileView` | **Profile tab**: traces, edges, best window, runway, readout, mouse, iris launch, import/export |
| `FilterView` | **RGB filters tab**: the three filter sweeps, normalize and fit-to-white |
| `RunwayView` | **Runway maps tab**: windows, worker thread, log-scale maps |
| `IrisDialog` | live reading at the WWWWW peak |
| `HelpDialog`, `INSTRUCTIONS` | Help → Instructions |
| `App` | the main window: menus, tabs, the one-at-a-time rule, connection, the file menu |

**Threading.** Measurements run in daemon worker threads that touch only the serial port and
a queue. The Tk thread drains the queue every 100–300 ms and does all the drawing.
`App.busy()` enforces one measurement at a time.

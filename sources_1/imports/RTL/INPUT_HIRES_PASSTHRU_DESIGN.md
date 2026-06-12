# Input (online) path: higher-resolution pass-through

> Goal (2026-06-08): raise the **online / pass-through** resolution ceiling above
> today's ~77 MHz window. This concerns the RECOVERED HDMI input path (SRC = 1,
> "online"), NOT the offline FPGA-generated path covered by
> `OUTPUT_CLK_EDID_DESIGN.md`.

---

## 1. Where the input path stands today

Pass-through advertises only modes with a pixel clock in **60–77 MHz**
(`edid_builder.v:23-24`, `F_MIN_10K=6000`, `F_MAX_10K=7700`). The two bounds have
**different causes**:

- **Lower bound 60 MHz — hard silicon limit.** The recovery MMCM
  (`hdmi_input.vhd:243`) locks to the incoming TMDS clock with a FIXED ×10 ratio
  (`CLKFBOUT_MULT_F=10`, `DIVCLK_DIVIDE=1`), so the VCO runs at `pixel × 10`. The
  Artix-7 MMCM VCO minimum is 600 MHz ⇒ `pixel ≥ 60 MHz` or it won't lock. This is
  why all sub-60 MHz modes (640×480, 800×600, …) are stripped from the served EDID
  (`edid_builder.v:12-13`, `:29-31`).
- **Upper bound 77 MHz — soft / characterization, NOT silicon.** Set by the static
  `CLKIN1_PERIOD = 13.000 ns` (`hdmi_input.vhd:249`) and the ISERDES/IDELAY bit
  alignment validated at that one operating point. The MMCM itself spans 60–120 MHz
  (`edid_builder.v:11`).

The input path has **no frequency measurement** — `CLKIN1_PERIOD` is a compile-time
constant and the ×10 ratio is fixed, so it cannot adapt and is pinned to the
validated band. (The parked `freq_detect.v` + `recovery_clk_drp.v` plan — see
`OUTPUT_CLK_EDID_DESIGN.md:9-11` — would fix this, but is out of scope here.)

## 2. The silicon ceiling: ~120 MHz (1080p60 is OUT)

Two independent limits both land at ~120 MHz pixel, and the **−2 speed grade does
not lift either**:

| Limit | Value | → pixel ceiling |
|---|---|---|
| HDMI input is `TMDS_33` on a 3.3 V **HR** bank (`Au2.xdc:87-88`) | ~1.2 Gb/s/ch (÷10) | ~120 MHz |
| Input **x5 clock runs through a `BUFG`** (`hdmi_input.vhd:315`) | ~600 MHz | ~120 MHz |
| (recovery MMCM VCO, ×10, −2 grade) | 1440 MHz | 144 MHz — *not* binding |

**1080p60 = 148.5 MHz = 1.485 Gb/s/ch, x5 = 742.5 MHz** — over BOTH walls. It is
**not reachable** on this board: 3.3 V TMDS on HR banks is the wall, and HP (1.8 V)
banks are not wired to the HDMI connector. This is a board / I/O-standard
limitation, not fixable in RTL. (The `6.734 ns` `create_clock` line in `Au2.xdc:16`
stays commented for this reason.)

> NOTE: README.md:53 lists "1080p60" as a mode the table could be extended to. That
> is **wrong** for the input path — superseded by this note and by
> `OUTPUT_CLK_EDID_DESIGN.md:6-7`. Stop at ~120 MHz (1680×1050-class).

## 3. Two phases

| Phase | Change | Reaches | Cost |
|---|---|---|---|
| **EDID-cap-only** (this note, §4) | EDID cap + constraints; **BUFG stays** | ~90–120 MHz (TBD by HW) | low |
| BUFIO rework ("step 1") | move input x5 `BUFG` → `BUFIO` (+`BUFR`) | full ~120 MHz reliably | higher |

Step 1 is NOT required to get *some* higher modes — only to reach the *top* of the
range reliably. The EDID-cap-only phase is the experiment that tells us how much
headroom the existing BUFG path actually has before committing to it.

---

## 4. EDID-cap-only scope

Leaves the `BUFG` untouched. Three files change.

### 4.1 `edid_builder.v` — the content change (two edits, not one)
- **Window parameter** (`:24`): `F_MAX_10K` 7700 → e.g. **10800** (108 MHz). This
  alone only moves the range-limits descriptor max-clock byte (`maxclk_byte`, `:97`)
  — it *permits* higher clocks but advertises nothing new.
- **Mode list** (`:52-62`): served modes are the `CAND[]` standard-timing codes
  intersected with the display; all 4 today are ≤75 MHz. Add candidates (e.g.
  1280×1024@60 = 108 MHz, 1440×900@60 = 106, 1680×1050@60 RB = 119) and bump
  `NCAND`.

⚠️ **Standard-timing trap:** a standard-timing code carries only
resolution+refresh+aspect — *the GPU picks the pixel clock* (CVT/DMT) and may choose
a non-reduced-blanking variant ABOVE the window. The code already warns of this
(`:59`, "1280x800@60 71.00 (RB; CVT non-RB is 83.5 → over)").

→ **For the new top mode, emit an explicit DTD with a pinned pixel clock**, not a
standard timing. Block-0 DTD #2 (bytes 72–89) is currently hardwired to zero
(`:154`) and there is a TODO for exactly this (`:85`, generate a per-candidate blob
with the Python EDID tool). Keep the existing `DTD_PREF` (1280×720@60, `:86-93`) as
the safe preferred / failsafe.

### 4.2 `Au2.xdc` — mandatory
- **`create_clock` (`:14`)**: `13.300 ns` is the STA constraint on the input clock
  domain. If the real input clock rises but this stays at 75 MHz, **STA
  under-constrains and reports optimistic timing that doesn't match hardware — a
  silent failure.** Tighten to the new worst-case top (~8.4 ns for 119 MHz). The
  commented `6.734`/`8.08` lines (`:15-16`) are the placeholders.
- **`set_max_delay 26.0` CE paths (`:31-36`)**: generous for a 13 ns pixel period;
  at ~8.4 ns they must scale down or they become meaningless. Review, don't keep
  blindly.

### 4.3 `hdmi_input.vhd` — one hint
- **`CLKIN1_PERIOD => 13.000` (`:249`)**: MMCM jitter/filter hint (does not gate
  locking). One static value across a wider 60–119 MHz span is suboptimal at the
  extremes — acceptable for a first pass; it is the recurring symptom of "no
  frequency adaptivity."

### 4.4 Explicitly NOT changed
- `BUFG` stays (`hdmi_input.vhd:315`). Reliable ceiling is therefore **unknown** —
  wherever ISERDES capture through the global-network skew holds, likely **~90–100
  MHz, possibly up to ~120**. That unknown is the point of this phase.
- No BUFIO/BUFR rework, no front-end changes.

### 4.5 The real risk is NOT the EDID
1. **Pixel-domain timing closure** — TMDS decode, `pixel_pipe`, the SLI pattern
   logic only meet ~75 MHz today. At 108 MHz they run 44% faster; at 119 MHz, 58%.
   Most likely thing to fail in P&R; pure STA/place-and-route work.
2. **ISERDES capture through BUFG** at the higher x5 (540–595 MHz) — the thing
   step 1 would fix. Flaky bit-alignment here is the signal that BUFIO is now
   required.

### 4.6 Recommended first target: 108 MHz / 1280×1024@60
- x5 = **540 MHz** — comfortably under the ~600 MHz BUFG edge, so this tests *timing
  closure* and *capture margin* without immediately slamming the buffer.
- Real win over today's 1280×720 (74.25 MHz).
- Pin it with a DTD (exact 108 MHz); keep 720p as preferred/failsafe.

If 108 MHz closes timing and captures cleanly on hardware, push the cap toward 119
and find where it breaks — that breakpoint tells us precisely whether step 1 (BUFIO)
is needed for the top of the range.

---

## 5. Net scope

~1 substantive RTL edit (`edid_builder.v`: cap + a pinned DTD), 2 constraint edits
(`Au2.xdc`: `create_clock`, CE delays), 1 hint (`hdmi_input.vhd`: `CLKIN1_PERIOD`).
No buffer / IO rework. The deliverable is really a **timing-closure + hardware-
capture experiment** that measures how much headroom the existing BUFG path has
before committing to the BUFIO rework.

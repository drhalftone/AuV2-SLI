# Offline output: EDID-driven, any-resolution pixel clock + timing

> Goal (confirmed 2026-06-08): the FPGA's locally-generated (offline) video should
> drive the projector at **whatever resolution / frame rate the projector's EDID
> asks for**, automatically, with **no HDMI input**. Any mode the Au V2 can
> physically synthesize (~25-120 MHz pixel; 1080p60/148.5 is out — x5 would exceed
> the ~600 MHz OSERDES/BUFG ceiling).
>
> NOTE: this SUPERSEDES the input-side recovery-clock plan
> (RECOVERY_CLK_DRP_DESIGN.md, now PARKED). freq_detect.v / recovery_clk_drp.v were
> for the input path and are not used here.

## Current offline path (what we're changing)

`vga` (pattern gen, Au2_SLI.vhd) is clocked by `pixel_clk`, which in OFFLINE mode
is `clk125` from the `ref_clk` IP (a clk_wiz MMCM off the 100 MHz oscillator),
muxed onto the output by `clk_selector`; `clk625` (=5x) is the serializer clock.
Today these are hardwired to 800x600@60 (clk125=40 MHz, clk625=200 MHz) and
`vga`'s timing is fixed by VHDL generics.

`ref_clk` also makes `clk200` (IDELAY ref) and `clk10`, so we must NOT retune it.

## New architecture

```
 projector EDID (already read into i2c_master_edid RAM by edid_merge)
        │  rd_addr/rd_data (tap preferred DTD, bytes 54..71)
        ▼
  edid_dtd_parser ──► mode descriptor {pixclk_10kHz, hRez,hFP,hSync,hTot,hPol,
        │                               vRez,vFP,vSync,vTot,vPol}
        ├──────────────► vga (timing now PORT-driven, not generics)
        ▼
  pixclk_synth: pixclk_10kHz ─► search (M, d_pix=5·d_x5, d_x5) for fixed 100 MHz in,
        │        VCO=100·M ∈[600,1200], pixel=VCO/(5·d_x5)≈target. Emits the DRP
        │        register words (CLKFBOUT frac from a 49-entry elaboration ROM;
        │        CLKOUT0/2 + DIVCLK encoded combinationally; lock/filter from
        │        M_int ROMs — all via XAPP888 mmcm_drp_func_7s.vh).
        ▼
  out_clk_drp (DRP FSM) ─► out_clkgen (MMCME2_ADV off clk100)
        │                      ├─ CLKOUT0 = pixel  ─┐
        │                      └─ CLKOUT2 = 5·pixel ─┤
        ▼                                            ▼
   on apply: reconfigure, relock ───────────► clk_selector OFFLINE inputs
                                              (replace clk125/clk625 feed)
```

### Why a dedicated MMCM (not DRP of ref_clk)
ref_clk must keep clk200/clk10 steady. A separate `out_clkgen` MMCME2_ADV (also
fed by clk100) is DRP-reconfigured freely and feeds only the offline clock inputs
of `clk_selector`. ref_clk's clk125/clk625 are no longer used for the output.

### Pixel-clock synthesis (fixed 100 MHz in → arbitrary out)
Keep pixel:x5 = 1:5 with integer divides: d_pix = 5·d_x5 (both integer), so
pixel = VCO/(5·d_x5), VCO = 100·M.  M is fractional in 1/8 steps → VCO in 12.5 MHz
steps → fine enough to hit most pixel clocks within <0.5% (projector locks to the
exact total H×V we program from its own DTD, so small clock error just nudges the
refresh by the same fraction).  Search d_x5 so VCO stays in [600,1200]; pick the M
on the 1/8 grid minimizing |pixel − target|.  Exact for 40 MHz (800x600) and 65
MHz (1024x768); ~0.1-0.5% for 720p / 1280x1024.

### DRP encoding
Reuse Xilinx `mmcm_drp_func_7s.vh` (already in RTL).  The only fractional piece
(CLKFBOUT for fractional M) is precomputed at elaboration into a 49-entry ROM for
M = 6.000 .. 12.000 step 0.125.  Lock/filter are M_int-indexed ROMs.  CLKOUT0/2 and
DIVCLK use plain integer counter encoding (combinational).  out_clk_drp walks the
register list with read-modify-write, holding out_clkgen in reset, then relocks.

## Build order (each step keeps the project buildable until integration)
1. **edid_dtd_parser.v** — DTD bytes → mode descriptor.            [next]
2. **pixclk_synth.v** — target pixclk → (M-idx, d_pix, d_x5) + DRP words.
3. **out_clk_drp.v** — DRP FSM for out_clkgen (adapt recovery_clk_drp).
4. **out_clkgen** wrapper — MMCME2_ADV + drp + synth.
5. **vga.vhd** — timing generics → input ports.
6. **Au2_SLI.vhd** — instantiate out_clkgen, tap i2c_master_edid DTD, drive vga
   timing + clk_selector offline inputs; sequence apply on EDID-read-done.
7. Build → flash (RAM) → projector shows its native mode with the test pattern.

## Open items
- Tap the projector DTD: edid_merge owns i2c_master_edid; expose a read port (or a
  parsed-DTD bus) from edid_merge, OR add a light second reader. Confirm read-port
  arbitration with edid_builder.
- Telemetry: add target pixclk / chosen M / lock / applied-mode to status_line.
- Re-apply on EDID change / hot-plug (edid_merge already pulses on HPD).
- Glitch on reconfig: clk_selector drops to... there is no other offline source;
  expect a brief output drop while out_clkgen relocks (~µs) — acceptable, projector
  re-syncs. Hold vga in reset during reconfig to avoid mid-frame tearing.

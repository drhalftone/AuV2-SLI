# ft_usb_video — FT601 USB-3 video throughput test (Pt V2 + Ft+)

**Goal:** answer "how many 1280×1024 frames/second can we push over the Ft+ (FT601Q)
USB-3 port?" — by generating real SLI video on the FPGA (the same phase-shifting
cosine fringes the HDMI path emits) at the **PYTHON 1300 raster**, streaming it out
the FT601, and grabbing/displaying it on the PC while measuring MB/s and FPS.

This is the first thing that moves **real data** across the Ft+. The stack I/O check
(`../LauPythonCamera_Pt_Stack/iocheck/`) only proved the Ft+ pins *place*; it moved
nothing. Everything here reuses those exact, already-proven pins.

```
  sli_frame_gen ──stream──▶ ft601_sync_tx ──245 sync FIFO──▶ FT601 ──USB3──▶ PC
   1280×1024 SLI            (FPGA = master)                          ft_video_grab.py
   8-bit mono, 4px/word                                             (PySide6 + ftd3xx)
```

## Files

| File | What |
|------|------|
| `rtl/sli_frame_gen.v` | 1280×1024 cosine-fringe frame source, 8-bit mono, 4 px / 32-bit word, 8-word framing header |
| `rtl/ft601_sync_tx.v` | FT601 **245-Synchronous-FIFO** write master (FPGA is the master, `ft_clk` = 100 MHz) |
| `rtl/ft_video_top.v` | top: wires generator → master → FT601 pins; single `ft_clk` domain |
| `build/run_ftvideo.tcl` | Vivado batch build → `out/ft_video.bit` + `out/ft_video.bin`, **+ FT601 IOB/timing assertions** |
| `host/ft_video_grab.py` | D3XX grabber: GUI display + FPS/MB-s, or `--bench` / `--raw` headless |
| `host/ft_video_snap.py` | **byte-exact verifier**: recomputes the RTL pattern and diffs every pixel; writes PNGs (stdlib only, no PySide6) |
| `host/ft_diag_rows.py` | corruption forensics: per-column consensus, error rate, byte-lane / offset histograms |
| `host/requirements.txt` | `ftd3xx`, `numpy`, `PySide6` |

## The numbers to expect

The FT601 in 245-sync FIFO mode clocks 32 bits at 100 MHz. **Measured 2026-07-30**
on this Pt V2 + Ft+ + host (superseding the earlier 340–380 MB/s estimate, which
this hardware does not reach):

```
  theoretical ceiling   = 4 B × 100 MHz            = 400 MB/s  (3.2 Gbps)
  frame on the wire     = 32 B header + 1280×1024  = 1,310,752 B  (8-bit mono)

  MEASURED:
    --raw --stream --chunk 0x400000   309 MB/s  (2.47 Gbps)   77% of ceiling
    --raw          --chunk 0x100000   287 MB/s  (2.29 Gbps)
    --bench --stream --chunk 0x400000 252 MB/s  ->  192 fps,  0 dropped
    --bench        --chunk 0x100000   233 MB/s  ->  178 fps,  0 dropped
```

Two things that matter when reading those:

- **`--stream` is worth ~24 MB/s**, and it plateaus by a 4 MiB chunk (16 MiB buys
  only ~1 MB/s more). Use `--stream --chunk 0x400000` for any real measurement.
- **The raw→framed gap (~57 MB/s) is the Python parse loop, not the link.**
  `FrameAssembler.feed` concatenates into a `bytearray` and `del`-slices it per
  chunk. That is a host software limit; the FPGA and USB are not involved.

So at 8-bit the **USB link is not the bottleneck for the sensor** (PYTHON 1300 tops out
at 150 fps): 192 fps sustained leaves headroom. `--raw` gives the pure link rate;
`--bench` gives the rate after framing/drop-checking; the GUI shows the moving fringes.

> Switching the generator to 10-bit-in-16 (2 px/word) doubles the frame to 2.62 MB,
> so expect **~95–118 fps** at the same MB/s — which *would* put you under the
> sensor's 150 fps ceiling. The MB/s figure is format-independent.

## Verify correctness, not just speed

**Throughput alone will not tell you the link is working.** On 2026-07-30 this design
streamed 1280×1024 at 192 fps with *zero dropped frames* while silently corrupting
half of every pixel word — see the bug note at the end of this file. Always run:

```powershell
cd host
python ft_video_snap.py                # 400 frames, byte-exact vs the RTL model
```

Expected output on healthy hardware:

```
  frames matching the RTL byte-for-byte : 400
  frames with ANY mismatched pixel      : 0
  distinct (frq,frm) states seen        : 24
  frame index gaps (dropped)            : 0
```

It also writes `snap_frq<q>_frm<m>.png` so the fringes can be eyeballed without any
GUI toolkit installed. If frames *do* mismatch, run `python ft_diag_rows.py` — it
prints an error histogram by byte lane (`x mod 4`) and by transfer offset, which is
what localizes the failure to specific data bits.

## Build

Uses the same Vivado batch flow as `build_pt.tcl` (part `xc7a100tfgg484-2`,
single-threaded to dodge this host's `.tcl`-read race). From this folder:

```powershell
cd build
vivado -mode batch -source run_ftvideo.tcl -log out/vivado.log -journal out/vivado.jou
```

Outputs `build/out/ft_video.bin`. The build prints, and **hard-fails on**, three things:

```
=== FT601 IOB packing: 32/32 data flops + 1 WR# copy in OLOGIC ===
=== FT601 bus setup slack (clock-to-out vs FT601 window) = 0.514 ns ===
=== FT601 bus hold slack = 1.893 ns ===
=== TIMING: setup WNS = 0.075 ns ===
```

Do not "fix" a failure there by deleting the check — it is guarding a bug that
produces perfect-looking throughput and wrong pixels. See the bug note at the end.

> `write_bitstream` on this host occasionally dies with `EXCEPTION_ACCESS_VIOLATION`
> (a garbage `CH1_RXDATA[127:0]]` name string). That is a tool flake, not the
> design — just re-run the build.

## Flash (RAM — this is a test, don't burn flash)

```powershell
Alchitry.exe load --bin build/out/ft_video.bin --board PtV2 --ram
```

Use the Alchitry Labs V2 loader (2.0.52+), same one the Au flow uses. `--ram` is
temporary (gone on power-cycle), which is what you want while iterating.

LEDs: the top six LEDs count frames (a visibly faster blink = frames leaving faster);
the bottom two show FT601 write activity. All dark ⇒ no `ft_clk` (Ft+ not powered /
not in sync-FIFO mode) or the board is held in reset.

## Grab + measure on the PC

Prereqs: **FTDI D3XX driver** installed and the FT601 enumerating as a D3XX device
(not the D2XX/VCP serial driver). Then:

```powershell
cd host
pip install -r requirements.txt

python ft_video_grab.py                # GUI: live fringes + fps/MB-s/drops
python ft_video_grab.py --bench        # headless: parse frames, print fps + MB/s + drops
python ft_video_grab.py --raw          # headless: pure read, link upper-bound MB/s
python ft_video_grab.py --bench --stream --chunk 0x100000   # streaming pipe, 1 MiB URBs
```

- `--raw` vs `--bench`: `--raw` measures the driver+link with zero parsing (the true
  ceiling); `--bench` measures after framing/drop-detection. A big gap between them
  means the Python parse loop is the limit, not the link — try `--stream` and a larger
  `--chunk`, or move parsing off the read thread.
- **dropped > 0** in `--bench`/GUI means the host isn't draining fast enough (frame
  indices skipped), *not* an FPGA fault — the generator only advances when the FT601
  accepts a word, so it never overruns; drops are always host/USB-buffer side.

## How the FT601 write actually works (why the master is tiny)

245-sync FIFO, FPGA = master, everything on the FT601's `ft_clk`:

- `TXE#` low ⇒ USB buffer has room. A word on `DATA`/`BE` is taken by the FT601 on
  every rising edge where `WR#`=0 **and** `TXE#`=0.
- `DATA` and `WR#` are launched from **IOB flops**, so what the FT601 sees is a
  fixed Tco after the edge rather than a combinational path through the pattern
  ROM. This is not optional — driving them from logic is what corrupted the upper
  16 bits (see the bug note below).
- Registering the outputs costs a cycle, so `WR#` can no longer be a combinational
  echo of `TXE#`. Instead the master asks, at each edge, whether the word it was
  *already presenting* got taken: `accepted = ~wr_n_int & ~ft_txe`. Both terms are
  evaluated at the same edge the bridge uses, so the two chips can never disagree.
- If not accepted (`TXE#` went high), the master simply **holds and re-presents**
  the same word. The FWFT source has not been popped yet, so that hold *is* the
  skid buffer — no extra storage, and no word is ever lost or duplicated.
- The generator advances one word per accepted write, so generation is paced by USB
  back-pressure. That's what makes the measured FPS a clean link number.
- `OE#`/`RD#`/`BE` are static levels (TX only, all bytes valid), driven from
  constants — no per-cycle edge, so they need no IOB flop.

## Assumptions / gotchas

- **FT601 mode.** The Ft+ must be configured for **245 Synchronous FIFO @ 100 MHz**
  (that's why `ft_clk` is 100 MHz in `pt_ft_plus_bottom.xdc`). Alchitry ships it this
  way; if `ft_clk` is a different rate or the chip is in multi-channel mode, reconfigure
  with FTDI's **FT60x Chip Configuration Programmer**. Read pipe is `0x82` (channel 0 IN).
- **`ft_data` output timing — this WAS broken; see below.** The bus is now launched
  from IOB flops and the interface is constrained. `run_ftvideo.tcl` hard-fails the
  build if either regresses, so this should stay fixed.
- **`ftd3xx` API drift.** The wrapper's read call has changed across releases; the host
  tries `readPipe(pipe, buf, len)` then falls back to `readPipeEx`. If neither matches
  your version, adjust `FtDevice.read()`.

## Bug found and fixed: silent corruption of `ft_data[31:16]` (2026-07-30)

Recorded because the failure mode is nasty and easy to reproduce by accident.

**Symptom.** Everything that a throughput test measures looked perfect: 1280×1024
frames locked, headers valid, frame indices monotonic, **zero dropped frames**,
~192 fps sustained. But `ft_video_snap.py` — which recomputes what
`sli_frame_gen.v` *should* have emitted and diffs it — reported 400/400 frames
wrong, and `ft_diag_rows.py` localized it precisely:

```
errors by byte lane (x mod 4):  {0: 0, 1: 0, 2: 11419, 3: 25120}
```

`ft_data[15:0]` was byte-perfect; `ft_data[31:16]` was corrupt. The upper half is
exactly the group placed in the far bank (G4/H3/G3/P5/P4/P6/N5/M6/M5/L5/L4/K6/J6/
E2/D2/M3), whose longer launch path missed the FT601's ~1 ns setup window while
the near half made it. `ft_wr` (E3) lives in that same far bank but never
glitched — with `s_valid` tied high it goes low once and stays low, so it has no
per-cycle edge to violate. Only DATA toggles every clock, and only DATA broke.

**Two root causes, both required to hide it:**

1. `ft601_sync_tx.v` drove the pads combinationally (`assign ft_dout = s_word`),
   straight through the cosine ROM and phase adders. Huge, per-bit-variable
   clock-to-out.
2. The XDC had **no `set_output_delay`** — despite the module header claiming it
   did. With no output delay the FT601 interface was not in the timing graph at
   all, so Vivado never checked it, never warned, and reported a clean WNS.
   *An unconstrained path is not a failing path; it is an invisible one.*

**Fix.** Launch DATA/WR# from IOB (`OLOGIC`) flops, constrain the interface with
the FT601Q datasheet window (out: 1.0 ns setup / 1.0 ns hold; in: 7.0/1.0 ns), and
restore exactness with a registered handshake — `accepted = ~wr_n_int & ~ft_txe`
evaluated at the same edge the bridge uses, holding (re-presenting) the word when
it is not taken. The FWFT source is only popped on acceptance, so the hold *is*
the skid buffer; no extra storage is needed.

WR# needs two physical copies: an IOB flop's `Q` is not visible to the fabric, but
`accepted` reads WR# back. Vivado replicates this itself (`wr_n_pad_reg` in a
SLICE for the logic, `wr_n_pad_reg_rep` in `OLOGIC` driving the pin), which is why
`run_ftvideo.tcl` asserts on **placement, not on cell names**.

**After the fix:** FT601 bus setup slack **+0.514 ns**, hold **+1.893 ns**,
400/400 frames byte-exact, all 24 (frq,frm) states intact, throughput unchanged
at 309 MB/s raw / 192 fps framed — the fix costs nothing.

**Guards added to `run_ftvideo.tcl`** (the build now fails, loudly, on either):

1. all 32 data flops must be placed in `OLOGIC` sites, and WR# must have an
   `OLOGIC` copy driving the pad;
2. the data pins must have **at least one max-delay timing path** — zero paths
   means `set_output_delay` went missing again and the interface is untimed.

**Lesson for the rest of this project:** a source-synchronous output bus that is
not constrained will not fail timing, it will fail *silently on the wire*, and
only on the bits that happen to place far away. Any interface where we drive data
that another chip clocks in (FT601 here, the camera sensor links elsewhere) needs
both a registered launch and a real `set_output_delay` — plus a byte-exact
end-to-end check, because MB/s and drop counts cannot see this class of bug.

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
| `build/run_ftvideo.tcl` | Vivado batch build → `out/ft_video.bit` + `out/ft_video.bin` |
| `host/ft_video_grab.py` | D3XX grabber: GUI display + FPS/MB-s, or `--bench` / `--raw` headless |
| `host/requirements.txt` | `ftd3xx`, `numpy`, `PySide6` |

## The numbers to expect

The FT601 in 245-sync FIFO mode clocks 32 bits at 100 MHz:

```
  theoretical ceiling   = 4 B × 100 MHz            = 400 MB/s  (3.2 Gbps)
  realistic USB3 (D3XX) ≈ 340 – 380 MB/s
  frame on the wire     = 32 B header + 1280×1024  = 1,310,752 B  (8-bit mono)

  → FPS ≈ throughput / 1.31 MB:
        at 350 MB/s  ≈ 267 fps
        at 380 MB/s  ≈ 290 fps
        at 400 MB/s  ≈ 305 fps
```

So at 8-bit the **USB link is not the bottleneck for the sensor** (PYTHON 1300 tops out
at 150 fps): the point of this test is to find the *actual* sustained ceiling of your
Pt+ Ft+ cable/host combination. `--raw` gives the pure link rate; `--bench` gives the
rate after framing/drop-checking; the GUI shows the moving fringes so you can *see* it.

> If you'd rather stream 10-bit-in-16 (the real sensor packing), change the generator
> to 2 px/word / `format 2` and the frame doubles to 2.62 MB → ~130–150 fps at the same
> MB/s. The **MB/s number is format-independent**; only the FPS headline changes.

## Build

Uses the same Vivado batch flow as `build_pt.tcl` (part `xc7a100tfgg484-2`,
single-threaded to dodge this host's `.tcl`-read race). From this folder:

```powershell
cd build
vivado -mode batch -source run_ftvideo.tcl -log out/vivado.log -journal out/vivado.jou
```

Outputs `build/out/ft_video.bin`. Check the printed `setup WNS` is ≥ 0.

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
- The master ties `WR# = ~(~TXE# & valid)` — i.e. it writes exactly when the FT can
  accept — and advances the source by the *same* term. Because the FT samples `WR#`
  and `TXE#` on the same edge, they can never disagree ⇒ no skid buffer, no dropped or
  duplicated words. `OE#`/`RD#` stay high (TX only; the FPGA owns the bus).
- The generator is FWFT and advances one word per accepted write, so generation is
  paced by USB back-pressure. That's what makes the measured FPS a clean link number.

## Assumptions / gotchas

- **FT601 mode.** The Ft+ must be configured for **245 Synchronous FIFO @ 100 MHz**
  (that's why `ft_clk` is 100 MHz in `pt_ft_plus_bottom.xdc`). Alchitry ships it this
  way; if `ft_clk` is a different rate or the chip is in multi-channel mode, reconfigure
  with FTDI's **FT60x Chip Configuration Programmer**. Read pipe is `0x82` (channel 0 IN).
- **`ft_data` output timing.** The generator drives `ft_data` combinationally from its
  state (one distributed-ROM lookup + a couple of adds). Internal 100 MHz closes
  trivially; the only board-level unknown is the FT601 setup window on that output,
  which this test validates empirically. If you see corruption at the top of the link's
  rate, register the generator's `word` output and add a 1-word skid in the master.
- **`ftd3xx` API drift.** The wrapper's read call has changed across releases; the host
  tries `readPipe(pipe, buf, len)` then falls back to `readPipeEx`. If neither matches
  your version, adjust `FtDevice.read()`.

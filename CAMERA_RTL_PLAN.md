# PYTHON 1300 — FPGA Bring-Up Plan

The staged plan for getting the onsemi PYTHON 1300 (NOIP1SN1300A) talking to the FPGA.
**Every step has a test gate that must pass before the next one starts.**

Companion documents:
- [`CAMERA_SENSOR_PROTOCOL.md`](CAMERA_SENSOR_PROTOCOL.md) — every sensor constant, cited to the datasheet
- [`CAMERA_IO_MAP.md`](CAMERA_IO_MAP.md) — pin maps (§1–§7 Pt V2, **§8 Au V2**)
- [`CAMERA_POWER_DESIGN.md`](CAMERA_POWER_DESIGN.md) — the power tree

---

## The shape of it: two tracks, run in parallel

```
   #1 datasheet constants  (done)
        |
        +---- TRACK A: SPI ------------------> runs on the Au V2 you already have
        |     #2 #3 #4 #5* #6 #14
        |
        +---- TRACK B: LVDS receiver --------> pure simulation, NO hardware at all
              #7 #8 #9 #10 #11
                                  \
                                   +--> #12* real pixels  (needs the Pt V2)
   * = hardware gate
```

**Track A runs on the Au.** The sensor's SPI is *asynchronous to its system clock* — it needs no
clock, no PLL, no LVDS, and no configuration (`CAMERA_SENSOR_PROTOCOL.md` §1). So an Au V2 can talk
to it, and **one chip-ID read proves the power tree, the DF40 pin map, the stack pass-through, and
our RTL, all at once** — see `CAMERA_IO_MAP.md` §8.4 for exactly what that does and does not cover.

**Track B needs no hardware.** The hard part of this job is not the I/O — it is training/bitslip,
sync-channel framing, and the de-interleave. All of it can be built and proven against a behavioral
model, which is what keeps Pt availability off the critical path.

---

## Track A — SPI (Au V2)

| # | Milestone | Test gate | Status |
|---|---|---|---|
| **1** | Pin down every protocol constant from the datasheet | No constant appears in RTL that is not traceable to a citation | ✅ `CAMERA_SENSOR_PROTOCOL.md` |
| **2** | `cam_spi_master.v` | Self-checking TB at the max legal 10 MHz | ✅ **686 checks, 0 errors** |
| **3** | SPI mailbox on the `0xA5` UART control plane | Real 115200 bytes in → chip ID out | ✅ **89 checks, 0 errors** |
| **4** | `cam_au2.xdc` (Au V2) + build a bitstream | Synth + impl clean, timing met, DRC clean | ✅ **WNS +2.106 ns; pins verified by readback** |
| **6** | Sensor boot sequencer (ROM-driven register upload) | Sensor reports PLL lock + ready | ✅ **12 checks, 0 errors** (`cam_boot_seq.v`) |
| **5** | 🔴 **HARDWARE GATE — read chip ID `0x50D0`** | The correct value comes back | ⬜ **needs the Au on the bench** |
| **14** | Settle the `clk_pll` 20 ps jitter spec | We know the number, its units, and whether our clock clears it | ⬜ a number to look up / measure |

### Milestone 5 is the one that matters

It is the whole "confirm the PCB, demonstrate working HDL" goal in a single transaction.
**Before powering up:**

1. **Do not populate the `VBSEL_A` strap resistor** on an Au build (`CAMERA_IO_MAP.md` §8.3).
2. **Meter the power tree first, with no FPGA involved** — `vdd_18` must come up *before* `vdd_33`,
   and `vdd_pix` must sit in 3.25–3.35 V. This is the part of the board that was rebuilt repeatedly
   and has only ever been validated in SPICE.
3. Run the 30-second pass-through check in `CAMERA_IO_MAP.md` §7.
4. **Never write sensor register 112 on an Au build** — that powers up the LVDS drivers, and
   `dout0±` lands on the Au's 1.35 V bank 15 (`CAMERA_IO_MAP.md` §8.2).

---

## Track B — the LVDS receiver (simulation, then Pt V2)

| # | Milestone | Test gate | Status |
|---|---|---|---|
| **7** | Behavioral PYTHON LVDS transmitter model (**bit-level**) | Golden decoder recovers a known image | ✅ **258 checks, 0 errors** (`python1300_lvds_model.v`) |
| **8** | `cam_lvds_rx.v` — ISERDES receiver | Deserialised words on all 5 channels; cold start works | ✅ proven in the full chain below |
| **9** | Per-lane training / bitslip alignment FSM | Every lane locks, over many seeds of random skew + bit phase | ✅ **locks from 4 phases + 0.9 ns skew** (`cam_align.v`) |
| **10** | Sync decoder + 4-lane de-interleave | Known image recovered **bit-exact** | ✅ **256 px bit-exact** (`cam_sync_decode.v`) |
| **11** | Line-capture buffer readable over UART | Known line out, checksum correct | ✅ **34 checks, 0 errors** (`cam_line_buf.v`) |
| **12** | 🔴 **HARDWARE GATE — capture a real line of pixels (Pt V2)** | Sane pixels from a known scene | 🟡 **RTL integrated; builds clean on the Pt (WNS +2.023 ns) — needs the bench** |

> The full receive chain — `python1300_lvds_model → cam_lvds_rx → cam_align → cam_sync_decode`
> — recovers a 32×8 test image **bit-exact** in `tb_cam_decode` (258 checks, 0 errors). The chain
> (plus `cam_line_buf` + `cam_boot_seq`) is now **assembled in `Au2_SLI` on the Pt and builds clean**
> (`build_pt.tcl`; commit `3dad19d`). What remains for #12 is the bench test — see §"Pt integration".

### The traps already identified

1. **The de-interleave is NOT a mod-4 split.** It is an 8-pixel kernel with an *alternating parity
   swap* — the sensor's ADC column-sequencer ordering. A naive `pixel[i] → lane[i mod 4]` produces a
   scrambled image that still *looks* like an image. See `CAMERA_SENSOR_PROTOCOL.md` §8.3.
2. **`iocheck/pt_camera_rx.v` is a placement proof, not a receiver.** Its `lvds_clock_in` output is a
   startup deadlock, and we no longer drive that pin at all (PLL mode). The file carries a warning
   header. Do not paste it forward.
3. **A full frame will never fit the UART.** `uart_ctrl`'s `len` is `reg [11:0]` (4095 bytes max) and
   the link is 11.5 kB/s — a 1.3 MB frame would take ~2 minutes. One *line* (1280 B) fits exactly and
   is a fine bring-up instrument. Frames need the Ft+.
4. **"Proven in simulation" ≠ synthesizable — hit during integration.** `cam_line_buf` wrote all 8
   kernel pixels to 8 computed addresses in one cycle. Correct in sim, but that is 8 write ports to one
   BRAM; it *crashed* Vivado synth (`EXCEPTION_ACCESS_VIOLATION` in RAM inference). Fix: store the line
   8 pixels per 64-bit word at `kbase/8` (`kbase` is 8-aligned) — one write port, infers as BRAM.
   `tb_cam_line` re-run byte-exact (34/0). Any sim-only module is suspect until it has been through synth.
5. **`clk100` was never on a global buffer — latent, not camera-specific.** It feeds MMCMs directly, so
   Vivado's auto-inserter skips it; the unbuffered net was tolerable only while the fabric stayed
   compact. Adding `cam_line_buf` (clocked on `clk100`, placed by the bank-13 pins) stretched it across
   the die → **−17 ns skew, timing blown**. Fix: put the whole `sys_clk_pin` domain (fabric + every MMCM
   `CLKIN`) on one explicit BUFG (`clk100_g` in `Au2_SLI.vhd`). Recovered to WNS +2.023 ns.

---

## Pt integration (task #12)

The RTL assembly is **done and builds clean on the Pt** (`build_pt.tcl`, commit `3dad19d`;
synth+impl clean, DRC 0 errors, setup WNS +2.023 ns, hold WHS +0.047 ns). Steps 1–4 below are
complete; **step 5, the bench test, is all that remains** — and it needs the physical Pt board.

1. ✅ **Port `Au2_SLI` to the Pt V2** (`XC7A100T-FGG484`) — task #15. From-scratch re-pin in
   `constrs_1/imports/RTL/Au2_pt.xdc`: every `PACKAGE_PIN` in `Au2.xdc` is invalid on the Pt (different
   die *and* package); HDMI on the Hd's connectors (TX = port 1, RX = port 2), USB on the onboard
   FT2232. Built as Phase 1 (commit `896eb0b`).
2. ✅ **72 MHz MMCM** for `cam_clk_pll` — a `MMCME2_BASE` (D=5, M=54 → VCO 1080 → /15 = 72.000 MHz
   exact), forwarded to the CMOS pin via ODDR+OBUF. **Still open:** its jitter vs the sensor's ≤20 ps
   `clk_pll` spec is unverified (task #14). Not gating — this build does not power the sensor.
3. ✅ **Chain instantiated** `cam_lvds_rx → cam_align → cam_sync_decode → cam_line_buf` on the
   bank-13 LVDS pins, with `cam_line_buf`'s read port wired to `usb_link`'s `cam_line_addr` /
   `cam_line_data` (was tied to 0 on the Au). `cam_lvds_rx` recovers its own 72 MHz word clock
   (BUFIO + BUFR /5) — no IDELAY / clk200 reference needed. `cam_align`/`cam_sync_decode` share a
   wordclk-domain reset counter (`cam_wc_rst`).
4. ✅ **`cam_boot_seq` instantiated with arbitration**, entirely inside `usb_link`:
   - `cam_spi_master` control is MUXed to `cam_boot_seq` while `boot.busy`, else the host mailbox.
   - `cam_reset_n` is MUXed to `cam_boot_seq` while booting, else the mailbox's reg `0x37` bit 7.
   - Host trigger register `0x39` added to `uart_ctrl`: write = go (dropped 'N' if busy),
     read = `{ready, busy, failed, pll_timeout}`. The Qt tool fires it after the chip-ID read.
5. ⬜ **Bench test**: boot the sensor, let the lanes train, capture one line, `readCameraLine()` in the
   Qt tool, plot it. Cover the lens → near-black; bright field → flat; a hard edge → a step in the
   expected column. **A de-interleave error shows as a period-4 comb** — look for it specifically.
   This gate is the first time the LVDS pin plan, `DIFF_TERM`, the SRCC/BUFIO choice, the even-row
   routing, and the within-kernel channel pairing are tested against reality rather than Vivado.

---

## Deliberately NOT in this plan (yet)

These are what you need to **stream** for SLI, not what you need to get the sensor **working**. They
come after #12.

- **The FT601 / Ft+ USB3 datapath — it does not exist in the RTL at all.** Nothing in
  `sources_1/imports/RTL/` speaks FT601; the `0xA5` control plane is a **115200-baud UART**. The
  245.8 MB/s bandwidth budget in the board README is a budget against hardware that is not yet
  implemented. This is its own subproject.
- **10-bit packing.** At the sensor's full 210 fps, packed 10-bit is ~344 MB/s — essentially *at* the
  Ft+'s measured 350 MB/s. The naive thing (10 bits sitting in a 16-bit word) is **393 MB/s and
  busts the ceiling**, so the packer is load-bearing, not an optimisation.
- **Locking exposure to the projected pattern.** Drive `trigger[0]` from the outgoing HDMI frame
  timing so each exposure is locked to a projected pattern. This is the entire reason the FPGA sits
  inline on HDMI.

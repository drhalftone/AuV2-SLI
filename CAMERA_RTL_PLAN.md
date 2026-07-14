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
| **4** | `cam_ctrl.xdc` (Au V2) + build a bitstream | Synth + impl clean, timing met, DRC clean | ⬜ |
| **5** | 🔴 **HARDWARE GATE — read chip ID `0x50D0`** | The correct value comes back | ⬜ |
| **6** | Sensor boot sequencer (ROM-driven register upload) | Sensor reports PLL lock + ready | ⬜ (blocked on nothing — see §7 of the protocol doc) |
| **14** | Settle the `clk_pll` 20 ps jitter spec | We know the number, its units, and whether our clock clears it | ⬜ |

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
| **7** | Behavioral PYTHON LVDS transmitter model (**bit-level**) | Golden decoder recovers a known image | ⬜ |
| **8** | `cam_lvds_rx.v` — ISERDES receiver | Deserialised words on all 5 channels; cold start works | ⬜ |
| **9** | Per-lane training / bitslip alignment FSM | Every lane locks, over many seeds of random skew + bit phase | ⬜ |
| **10** | Sync decoder + 4-lane de-interleave | Known image recovered **bit-exact** | ⬜ |
| **11** | Line-capture buffer readable over UART | Known line out, checksum correct | ⬜ |
| **12** | 🔴 **HARDWARE GATE — capture a real line of pixels (Pt V2)** | Sane pixels from a known scene | ⬜ |

### The three traps already identified

1. **The de-interleave is NOT a mod-4 split.** It is an 8-pixel kernel with an *alternating parity
   swap* — the sensor's ADC column-sequencer ordering. A naive `pixel[i] → lane[i mod 4]` produces a
   scrambled image that still *looks* like an image. See `CAMERA_SENSOR_PROTOCOL.md` §8.3.
2. **`iocheck/pt_camera_rx.v` is a placement proof, not a receiver.** Its `lvds_clock_in` output is a
   startup deadlock, and we no longer drive that pin at all (PLL mode). The file carries a warning
   header. Do not paste it forward.
3. **A full frame will never fit the UART.** `uart_ctrl`'s `len` is `reg [11:0]` (4095 bytes max) and
   the link is 11.5 kB/s — a 1.3 MB frame would take ~2 minutes. One *line* (1280 B) fits exactly and
   is a fine bring-up instrument. Frames need the Ft+.

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

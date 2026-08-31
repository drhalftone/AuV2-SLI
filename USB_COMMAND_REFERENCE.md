# USB command reference — every get and put

_Extracted from `sources_1/imports/RTL/uart_ctrl.v` on 2026-08-31, from the **code**
(the `rd_data` decoder and the write handler), not from header comments. Several
comments in this codebase have been found describing signals other than the ones
their code touches, so where a comment and the decoder disagreed, the decoder won._

Supersedes the register portions of `PC_INTERFACE_INVENTORY.md`, which was compiled
2026-08-17 "ahead of merging" and predates the camera block (`0x30`–`0x59`), the
genlock counters, and the whole HDMI diagnostic range (`0x60`–`0x8D`).

## Transports

| Port | Path | Availability |
|---|---|---|
| **A** | Pt V2 onboard FT2232H ch.B → host `COM6`, 115200 8N1 | every build |
| **B** | Ft+ FT601Q → host D3XX, SuperSpeed | only when the Ft+ is stacked |

Port A shares the line with ASCII status telemetry; replies interleave with it and
the host frame-scans past it. **Port B replies ride the video frame stream**, so with
the camera idle there are no replies at all — that is a property of the transport,
not a fault.

## Frame formats

`SYNC = 0xA5`, and **SYNC is never included in any checksum**. Every checksum sums
its payload to 0 mod 256.

| Operation | Request | Reply |
|---|---|---|
| **Write register** | `A5 57 ADDR DATA CK` | `K` ok · `N` read-only/undefined · `E` bad checksum |
| **Read register** | `A5 52 ADDR CK` | `ADDR DATA CK2` · `E` on bad request CK |
| **Upload table** | `A5 5B TGT D[0..N-1] CK` | `K` ok · `E` bad checksum or unknown target |
| **Read table** | `A5 72 TGT CK` | `TGT D[0..N-1] CK2` · `E` on bad CK or target |

A table is committed only on `K`. Reading an undefined register returns `0x00` — it
does not error, so a wrong address looks like a working register reading zero.

---

# PUT — writable registers

Eleven addresses accept writes. Everything else returns `N`.

| Addr | Name | Payload |
|---|---|---|
| `0x13` | **SLICTRL** | `{7:sw_en, 6:mode_en, 5:mode_val, 3:R, 2:G, 1:B, 0:orient}` |
| `0x14` | **MODEFORCE** | `{7:force_en, 3..0:idx}` — pin the offline mode, overriding the EDID pick |
| `0x15` | **LINKCTL** | `{7..2:secs×2, 1:proj, 0:host}` — self-timed HDMI disconnect |
| `0x16` | **CAMSIM** | `{7:rdy_en, 0:rdy_val}` — drive the camera-ready GPIO from the host |
| `0x30` | cam SPI addr lo | sensor register address `[7:0]` |
| `0x31` | cam SPI addr hi | `{7:rw, 0:addr[8]}` |
| `0x32` | cam SPI wdata lo | `[7:0]` |
| `0x33` | cam SPI wdata hi | `[15:8]` |
| `0x34` | cam SPI **GO** | any value fires the transaction |
| `0x37` | cam GPIO out | `{reset_n, …, trigger[2:0]}` |
| `0x39` | cam boot **GO** | any value starts the boot sequencer |

**`0x13` notes.** `sw_en` makes USB drive R/G/B/orient instead of the physical
`SW[3:0]` pins. `mode_en`+`mode_val` drive the SLI pattern enable instead of the
camera `mode` GPIO (`C1_in[1]`), which the XDC pulls LOW — so with no camera board
attached, pattern generation is off and video passes through. The `corr` LUT only
shapes **fringe** pixels, so an uploaded curve has no visible effect until the
pattern generator is actually enabled.

**`0x15` notes.** bit0 drops the HOST hot-plug (`hdmi_rx_hpa` low → the source
re-reads EDID and re-negotiates); bit1 tristates the TMDS **output** (the projector
loses signal). bits`[7:2]` = duration in **half-seconds**, 1–63 → 0.5–31.5 s. It
reconnects automatically; no host follow-up needed. Write `0x00` to cancel.
Reading `0x15` returns `{proj_active, host_active, remaining_half_secs[5:0]}`.
Example: `A5 57 15 19 CK` drops the host for 3 s.

**`0x14` gotcha.** Clearing `force_en` does **not** restore the power-up mode — it
leaves whatever was last forced. Logged against genlock G4.

---

# GET — readable registers

## Identity and control state

| Addr | Contents |
|---|---|
| `0x00` | **ID** = `0x48` `'H'` — proves the control bitstream is loaded |
| `0x01` | **VERSION** = `0x01` |
| `0x02` | **STATUS** — live `led` byte `{vsync, hsync, VPolarity, sel, mode, rdy, f_frm, trig}` |
| `0x06` | **FLAGS** `{…, usb_sw_en, lut_loaded}` |
| `0x10` | **PINS** `{eff_sw[3:0], phys_sw[3:0]}` — active vs physical switches |
| `0x13` `0x14` `0x15` `0x16` | readback of the control registers above |

## Offline mode decision — what `mode_select` chose from the EDID

| Addr | Contents |
|---|---|
| `0x20` | `{7:valid, 6:edid_ok, 3..0:mode_idx}` |
| `0x21` | refresh, Hz |
| `0x22` `0x23` | h_active lo / hi (12-bit) |
| `0x24` `0x25` | v_active lo / hi (12-bit) |
| `0x26` `0x27` `0x28` | pixel clock, kHz, lo / mid / hi (17-bit) |
| `0x29` `0x2A` | supported-mode mask lo / hi (13-bit, bit *i* = table index *i*) |

> **These are NOT the incoming video.** They report the mode the board *could*
> generate offline. With a PC sending 1280×720 these can read 1280×800. The incoming
> raster is `0x60`–`0x67`.

## PYTHON 1300 sensor

| Addr | Contents |
|---|---|
| `0x30`–`0x33` | SPI address / write data readback |
| `0x34` | `{cam_spi_busy, cam_done, …}` |
| `0x35` `0x36` | SPI read data lo / hi |
| `0x37` `0x38` | GPIO out / GPIO in |
| `0x39` | boot sequencer status |

## Camera datapath health

| Addr | Contents |
|---|---|
| `0x3A` | `{stw[2:0], rd_busy, calib, aligned, streaming, cap}` |
| `0x3B` | `{cfifo_ovf, ufifo_ovf, ufifo_empty, txe, 0000}` |
| `0x3C` `0x3D` | **`ldrop`** lo / hi — padded frames; **static = none lost** |
| `0x3E` `0x3F` | frame period **÷ 16**, in 72 MHz wordclk cycles |
| `0x40` `0x41` | exposure0 lo / hi, in **375 ns** units |

> **`0x3E`/`0x3F` is the period divided by 16** — multiply by 16 before converting.
> `0x40` is EXPOSURE, not the period's high byte; reading `0x3D`–`0x3F` as a 24-bit
> period yields a value 16× too large. Both mistakes were made on 2026-08-31.
> Frame rate = 72e6 / (16 × `[0x3F:0x3E]`).

## Genlock instrumentation

| Addr | Contents |
|---|---|
| `0x42`–`0x44` | trigger → integration start, **minimum**, 10 ns units |
| `0x45`–`0x47` | same, **maximum** (max−min *is* the measured jitter) |
| `0x48` `0x49` | last integration length, 160 ns units |
| `0x4A`–`0x4C` | `out_vsync` period, **last**, 10 ns units |
| `0x4D`–`0x4F` | period **minimum** over the window |
| `0x50`–`0x52` | period **maximum** over the window |
| `0x53`–`0x57` | max-exposure calculation (40-bit) |
| `0x58` `0x59` | **`ext_sync` edge count**, free-running 16-bit, wraps |

> The min/max windows reset every status tick, so each read is a fresh window, not a
> high-water mark. For `0x58`/`0x59`, sample over < 1 s at high edge rates or the
> 16-bit counter wraps and the computed rate is an aliased under-estimate.

## Incoming HDMI (pass-through), measured

| Addr | Contents |
|---|---|
| `0x60` `0x61` | h_active lo / hi |
| `0x62` `0x63` | v_active lo / hi |
| `0x64`–`0x66` | frame period, 10 ns units (Hz = 1e8 / period) |
| `0x67` | `{2:vs_pol, 1:vid_valid, 0:meas_ok}` |
| `0x68`–`0x6A` | recovered pixel clock, kHz |
| `0x6B` | `{7:primed, 6:idelay_rdy, 5:3:inv ch2/ch1/ch0, 2:dvid, 1:pll, 0:sym}` |
| `0x6C` | phase-FIFO re-primes — **static = healthy** |

> **Read `0x67` first.** `meas_ok = 0` means the FPGA is refusing to answer, and the
> other fields hold **stale** values rather than zeroing — a previous mode's raster
> can look live. `host/rx_timing.py` enforces this.

## HDMI decoder diagnostics

| Addr | Contents |
|---|---|
| `0x6D`–`0x70` | ch0 control cycles / all-three-lane coincidence |
| `0x71`–`0x74` | `in_vdp` cycles / **`in_adp`** cycles (upper half was labelled `in_dvid`) |
| `0x75`–`0x78` | **vsync rises / hsync rises** per window — *not* preamble counts |
| `0x79`–`0x7C` | guard band as coded / **`vdp_guardband_detect`** assertions |

> Sanity check for `0x75`–`0x78`: hsync must read `65536 / htotal` per window — 78 at
> 640×480@75, 40 at 720p — and vsync 0 or 1. Anything else means the link is sick.

## Link events and transmitted raster

| Addr | Contents |
|---|---|
| `0x7D`–`0x80` | drop counters: `evt_sel`, `evt_pll`, `evt_sym` (saturate at 255) |
| `0x81` | HPD-to-PC falling edges |
| `0x82` | `edid_ok` falling edges |
| `0x83`–`0x8A` | **outgoing** raster: h_active, v_active, period, `{valid, ok}` |
| `0x8B`–`0x8D` | transmitted pixel clock, kHz |

> `0x83`–`0x8D` measure what the board **transmits**, in either offline or
> pass-through mode, which is what makes the two directly comparable.
>
> **`0x82` is not an error counter.** `edid_merge` re-reads the display's EDID every
> 0.5 s to detect monitor presence, and `chk0_ok` drops during each read — so a
> **healthy** board counts exactly 2/s. It was twice mistaken for a fault.
>
> All of these saturate at 255 and then read *static*, which looks stale while they
> are still incrementing. Only a bitstream reload zeroes them.

---

# Tables

| TGT | Contents | Size | Direction |
|---|---|---|---|
| `0x00` | row LUT | 720 B | upload + readback — **vestigial**, tied off, read by nothing |
| `0x01` | column LUT | 1280 B | upload + readback — **vestigial** |
| `0x02` | **`corr` intensity correction** | 256 B | upload + readback — **the live linearisation table** |
| `0x03` | display's captured EDID | 256 B | **read-only** (`A5 5B 03` is rejected) |
| `0x04` | one captured camera line | — | **read-only** |
| `0x05` | the merged EDID served to the PC | 256 B | **read-only** |

**`0x02` is the one that matters.** 256 entries, one per grey level, applied as
`out = corr[cos]` in `pattern_gen` — that is what makes a 0–255 ramp reproduce
linearly on the projector. It powers up as identity. `0x00` and `0x01` are tied off
in `Au2_SLI.vhd` ("vestigial from the old indexMap/LUT ROM design").

For `0x03`: always 256 B, but if the display has no extension block, bytes 128–255
are stale RAM. Byte `0x7E` of block 0 is the authoritative extension count.

---

# Gotchas worth knowing before you drive this

* **`host/test_silicon.py` is destructive.** It uploads test patterns into `corr`,
  `lut` and `lutv` and leaves them there, which visibly corrupts the display until
  the bitstream is reloaded or a real curve is re-uploaded with `upload_corr.py`.
* **Replies longer than 576 bytes** were silently truncated until 2026-08-31: a 50 ms
  inter-byte watchdog counted down against the board's own reply. Fixed in `dd4ea98`.
* **An abandoned upload used to wedge the control plane** until the remaining bytes
  arrived — Ctrl-C during `upload_corr.py` did it. The same watchdog is the cure, and
  it still fires for that case.
* **Undefined reads return `0x00`**, so a mistyped address is indistinguishable from
  a register that legitimately reads zero.

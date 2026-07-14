# PYTHON 1300 (NOIP1SN1300A) — Protocol Constants for the FPGA

**Every number in this file is cited to the datasheet.** Nothing here is inferred, remembered, or
guessed. If the datasheet does not publish a value, this document says so rather than inventing one
— see §7, which is the one that will bite us.

Source: the onsemi NOIP1SN1300A datasheet (77 pp). Line numbers below (`L1428`) refer to a text
extract of it. **Neither the PDF nor the extract is committed** — this repo is public and the
datasheet is onsemi's copyright. Re-create both in one step (§8 lists every external source):

```sh
mkdir -p docs/datasheets
curl -sL -o docs/datasheets/NOIP1SN1300A.pdf \
     https://www.onsemi.com/download/data-sheet/pdf/noip1sn1300a-d.pdf
pdftotext -layout docs/datasheets/NOIP1SN1300A.pdf docs/datasheets/python1300.txt
```

Every `L<n>` citation below indexes that generated `python1300.txt`, so the citations are
reproducible without redistributing the document.

Part in the socket: **NOIP1SN1300A-QDI** — P1-SN, monochrome, **4 LVDS data channels**, 48-pin LCC.

---

## 1. SPI — the register interface

*(datasheet p.23, Figure 22 + Table 11; extract L1428–1507)*

Four wires: `sck`, `ss_n` (active low), `mosi`, `miso`. Synchronous to the master's `sck`,
**asynchronous to the sensor's system clock** — so SPI works with no sensor clock running at all.
That is what makes the Au V2 bring-up possible.

**9-bit addresses, 16-bit data words.** One transaction is **26 `sck` cycles**:

```
  ss_n  ‾‾\____________________________________________________/‾‾‾
  sck     _ 1 sck cycle _ | A8 A7 ... A1 A0 | R/W | D15 D14 ... D1 D0 |
                             9 addr bits      1     16 data bits
                             MSB first              MSB first
```

| Item | Value | Note |
|---|---|---|
| Address width | **9 bits**, MSB first | L1447–1448 |
| Data width | **16 bits**, MSB first | L1447–1448 |
| 10th bit after address | **R/W: `1` = write, `0` = read** | L1443–1447 |
| Start | `ss_n` low, then **one `sck` cycle** before the address begins | L1458–1461 |
| End | `ss_n` high **one clock period after** the last bit | L1454–1456 |
| Between transactions | **≥ 2 `sck` periods**, `ss_n` **high** between uploads | L1452–1456 |

**Edges — read this twice, it is asymmetric:**

- The **sensor samples `mosi` on the RISING edge** of `sck`. Therefore the **master must drive
  `mosi` on the FALLING edge**. *(L1429–1432)*
- The **master must sample `miso` on the FALLING edge** of `sck`. *(L1441–1444)*

Both directions move on the falling edge and are captured on the opposite edge — that is *not* the
usual SPI mode-0 arrangement for `miso`, and getting it wrong yields data shifted by one bit.

For a read, the sensor drives `miso` starting after the R/W bit; `miso` is high-Z outside that
window (so a pull is fine, and the board fits one).

**Timing** *(Table 11, L1492–1507)*:

| Symbol | Meaning | Limit |
|---|---|---|
| `tsck` | `sck` period | **≥ 100 ns** (i.e. **max 10 MHz**) |
| `ts_mosi` / `th_mosi` | `mosi` setup / hold | 20 ns / 20 ns |
| `ts_miso` | `miso` setup | `tsck`/2 − 10 ns |
| `tsssck` | `ss_n` low → `sck` rising | `tsck` |
| `tsckss` | `sck` falling → `ss_n` high | `tsck` |

The datasheet notes the max SPI frequency *scales with the input clock frequency* (L1449–1451).
With **no sensor clock running** during first bring-up, run `sck` slowly — a few hundred kHz off
`clk100` is free and removes the question entirely.

---

## 2. Chip ID — the bring-up target

*(Table 28, Register Map; extract L2926–2957)*

| Reg | Field | Default | Type | Meaning |
|---|---|---|---|---|
| **0** | `chip_id[15:0]` | **`0x50D0`** | **Status (read-only)** | **Chip ID** |
| 1 | `resolution[9:8]` | `0x0` | RW | **`0x0` = PYTHON1300** (0x1 = P300, 0x2 = P500) |
| 2 | `color[0]` | `0x0` | RW | `0` = Monochrome ✔ (our part is SN) |
| 2 | `parallel[1]` | `0x0` | RW | `0` = **LVDS** ✔ (`1` = parallel) |

> ### Reading `0x50D0` from register 0 is the whole hardware gate.
> It requires no sensor clock, no PLL, no LVDS, and no NDA material. It proves the power tree came
> up, the DF40 pin map and the stack pass-through are right, `reset_n` released, and our SPI master
> works — in one transaction.

For a write/read-back check that does not disturb anything, use **register 116** (training pattern,
RW, default `0x3A6`): read it (expect `0x3A6`), write a different value, read it back, restore.

---

## 3. ⚠️ LVDS outputs default to POWERED DOWN — this is why the Au is safe

*(Serializers/LVDS/IO, Block Offset 112; extract L3339–3353)*

| Reg | Bit | Field | Default | Meaning |
|---|---|---|---|---|
| 112 | [0] | `clock_out_pwd_n` | **`0`** | `0` = **powered down**, `1` = powered up |
| 112 | [1] | `sync_pwd_n` | **`0`** | `0` = **powered down**, `1` = powered up |
| 112 | [2] | `data_pwd_n` | **`0`** | `0` = **powered down**, `1` = powered up |

**All LVDS drivers — clock, sync, and all four data channels — are OFF at reset** and only turn on
when we explicitly write register 112.

This settles the hazard flagged during the Au V2 pin analysis: the sensor's `dout0±` pair lands on
Au bank 15 (VCCO = 1.35 V, which Alchitry documents as *not* 3.3 V tolerant). Since the LVDS drivers
are powered down by default, **`dout0` never drives that bank during SPI-only bring-up** — provided
we simply never write register 112 on the Au. That is a one-line rule, not a gamble.

---

## 4. Clocking — **DECISION: use the sensor's internal PLL** (72 MHz on `clk_pll`)

> ### This supersedes the board README §5, which planned to bypass the PLL.
>
> The README's plan was *"the FPGA drives the sensor's LVDS clock directly (`lvds_clock_in±` at
> ~360 MHz, PLL bypassed). `clk_pll` is routed but unused — kept as an escape hatch."*
> **We are taking the escape hatch.** Reasons, in order of weight:
>
> 1. **Avnet's published register sequence is the PLL variant** (`docs/reference/onsemi_python_sw.c`:
>    reg 16 = `0x0003` → bypass cleared; reg 32[2] = 1 → PLL clock selected). There is **no**
>    published sequence for bypass mode, and hand-modifying it is exactly what the datasheet warns
>    against: *"Different settings are not allowed and may cause the sensor to malfunction."*
> 2. **It deletes the entire 360 MHz LVDS transmit path** — no ODDR, no OBUFDS, no 360 MHz clock to
>    route or constrain. It also makes the startup-circularity trap impossible by construction.
> 3. **It costs nothing.** Both modes reach the same 720 Mbps/lane and the same max frame rate (§4.1).
>
> **Open risk:** the ≤ 20 ps input-jitter spec (§4.1). Unresolved — see task #14.

### 4.0 The two modes, and why the frame rate is identical

*(Electrical Interface — P1-SN/SE/FN LVDS; extract L385–417)*

| Mode | FPGA drives | Rate | Sensor's ×5 |
|---|---|---|---|
| **PLL used** ✔ *chosen* | `clk_pll` (**CMOS**, LVCMOS33) | **72 MHz** | internal PLL → 360 MHz bit clock |
| LVDS clock in (bypass) | `lvds_clock_in±` (**LVDS**) | **360 MHz** | — (already the bit clock) |

In 10-bit mode the clock generator runs **divide-by-5** (reg 32[3] `adc_mode` = 0), so
**72 MHz × 5 = 360 MHz**, DDR → **720 Mbps per lane in either mode**. The PLL merely moves the ×5
from our FPGA into the sensor. `fserdata` = 720 Mbps (L343) and the headline **210 fps ZROT /
165 fps NROT @ SXGA** carry no dependence on the clocking mode.

The `ratspi` table confirms it from the other side: 10-bit / 4 channels is `fin/6` with the PLL
(72/6 = 12 MHz) and `fin/30` with the LVDS clock (360/30 = 12 MHz) — the same SPI ceiling.

> **The binding constraint is USB, not the sensor clock.** At the sensor's full 210 fps, packed
> 10-bit is 1280 × 1024 × 210 × 1.25 B ≈ **344 MB/s** — essentially *at* the Ft+'s measured
> 350 MB/s. So we are USB-limited well before the clocking choice matters.

### 4.1 `clk_pll` requirements

*(extract L387–393)*

| Spec | Value |
|---|---|
| `fin` (PLL used) | **72 MHz** |
| `tidc` duty cycle | **45 – 50 – 55 %** |
| `tj` input clock jitter | **≤ 20 ps** ⚠️ *(RMS or p-p? unresolved — task #14)* |

FPGA side: 100 MHz → MMCM (**D=5, M=54 → VCO 1080 MHz, CLKOUT /15 = 72.000 MHz exact**) → BUFG →
OBUF → `cam_clk_pll`. Two MMCMs and five PLLs are spare. Pin: Au **N9** / Pt **AA18**, bank 14,
LVCMOS33 — already routed on the board.

### 4.2 Register fields (for reference)

*(PLL Block Offset 16, extract L2990–3004; Clock Generator Block Offset 32, extract L3054–3072)*

| Reg | Bit | Field | Default | Meaning |
|---|---|---|---|---|
| 16 | [0] | `pwd_n` | `0` | PLL power down (`0` = down, `1` = operational) |
| 16 | [1] | `enable` | `0` | PLL enable |
| 16 | [2] | `bypass` | `1` | `1` = PLL bypassed, **`0` = PLL active** ← Avnet writes reg 16 = `0x0003` |
| 32 | [0] | `enable_analog` | `0` | Enable analogue clocks |
| 32 | [1] | `enable_log` | `0` | Enable logic clock |
| 32 | [2] | `select_pll` | `1` | Input clock select: `0` = LVDS clock input, **`1` = PLL clock input** ✔ (keep) |
| 32 | [3] | `adc_mode` | `0` | **`0` = divide-by-5 (10-bit mode)** ✔ (`1` = divide-by-4, 8-bit) |
| 32 | [5:4] | `mux` | `0x0` | LVDS channel multiplexing; **`0x0` = all 4 channels** *(Table 26, L2511–2516)* |

In **PLL mode** (our choice) the reset defaults for reg 16 are *not* what we want and Avnet's SEQ01
overwrites them: `{16, 0xFFFF, 0x0003}` sets `pwd_n`=1, `enable`=1, `bypass`=**0**. Register 32[2]
stays at its default `1` (PLL clock input selected) — no deviation needed there.

The datasheet's warning applies only to the road we did **not** take:

> *"In the serial modes, if the PLL is not used, the LVDS clock input must be running."* *(L1078–1080)*

Since we *are* using the PLL, we do not drive `lvds_clock_in±` at all — those two pins stay
undriven and unconstrained, and the FPGA's only clock obligation to the sensor is a steady
**72 MHz on `clk_pll`**.

**After SEQ01, poll the PLL lock bit — register 24[0] `lock`** *(PLL Lock Detector, L3034–3038)* —
before proceeding to SEQ03. That is Avnet's `SENSOR_INIT_SEQ02` step, and the datasheet says the
lock-detect flag is the documented way to know the clock is stable *(L1091–1097)*.

---

## 5. Sync channel — framing codes (10-bit mode)

*(Table 20/21, extract L2343–2398; line structure from Figure 34, L2326–2341)*

The frame-sync word is `{type[9:7], marker[6:0]}` where `marker` = **`0x2A`** (register 117[6:0]):

| Code | `[9:7]` | Full 10-bit word | Meaning |
|---|---|---|---|
| **FS** | `0x5` | **`0x2AA`** | Frame start |
| **FE** | `0x6` | **`0x32A`** | Frame end |
| **LS** | `0x1` | **`0x0AA`** | Line start |
| **LE** | `0x2` | **`0x12A`** | Line end |

> **Every frame-sync code is followed by a separate 3-bit window-ID word** (bits [2:0], value 0–7;
> all other bits `0`). Do not mistake it for pixel data. *(L2371–2377)*

Data-classification codes — what the *data* channels are carrying this cycle:

| Code | Reg | Default | Meaning |
|---|---|---|---|
| **BL** | 118[9:0] | **`0x015`** | Black pixel data (not image; used for offset correction) |
| **IMG** | 119[9:0] | **`0x035`** | Valid image pixel data |
| **CRC** | 125[9:0] | **`0x059`** | Data channels are carrying the line's CRC |
| **TR** | 126[9:0] | **`0x3A6`** | Training pattern (idle) |

**Line structure** *(Figure 34)*:

```
  TR ... | LS  ID  IMG IMG ... IMG  LE  ID  CRC | TR ...
```

## 5.1 Training pattern — for word alignment / bitslip

*(Table 24, extract L2462–2472; register 116, L3357–3361)*

| Reg | Default | Meaning |
|---|---|---|
| **116[9:0]** | **`0x3A6`** | Training pattern sent on the **data** channels during idle. |

The datasheet is explicit about its purpose: *"This data is used to perform word alignment on the
LVDS data channels."* (L3359–3361). The sync channel's TR code (reg 126) is `0x3A6` as well, so all
five channels train on the same word by default.

`0x3A6` = `11 1010 0110`b.

## 5.2 CRC

*(L2484–2509)* Per line, per data channel. Idle/training words are **not** included.
The sync channel is **not** protected. 10-bit polynomial:

```
  x^10 + x^9 + x^6 + x^3 + x^2 + x + 1
```

Seeded at the start of each line; seed is all-0s or all-1s per the `crc_seed` register.
A CRC check gives us a free, self-checking correctness signal for the receiver — worth wiring up.

---

## 6. Sequencer, power, and reset

- **Enable sequencer = set bit `192[0]`.** Disable = clear it. *(L1120–1121, L1132–1133)*
- Static registers (32, 40, 48, 64–71, 112) must only be changed while **`192[0] = 0`**
  *(Table 6, L1181–1196)*.
- **Power-up order: `vdd_18` → `vdd_33` → `vdd_pix`**, each **> 10 µs** apart *(Figure 18, L1069–1071)*.
- **Power-down order: `vdd_pix` → `vdd_33` → `vdd_18`.** *(L1102–1106)*
- **The sensor must be in reset BEFORE the clock input stops.** Otherwise *"the internal PLL becomes
  unstable and the sensor gets into an unknown state. This can cause high peak currents."*
  *(L1096–1101)*

---

## 7. ⚠️ THE GAP: the register upload sequence is NDA-gated

**This is the one thing the datasheet does not give us**, and it blocks the boot sequencer (task #6).

> *"The SPI uploads that need to be executed to configure the sensor for P1-SN/SE/FN, P3-SN/SE/FN
> 10-bit serial mode, with the PLL, and all available LVDS channels, as well as all other supported
> modes ... **are available to customers under NDA at the onsemi Image Sensor Portal.**"*
> — extract L1082–1084

Reinforced by Table 6 (L1181–1196), which lists the static registers — clock generator (32), image
core (40), AFE (48), bias (64–71), LVDS (112) — with the description **"Configure according to
recommendation"**, and by §"Required Register Upload" (L1101–1106): *"the `reserved` register
settings are uploaded through the SPI register. Different settings are not allowed and may cause the
sensor to malfunction."*

**So: the register *map* is public; the recommended *values* — including the reserved registers —
are not.**

**What this does and does not block:**

| | Blocked? |
|---|---|
| SPI master (#2), mailbox (#3), XDC (#4) | **No** — §1 is fully specified |
| **Chip-ID read (#5)** | **No** — register 0 is a read-only status register; no configuration needed |
| Behavioral LVDS model (#7), receiver (#8), bitslip (#9), sync decode (#10) | **No** — §5 gives every code we need |
| **Boot sequencer (#6)** — getting the sensor to actually *stream* | **YES** |
| Real pixel capture (#12) | **YES** (transitively — needs #6) |

**Routes to the NDA material, in order of preference:**

1. **onsemi Image Sensor Portal** — register the part, sign the NDA, download the config for
   `P1-SN 10-bit serial, 4 LVDS, PLL bypassed`. This is the intended path and gives a supported answer.
2. **The eval kit** — `NOIP1SN1300A-QDI-A-GEVK` ships with host software whose register-config
   files are the same sequence.
3. **Published reference designs** — several open camera projects carry a PYTHON init sequence.
   Usable to cross-check, but unsupported and version-sensitive; not a substitute for (1).

Note this is *not* on the critical path for the Au V2 milestone. Steps #2–#5 — the SPI master, the
mailbox, the constraints, and the chip-ID read that validates the whole PCB — need **none** of it.
Start the NDA request now so it lands in parallel, and keep building.

---

## 8. External sources — how to re-fetch (nothing here is vendored)

**This repo is public, so no third-party material is committed.** All of it is fetched into
gitignored directories. Everything below is a *reference we read*, not code we ship — our RTL is
our own.

### 8.1 onsemi datasheet — `docs/datasheets/` (gitignored)

onsemi's copyright. See the fetch command at the top of this file.

### 8.2 Avnet reference driver — `docs/reference/onsemi_python_sw.c` (gitignored)

The source of the register upload sequence (§7). Avnet publish it themselves, but the file header
states: *"This design is the property of Avnet. **Publication of this design is not authorized**
without written consent from Avnet."* So we read it and re-derive; we do **not** redistribute it.

```sh
mkdir -p docs/reference
gh api "repos/Avnet/hdl/contents/Projects/fmchc_python1300c/software/sw_repository/sw_services/onsemi_python_sw_v3_3/src/onsemi_python_sw.c" \
  --jq '.content' | base64 -d > docs/reference/onsemi_python_sw.c
```

> The *register values* are facts about a chip and we use them freely (with the two deviations in
> §7 — monochrome, and our clocking mode). The *file* is Avnet's and stays out of the tree.

### 8.3 Open Vision Computer — `docs/reference/ovc_*` (gitignored)

`github.com/osrf/ovc` — **Apache-2.0**, DARPA FLA program. 3× PYTHON 1300 on an Artix-7 (OVC0);
the OVC1/OVC2 firmware is Intel (Cyclone), so **only the decoder ports to us** — the SERDES front
end does not.

```sh
git clone --depth 1 https://github.com/osrf/ovc.git
cp ovc/ovc1/firmware/fpga/ovc/python_decoder.v docs/reference/ovc_python_decoder.v
cp ovc/ovc1/firmware/fpga/ovc/python_defs.inc  docs/reference/ovc_python_defs.inc
```

**Two things it gave us** (see task #10):

1. **The de-interleave is NOT a mod-4 split.** It is an 8-pixel kernel with an *alternating parity
   swap* (their `UNSWAP_KERNELS`) — the sensor's ADC column-sequencer ordering. Their own sim model
   punts on it (*"TODO: actually model the ADC sequencer someday. It's tricky."*). A naive mod-4
   de-interleave yields a scrambled image that still looks like an image.
2. **It confirms §5.** They run 8-bit mode, and their codes are *exactly* our 10-bit codes `>> 2`,
   eight for eight — which is what the datasheet says 8-bit mode does. Independent corroboration
   of our derivation from a flight-proven design.

If we adapt that decoder, **retain Apache-2.0 attribution** — the upstream file carries no header,
so we must add one.

### 8.4 McMaster HFRC — read only, DO NOT COPY

`github.com/yamnchalich/HFRC` — full PYTHON 1300 HDL (LVDS deserializer, SPI FSM), PLOS ONE 2020.
**GPL-3.0.** Copying its HDL would relicense this design. Read for understanding; do not paste.

### 8.5 Xilinx XAPP1017

The 1:10 LVDS SERDES reference for the Artix-7 front end (what HFRC used). Our
`LauPythonCamera_Pt_Stack/iocheck/pt_camera_rx.v` already implements this structure and is proven
to place on the real pins.

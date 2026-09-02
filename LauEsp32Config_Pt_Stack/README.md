# ESP32 wireless programmer — Pt V2 element card

_Started 2026-09-02. Nothing is built. Scope is deliberately narrow: **reprogram
the Pt V2's FPGA over WiFi from a board inside the stack.** Everything needed
electrically is now confirmed from Alchitry's own Rev A schematic and pinout sheet,
both in `docs/`. Read the **rev B warning** in §3 before committing a layout._

---

## 1. Why this board exists

**The Ft+ cannot configure the FPGA, and it is the only host↔FPGA link this camera
has.** That single fact is the whole justification.

Today the Pt is programmed over its own USB-C through the FT2232H, by the Alchitry
loader. Fine on a bench with the enclosure open; not fine for

- a camera **sealed in the printed enclosure**, where the Pt's USB-C is not meant
  to be user-accessible;
- a **just-in-time workflow** — write HDL, compile, push — where walking a cable to
  the part defeats the point;
- **multiple cameras**, where the cable count grows with the array.

So: an element card carrying an ESP32, sitting in the stack, driving the FPGA's
JTAG from inside. It replaces the GPIO board currently used as a spacer, so it
costs no stack height that is not already accounted for.

> The founder of Alchitry, who designed these boards, says the approach should
> work. §3 and §4 are the pin- and part-level confirmation of that.

## 2. Configure RAM, never the boot flash

**The bitstream goes into the FPGA's volatile configuration memory. The QSPI boot
flash is never written.**

The consequence is the point: **power-cycling the camera restores its camera
functionality.** The Pt reloads the shipped image from flash on boot, so whatever
experimental bitstream was pushed is simply gone.

That makes the failure mode of a bad JIT build *a reboot*, not a brick. A pipeline
that can write boot flash can permanently kill a sealed unit; one that can only
write configuration RAM cannot. **Treat "never touch the QSPI flash" as a design
rule, not a phase-one simplification.**

The schematic reinforces it: **`CCLK_0` (L12) is shared with `QSPI_SCK`** — the
configuration clock and the flash clock are the same net. Anything driving
configuration is already adjacent to the flash bus, so the separation has to be
kept deliberately, in firmware.

## 3. J3 "Control" — the connector this board lives on

From Alchitry's **"Pinout and Trace Lengths"** sheet, exported to
`docs/Pt_V2_pinout_trace_lengths.csv`. Generated from the CSV, not transcribed.
**Identical on the top and bottom sides**, so a pass-through card sees the same map
on both faces.

| Pin | Signal | | Pin | Signal |
|---|---|---|---|---|
| 1–15 odd | `+3V3` | | 2–16 even | `RAW` |
| 17–27 odd | `GND` | | 18–28 even | `GND` |
| 29, 33 | `IO_N` | | 30, 34 | `IO_N` |
| 31, 35 | `IO_P` | | 32, 36 | `IO_P` |
| **37** | **`Reset`** | | 38 | `VBSEL_A` |
| **39** | **`DONE`** | | 40 | `VBSEL_B` |
| **41** | **`Program`** | | 42 | `A1V8` |
| **43** | **`TDI`** | | 44 | `AVP` |
| **45** | **`TDO`** | | 46 | `AVN` |
| **47** | **`TMS`** | | 48 | `AREF` |
| **49** | **`TCK`** | | 50 | `AGND` |

Everything the board needs is here, and more than just the four JTAG pins:

- **`Program` (41)** — force a reconfigure rather than relying on JTAG state alone.
- **`DONE` (39)** — read back whether a push actually took. Without it the ESP32
  cannot tell the host a configuration succeeded, and a JIT loop would be debugging
  HDL when the load silently failed.
- **`Reset` (37)** — a clean way back to a known state.
- **`+3V3` on eight pins, `RAW` on eight** — take either rail.
- **Four spare `IO_N`/`IO_P` pairs (29–36)** — a side channel between ESP32 and
  fabric that costs nothing from Bank A or Bank B.

**`INIT_B` is NOT on J3.** It exists on the FPGA (U12) but does not reach the
connector, so the init/clear phase cannot be observed. `DONE` is the completion
signal available, and the firmware should not assume more.

> ### ⚠ THE JTAG PINS MOVE IN REV B
>
> Alchitry's sheet carries this note verbatim in the Control column:
>
> **"Note: JTAG pins will change places to match Au with rev B"**
>
> The table above is **rev A**. A layout hard-wired to these numbers is wrong on
> rev B, and the failure is not obvious — TDI/TDO swapped is a chain that does not
> respond, which reads exactly like a dead ESP32 or a bad joint. This project has
> already lost days to that class of fault twice: the LVDS eye that looked like bad
> solder, and the chip-ID silence that was three hand-solder defects.
>
> **Confirm the board revision before committing a layout**, and prefer a footprint
> that can be strapped, or a buffer whose pin order can be jumpered, over one that
> hard-wires rev A.

## 3b. A second path: SPI to the running design

J3 carries **four spare FPGA I/O pairs — eight signals — and the current designs use
none of them.** Decoded from sheet 3 of the Rev A schematic (Altium doubles
characters in the PDF text layer; these are the de-doubled net names):

| J3 pin | FPGA net | | J3 pin | FPGA net |
|---|---|---|---|---|
| 29 | `BANK14_L18_N` | | 30 | `BANK34_L5_N` |
| 31 | `BANK14_L18_P` | | 32 | `BANK34_L5_P` |
| 33 | `BANK34_L2_N` | | 34 | `BANK14_L23_N` |
| 35 | `BANK34_L2_P` | | 36 | `BANK14_L23_P` |

Two pairs on bank 14, two on bank 34. **SPI needs four signals** — SCK, MOSI, MISO,
CS — so half of them do the job and the other four are free for an interrupt, a
handshake, or flow control. They are differential-capable pairs, so LVDS is
available on the same wires if CMOS SPI ever proves too slow.

**Keep these two paths distinct, because they solve different problems:**

| | JTAG (§3) | SPI (here) |
|---|---|---|
| Talks to | the configuration engine | a design that is already running |
| Available | always, even with dead fabric | only once a bitstream is loaded |
| Used for | pushing a bitstream | exchanging data with it |

That second row is the point. Once a JIT image is configured, the ESP32 and the
fabric can move data directly — results out over WiFi without involving the host or
the Ft+ at all. For a compute module that is arguably worth as much as the
programming path.

**Unused today, confirmed:** nothing in `constrs_1/imports/RTL/*.xdc` references a
bank 14 or bank 34 pin, so the SLI and camera designs leave the control connector's
I/O entirely free.

> **This corrects the repo.** `CAMERA_IO_MAP.md:35` records J3's signal pins as
> "—", i.e. none. That extraction was aimed at Bank A/B and missed these four
> pairs; Alchitry's pinout sheet and sheet 3 of the schematic both show them.

### 3b.1 How fast, and what the eight signals should carry

**The wire is not the constraint; the C3's pin count is.**

GP-SPI2 runs to **80 MHz**, and the part has Dual and Quad SPI:

| Mode | Signals | Theoretical | Realistic sustained |
|---|---|---|---|
| Single-bit SPI @ 80 MHz | 4 | 80 Mbps = **10 MB/s** | ~8 MB/s |
| **Quad SPI @ 80 MHz** | 6 | 320 Mbps = **40 MB/s** | **~10–25 MB/s** |

The FPGA side is nowhere near its limit — Artix-7 I/O would go far faster. The
ceiling is the C3's GDMA, its single 160 MHz core, and 400 KB of SRAM to land in.
Note this is **3–6× the radio's ~4 MB/s**, so the local link is never what limits
anything leaving over WiFi — but it is real bandwidth for ESP32↔fabric work.

**Audio, as a worked case, is trivial:** 48 kHz × 24-bit stereo is 2.3 Mbps =
**288 kB/s**, about 1–3% of the link. Even 192 kHz × 32-bit × 8 channels is 6 MB/s
and still fits.

**But the GPIO budget does not.** The C3-MINI-1 has **15 GPIOs** — that is in the
datasheet title, not a derived number:

| Function | Pins |
|---|---|
| JTAG: TCK, TDI, TDO, TMS | 4 |
| `PROGRAM_B`, `DONE` | 2 |
| USB D+/D− (GPIO18/19) | 2 |
| SPI to FPGA | 4 |
| I2S microphone (BCLK, WS, SD) | 3 |
| **Total** | **15** |

Exactly at the limit — with no `Reset`, no margin, single-bit SPI only, and ignoring
that GPIO2/8/9 are strapping pins (GPIO9 is reserved for the §6.0 boot pad). In
practice it is over budget.

> ### Put sensors on the FPGA side, not the ESP32 side
>
> A microphone can sit **physically on this card** while its data lines run to J3's
> spare pairs rather than into the ESP32. That returns 3 GPIOs — but the better
> reason is **timing**.
>
> Audio in this system will want to be aligned with camera frames or projector
> vsync, and **the FPGA owns those clock domains**. Routing it through the ESP32
> inserts buffering and jitter and gives up sample-accurate alignment to the exact
> events worth correlating against. The FPGA can timestamp audio in the same domain
> it already timestamps vsync.
>
> A PDM MEMS mic needs **two wires** (clock out, data in); I2S needs three. A CIC
> decimator for PDM is a small amount of fabric. The ESP32 can still configure the
> part over I2C if it needs configuring.

**Proposed allocation of the eight signals** — six used, two in reserve:

| J3 pins | Net | Use |
|---|---|---|
| 29 / 31 | `BANK14_L18_N/P` | SPI SCK, MOSI |
| 34 / 36 | `BANK14_L23_N/P` | SPI MISO, CS |
| 30 / 32 | `BANK34_L5_N/P` | PDM mic CLK, DATA |
| 33 / 35 | `BANK34_L2_N/P` | spare — IRQ, handshake |

If Quad SPI is wanted later it needs 6 signals, which costs the spare pair and the
mic has to move to a pair shared with something else. Single-bit SPI at 80 MHz is
already 2× the radio; take quad only if the ESP32 is doing local processing.

**Two things to confirm before relying on it:**

- **Bank VCCO.** ESP32-C3 GPIO is 3.3 V. Bank 14 is almost certainly 3.3 V, but
  bank 34's rail is unverified here, and the Pt's `VBSEL` mechanism can switch a
  bank to 2.5 V. Check sheet 6, LINEAR REGULATORS, before committing.
- **The bank-16/34 naming caveat.** Alchitry's own product page warns that "the
  names of signal on bank 16 and bank 34 are swapped in the schematic. This makes no
  functional difference but may be confusing." Treat `BANK34` as a label, not a bank
  number, and **constrain by package pin**.

## 4. What the schematic settles

From `docs/Alchitry_Platinum_RevA_schematic.pdf`, **sheet 8 "FTDI PROGRAMMING"**.
These two findings are why this board is straightforward rather than delicate.

### 4.1 JTAG is at 3.3 V — no level shifting

The JTAG and configuration pins are all on **bank 0**, the dedicated configuration
bank: `TCK_0` (V12), `TDI_0` (R13), `TDO_0` (U13), `TMS_0` (T13), `PROGRAM_B_0`
(N12), `INIT_B_0` (U12), `DONE_0`, `CFGBVS_0`. `VCCO_0` and the `PROGRAM_B` /
`DONE` pull-ups all sit on **+3V3**.

**ESP32 GPIO drives these directly.** No translator, nothing extra in the fast
path.

### 4.2 The FT2232H is isolated by 100 Ω — contention is survivable

**`RN2` is a 100 Ω series resistor array** between the FT2232H's ADBUS pins and the
`TCK` / `TDI` / `TMS` nets. That is the most useful thing on the sheet.

- A hard fight — FTDI driving high, ESP32 driving low — is limited to
  3.3 V / 100 Ω ≈ **33 mA**, which both parts survive. The board cannot destroy
  itself if both ends drive at once.
- In practice there is no fight: the FT2232H's MPSSE pins are high-Z unless a USB
  host has the interface open, so with no cable attached the ESP32 owns the bus.
- **`TDO` is an FPGA output** feeding the FTDI's input, so the ESP32 only ever
  reads it. No contention on that line at all.

**So a mux is a robustness choice, not a requirement.** Direct connection works. A
buffer with an output enable is still the better build — it makes "USB cable wins"
explicit rather than emergent — but the design is not blocked on it, and a first
prototype can go direct.

## 5. Programming modes

| Mode | What it is | Use |
|---|---|---|
| **XVC over WiFi** | ESP32 presents as a Xilinx Virtual Cable on a TCP port; Vivado connects and programs as though the part were local | development, debug, ILA |
| **Standalone push** | bitstream streamed over WiFi and clocked straight in | the JIT path, no PC tooling in the loop |
| ~~QSPI boot flash~~ | writes the persistent image | **excluded** — see §2 |

XVC is worth having even though the JIT path does not need it: it makes Vivado's
own tools, including hardware debug, work over the air — which is what you will
want the first time a pushed image misbehaves.

**Timing.** The merged Pt bitstream is **2.46 MB = 19.7 Mbit**
(`build_merged/Au2_SLI_merged.bin`). Bit-banged at 1 MHz TCK that is ~20 s; driving
TCK/TDI from the ESP32's SPI peripheral at 10 MHz brings it to ~2 s. The compile
dominates the JIT loop either way.

## 6. Remaining design problems

### 6.0 No USB connector — pads instead. DECIDED

**The board carries no USB receptacle.** Firmware reaches the ESP32 by a pad field
for the first flash, and by OTA thereafter.

The C3 makes this cheap: it has a **native USB Serial/JTAG controller**, so there is
no bridge chip to justify — `GPIO18 = USB_D−`, `GPIO19 = USB_D+` go straight to a
host (`docs/esp32-c3-mini-1_datasheet.pdf`). The classic UART bootloader is also
available on `GPIO20/21` (U0RXD/U0TXD) as a fallback.

A connector was rejected on the edge budget, not the parts cost. The stack already
presents **two HDMI and two USB-C** (Pt config, Ft+ data), and the printed enclosure
has exactly **one port window**. A fifth connector means another opening to cut and
seal — in a design whose entire purpose is that a *sealed* unit can be
reprogrammed.

**Bring out, as castellated edge pads or a pad field:**

| Pad | Why |
|---|---|
| `USB_D+` (GPIO19) | native USB, first flash |
| `USB_D−` (GPIO18) | " |
| `EN` | reset into the bootloader |
| `GPIO9` | boot strap — hold low to force download mode |
| `GND` | return |
| `+3V3` | bench power when the card is out of the stack |

**Place them at the enclosure's open face** so a pogo fixture can reach them without
pulling the stack apart.

**The recovery story is OTA with A/B partitions and rollback.** That is the same
philosophy as §2: do not try to prevent a bad update, make it recoverable. Rollback
is the ESP32's equivalent of the power-cycle that restores the FPGA. The residual
risk is a bootloader-level mistake that rollback cannot catch — that one needs the
pads, which is why they exist and why their placement matters.

> If a real port is ever wanted, it does fit: a USB-C receptacle is ~3.2 mm against
> the 4.125 mm gap. It would have to sit at the **W edge** to line up with the base
> box's existing port window — the same end where the lens box seam is now filled.

### 6.1 Bitstream storage
2.46 MB does not sit comfortably in a 4 MB module beside an application. Either an
8/16 MB module, or **stream over WiFi and never store it** — which keeps versioning
on the server and leaves no stale image to load by accident.

### 6.2 Power
The 3V3 rail is a **4 A** supply (`CAMERA_POWER_DESIGN.md:300`), so the ESP32's
average draw is irrelevant. Its WiFi TX peaks are fast transients, though: **local
bulk capacitance on this card**, not borrowed from the Pt's decoupling.

### 6.3 Mechanical budget — SETTLED

The DF40 stack sets **4.125 mm** board-surface to board-surface with the spacer in
place: `(24.50 − 5 × 1.60) / 4`, against the connector's 4.0 mm nominal. Before the
spacer it was 4.200 mm over three gaps, so the two derivations agree.

**The Ft+ has no components on its underside** (inspected on the assembled stack),
so the whole gap above this board is available.

| | |
|---|---|
| Gap to the Ft+ above | **4.125 mm**, all of it usable |
| **ESP32-MINI-1 / C3-MINI-1**, on the **top** face | 2.4 mm |
| Clearance remaining | **1.725 mm** |

That is a comfortable fit, and the MINI series is the right pick over a WROOM
(3.1 mm) — 0.7 mm is a large fraction of a 4 mm budget for identical silicon.

**The bottom face is NOT yet budgeted.** The Hd+'s top-side component heights have
not been measured, so keep the underside low-profile — 0402/0603 passives, small
SOT/SOIC — until someone puts calipers on the Hd+. Decoupling and any JTAG buffer
can live there; nothing tall should.

> The STEP models in `docs/` cannot settle this. Their point clouds span −13.71 to
> +19.20 mm on both boards — ~33 mm around a 1.6 mm PCB — so they carry connector
> bodies and construction geometry that cannot be attributed to parts without a
> full B-rep traversal. `check_step.py` parses them but rejects Alchitry's face
> winding. Measure the hardware; do not trust a number derived from those files.

### 6.4 RF, and the aluminium shroud
A 2.4 GHz radio is going inside a stack with 720 Mbps LVDS, DDR3 and HDMI TMDS —
noise in both directions.

**Choosing the MINI on the top face makes this the binding constraint, not the
mechanical one.** The board fits easily; the antenna is the part that does not.

- The `-MINI-1` variants carry a **PCB antenna** at one end. That antenna would sit
  **4.125 mm below the Ft+'s ground plane**, with the Hd+'s plane below it — a
  patch antenna sandwiched between two copper planes a few millimetres away. It
  will detune and lose most of its efficiency. This fits and does not work.
- **The CNC aluminium shroud ring would then sit around it as well**, which no
  firmware can fix.

**DECIDED: external wired antenna — the `U` variant, `ESP32-C3-MINI-1U`.** The
PCB-antenna part is not viable in this stack, and the wired antenna is the right
answer rather than a workaround: it is the only version whose radiating element can
be placed where neither the two ground planes nor the aluminium ring surround it.

**The height works out, confirmed from the datasheet** (`docs/esp32-c3-mini-1_datasheet.pdf`,
Table 1 and the §7.1 side views):

| Variant | Dimensions | Antenna |
|---|---|---|
| ESP32-C3-MINI-1 | 13.2 × 16.6 × **2.4** mm | PCB antenna |
| **ESP32-C3-MINI-1U** | 13.2 × **12.5** × **2.4** mm | external connector |

Both side views state **2.4 ± 0.15 mm**, so the connector is inside the module
envelope — Espressif use a low-profile third-generation antenna connector, not a
standard full-height u.FL. Against §6.3's 4.125 mm gap that leaves **1.725 mm**,
unchanged from the PCB-antenna part. The `-1U` is also **4.1 mm shorter** (12.5 vs
16.6), since the antenna keep-out area is gone — free board area on a small card.

**What is still unverified: the MATED height.** The datasheet's 2.4 mm is the
module alone. A plug pushed onto that connector, plus the coax bend radius, sits
above it, and 1.725 mm is not much to give away. Two mitigations, both cheap to
adopt now:

- **Place the module so its connector end faces the board edge**, so a plug can
  overhang the stack outline rather than fight the Ft+ above it.
- **1.13 mm coax routes laterally within the gap without trouble** — it is the
  vertical mating stack-up, not the cable, that is at risk.

Measure a mated plug before committing the placement. If it does not clear,
soldering the coax directly to the RF pads removes the connector height entirely at
the cost of serviceability.

### 6.5 Mechanical
A true pass-through element card — DF40 top *and* bottom — to preserve the stack,
exactly as the GPIO board it replaces. The 5.5 mm it occupies is already carried in
the 24.5 mm stack height and the lens box skirt
(`LauPythonCamera_Pt_Stack/mech/gen_lens_box.py --pcb-t 7.1`).

## 7. Source documents (`docs/`)

Downloaded 2026-09-02. **None of these are committed** -- see `docs/.gitignore`.
They are vendor documents, they total ~135 MB, and every number in this README is
sourced to a named sheet, page or table below, so the analysis stands without them.
Fetch them again with the URLs here.

| File | Source |
|---|---|
| `Alchitry_Platinum_RevA_schematic.pdf` | `cdn.alchitry.com/docs/Pt-V2/Alchitry Platinum Rev A.pdf` |
| `Alchitry_Platinum_v2.step` | `cdn.alchitry.com/docs/Pt-V2/Alchitry Platinum v2.step` |
| `Pt_V2_pinout_trace_lengths.csv` | "Pinout and Trace Lengths", linked from the Pt V2 product page |
| `FtPlusSchematic.pdf` | `cdn.alchitry.com/docs/Ft-V2/FtPlusSchematic.pdf` |
| `FtPlus.step` | `cdn.alchitry.com/docs/Ft-V2/FtPlus.step` |
| `HdSchematic.pdf` | `cdn.alchitry.com/docs/Hd-V2/HdSchematic.pdf` |
| `Hd.step` | `cdn.alchitry.com/docs/Hd-V2/Hd.step` |
| `esp32-c3-mini-1_datasheet.pdf` | Espressif, ESP32-C3-MINI-1 / 1U datasheet |

Sheet map: **3** TOP CONNECTORS · **4** BOTTOM CONNECTORS · **5** ARTIX DECOUPLING ·
**6** LINEAR REGULATORS · **8** FTDI PROGRAMMING.

**Use Rev A, not "First Batch."** A separate `Alchitry Platinum Rev A - First
Batch.pdf` sits on the same CDN path; it is the pre-production drawing and reports
7,880 pages against Rev A's 9.

## 8. Status

| | |
|---|---|
| Schematic | not started |
| Layout | not started |
| Firmware | not started |
| Electrical unknowns | **none** — J3 mapped, 3.3 V confirmed, FTDI isolation confirmed |
| Module | **ESP32-C3-MINI-1U** — external wired antenna, top face, 2.4 mm in a 4.125 mm gap |
| Antenna | external, routed outside the aluminium shroud (§6.4) |
| SPI to fabric | 4 spare I/O pairs on J3 29-36, unused today; ~10 MB/s single-bit (§3b) |
| GPIO budget | **15 on the C3-MINI-1 — at the limit.** Sensors go to the FPGA, not the ESP32 |
| USB | **no connector** — USB_D+/D-, EN, GPIO9, GND, +3V3 as edge pads; OTA after (§6.0) |
| Next | confirm Hd+ top-side heights for the bottom face, then schematic |

Out of scope for now: using the daisy-chained HDMI as a data fabric between
cameras. It is a real capability and the pixels-as-data mechanism is sound, but it
is a separate piece of work from getting a bitstream into the part.

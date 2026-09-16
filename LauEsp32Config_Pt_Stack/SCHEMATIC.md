# ESP32 element card — schematic and JLCPCB parts list

_Started 2026-09-16. **Schematic only: no layout, nothing ordered, nothing built.**
The schematic, BOM and parts table all come out of one script, `gen_schematic.py`.
**Edit the script, not the `.kicad_sch`.** Running it again overwrites the sheet._

| File | What it is |
|---|---|
| `gen_schematic.py` | parts list + netlist → `LauEsp32Config_Pt_Stack.kicad_sch`, `production/bom*.csv` |
| `check_jlc_parts.py` | checks every LCSC code against JLCPCB's live library: code exists, **MPN matches**, stock covers N boards |
| `LauEsp32.pretty/` | DF40 **plug** footprints, copied unchanged from `LauPythonCamera_Pt_Stack/LauCamera.pretty` (geometry already fabricated), plus four KiCad-stock footprints re-homed only to carry verified 3D-model transforms (§10) |
| `3dmodels/` | STEP models for the eight connector/module footprints (§10) |
| `check_model_alignment.py` | renders each footprint with and without its model and checks size, centring and seating against the datasheet |
| `production/bom.csv` | JLCPCB assembly BOM — 16 lines, 46 placements |
| `production/bom_full.csv` | the same parts plus MPN, quantity and the DNP rev B straps |

```powershell
python gen_schematic.py                          # regenerate sheet + BOMs
python check_jlc_parts.py production/bom_full.csv --boards 5
& "$env:LOCALAPPDATA\Programs\KiCad\10.0\bin\kicad-cli.exe" sch erc LauEsp32Config_Pt_Stack.kicad_sch
```

**Verified 2026-09-16:** ERC **0 violations** at all severities. Netlist audit: **no
single-pin nets**, and all 104 Bank A/B pass-through nets are exactly plug pin *n* ↔
receptacle pin *n*. The JLCPCB check passed **16 of 16 lines** for 5 boards.

---

## 1. Decisions this sheet makes

| Decision | Choice | Source |
|---|---|---|
| Module | **ESP32-S3-MINI-1U-N8** (8 MB flash, no PSRAM) | closes `SD_BITSTREAM_LIBRARY.md` §8 "DECISION PENDING" |
| Bitstream library | microSD, SDMMC **4-bit** | `SD_BITSTREAM_LIBRARY.md` §2 |
| Pt revision | **rev A wired, rev B strappable** (§4) | `README.md` §3 warning |
| Stack role | **true pass-through**: DF40 plugs on bottom, 4.0 mm receptacles on top | `README.md` §6.5 |
| JTAG | behind a **74LVC125 buffer, off by default** | `README.md` §4.2 said "robustness choice" — taken |
| PROGRAM_B / Reset | **open-drain N-FETs with gate pull-downs** | new, §5 |
| USB | **no receptacle**: pads only | `README.md` §6.0 |

**Why N8 and not N4R2 (PSRAM):** the N4R2-1U had **6 units** at LCSC on 2026-09-16, while the
N8 had 2,207. `SD_BITSTREAM_LIBRARY.md` §6.3 already keeps bitstreams out of JSON, which is the
only thing that needed PSRAM. On an N8, GPIO26 is free as well.

## 2. The DF40s — pass-through, and which parts

The Pt V2 sheet 3 lists its top-side sockets as `DF40C-80DS-0.4V4.0` / `DF40C-50DS-0.4V4.0`.
Hirose sells those as **`DF40HC(4.0)-xxDS-0.4V(51)`**: the 4.0 mm stacking receptacle,
which mates the same `DF40C-xxDP-0.4V` plug every board in this stack already uses.

| Ref | Part | LCSC | Side | Carries |
|---|---|---|---|---|
| J1 / J4 | DF40C-80DP / DF40HC(4.0)-80DS | C294544 / C5254351 | bottom / top | Bank A, straight through |
| J2 / J5 | DF40C-80DP / DF40HC(4.0)-80DS | C294544 / C5254351 | bottom / top | Bank B, straight through |
| J3 / J6 | DF40C-50DP / DF40HC(4.0)-50DS | C424645 / C5338326 | bottom / top | Control, straight through **plus the taps below** |

The 80-pin GND pattern (pins ≡ 1, 2 mod 6) was checked against the camera board's netlist. That
board already mates the Pt V2 with the same connectors.

> **Layout gates these parts must pass:**
> - **Receptacle footprints are KiCad 10 stock** (`Connector_Hirose_DF40`), never fabricated.
>   Check them against Hirose's DF40 drawing before layout. The plug footprints are the proven
>   ones.
> - **Pin-1 mirroring on the bottom plugs.** `LauPythonCamera_Pt_Stack/README.md` §9 has the
>   check that caught this once.
> - **The top receptacles must land exactly on the plug XY positions**, or the board above will
>   not seat.
> - **LCSC stock on C5254351 is only 1,261** (2 per board). Re-run the check at order time.

## 3. J3 Control — what the card taps

| J3 pin | Net | To |
|---|---|---|
| 1–15 odd | `+3V3` | card power (Pt's 4 A rail) |
| 2–16 even | `RAW` | **pass-through only**, not used |
| 29 / 31 / 34 / 36 | `FAB_SCK` / `FAB_MOSI` / `FAB_MISO` / `FAB_CS` | U1 FSPI IO_MUX pins, 33 Ω series |
| 30 / 32 | `FAB_IO2` / `FAB_IO3` | FSPIWP / FSPIHD → **quad SPI available** |
| 33 / 35 | `FAB_IRQ` / `FAB_SPARE` | GPIO8 / GPIO21 |
| 37 | `FPGA_RESET` | Q2 drain |
| 39 | `FPGA_DONE` | 1 kΩ → GPIO16 (read only) |
| 41 | `FPGA_PROGRAM_B` | Q1 drain |
| 43 / 45 / 47 / 49 | `J3_P43`…`J3_P49` | **JTAG straps, §4** |
| 38 / 40 / 42 / 44 / 46 / 48 / 50 | `VBSEL_A/B`, `A1V8`, `AVP`, `AVN`, `AREF`, `AGND` | **pass-through only**. `AGND` is kept separate from `GND` on purpose |

> The `README.md` §3b allocation put a PDM mic on pins 30/32. That was because the C3 had no
> spare pins. The S3 has enough, so all eight go to the ESP32 and quad SPI fits. There is **no
> mic on this sheet.** If one is wanted, wire it to the fabric side as §3b argues.
>
> **Still unconfirmed (from `README.md` §3b.1):** bank 14 and bank 34 VCCO. The project's own
> finding is that bank 13 is the only 2.5 V bank. Confirm against Pt sheet 6 before firmware
> drives these pins.

## 4. JTAG straps: rev A as built, rev B by moving four resistors

Both orders were **read off the rendered schematics, not from the PDF text layer**. Alchitry's
Altium export shifts labels by a row in `pdftotext`.

| J3 pin | **Pt V2 rev A** (Pt sheet 3) | **Rev B = Au V2 order** (`AuSchematic.pdf` sheet 1) |
|---|---|---|
| 43 | TDI | TMS |
| 45 | TDO | TCK |
| 47 | TMS | TDI |
| 49 | TCK | TDO |

Reset (37), DONE (39) and PROGRAM_B (41) are **the same on both**. Alchitry's forum says
only that "the Pt will match the Au." That matches this table, but **rev B hardware has
not been seen**.

| Signal | Rev A position (populated) | Rev B position (DNP) |
|---|---|---|
| TCK | R10 → pin 49 | R14 → pin 45 |
| TMS | R11 → pin 47 | R15 → pin 43 |
| TDI | R12 → pin 43 | R16 → pin 47 |
| TDO | R13 ← pin 45 | R17 ← pin 49 |

All eight are 33 Ω, so each populated strap also acts as the source termination. **Never fit
both sets.** Every J3 JTAG pin sits on one rev A strap and one rev B strap, each tied to a
different signal, so fitting both shorts two JTAG lines together. The DNP set is in
`bom_full.csv` and left out of `bom.csv`.

## 5. Safety of the FPGA against the ESP32

`README.md` §2's promise is that a bad push costs a reboot and nothing more. The ESP32 must not
break that promise while it boots, crashes or sits unprogrammed:

- **JTAG buffer U2 (SN74LVC125A), /OE pulled HIGH by R2.** The ESP32 is electrically off the
  JTAG bus until firmware drives GPIO15 low. With no firmware, or during boot, the Pt's own
  USB/FT2232H path owns JTAG. R3 holds `ESP_TDO` low while the buffer is off.
- **PROGRAM_B and Reset go through 2N7002s**, with 10 kΩ gate pull-downs (R4, R5). A floating
  or glitching GPIO cannot pull either line low. A PROGRAM_B glitch would wipe the running
  image, and neither the camera nor anyone watching it would know why. The Pt's own pull-ups
  (`README.md` §4.1) give the high level.
- **DONE is input only**, through a 1 kΩ resistor.

## 6. ESP32-S3 pin map

Cross-checked three ways: KiCad's `RF_Module:ESP32-S3-MINI-1U` symbol, Figure 3-1 of the
Espressif datasheet (v1.7), and the module's pin numbers.

| GPIO | Module pin | Net | Why this pin |
|---|---|---|---|
| 0 | 4 | `ESP_BOOT` → TP6 | strap: low at reset = download mode |
| 4 / 5 / 6 / 7 | 8 / 9 / 10 / 11 | `ESP_TCK` / `TDI` / `TDO` / `TMS` | JTAG via SPI3 through the matrix; 10 MHz is enough (`README.md` §5) |
| 8 | 12 | `ESP_FAB_IRQ` | |
| 9 / 10 / 11 / 12 / 13 / 14 | 13–18 | `FAB_IO3` / `CS` / `MOSI` / `SCK` / `MISO` / `IO2` | **FSPI IO_MUX pins**: 80 MHz without the matrix |
| 15 | 19 | `JTAG_OE_N` | |
| 16 | 20 | `ESP_DONE` | |
| 17 / 18 | 21 / 22 | `PROG_EN` / `RESET_EN` | FET gates |
| 19 / 20 | 23 / 24 | `USB_DN` / `USB_DP` → TP4 / TP3 | native USB Serial/JTAG |
| 21 | 25 | `ESP_FAB_SPARE` | |
| 33 / 34 / 35 / 36 / 37 / 38 | 28 / 29 / 31 / 32 / 33 / 34 | `SD_D2` / `D3` / `CMD` / `CLK` / `D0` / `D1` | SDMMC through the matrix |
| 47 | 27 | `SD_CD_N` | |
| 48 | 30 | `LED_STATUS` | |
| 43 / 44 | 39 / 40 | `ESP_TXD0` / `ESP_RXD0` → TP7 / TP8 | fallback bootloader UART |
| 3, 45, 46 | 7, 41, 44 | NC | **strapping pins, left at their defaults on purpose** |
| 1, 2, 26, 39–42 | | NC | spare |

**Footprint:** KiCad's S3 symbol points at `RF_Module:ESP32-S2-MINI-1U`. The S2 and S3 MINI-1U
share one land pattern, per the S3 datasheet §11.2. **Check this against Figure 11-2 at layout.**

## 7. microSD

**Molex 104031-0811** (C585350): **1.42 mm** tall, the lowest stocked socket found. The
DM3AT is 1.68 mm and the TF-01A is 1.85 mm. It goes on the **top face**
(`SD_BITSTREAM_LIBRARY.md` §3). The KiCad footprint was checked against Molex drawing
SD-104031-001:
pads 9/10 are the detect switch, **open without a card and closed with one**. Pad 9 goes to GND
and pad 10 to `SD_CD_N`, pulled up by R36, so **low = card present**. `CMD` and `DAT0–3` have
10 kΩ pull-ups (R31–R35), and `CLK` has 33 Ω series (R30). Local decoupling is C7 + C8.

## 8. JLCPCB parts — every assembled part is a JLCPCB part

Live check on 2026-09-16 (`check_jlc_parts.py`, 5 boards):

| LCSC | Part | Refs | Lib | Stock |
|---|---|---|---|---|
| C2980299 | ESP32-S3-MINI-1U-N8 | U1 | Extended | 2,207 |
| C7813 | SN74LVC125APWR | U2 | Extended | 41,203 |
| C585350 | Molex 1040310811 microSD | J7 | Extended | 11,575 |
| C294544 | DF40C-80DP-0.4V(51) | J1, J2 | Extended | 29,925 |
| C424645 | DF40C-50DP-0.4V(51) | J3 | Extended | 4,078 |
| C5254351 | DF40HC(4.0)-80DS-0.4V(51) | J4, J5 | Extended | **1,261** |
| C5338326 | DF40HC(4.0)-50DS-0.4V(51) | J6 | Extended | 8,103 |
| C8545 | 2N7002 | Q1, Q2 | Basic | 1.8 M |
| C2286 | KT-0603R red LED | D1 | Basic | 3.5 M |
| C25744 | 10 kΩ 0402 | 11 | Basic | 27 M |
| C25105 | 33 Ω 0402 | 13 (+4 DNP) | Basic | 2.0 M |
| C11702 | 1 kΩ 0402 | R6, R7 | Basic | 11.6 M |
| C1525 | 100 nF 0402 | C4, C6, C8 | Basic | 30.8 M |
| C52923 | 1 µF 0402 | C5 | Basic | 9.0 M |
| C15850 | 10 µF 0805 | C3, C7 | Basic | 6.6 M |
| C45783 | 22 µF 0805 | C1, C2 | Basic | 4.7 M |

**7 Extended lines**, each carrying a feeder fee. Every one of them is a function-defining part
(the module, buffer, socket and connectors), so no Basic substitute exists. All passives, the
FETs and the LED are Basic.

> **The check caught a real error on its first run.** The LED was first entered from memory as
> `C72043, KT-0603G`. That code is actually a *different* Everlight part with **1 unit** in
> stock. A green 0603 at JLCPCB is Extended only (C12624), so the LED is now a **red KT-0603R**,
> a Basic part. This is `LauPythonCamera_Pt_Stack` §15.3's lesson again: **the order-time check
> is what catches it.** Run it before every order.

**Not in the BOM, by design:**
- **TP1–TP8**, the pad field: bare copper, no part.
- **The antenna.** U1-1U has a connector and no antenna. A 2.4 GHz antenna with a mating pigtail
  is bought separately. **Measure the mated plug height** before placement (`README.md` §6.4:
  1.725 mm of clearance).
- **The DNP rev B straps** R14–R17.

## 10. 3D models — sources, and the alignment check

KiCad 10 installs no 3D model for four of the parts, and its online 3D library doesn't have
them either. Those four come from **LCSC/EasyEDA**, keyed by the same LCSC code the BOM orders.
The DF40 plugs reuse the camera board's models.

| Footprint (`LauEsp32:`) | Model (`3dmodels/`) | Source | Correction |
|---|---|---|---|
| `Hirose_DF40HC(4.0)-80DS-…` | `DF40HC(4.0)-80DS-0.4V(51)_C5254351.step` | EasyEDA | **Z +2.5 mm** |
| `Hirose_DF40HC(4.0)-50DS-…` | `DF40HC(4.0)-50DS-0.4V(51)_C5338326.step` | EasyEDA | **Z +2.5 mm** |
| `ESP32-S2-MINI-1U` | `ESP32-S3-MINI-1U-N8_C2980299.step` | EasyEDA | none |
| `microSD_HC_Molex_104031-0811` | `Molex_1040310811_C585350.step` | EasyEDA | **rotate 180°** |
| `Hirose_DF40C-80DP-…` / `-50DP-…` | `DF40C-80DP.step` / `DF40C-50DP.step` | camera board | none |

**Stripping out the model blocks, the four re-homed footprints are byte-identical to KiCad
stock**: pads, silkscreen and courtyard are unchanged. They live in `LauEsp32.pretty` only so the
corrected model transform travels with the project.

**What was wrong, and how it was found.** Each footprint was rendered alone
(`kicad-cli pcb render`, orthographic) with and without its model, and the model was measured
from the pixels that changed:

- **Both DF40HC(4.0) receptacles were sunk 2.50 mm into the board.** They were correct in XY,
  but ran Z −2.50 … +1.39. The body sizes matched Hirose (18.6 / 12.6 mm long, 3.9 mm tall), so
  only the origin was wrong. In a stack check this model would show the Ft+ with 2.5 mm more
  clearance than it really has.
- **The microSD model was turned 180°.** Its bounding box was centred, so no size check could
  catch it. The top view showed the eight solder tails over the shield pads, and the
  detect-switch features on the side away from pads 9/10. After rotating, the tails sit on pads
  1–8 and the switch sits over 9/10.
- **The ESP32-S3-MINI-1U model was correct.** It's 15.4 mm square and 2.40 mm tall, with the
  antenna connector and shield cutout in the pin-1 corner as datasheet Fig. 10-2 shows.

**The footprint was checked as well,** against Fig. 11-2: 60 × 0.4 × 0.8 mm pads on a
0.85 mm pitch, rows 14 mm apart spanning 11.9 mm, 4 × 0.8 × 0.8 mm corner pads, and a 3×3
thermal pad 4.5 mm across. Pin 1 is top-left, numbered counter-clockwise, the same as Fig. 3-1.
**The S2 land pattern is the S3's.**

**Result on the final footprints** (`python check_model_alignment.py`, 2026-09-16):

| Footprint | Side | Length | Height | Seat | Reference |
|---|---|---|---|---|---|
| DF40HC(4.0)-80DS | top | 18.60 | 3.88 | −0.01 | Hirose A = 18.6, D = 3.9 |
| DF40HC(4.0)-50DS | top | 12.60 | 3.89 | −0.01 | Hirose A = 12.6, D = 3.9 |
| ESP32-S3-MINI-1U | top | 15.41 | 2.40 | −0.03 | Espressif 15.4, 2.4 ± 0.15 |
| Molex 104031-0811 | top | 11.95 | 1.40 | −0.06 | Molex 11.95, 1.42 |
| DF40C-80DP | bottom | 17.52 | 1.08 | −0.05 | Hirose A = 17.52 |
| DF40C-50DP | bottom | 11.52 | 1.08 | −0.04 | Hirose A = 11.52 |

All within 0.1 mm. As a consistency check, a 1.08 mm plug plus a 3.88 mm receptacle is
4.96 mm, against the 4.0 mm mated height. That leaves ~0.96 mm of engagement, which is plausible.

> **The script cannot see orientation.** It checks size, centring and seating. A model turned
> 180° passes all three, as the microSD shows. After replacing any model, look at the top view
> as well: `--keep DIR` saves the renders.

## 11. Board seed — outline and DF40s (`gen_pcb.py`, 2026-09-16)

`gen_pcb.py` wrote the starting `LauEsp32Config_Pt_Stack.kicad_pcb`. **It is a one-time seed:**
it refuses to overwrite an existing board without `--force`, because once layout starts the
`.kicad_pcb` is the source of truth.

| Item | Value | Source |
|---|---|---|
| Outline | 55 × 45 mm, 1.5 mm chamfers, right-edge notch to x = 49.5 over y 8–37 | copied from `LauPythonCamera_Pt_Stack.kicad_pcb` |
| Mount holes | 4 × Ø2.2 mm at (2.5, 2.5), (52.5, 2.5), (2.5, 42.5), (52.5, 42.5) | same |
| Origin | board corner at (100, 60), so coordinates match the camera board's | |
| Stackup | JLC04161H-7628, 4-layer, 1.6 mm, ENIG | camera board |
| J3 / J6 Control | (16.5, 4.0), plug on B.Cu / receptacle on F.Cu | element standard, camera README §8 |
| J1 / J4 Bank A | (38.0, 4.0), same | same |
| J2 / J5 Bank B | (38.0, 41.0), same | same |

**The geometry was cross-checked, not assumed.** The camera board's outline, holes and
connector positions match the fabricated, working `LauCameraTrigger_Alchitry_Stack` to
0.001 mm once its origin shift is removed. The plug footprints are pad-for-pad identical to
the camera board's.

**Pass-through checked from pad data.** With the receptacles at rotation 0, every top pin *n*
lands directly above bottom pin *n* (0.000 mm in X, same row side) for all 210 pins. The rows
differ by 0.185 mm in Y because the receptacle land pattern is wider (±1.54 vs ±1.355). That
is not a pin swap. At 180° the pins would be 10–16 mm apart. `gen_pcb.py` repeats this check
and refuses to write on a mismatch.

**Nets and links.** Pad nets come from `kicad-cli`'s netlist export of the schematic. Each
footprint carries its schematic symbol's UUID, so KiCad's "Update PCB from Schematic" matches
these footprints instead of adding duplicates.

> ### ⚠ BUG, found and fixed 2026-09-16: the bottom plugs were on the WRONG NETS
>
> The DF40 plug footprints copied from the camera board still carried **that board's pad
> nets** (`unconnected-(J1-Pin_29-Pad29)` and so on). `gen_pcb.py` added the schematic net
> next to the stale one, and **the stale one won**. So **207 pads on J1/J2/J3** sat on wrong
> nets from the seed onward, and every placement step since inherited them.
>
> Earlier checks missed it because they counted *pads that have a net*, which a wrong net
> passes. That makes the "420 pads with nets" above, and the "163 / 310 unconnected" counts in
> §11–§14, **wrong**.
>
> **Fixed:**
> - The stale nets are stripped from both library footprints.
> - `gen_pcb.py` and `place_components.py` now drop any net a library footprint carries.
> - The board was repaired in place (positions untouched).
> - **`check_board_nets.py`** now compares every pad's net **name** against the schematic
>   netlist, and exits non-zero on any mismatch. **Result: 605 pads, 0 wrong.**
>
> With the pass-through connections real, the unrouted count is **438**.

**DRC: 0 violations** (the unconnected counts in §11–§14 are superseded by the correction above). Getting
to 0 needed one rule, in `LauEsp32Config_Pt_Stack.kicad_dru`. The DF40 plug pads are 0.23 mm on
a 0.4 mm pitch, which leaves a 0.17 mm gap, below the 0.2 mm default. The rule allows 0.1 mm
**only between pads of the same connector**. This is the camera board's rule, and the fabricated
trigger board uses this exact land pattern. Board Setup rules are the camera project's; its
LVDS net classes were dropped.

> **Routing ahead is the hard part.** Each of the 210 pass-through nets has to get from a
> bottom pad to the top pad directly above it, inside a 0.4 mm pitch field. Via strategy (in-pad
> or dog-bone) and layer count need settling before anything else is routed.

## 12. Initial placement (`place_components.py`, 2026-09-16)

`place_components.py` adds every schematic part **not already on the board**, so re-running it
never moves or duplicates anything placed by hand. Pads get their nets from the netlist, and
each footprint is linked to its symbol UUID, the same as `gen_pcb.py`. **58 footprints in all.
DRC: 0 violations**, 310 unconnected (the unrouted ratsnest).

**All 52 new parts are on the TOP face.** The underside faces the Hd+, whose component heights
have never been measured (`README.md` §6.3), so only the DF40 plugs are on B.Cu.

| Group | Where (mm from board corner) | Why |
|---|---|---|
| U1 ESP32-S3-MINI-1U | (40, 23), **rotated 180°** | puts the antenna-connector corner at (47, 30), beside the notch, so the coax can leave through it (`README.md` §6.4) |
| C1–C4 bulk/decoupling | row at y = 33.5, x 36–46 | beside the module; the 3V3 pin is on its right column |
| R1 / C5 EN RC | (30.3, 26 / 29) | next to EN (module pin 45), now on the left column |
| J7 microSD | (7.5, 23), **rotated 90°** | slot opens toward the **left board edge**, so a card can be inserted on the bench |
| R30–R36, C7, C8 | row at y = 13, x 3–14 | SD pull-ups and decoupling, between the socket and J6 |
| R20–R27 fabric-SPI series | row at y = 8.6, x 9.5–18.6 | under J3/J6 pins 29–36 |
| R10–R17 JTAG straps | row at y = 8.6, x 19.9–29.0 | under J3/J6 pins 43–49. Rev A R10–R13, rev B R14–R17 (DNP) |
| U2 JTAG buffer, C6, R2, R3 | around (24, 13) | between the straps and the module |
| Q1/Q2, R4–R7, D1 | y 9.8–13.3, x 31.5–39.5 | PROGRAM_B/Reset FETs by J3 pins 37/41, status LED |
| TP1–TP8 pad field | y = 36, x 3–20.8, 2.54 mm pitch | lower-left open area |

**Silkscreen:** designators for the 0402/0805 passives, the LED, U2, Q1/Q2, J7 and the test
pads sit on **F.Fab**, not the silkscreen. At 1.3 mm pitch the printed labels overlapped each
other and the pads (104 DRC warnings). They still show in KiCad and on the assembly drawing.

> **Assumptions to confirm before routing:**
> - **Which edge is the enclosure's open face.** The pad field (`README.md` §6.0) is meant to
>   sit there for a pogo fixture. Mid-stack, both faces are covered by the neighbouring boards,
>   so the pads are only reachable at an edge or with the stack apart.
> - **The antenna exit through the notch**, and the mated height of the coax plug
>   (`README.md` §6.4). Measure a plug.
> - **The microSD insertion edge** (left). The slot is on the tail side, per Molex drawing
>   SD-104031-001.
> - **Decoupling distance.** C1–C4 are ~6 mm from the module's 3V3 pin. Tighten when routing.

## 13. Spring (force-directed) placement (`spring_place.py`, 2026-09-16)

This replaces §12's hand floorplan for everything except **U1, J7 and the six DF40s**, which
stay fixed. Run as
`python spring_place.py --iters 1500 --floor 0.2`, starting from the §12 placement.
**DRC: 0 violations.**

**The model:** springs on every signal net. Soft repulsion spreads the parts, annealing to a
**floor** rather than to zero. Hard overlap and boundary constraints apply throughout, with
keep-outs for the notch and its chamfers, the four stand-offs, and the antenna-cable exit.
After the run come a legalise pass, a best-of-four rotation pass and a 0.05 mm grid snap.
TP1–TP8 move as one rigid strip. Where the push-apart stalls, a spiral search moves the part to
the nearest free spot.

**Three findings from tuning, each written into the script:**

1. **GND and +3V3 get no springs.** Every part touches them, so their springs would collapse
   the board into one clump. Decoupling caps get a stiff spring to the one pin they serve
   instead.
2. **With spreading annealed to zero, the parts clustered anyway** around U2 once the spreading
   faded. The floor sets how much of the board gets covered, and it costs wire length (signal
   HPWL, power excluded):

   | floor | HPWL (from 1258 mm) |
   |---|---|
   | 0.2 | 1300 |
   | 0.35 | 1370 |
   | 0.6 | 1544, with parts pinned against the board edges |

3. **Parts with a power pin must not be spread.** A pull-up, pull-down or RC has just one weak
   signal spring, and spreading won: R2/R3 ended ~30 mm from U2 and R1/C5 ~30 mm from EN.
   Exempting the 23 such parts from spreading brought them back: R2/R3 4–5 mm from U2, R5
   2.9 mm from Q2, R7 2.3 mm from D1. HPWL fell to **1266 mm**, essentially the hand
   floorplan's, while the rest still covers the board.

**Known weak spots to fix by hand while routing:**
- **R14/R15, the rev B JTAG straps (DNP),** drifted to the top-right corner, ~30 mm from J3
  pins 43/45. That's harmless while unfitted, but a long route if rev B is ever built.
- **R11 (rev A TMS strap)** sits at x ≈ 42, well right of J3's pins.
- **C1–C4** are still ~6–12 mm from U1's 3V3 pin. That pin faces the notch, and the antenna
  keep-out leaves no room beside it.

## 14. 0402 resistors on the bottom face (`move_resistors_bottom.py`, 2026-09-16)

**All 30 0402 resistors are now on B.Cu:** R1–R7, R10–R17, R20–R27, R30–R36. Capacitors,
U1, U2, Q1/Q2, D1, J7 and the pad field stay on top.

**Why it's safe:** the bottom face looks down on the Hd's top side. Outside the HDMI area
there's at least 2.67 mm of clearance (README §6.3), and an 0402 is ~0.35 mm tall.
`add_hdmi_keepout.py`'s B.Cu no-footprint area keeps parts off the HDMI connectors, and DRC
enforces it.

**How:** the script runs under KiCad's bundled Python and calls `FOOTPRINT.Flip`, KiCad's
default left/right flip about each part's own position. That way pads, mask, paste,
courtyard and text all mirror together. Checked afterwards: the flipped pads are on
`B.Cu`/`B.Mask`/`B.Paste`, nets are intact (310 unconnected before and after), and the
designators went to `B.Fab`.

**Legalisation moved the fewest parts possible.** 15 resistors stayed exactly where they were
and only flipped. The other 15 overlapped a DF40 plug or each other on the bottom, and moved
to the nearest legal spot: R23 1.22 mm, R27 0.51 mm, and everything else ≤ 0.30 mm.
**DRC: 0 violations.**

> Assembly is unchanged in kind. The bottom side was already populated by the DF40 plugs
> (§2), so JLCPCB was assembling both sides anyway. The CPL will now carry 33 bottom-side
> placements instead of 3.

## 15. Resistors placed by function (`place_resistors_near_pins.py`, 2026-09-16)

**The first version pulled every resistor toward the connector pins (×3). That was wrong.**
A 33 Ω series resistor is a source termination, and it only works within a few mm of the pin
**driving** the line. With ~1 ns edges at ~6–7 ps/mm, a few mm is electrically one point. The
connector end is the far end. That version also crowded 17 resistors around J3 for nothing.

**Now each resistor has an explicit role and anchor** (`SPEC` in the script):

| Role | Resistors | Anchored to | Weight |
|---|---|---|---|
| Source termination, ESP32 drives | R20 SCK, R21 MOSI, R23 CS, R24 IO2, R25 IO3, R30 SD_CLK | the U1 GPIO pin | 5 |
| Source termination, U2 drives | R10 TCK, R11 TMS, R12 TDI (rev A straps) | U2 output pin | 5 |
| Rev B straps (DNP) | R14–R17 | their rev A partner (5), U2 (1) | |
| TDO strap | R13 | U2 (FPGA drives TDO) | 1 |
| Gate pull-downs | R4, R5 | Q1 / Q2 gate | 1 |
| FPGA-driven series | R22 MISO, R26 IRQ, R27 SPARE | loosely, both ends: no spot on this card terminates them | 0.3 |
| DC pull-ups/downs, EN RC, DONE series, LED | R1, R2, R3, R6, R7, R31–R36 | loosely: position is electrically irrelevant | 0.3 |

Result: every termination sits within **1.3 mm** of its driver pin, most within **0.05 mm**,
i.e. directly under it on the other face, one via away. Before this change they were 5.5–31 mm
away. **The four rev A/rev B strap pairs sit 1.30 mm apart**, clustered at U2. The
loosely-weighted parts stay out of the way (DONE series, MISO/IRQ/SPARE 11–16 mm from U1,
roughly midway to J3; SD pull-ups 0.03–7.9 mm from J7).
**Nets: `check_board_nets.py` 0 wrong. DRC: 0 violations.**

## 16. Capacitors next to their power pins (`place_caps.py`, 2026-09-16)

**The review found the caps 6–15 mm from their pins after the spring placement:** C4, the
ESP32's 100 nF, at 8.1 mm, and C8, the microSD's 100 nF, at 12.8 mm. On top there was no room.
U1's 3V3 pin (module pin 3) sits beside the notch, where the antenna-cable keep-out is, and
J7's VDD pad is under the socket body at the left edge.

**The fix used the bottom face.** It's measured clear to the Hd (README §6.3). Each cap goes to
the legal spot minimising `w · dist(+3V3 pad, power pin) + w' · dist(GND pad, nearest GND pad
of that part)`, in priority order (100 nF first).

| Cap | Serves | Side | +3V3 pad to power pin, before → after | Air gap to the Hd below |
|---|---|---|---|---|
| **C4 100 nF** | U1 pin 3 | bottom | 8.13 → **0.03 mm** (directly under the pin) | 3.58 mm |
| C3 10 µF | U1 pin 3 | bottom | 6.05 → **1.90 mm** | 2.88 mm |
| C2 22 µF | U1 pin 3 | bottom | 7.96 → **1.87 mm** | 2.88 mm |
| C1 22 µF | U1 pin 3 | bottom | 8.55 → **2.55 mm** | 2.88 mm |
| **C8 100 nF** | J7 pad 4 | bottom | 12.79 → **5.92 mm** (GND pad 0.39 mm from J7's GND) | 3.58 mm |
| C7 10 µF | J7 pad 4 | top | 15.40 → **8.04 mm** (bulk) | — |
| C6 100 nF | U2 pin 14 | top | unchanged, 2.31 mm | — |
| C5 1 µF | EN RC (not decoupling) | top | unchanged | — |

**C8 can't get closer than ~5.9 mm without breaking a rule.** J7 pad 4 is inside the HDMI
no-footprint area on the bottom (x < 9.5, y 17.5–40) and under the socket body on top. C8 now
sits just outside that area. For that spot it was allowed to displace the SD pull-ups R31–R36,
which are DC and position-insensitive. `place_resistors_near_pins.py` was then re-run to
re-place them around the caps. **Always run the two scripts in that order.**

After both: every termination is still within 1.3 mm of its driver, the rev A/B pairs are
1.30 mm apart, **`check_board_nets.py` finds 0 wrong nets, and DRC shows 0 violations.**

## 17. Pad field on the edge opposite J6 (`move_testpads.py`, 2026-09-16)

**User decision: TP1–TP8 go on the board edge opposite J6.** J6 is on the top edge, so the
pads run along the bottom edge (y = 45), on the top face, where a side-entry pogo fixture can
reach them with the stack assembled. This closes the "which edge" question in §12.

| Pad | Net | x (mm) |
|---|---|---|
| TP1 | +3V3 | 7.94 |
| TP2 | GND | 10.48 |
| TP3 | USB_DP | 13.02 |
| TP4 | USB_DN | 15.56 |
| TP5 | ESP_EN | 18.10 |
| TP6 | ESP_BOOT (GPIO0) | 20.64 |
| TP7 | ESP_TXD0 | 23.18 |
| TP8 | ESP_RXD0 | 25.72 |

All pads are at **y = 43.40**, 1.6 mm from the edge, at 2.54 mm pitch. The strip is centred in
the free run between the lower-left stand-off keep-out (x ≤ 5.5) and J5 (x ≥ 28.2).
**1.6 mm, not 1.5:** the test-pad courtyard is 2.09 mm across, so at 1.5 mm it breaks the 0.5 mm
edge rule by 0.05 mm. The script caught that and saved nothing until it was fixed.

**Nets: 0 wrong. DRC: 0 violations.**

> **For the fixture design:** the Ft+ sits 4.125 mm above this face, so a fixture reaching in
> from the edge has about that much vertical room over the pads. The USB pair (TP3/TP4)
> now routes ~25 mm from U1 pins 23/24. That's fine for full-speed USB (12 Mbit/s), but keep
> it as a 90 Ω-ish pair and away from the J5 pass-through.

## 18. Routing step 1: rules, planes, DF40 pass-through fan-out (`route_df40_fanout.py`, 2026-09-16)

**Rules**, from JLCPCB's 4-layer capabilities page (checked 2026-09-16):

| | Value | Why |
|---|---|---|
| Via | 0.45 mm pad / 0.20 mm hole | Smallest via at standard price. JLC: "0.2 or 0.25 mm hole with via diameter less than 0.45 mm will cost more" |
| Tracks | 0.15 mm default, 0.30 mm Power netclass (+3V3, GND), 0.10 mm minimum | JLC minimum 0.10/0.10 |
| Hole to track | 0.20 mm | JLC "via hole to track 0.2 mm" |
| Via-in-pad | not used | filled/capped vias are an extra-cost process on 4-layer |

These are written to the `.kicad_pro` board rules and netclasses. The `.kicad_dru` allows 0.10 mm
clearance **only** inside the three named rule areas `DF40 fan-out J1/J2/J3`.

**Planes:** In1.Cu is GND (solid) and In2.Cu is +3V3. Both fill over ~2,100 mm² of the board.
Every GND and +3V3 connector pin reaches its plane through its own fan-out via, and F.Cu/B.Cu
stay free for signals. Nothing on this card is faster than 80 MHz SPI; the HDMI TMDS pairs only
pass through the via field.

**Fan-out:** each of the 210 DF40 pins gets one through via outboard of its pad row, in its own
column. Columns are 0.40 mm apart, narrower than a 0.45 mm via, so they alternate between two rows:

- **NEAR row, 2.05 mm from the connector centre.** Clears the neighbouring columns' pads
  (receptacle 0.20 × 0.70 mm at ±1.54; plug 0.23 × 0.66 mm at ±1.355) by at least 0.10 mm.
- **FAR row, 2.60 mm.** 0.65 mm centre-to-centre from the NEAR vias. A 0.10 mm track to a FAR via
  passes between two NEAR vias with 0.125 mm copper clearance and 0.25 mm hole clearance.

Each via connects plug pad (B.Cu) → via → receptacle pad (F.Cu).

**Result:** 210 vias, 420 fan-out tracks. `check_board_nets.py` finds 0 wrong nets, and DRC shows
**0 violations**. Unconnected items went 438 → **154**, because every pass-through and power
connection is now made. The 154 left are the logic nets.

> ### ⚠ Incident: the board file was truncated to 0 bytes, then restored
>
> The first run crashed inside `pcbnew.SaveBoard` (Windows access violation) and left
> `LauEsp32Config_Pt_Stack.kicad_pcb` empty. It was restored from the backup made just before
> the run (58 footprints, 0 wrong nets, DRC clean). No layout was lost.
>
> **Root cause:** `ZONE.SetOutline(poly)` keeps a pointer to a Python-created `SHAPE_POLY_SET`.
> Python freed that object when the helper function returned, so `SaveBoard` later read freed
> memory. The crash was intermittent: a staged test crashed at one stage and survived the next.
>
> **Fixes:**
> - Zone outlines are now built inside `zone.Outline()`. Never pass a temporary to `SetOutline`.
> - Every pcbnew-writing script should save through `safe_save()`: write a temp file, prove it
>   reloads with the same footprint count, then replace the board. `SaveBoard` also writes a
>   `.kicad_pro` beside the temp file, and the helper removes it.
>
> **Also: never run these scripts while KiCad has the project open.** A later save from the GUI
> silently overwrites the scripted changes.

## 19. Routing complete: scripted prep + Freerouting (2026-09-16)

**The board is fully routed.** On the real `.kicad_pcb`: **DRC 0 violations, 0 unconnected, 0
footprint errors; `check_board_nets.py` 0 wrong nets.**

| | Count |
|---|---|
| Vias | 364 (210 DF40 fan-out, 73 plane stitching, 81 autorouter) |
| Track segments | 964 |
| Locked (scripted) | 808 items: fan-out, plane vias, J3 escapes, RAW tie |
| Autorouted, unlocked | 556 |

### Pipeline (re-runnable, KiCad closed)

```powershell
$py = "$env:LOCALAPPDATA\Programs\KiCad\10.0\bin\python.exe"
& $py route_df40_fanout.py        # §18: rules, planes, fan-out
& $py lock_fanout.py
& $py route_prep.py               # plane vias, RAW tie, J3 escapes (all locked)
#   export DSN (pcbnew.ExportSpecctraDSN), then:
java -jar C:\Users\drhal\tools\freerouting\freerouting-2.4.1.jar -de prep.dsn -do prep.ses -mp 100 --gui.enabled=false
& $py import_routes.py prep.ses   # checks the locked items survive, refills planes, safe save
& $py check_board_nets.py; kicad-cli pcb drc ...
```

**Freerouting** v2.4.1 is the jar at `C:\Users\drhal\tools\freerouting\`. Its SHA-256 matches
GitHub's published digest. **Its telemetry, contact and analytics were ON by default** and are now
off in `%APPDATA%\freerouting\freerouting.json`, along with the GUI and its SMD fan-out stage
(which re-fanned the DF40 pins that already had vias). The first smoke test ran before this
change, so it probably sent anonymous usage events. The logs show no board upload.

### Why the scripted prep exists: what Freerouting could not do

On the fan-out board, Freerouting alone left **70 of 154** connections unrouted:
- **GND ×32 and +3V3 ×16:** it does not add plane vias.
- **RAW ×7:** no plane to tie the pins together.
- **J3 taps ×15:** it could not reach vias inside the dense fan-out field.

It also reports the locked fan-out as ~2,000 "violations", because the DSN format cannot carry
KiCad's area-scoped 0.10 mm rule. **Ignore its unrouted and violation counts; only KiCad's DRC
counts.**

`route_prep.py` does those three jobs deterministically. Every via and segment it adds is checked
with KiCad's own collision geometry against all other-net copper, JLC's 0.20 mm hole-to-track
rule, and the board edge.

**Bugs found on the way, each now written into the script:**
- **Closed outlines:** `SHAPE_LINE_CHAIN.Collide` counts *inside* a closed outline as a collision.
  Measure distance to the edge segments instead.
- **Flipped pads:** `pad.GetLayer()` says F.Cu for pads of a bottom-side (flipped) footprint.
  Use `IsOnLayer(B_Cu)`. Trusting it put 27 plane tracks on the wrong layer.
- **Escape legs, same x:** with both layers' legs at the same x, a leg's end sat over the other
  layer's leg, leaving no room for a via (FAB_IO2/CS unrouted). Legs are now staggered by layer.
- **Escape legs, too close:** at 0.30 mm apart within a layer, the inner leg's end still had no
  room for a via (FAB_IO3/CS unrouted). Legs are now 0.60 mm apart: F.Cu 23.35/23.95, B.Cu
  24.65/25.25.

### Signal quality notes

- **Source terminations:** every one is 1.7–2.3 mm of track from its ESP32 pin (§15).
- **Quad-SPI to the FPGA:** 41–47 mm, worst skew ~6 mm ≈ 40 ps, 0.3 % of the 12.5 ns period at
  80 MHz. SD_CLK is 33 mm.
- **USB D+/D−:** 47 / 42 mm, not coupled. Fine for full speed (12 Mbit/s). If high speed were ever
  wanted this would need a controlled 90 Ω pair, but the ESP32-S3's USB is full speed only.
- **Review before fab:** the autorouter uses diagonal segments and is not tuned for aesthetics.
  Worth a visual pass in KiCad for any obviously long detours, but the metrics above show none
  on the critical nets.

## 9. What is not done

| | |
|---|---|
| Layout | **placed and fully routed** (§11–§19): DRC 0 violations, 0 unconnected, 0 wrong nets. Still to do: visual review in KiCad, silkscreen tidy, fab outputs |
| CPL | none until layout exists. Use the JLC-format transform in `LauPythonCamera_Pt_Stack/README.md` §15.6, **not** raw `kicad-cli pcb export pos` |
| Bank 14/34 VCCO | unconfirmed (§3) |
| ESD on the pad field | none; the pads are reached only by a pogo fixture on the bench |
| Rev B hardware | unseen. The straps cover the published Au order and nothing else |

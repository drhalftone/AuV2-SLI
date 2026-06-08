# Camera + USB3 expansion plan — Alchitry Pt V2 platform

> Planning doc (drafted 2026-06-04). Captures the hardware selection and the
> feasibility/architecture analysis for moving the HDMI structured-light project
> onto the Alchitry V2 ecosystem and adding a MIPI camera → USB3 host path.
> Nothing here is built yet — the current working hardware is the Numato Mimas A7
> (XC7A50T-1). This is the target for the *next* platform.

---

## 1. Target hardware (decided)

| Item | Part | Why |
|---|---|---|
| **Main board** | **Alchitry Pt V2** | XC7A100T-**2** (Artix-7 100T, fast −2 grade), **206 I/O pins**, 256 MB DDR3L, FT2232H (JTAG+UART), DF40 V2 connectors. |
| **HDMI** | **Alchitry Hd V2** | Two micro-HDMI ports, each switchable input/output via solder jumpers (**default: one in, one out** — exactly our PC-in / projector-out need). 5V↔3.3V level shifters on HPD + I²C/DDC. |
| **USB3** | **Alchitry Ft+ (FT601)** | 32-bit USB-3 FIFO bridge, ~**350 MB/s** real-world (vs ~190 MB/s for the 16-bit FT600 "Ft"). Costs ~18 more I/O pins. |
| GPIO | Alchitry GPIO/Io V2 | aux I/O, buttons, LEDs as needed |
| **Camera** | **MIPI CSI-2 element** (unreleased as of 2026-06-04) | camera input; specs/lane-count/connector TBD until it ships |

### Why Pt V2 over Au+
Both carry the same XC7A100T-2 die, BUT the daughter cards we want (Hd, Ft+, GPIO,
future MIPI) are **V2 elements on DF40 connectors**. The Au+ uses the *legacy*
connector system → won't mate with V2 elements. Among V2 main boards the Au V2 is
only a 35T. So **Pt V2 is the only board that gives the big/fast 100T AND V2-element
compatibility**, plus the most I/O (206) and onboard DDR3L.

### Mechanical / stack caveats (verify before ordering)
- Pt V2's dense DF40 connectors need a **Br / Fn / Sp** element to break out.
- The Hd element needs an **Sp (spacer)** between it and the Ft so the USB + micro-HDMI
  cables physically fit (per Alchitry notes).
- Confirm the Hd, GPIO, and MIPI cards are all the **V2/DF40** versions.

---

## 2. Target system (what the FPGA will do)

Structured-light scanner, all on one XC7A100T-2:

```
            ┌─────────────── existing HDMI project (port of hdmi_unified) ───────────────┐
  PC ──HDMI in──► hdmi_input (recover) ─┐                                                 │
                                        ├─ unified_fsm + clk_mux ─► DVID_output ─HDMI out─► projector
  (offline) drp_clkgen13 + timing ──────┘    (online↔offline auto-switch, EDID mode pick) │
            └───────────────────────────────────────────────────────────────────────────┘

  camera ──MIPI CSI-2──► D-PHY RX (soft) ─► CSI-2 decode ─► (DDR3 frame buf) ─► FT601 packer ─USB3─► host PC
                                                                  ▲
                                            (optional) capture sync to projected pattern
```

Two largely independent datapaths that run **concurrently** (project patterns +
capture frames + stream to host) — the natural shape of a structured-light scanner.

---

## 3. Feasibility verdict — YES (XC7A100T-2 has the headroom)

### Fits comfortably
- **Logic/BRAM/DSP:** current HDMI design is small (<⅓ of the 50T); + soft MIPI RX +
  USB3 packer still leaves big headroom on the 100T (~2× the 50T). 4,860 Kb BRAM
  covers line buffers + FIFOs.
- **DDR3L (256 MB):** real frame buffer for camera↔USB rate-matching / pattern store.
- **I/O (206 pins):** Hd (~8 diff pairs) + GPIO + Ft+ (~43) + 1–2-lane MIPI (~6–10)
  is nowhere near 206.

### Bandwidth (camera → Ft+ → host)
FT601 ≈ **350 MB/s** real-world. Typical sensor modes fit with margin:
| Mode | approx rate |
|---|---|
| 1080p30 10-bit RAW | ~78 MB/s |
| 1080p60 10-bit RAW | ~156 MB/s |
| ~5 MP @ 30 | ~150–200 MB/s |
DDR3 absorbs bursts. (FT601 ~350 MB/s is shared bidirectional — budget if also
sending control/other data upstream.)

---

## 4. Risks / the real engineering

1. **MIPI CSI-2 is the main new work + main risk.** Artix-7 has **no hard MIPI** →
   the D-PHY receiver is **soft** (ISERDESE2-based) + a CSI-2 packet decoder. Artix-7
   HR banks top out ~**1.25 Gbps/lane** → fine for 1–2-lane sensors at 1080p, but caps
   very fast 4-lane sensors. Plan to use/adapt an existing soft-D-PHY core and check
   the chosen sensor's lane rate against ~1.25 Gbps.
2. **The MIPI card is unreleased** — lane count, connector, timing unknown until it
   ships. Design *for* it but don't commit that leg yet.
3. **Clocking discipline** (the thing we keep fighting). Combined clock domains:
   clk100, 200 MHz IDELAY ref, recovered HDMI-in trio, DRP HDMI-out trio + the mux,
   6.25 MHz EDID-parse clock, **FT601 FIFO clock (~100 MHz)**, **MIPI byte/pixel clock**.
   The 100T has **6 CMTs (6 MMCM + 6 PLL)** and more clock regions than the 50T → both
   more clock generators AND easier BUFGMUX placement (the exact wall we hit on the
   50T). It fits, but **consolidate** (derive the slow parse + 200 MHz ref from shared
   MMCMs rather than one MMCM per function). This is also where the E0b raw-clock
   discipline (feed BUFGMUX raw, not BUFG'd) must carry over.
4. **Re-port (mechanical but real):** new XDC/pinout for the Pt V2; confirm `TMDS_33`
   lands on the Hd element's diff-pair pins for BOTH the input (clock recovery) and
   output ports; wire DDC/HPD through the Hd level shifters. **Bonus:** the Hd's
   level-shifted HPD may actually work here (unlike the dead tx-HPD on the Mimas), so
   hot-plug could use HPD in addition to the DDC-poll method we built.

---

## 5. Suggested bring-up order (when hardware arrives)

1. **Flasher / toolchain:** confirm `AlchitryFlasher/` (this folder) programs the Pt V2
   over USB-C (openFPGALoader / Alchitry Labs), replacing the Tenagra XVC + SPI flow.
2. **Blink + UART** on bare Pt V2 (clock + USB serial alive).
3. **Port the HDMI project** (hdmi_unified D2+E0b) to the Pt V2 + Hd element: re-pin,
   re-time, re-validate the online↔offline auto-switch on the new board. (Logic is
   already HW-proven on the Mimas; this is a board re-port.)
4. **Ft+ loopback:** bring up the FT601 245-synchronous-FIFO interface, host-side
   read/write throughput test (~350 MB/s target).
5. **MIPI RX** (when card ships): soft D-PHY + CSI-2 decode → line/frame capture to
   BRAM/DDR3; verify against a known sensor mode.
6. **Camera → USB3 passthrough:** wire capture → FT601 packer → host; verify frame
   integrity + sustained rate.
7. **Concurrent:** run HDMI project + camera passthrough together; check clocking and
   that capture can be synced to the projected pattern (the scanner use case).

---

## 6. Cross-references
- Current HDMI work: `../hdmi_unified/` (M2 + D2 + E0b), `../hdmi_offline/` (D2 proven
  standalone), `../hdmi_passthru/` (online base).
- Flasher: `AlchitryFlasher.ps1` / `.cmd` / `README.md` in this folder.
- Hardware sources: alchitry.com (Pt V2, Hd V2, Ft / Ft+), FTDI FT600/FT601 datasheet.

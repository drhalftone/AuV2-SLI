# MIPI CSI-2 Camera Bus — Design Roadmap

_Last updated: 2026-06-20_

Plan for a **custom MIPI CSI-2 receiver** on the AuV2-SLI stack: a daughter board carrying a
**22-pin MIPI camera connector** that plugs into the Alchitry DF40 stack, plus a **hand-written
D-PHY + CSI-2 RX** in the FPGA. This is the "roll our own MIPI bus" path — no off-the-shelf
camera-interface silicon, no vendor IP on the protocol side.

> **Status:** planning. Nothing fabricated. Targets the **Alchitry Pt V2** as the FPGA mainboard
> (not the Au V2 — see §1). Pin assignment below is **verified against the package file**; the
> D-PHY analog front end is the open engineering gate (§6, §9).

See [`ROADMAP.md`](ROADMAP.md) §7 for how this slots into the broader stack/bank plan, and
[`LauCameraTrigger_Alchitry_Stack/SCHEMATIC.md`](LauCameraTrigger_Alchitry_Stack/SCHEMATIC.md)
for the DF40 stacking-board pattern this board follows.

---

## 1. Target board: Alchitry Pt V2 (not the Au V2)

| | Au V2 | **Pt V2 (target)** |
|---|---|---|
| FPGA | Artix-7 `XC7A35T-1` | **Artix-7 `XC7A100T-2FGG484I`** |
| Logic cells | ~33k | **~101k (3×)** |
| DSP48E1 | 90 | **240** |
| I/O | ~102 | **206** |
| Speed grade | `-1` | **`-2`** (more ISERDES/timing margin) |
| High-speed | — | **GTP transceivers** (PCIe 2.0-class, bottom side) |
| Stack VCCO flex | 3.3 V only | **Bank 13 = 3.3 / 2.5 / 1.8 V** |

Why the Pt V2 matters for MIPI:
- **`-2` grade** lifts the HR-bank ISERDES line-rate ceiling → more comfortable ~1.0–1.25 Gbps/lane.
- **Bank 13 supports 1.8 V VCCO** — the only stack bank that does — which is where all the MIPI
  pins live and what the D-PHY front end + 1.8 V CCI want.
- **3× logic + 240 DSP** leaves room for CSI-2 RX **and** debayer/ISP **and** the SLI pipeline.

> ⚠️ This is a **mainboard swap**. The Pt is a different die/package with a different ball map, so
> `Au2.xdc` does **not** carry over — a new `Pt2.xdc` is required. The DF40 *signal* namespace
> (A1–A78 / B1–B78) is identical, so the Br, Hd, and camera/config daughter boards remain
> pin-compatible; only the FPGA ball assignments change.

Still 7-series → **no hardened MIPI D-PHY**. The receiver is a *soft* D-PHY on HR I/O. The GTP
transceivers do **not** help here (CML inputs can't handle D-PHY's 1.2 V single-ended LP mode);
they are reserved for a future SerDes/SLVS-EC/PCIe camera or high-speed frame egress.

---

## 2. Connector & lane scope

- **22-pin 0.5 mm FFC** (Raspberry Pi CM/Pi-5 family) = **2 data lanes + 1 clock lane** CSI-2.
- 3 differential pairs total (CLK, D0, D1) on the D-PHY; plus I²C (CCI), sensor reference clock,
  and power rails.
- **Reference sensor: Sony IMX219** (Raspberry Pi Camera v2) — 2-lane, abundant open register
  sets, lots of prior FPGA bring-up to crib from. De-risks I²C config more than anything else.
- **4-lane headroom:** bank 13 has ~11 usable pairs, so a future 4-lane (5-pair) sensor fits
  without touching another bank (would need a larger FFC, e.g. the CM4/CM5 connector).

---

## 3. Verified FPGA pin assignment (Pt V2, bank 13)

Confirmed against the AMD package file `xc7a100tfgg484pkg.txt` (6/6/2012). All pins are **HR**
I/O in **bank 13**, one clock region → a single bank-13 BUFIO reaches every data lane.

### 3.1 Recommended 2-lane map

| MIPI lane | FPGA P / N | Alchitry sig P / N | Pair / clock |
|---|---|---|---|
| **CLK**   | V13 / V14 | B47 / B45 | `IO_L13_T2` **MRCC** → BUFIO/BUFR/MMCM |
| **DATA0** | Y11 / Y12 | B42 / B40 | `IO_L11_T1` SRCC |
| **DATA1** | U15 / V15 | B48 / B46 | `IO_L14_T2` SRCC |
| **I²C SCL** | W10 | B51 | `IO_L10_T1` (single-ended, 1.8 V CCI) |
| **I²C SDA** | V10 | B53 | `IO_L10_T1` (single-ended, 1.8 V CCI) |
| **sensor refclk** | AB16 | B54 | or on-board 24 MHz oscillator (preferred) |

- **Spare clock-capable pair:** `IO_L12_T1` **MRCC** = W11 / W12 = B41 / B39 — left free.
- **Polarity:** in every bank-13 pair the **higher B-number is the FPGA P** ball. Route CLK+/D+ →
  P ball, CLK−/D− → N ball. (Data-lane P/N swaps only invert bits — recoverable in logic; keep
  the **clock** pair polarity correct.)

### 3.2 Full bank-13 pair reference (for re-allocation / 4-lane)

| Alchitry P/N | FPGA P/N | Pair | Note |
|---|---|---|---|
| B47/B45 | V13/V14 | L13 | **MRCC** |
| B41/B39 | W11/W12 | L12 | **MRCC** |
| B42/B40 | Y11/Y12 | L11 | SRCC |
| B48/B46 | U15/V15 | L14 | SRCC |
| B53/B51 | V10/W10 | L10 | — |
| B54/B52 | AB16/AB17 | L2 | — |
| B57/B59 | AB11/AB12 | L7 | — |
| B63/B65 | AA10/AA11 | L9 | DQS |
| B69/B71 | AA13/AB13 | L3 | DQS |
| B75/B77 | Y13/AA14 | L5 | — |

> The old camera/config switch pairs (`L8` = AA9/AB10 = B33/B35, `L4` = AA15/AB15 = B34/B36) are
> **freed** because switching moves onto this camera board (§4) — they're available for LP inputs
> or spare GPIO.

---

## 4. Camera board (hardware)

A DF40 stacking daughter board, same pattern as `LauCameraTrigger_Alchitry_Stack`:

- **DF40 plugs (bottom, facing down)** at the three Br sites for retention; taps **bank 13**
  signals on Site C (Bank B). Power (+3V3) from the 50-pin Site A.
- **22-pin MIPI FFC** on top.
- **D-PHY front end** (§6) between the FFC and the DF40 — the resistor network is the heart of
  the board.
- **Config switching relocated here.** The 4× SPDT scan/colour switches (HvsV/Blue/Green/Red)
  move onto this board, off bank 13, onto **bank-14 3.3 V** B-pins (B27–B32 region). This is what
  frees bank 13 to run at **1.8 V** for MIPI. (Decision: switching lives with the camera board.)
- **Power rails:** +3V3 from the stack; on-board LDO(s) for **1.8 V** (CCI + VCCO13 reference for
  the front end) and any sensor rails (IMX219 needs 1.8 V + 2.8 V analog + 1.2 V — provide per the
  sensor module, or use a pre-regulated camera module).
- **Sensor reference clock:** on-board 24 MHz oscillator preferred (offloads the FPGA and keeps
  jitter low); FPGA-driven refclk on B54 is the fallback.
- **Br is optional** (per `ROADMAP.md` §1): board can mate the Hd directly. Keep MIPI pairs short
  and length-matched through the stack — sub-LVDS 200 mV HS is unforgiving.

---

## 5. FPGA gateware (the "MIPI bus" we write)

Bottom-up, all in fabric:

1. **Soft D-PHY RX** (the hard part):
   - HS clock lane → MRCC (B47/B45) → `BUFIO` (bit clock) + `BUFR` (÷ byte clock).
   - Each HS data lane → `IDELAYE2` → `ISERDESE2` (8:1 DDR) for byte deserialization.
   - **LP/HS state machine:** detect LP-11 (stop) → LP-01 → LP-00 (HS-request) → HS-sync, and the
     reverse on EoT. This is the fiddliest logic and the most bring-up risk.
2. **Lane align & merge:** detect the `0xB8` sync (SoT) per lane, byte-align, de-skew lanes,
   de-interleave to a packet byte stream.
3. **CSI-2 protocol layer:**
   - Parse the packet header (Data Identifier = virtual channel + data type), check the **ECC**
     (header is Hamming-protected, single-bit correct).
   - **Short packets:** Frame Start / Frame End / line sync.
   - **Long packets:** payload + **CRC-16** check; data type → RAW8/10/12, YUV422, RGB888, embedded.
4. **Pixel unpack:** e.g. RAW10 (4 px in 5 bytes) → 10-bit pixels; emit a clean pixel stream +
   line/frame valid into the SLI / ISP pipeline (line buffers, debayer — DSP-friendly, plenty of
   DSP48 on the Pt).

> **Build vs. buy on the front end:** Xilinx's MIPI CSI-2 RX Subsystem + soft D-PHY (PG202 /
> **XAPP894**) is supported on 7-series HR banks and gives you the D-PHY state machine + decoder
> (may need a license; black box). Even rolling our own *logic*, the **XAPP894 resistor network is
> the canonical reference for the electrical front end** — follow it for the LP/HS analog scheme.

---

## 6. D-PHY analog front end (XAPP894) — the open gate

MIPI runs two electrically different modes on the same wires:
- **HS:** ~200 mV differential (sub-LVDS), common mode ~200 mV.
- **LP:** 1.2 V single-ended (both lines), for control/handshake.

On 7-series HR I/O there is no native D-PHY, so a **resistor network** presents:
- the **HS** pair to a differential (LVDS-class) input for the ISERDES, and
- the **LP** levels to single-ended inputs (1.2 V is below LVCMOS18 `VIH`, so LP needs a
  divider/comparator path).

Budget **extra single-ended bank-13 inputs for LP detection** (the freed `L8`/`L4` pins cover
this). Exact resistor values, termination, and the precise per-lane input count must come from
**XAPP894 / PG202** — pin these before layout. VCCO13 = 1.8 V suits the HS receiver and CCI;
the front end handles LP regardless of VCCO.

---

## 7. Line-rate budget

| Item | Value |
|---|---|
| HR-bank ISERDES ceiling (`-2`) | ~1.0–1.25 Gbps/lane |
| Lanes (22-pin) | 2 |
| Aggregate | ~2.0–2.5 Gbps |
| Realistic target | **1080p30 RAW10** (≈ 1.5 Gbps incl. overhead) |
| Out of reach on this front end | 4K, high-FPS — would need 4-lane + HP banks/SerDes |

Pick the sensor mode (resolution / framerate / bit depth) to live under the aggregate. IMX219
1080p30 2-lane is a safe first target.

---

## 8. Bank / VCCO plan (Pt V2 stack banks)

| Bank | VCCO | Use |
|---|---|---|
| **13** | **1.8 V** | **MIPI D-PHY (B39–B54) + 1.8 V CCI/I²C** — set by this board |
| 14 | 3.3 V | camera trigger lines (B27–B30) + relocated config switches |
| 34, 35, 16 | 3.3 V | existing SLI / HDMI-adjacent I/O |
| 15 | 1.35 V | DDR (do not touch) |
| 0 | — | config |

> Setting VCCO13 = 1.8 V is **only safe once the 3.3 V switch lines leave bank 13** (§4). Do not
> drive 3.3 V into a 1.8 V-VCCO bank input.

---

## 9. Risks & gates

**Before fabricating the camera board:**
1. **D-PHY front-end network finalized** against XAPP894/PG202 (resistor values, termination,
   LP input count) — _the_ critical gate.
2. **Differential P/N polarity + MRCC** — ✅ verified (§3) against `xc7a100tfgg484pkg.txt`.
3. **`Pt2.xdc`** created: the 8 SLI/camera ports + the 3 MIPI pairs reassigned to Pt V2 balls
   (bank 13 at 1.8 V), pulls/IOSTANDARDs set; bench-tested before fab.
4. **VCCO13 = 1.8 V** confirmed safe (no 3.3 V loads left in bank 13).
5. **SI through the DF40 stack** — length-match the 3 pairs; prefer mating the Hd directly
   (Br optional) to minimize discontinuities at 200 mV HS.
6. **Sensor power** rails (1.8 / 2.8 / 1.2 V for IMX219) provided on-board or via a pre-regulated
   module.

**Confirmed / de-risked:**
- Pt V2 top DF40 is Au-compatible (same A/B signal namespace) → Br/Hd/daughter boards fit.
- Bank 13 carries the MIPI signals and is the only stack bank that supports 1.8 V VCCO.
- Two MRCC + two SRCC pairs available in bank 13 — enough for 2-lane now, 4-lane later.

**Open engineering work (not pin work):**
- The soft D-PHY LP/HS state machine + ISERDES capture.
- The XAPP894 resistor-network front end.
- CSI-2 packet parser (ECC/CRC) + pixel unpack.

---

## 10. Source references

- **Pt V2 silicon/specs:** SparkFun / Alchitry product pages
  (`XC7A100T-2FGG484I`, 101k LC, 240 DSP, 206 IO, GTP, 1.5 mm DF40).
- **Pt V2 pin map (signal ↔ ball):** `alchitry/Alchitry-Labs-V2` →
  `src/main/kotlin/com/alchitry/labs2/hardware/pinout/PtV2TopPin.kt`
  (top stack; `PtV2BottomPin.kt` = GTP/bottom side).
- **Package pin names (P/N, MRCC/SRCC, bank):** AMD `xc7a100tfgg484pkg.txt`
  (Artix-7 package pinout files).
- **D-PHY front end + CSI-2 RX on 7-series:** Xilinx **XAPP894**, **PG202** (MIPI CSI-2 RX
  Subsystem + MIPI D-PHY).
- **Reference sensor:** Sony IMX219 datasheet + Raspberry Pi Camera v2 register sets.
- **Stack/bank context:** [`ROADMAP.md`](ROADMAP.md) §3, §7.

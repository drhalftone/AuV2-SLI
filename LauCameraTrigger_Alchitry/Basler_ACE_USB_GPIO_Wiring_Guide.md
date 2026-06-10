# Basler ACE USB 3.0 GPIO Wiring Guide

## FPGA Slave Mode Configuration

This document describes the GPIO wiring between a Basler ACE USB 3.0 camera and an FPGA that synchronizes the camera to an HDMI signal.

---

## Basler ACE USB 3.0 I/O Connector Pinout

**Connector Type:** Hirose 6-pin [HR10A-7P-6S (73)]

| Pin | Signal Name       | Line       | Type                 | Direction    |
|-----|-------------------|------------|----------------------|--------------|
| 1   | GPIO              | **Line 3** | TTL (direct-coupled) | Input/Output |
| 2   | Opto-isolated IN  | **Line 1** | Opto-isolated        | Input only   |
| 3   | GPIO              | **Line 4** | TTL (direct-coupled) | Input/Output |
| 4   | Opto-isolated OUT | **Line 2** | Opto-isolated        | Output only  |
| 5   | Opto Ground       | —          | Ground               | —            |
| 6   | GPIO Ground       | —          | Ground               | —            |

---

## Opto-GP-I/O Y-Cable Wire Colors

The Basler Opto-GP-I/O Y-Cable (Part# 2000034088) splits into two branches:

### Blue Jacket Cable (Opto-Isolated Signals)

| Pin | Wire Color      | Signal            | Line   |
|-----|-----------------|-------------------|--------|
| 2   | **Brown**       | Opto-isolated IN  | Line 1 |
| 4   | **Yellow**      | Opto-isolated OUT | Line 2 |
| 5   | **White/Green** | Opto Ground       | —      |

### Yellow Jacket Cable (TTL GPIO Signals)

| Pin | Wire Color      | Signal      | Line   |
|-----|-----------------|-------------|--------|
| 1   | **Brown**       | GPIO        | Line 3 |
| 3   | **Yellow**      | GPIO        | Line 4 |
| 6   | **White/Green** | GPIO Ground | —      |

---

## Complete Wiring Chart for FPGA Slave Mode

| Basler Pin | Basler Line | Type     | Camera Dir | Function       | Cable  | Wire Color  |
|------------|-------------|----------|------------|----------------|--------|-------------|
| 2          | Line 1      | Opto IN  | INPUT      | Frame trigger  | Blue   | Brown       |
| 1          | Line 3      | TTL GPIO | INPUT      | Pattern status | Yellow | Brown       |
| 4          | Line 2      | Opto OUT | OUTPUT     | Camera ready   | Blue   | Yellow      |
| 3          | Line 4      | TTL GPIO | OUTPUT     | Mode switch    | Yellow | Yellow      |
| 5          | —           | Opto GND | —          | Ground         | Blue   | White/Green |
| 6          | —           | GPIO GND | —          | Ground         | Yellow | White/Green |

---

## DB9 Connector Wiring (FPGA Interface)

### DB9 Pin Functions (from FPGA perspective)

| DB9 Pin | FPGA Signal       | FPGA Direction | Description                                |
|---------|-------------------|----------------|--------------------------------------------|
| 1       | GND               | —              | Ground                                     |
| 4       | Pattern frame out | Output         | Sends pattern status TO camera (Line 3)    |
| 5       | Frame trigger out | Output         | Sends frame trigger TO camera (Line 1)     |
| 8       | Camera ready in   | Input          | Receives camera ready FROM camera (Line 2) |
| 9       | Mode select in    | Input          | Receives mode switch FROM camera (Line 4)  |

### Basler ACE USB to DB9 Wiring Table

| Basler Pin | Basler Line | Cable  | Wire Color      | Camera Dir | Function       | DB9 Pin | FPGA Signal       |
|------------|-------------|--------|-----------------|------------|----------------|---------|-------------------|
| 2          | Line 1      | Blue   | **Brown**       | INPUT      | Frame trigger  | **5**   | Frame trigger out |
| 1          | Line 3      | Yellow | **Brown**       | INPUT      | Pattern status | **4**   | Pattern frame out |
| 4          | Line 2      | Blue   | **Yellow**      | OUTPUT     | Camera ready   | **8**   | Camera ready in   |
| 3          | Line 4      | Yellow | **Yellow**      | OUTPUT     | Mode switch    | **9**   | Mode select in    |
| 5          | —           | Blue   | **White/Green** | —          | Opto Ground    | **1**   | GND               |
| 6          | —           | Yellow | **White/Green** | —          | GPIO Ground    | **1**   | GND               |

### Quick Reference: Wire to DB9

| Cable  | Wire Color  | DB9 Pin |
|--------|-------------|---------|
| Blue   | Brown       | 5       |
| Blue   | Yellow      | 8       |
| Blue   | White/Green | 1       |
| Yellow | Brown       | 4       |
| Yellow | Yellow      | 9       |
| Yellow | White/Green | 1       |

### DB9 Wiring Diagram

```
Basler ACE USB 3.0                                  DB9 Female (to FPGA)
(Hirose 6-pin)
──────────────────────────────────────────────────────────────────────────
Pin 2 (Blue/Brown)    - Line 1 [IN]  ◄─────────────  Pin 5 (Frame trigger out) - Trigger
Pin 1 (Yellow/Brown)  - Line 3 [IN]  ◄─────────────  Pin 4 (Pattern frame out) - Pattern Status
Pin 4 (Blue/Yellow)   - Line 2 [OUT] ──────────────► Pin 8 (Camera ready in)   - Camera Ready
Pin 3 (Yellow/Yellow) - Line 4 [OUT] ──────────────► Pin 9 (Mode select in)    - Mode Switch
Pin 5 (Blue/Wht-Grn)  - Opto GND    ──────────────  Pin 1 (GND)
Pin 6 (Yellow/Wht-Grn)- GPIO GND    ──────────────  Pin 1 (GND)
```

---

## Signal Descriptions

| Signal                  | Line  | Purpose        | Behavior                                                              |
|-------------------------|-------|----------------|-----------------------------------------------------------------------|
| **Line 1** (Opto IN)   | Pin 2 | Frame trigger  | Rising edge triggers frame capture (opto-isolated for noise immunity) |
| **Line 3** (GPIO)      | Pin 1 | Pattern status | Input from FPGA indicating pattern index/zero status                  |
| **Line 2** (Opto OUT)  | Pin 4 | Camera ready   | Indicates camera is ready for next trigger                            |
| **Line 4** (GPIO)      | Pin 3 | Mode switch    | Output for SLI vs HDMI pass-through control                          |

---

## Electrical Specifications

### Opto-Isolated Input (Line 1)

| Parameter            | Value           |
|----------------------|-----------------|
| Input Voltage (VIL)  | 0 - 2.0 VDC    |
| Input Voltage (VIH)  | 5.0 - 24.0 VDC |
| Max Input Voltage    | 24 VDC          |
| Input Current        | ~8 mA typical   |

### Opto-Isolated Output (Line 2)

| Parameter        | Value          |
|------------------|----------------|
| Output Type      | Open collector |
| Max Sink Current | 25 mA          |
| Max Voltage      | 24 VDC         |

### TTL GPIO (Line 3, Line 4)

| Parameter          | Value          |
|--------------------|----------------|
| Logic Levels       | 3.3V TTL       |
| Output High (VOH)  | 2.4 - 3.3 VDC |
| Output Low (VOL)   | 0 - 0.4 VDC   |
| Input High (VIH)   | 2.0 - 3.3 VDC |
| Input Low (VIL)    | 0 - 0.8 VDC   |

---

## Software Configuration (Pylon SDK)

### Trigger Configuration

```cpp
camera.TriggerSelector.SetValue(TriggerSelector_FrameStart);
camera.TriggerMode.SetValue(TriggerMode_On);
camera.TriggerSource.SetValue(TriggerSource_Line1);
camera.TriggerActivation.SetValue(TriggerActivation_RisingEdge);
```

### GPIO Configuration

```cpp
// Line 3 as Input (Pattern Status from FPGA)
camera.LineSelector.SetValue(LineSelector_Line3);
camera.LineMode.SetValue(LineMode_Input);

// Line 4 as Output (Mode Switch to FPGA)
camera.LineSelector.SetValue(LineSelector_Line4);
camera.LineMode.SetValue(LineMode_Output);
camera.LineSource.SetValue(LineSource_UserOutput1);

// Line 2 as Output (Camera Ready - typically automatic)
camera.LineSelector.SetValue(LineSelector_Line2);
camera.LineSource.SetValue(LineSource_ExposureActive);
```

---

## Important Notes

1. **Opto-isolated vs TTL**: Line 1 (opto-isolated input) is more robust against EMI than the TTL GPIO lines. Use Line 1 for the trigger signal in noisy environments.

2. **Pull-ups required for open-collector output**: Line 2 (opto-isolated output, camera ready) is an **open-collector** output — it can only sink current to ground but cannot drive a high level. A **pull-up resistor** is required on the FPGA input side for a valid logic high. The Mimas A7 PCB includes pull-up resistors on the FPGA input lines (DB9 pins 8 and 9) for this reason. Unlike the Basler, the Alvium 1800 USB has push-pull 3.3V GPIO outputs and does not require pull-ups.

3. **Opto-isolated input voltage**: Line 1 (opto-isolated input, trigger) requires **5–24V** to drive the internal opto-coupler. The FPGA's 3.3V GPIO output may not reliably trigger it — verify that the Mimas A7 PCB provides level shifting or a higher voltage supply for this line.

4. **Ground connections**: Both ground wires (Pin 5 Opto Ground and Pin 6 GPIO Ground) should be connected to ground for optimal signal-to-noise ratio.

5. **Unused GPIO lines**: If not using a GPIO line, connect its wire to ground to reduce noise.

6. **Y-Cable identification**: The blue jacket cable connects to opto-isolated pins; the yellow jacket cable connects to TTL GPIO pins.

7. **3.3V Logic**: The TTL GPIO outputs (Line 3, Line 4) are 3.3V logic level - ensure your FPGA inputs are compatible.

---

## FPGA Compatibility (MimasA7-SLI vs AuV2-SLI)

The DB-9 pin functions and FPGA directions are identical between the two FPGAs, so wire-level the same Basler cable harness can plug into either. However, the breakout-board electrical support differs:

- **MimasA7-SLI** (Numato Mimas A7 Rev3): uses the [`LauCameraTrigger_MimasA7`](https://github.com/ruffner/MojoV3_HDMI_Interface/tree/master/pcb/LauCameraTrigger_MimasA7) PCB. Includes the pull-ups on DB-9 pins 8 and 9 that the Basler open-collector Line 2 needs, and provides the 5–24V drive required by the opto-isolated Line 1 trigger. Single camera channel.
- **AuV2-SLI** (Alchitry Au V2 + Hd V2 + Br V2): uses the stock Br V2 breakout. The DB-9 pinout is the same, but the Br V2 does **not** add pull-ups for the open-collector Line 2 output, nor does it level-shift the trigger line above 3.3V. For a Basler camera on the AuV2 you must add an external pull-up on DB-9 pin 8 (and pin 9 if using Line 4) and either a level shifter or higher-voltage drive on DB-9 pin 5 to reliably fire Line 1. The Alvium 1800 (push-pull 3.3V) works on the AuV2 with no extra circuitry.

### Camera 2 on AuV2-SLI: outputs-only in current bitstream

The AuV2 exposes a second camera channel on DB-9 pins 2, 3, 6, 7, but in `constrs_1/imports/RTL/Au2.xdc` the `C2_in[0]` (DB-9 pin 7 / camera-ready) and `C2_in[1]` (DB-9 pin 2 / mode) constraints are **commented out**. Only `C2_out[0]` (trigger, DB-9 6) and `C2_out[1]` (first frame, DB-9 3) are bound. Camera 2 therefore runs open-loop with no camera-ready handshake until the XDC is updated upstream.

---

## Cable Part Numbers

| Part Number | Description                                                       |
|-------------|-------------------------------------------------------------------|
| 2000034088  | Opto-GP-I/O Y-Cable, HRS 6p/open, 2 x 10 m (both opto and GPIO) |
| 2000034087  | GP-I/O Cable, HRS 6p/open, 10 m (GPIO only)                      |
| 2000034084  | Power-I/O Cable, HRS 6p/open, 10 m (power + opto I/O)            |

---

## Comparison with AVT Alvium 1800 USB

| Function                    | Basler Line    | Basler Pin | AVT Line | AVT JST Pin | DB9 Pin |
|-----------------------------|----------------|------------|----------|-------------|---------|
| Trigger (to camera)         | Line 1 (Opto)  | 2          | Line0    | 2           | 5       |
| Pattern status (to camera)  | Line 3 (GPIO)  | 1          | Line2    | 4           | 4       |
| Camera ready (from camera)  | Line 2 (Opto)  | 4          | Line3    | 5           | 8       |
| Mode switch (from camera)   | Line 4 (GPIO)  | 3          | Line1    | 3           | 9       |
| Ground                      | —              | 5 & 6      | GND      | 1           | 1       |

---

## References

### Camera Documentation
- [Basler Opto-GP-I/O Y-Cable Documentation](https://docs.baslerweb.com/basler-opto-gp-io-y-cable-hrs-6p-open-p-2x10m)
- [Basler GP-I/O Cable Documentation](https://docs.baslerweb.com/basler-gp-io-cable-hrs-6p-open-p)
- [Basler ACE Circuit Diagrams](https://docs.baslerweb.com/circuit-diagrams-(ace))
- [Basler ACE USB 3.0 User Manual](https://www.micropticsl.com/wp-content/uploads/2013/09/basler_ace_usb_manual.pdf)

### FPGA Code Repositories
- [MimasA7-SLI](https://github.com/Qishi-Hu/MimasA7-SLI) - Structured light illumination system using Numato Mimas A7 Rev3 board (Artix-7 FPGA, VHDL)
- [AuV2-SLI](https://github.com/Qishi-Hu/AuV2-SLI) - Structured light illumination system using Alchitry Au V2 board (Artix-7 FPGA, VHDL)

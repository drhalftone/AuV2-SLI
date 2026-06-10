# Allied Vision Alvium 1800 USB GPIO Wiring Guide

## FPGA Slave Mode Configuration

This document describes the GPIO wiring between an Allied Vision Alvium 1800 USB camera and an FPGA that synchronizes the camera to an HDMI signal.

---

## Alvium 1800 USB I/O Connector Pinout

**Connector Type:** JST BM07B-SRSS-TBT (7-pin)

**Pin 1 is closest to the screw lock hole on the camera body.**

| Pin | Signal Name | Software Name | Wire Color (12322 cable) |
|-----|-------------|---------------|--------------------------|
| 1   | GND         | —             | Black                    |
| 2   | EXT-GPIO 0  | **Line0**     | Brown                    |
| 3   | EXT-GPIO 1  | **Line1**     | Orange                   |
| 4   | EXT-GPIO 2  | **Line2**     | Yellow                   |
| 5   | EXT-GPIO 3  | **Line3**     | Green                    |
| 6   | Don't care  | —             | —                        |
| 7   | Don't care  | —             | —                        |

---

## Complete Wiring Chart for FPGA Slave Mode

| JST Pin | Wire Color | Signal | Direction | Software Setting                       | Connect to FPGA             |
|---------|------------|--------|-----------|----------------------------------------|-----------------------------|
| **1**   | Black      | GND    | —         | —                                      | FPGA GND                    |
| **2**   | Brown      | Line0  | INPUT     | TriggerSource=Line0, RisingEdge        | FPGA Trigger Output         |
| **3**   | Orange     | Line1  | OUTPUT    | LineSource=Off, Inverted               | FPGA: SLI/HDMI Mode Select  |
| **4**   | Yellow     | Line2  | INPUT     | LineMode=Input                         | FPGA: Pattern Zero Status   |
| **5**   | Green      | Line3  | OUTPUT    | LineSource=FrameTriggerWait, Inverted  | FPGA: Camera Ready Signal   |

---

## Wiring Diagram

```
Allied Vision Alvium 1800 USB                Your FPGA
(JST 7-pin connector, Pin 1 closest to screw lock hole)
───────────────────────────────────────────────────────────
Pin 1 (Black)  - GND    ──────────────────  GND (common ground)
Pin 2 (Brown)  - Line0  ◄─────────────────  Trigger Output
Pin 3 (Orange) - Line1  ──────────────────► SLI/HDMI Mode Input
Pin 4 (Yellow) - Line2  ◄─────────────────  Pattern Status Output
Pin 5 (Green)  - Line3  ──────────────────► Camera Ready Input
```

---

## DB9 Connector Wiring (FPGA Interface)

This section describes how to wire the AVT Alvium camera to a DB9 connector for interfacing with an FPGA. The mapping is based on compatibility with existing Basler ACE USB camera wiring.

### DB9 Pin Functions (from FPGA perspective)

| DB9 Pin | FPGA Pin | FPGA Signal        | FPGA Direction | Description                          |
|---------|----------|--------------------|----------------|--------------------------------------|
| 1       | —        | GND                | —              | Ground                               |
| 4       | A29_1    | Pattern frame out  | Output         | Sends pattern frame indicator TO camera |
| 5       | A31_1    | Trigger out        | Output         | Sends trigger signal TO camera       |
| 8       | A32_1    | Camera ready in    | Input          | Receives camera ready FROM camera    |
| 9       | A28_1    | Mode select in     | Input          | Receives mode switch FROM camera     |

### AVT Alvium to DB9 Wiring Table

| AVT JST Pin | AVT Signal | Wire Color | Camera Dir | Function       | DB9 Pin | FPGA Pin |
|-------------|------------|------------|------------|----------------|---------|----------|
| 1           | GND        | **Black**  | —          | Ground         | **1**   | GND      |
| 2           | Line0      | **Brown**  | INPUT      | Frame trigger  | **5**   | A31_1    |
| 3           | Line1      | **Orange** | OUTPUT     | SLI/HDMI mode  | **9**   | A28_1    |
| 4           | Line2      | **Yellow** | INPUT      | Pattern status | **4**   | A29_1    |
| 5           | Line3      | **Green**  | OUTPUT     | Camera ready   | **8**   | A32_1    |

### Quick Reference: Wire Color to DB9

| Wire Color | DB9 Pin |
|------------|---------|
| Black      | 1       |
| Brown      | 5       |
| Orange     | 9       |
| Yellow     | 4       |
| Green      | 8       |

### DB9 Wiring Diagram

```
AVT Alvium 1800 USB                            DB9 Female (to FPGA)
(JST 7-pin, Pin 1 closest to screw lock hole)
──────────────────────────────────────────────────────────────────────
Pin 1 (Black)  - GND    [IN]  ─────────────────  Pin 1 (GND)
Pin 2 (Brown)  - Line0  [IN]  ◄────────────────  Pin 5 (A31_1) - Trigger
Pin 3 (Orange) - Line1  [OUT] ─────────────────► Pin 9 (A28_1) - Mode Switch
Pin 4 (Yellow) - Line2  [IN]  ◄────────────────  Pin 4 (A29_1) - Pattern Status
Pin 5 (Green)  - Line3  [OUT] ─────────────────► Pin 8 (A32_1) - Camera Ready
```

### Comparison with Basler ACE USB Wiring

| Function                    | Basler Line | Basler Pin | AVT Line | AVT JST Pin | AVT Wire | DB9 Pin |
|-----------------------------|-------------|------------|----------|-------------|----------|---------|
| Ground                      | —           | 5 & 6      | GND      | 1           | Black    | 1       |
| Trigger (to camera)         | Line 1      | 2          | Line0    | 2           | Brown    | 5       |
| Mode switch (from camera)   | Line 4      | 3          | Line1    | 3           | Orange   | 9       |
| Pattern status (to camera)  | Line 3      | 1          | Line2    | 4           | Yellow   | 4       |
| Camera ready (from camera)  | Line 2      | 4          | Line3    | 5           | Green    | 8       |

---

## Signal Descriptions

| Signal              | Purpose       | Behavior                                                         |
|---------------------|---------------|------------------------------------------------------------------|
| **Line0** (Brown)   | Frame trigger | Rising edge triggers frame capture                               |
| **Line1** (Orange)  | Mode switch   | Inverted output; controls SLI vs HDMI pass-through               |
| **Line3** (Green)   | Camera ready  | Inverted; goes LOW on wire when camera is ready for next trigger |
| **Line2** (Yellow)  | Pattern sync  | Input from FPGA indicating pattern index/zero status             |

---

## Electrical Specifications

| Parameter                   | Value          |
|-----------------------------|----------------|
| Output High (Uout)          | 2.4 - 3.3 VDC  |
| Output Current (max)        | 12 mA          |
| External Power (VCC-EXT-IN) | 4.5 - 5.5 VDC  |

---

## Software Configuration (Vimba SDK)

### Trigger Configuration (in connectToHost)

```cpp
setAttribute(handle, "TriggerMode", "On");
setAttribute(handle, "TriggerSelector", "FrameStart");
setAttribute(handle, "TriggerSource", "Line0");
setAttribute(handle, "TriggerActivation", "RisingEdge");
setAttribute(handle, "AcquisitionMode", "MultiFrame");
```

### GPIO Configuration (in setSynchronization for ModeSlave)

```cpp
// Line0 = Trigger INPUT from FPGA
setAttribute(handle, "LineSelector", "Line0");
setAttribute(handle, "LineMode", "Input");
setAttribute(handle, "LineInverter", "false");

// Line2 = Pattern zero status INPUT from FPGA
setAttribute(handle, "LineSelector", "Line2");
setAttribute(handle, "LineMode", "Input");
setAttribute(handle, "LineInverter", "false");

// Line1 = OUTPUT to FPGA (SLI/HDMI mode switch)
setAttribute(handle, "LineSelector", "Line1");
setAttribute(handle, "LineMode", "Output");
setAttribute(handle, "LineInverter", "true");
setAttribute(handle, "LineSource", "Off");

// Line3 = OUTPUT: FrameTriggerWait (camera ready for next trigger)
setAttribute(handle, "LineSelector", "Line3");
setAttribute(handle, "LineMode", "Output");
setAttribute(handle, "LineInverter", "true");
setAttribute(handle, "LineSource", "FrameTriggerWait");
```

---

## Important Notes

1. **Pin 1 identification**: Pin 1 is closest to the screw lock hole on the camera body. The non-screw-lock cable (12319) has **all blue wires** - use a multimeter to verify pin continuity if needed.

2. **Wire colors** apply to Allied Vision cables with screw locks (12322, 12326, 12327).

3. **Inverted signals**: Line1 and Line3 are configured with `LineInverter = true`, so the physical voltage is opposite to the logical state.

4. **3.3V Logic**: The GPIO outputs are 3.3V logic level - ensure your FPGA inputs are compatible.

5. **No pull-ups required**: Unlike the Basler ACE USB (which has open-collector opto-isolated outputs requiring pull-up resistors), the Alvium's GPIO lines are all **3.3V push-pull** and can drive the FPGA inputs directly. The Mimas A7 PCB includes pull-up resistors on the FPGA input lines (DB9 pins 8 and 9) for Basler compatibility — these are harmless when using an Alvium but not necessary.

6. **Handshaking**: The FPGA should wait for Line3 (FrameTriggerWait) to indicate the camera is ready before sending the next trigger pulse on Line0.

---

## FPGA Compatibility (MimasA7-SLI vs AuV2-SLI)

The same DB-9 cable described above is wire-level compatible with both FPGAs — the DB-9 pin functions and FPGA directions match exactly.

- **MimasA7-SLI** (Numato Mimas A7 Rev3): uses the [`LauCameraTrigger_MimasA7`](https://github.com/ruffner/MojoV3_HDMI_Interface/tree/master/pcb/LauCameraTrigger_MimasA7) breakout PCB. Adds pull-ups on DB-9 pins 8 and 9 (FPGA-input lines) and level shifting for the 5–24V Basler opto-trigger. Single camera channel.
- **AuV2-SLI** (Alchitry Au V2 + Hd V2 + Br V2): uses the stock Alchitry Br V2 breakout. DB-9 pin functions are identical. The Br V2 does **not** add pull-ups or level shifting — fine for the Alvium (3.3V push-pull), but a Basler would need them added externally. Two camera channels exposed on the DB-9.

### Camera 2 on AuV2-SLI: outputs-only in current bitstream

The AuV2 README lists a second camera channel on DB-9 pins 2, 3, 6, 7, but in `constrs_1/imports/RTL/Au2.xdc` the `C2_in[0]` (DB-9 pin 7 / camera-ready) and `C2_in[1]` (DB-9 pin 2 / mode) constraints are **commented out**. Only `C2_out[0]` (trigger, DB-9 6) and `C2_out[1]` (first frame, DB-9 3) are bound. Camera 2 therefore runs open-loop with no `FrameTriggerWait` handshake until the XDC is updated upstream.

---

## Cable Part Numbers

| Part Number | Description                                                   |
|-------------|---------------------------------------------------------------|
| 12322       | I/O cable 7-pin JST with screw locks, 3m (color-coded wires)  |
| 12326       | I/O cable 7-pin JST with screw locks, 5m (color-coded wires)  |
| 12327       | I/O cable 7-pin JST with screw locks, 10m (color-coded wires) |
| 12319       | I/O cable 7-pin JST without screw lock, 0.4m (all blue wires) |

---

## References

### Camera Documentation
- [Allied Vision Alvium Pin Assignment PDF](https://cdn.alliedvision.com/fileadmin/content/documents/products/cameras/Alvium_common/appnote/Alvium_Pin-Assignment.pdf)
- [Alvium USB Cameras User Guide](https://cdn.alliedvision.com/fileadmin/content/documents/products/cameras/Alvium_USB/techman/Alvium-USB-Cameras_User-Guide.pdf)
- [ManualsLib - Alvium Series Manual](https://www.manualslib.com/manual/1679546/Allied-Vision-Alvium-Series.html?page=77)
- [DigiKey - Allied Vision 12322 Cable](https://www.digikey.com/en/products/detail/allied-vision-inc/12322/11200629)

### FPGA Code Repositories
- [MimasA7-SLI](https://github.com/Qishi-Hu/MimasA7-SLI) - Structured light illumination system using Numato Mimas A7 Rev3 board (Artix-7 FPGA, VHDL)
- [AuV2-SLI](https://github.com/Qishi-Hu/AuV2-SLI) - Structured light illumination system using Alchitry Au V2 board (Artix-7 FPGA, VHDL)

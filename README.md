# AuV2-SLI

This repository serves a structured light illumination (SLI) system orchestrated by an FPGA controller. The system is based on the Alchitry Au V2 board, which is powered by an Artix-7 FPGA. The board is extended with an Hd V2 board that has two HDMI shields and a Br V2 board for GPIO connections with external DB9 camera modules following a 4-line protocol.

In this project, the FPGA can:
- Take HDMI video input from a host PC and output HDMI video to a DLP projector, triggering the camera when the top-left pixel changes.
- Replace the HDMI input frames with locally generated SLI patterns that vary in spatial frequency and temporal frequency.
- Synchronize the projection and capture of each frame by interacting with the camera modules through the GPIO pins via the 4-line protocol.
- When the HDMI input cable is unplugged, the FPGA will be driven by the local oscillator clock instead of the HDMI Rx clock, dedicated to the local pattern generation mode only.

The phase images captured by the camera modules are sent to the host PC through USB, and a host Qt program will complete the 3-D reconstruction. The system can scan 1280x720 resolution at 120FPS.

## How to configure the bitstream?

1. Download and install the latest version of [AlchitryLab v2](https://alchitry.com/alchitry-labs/).
2. Download `Bitstream\Au2_SLI.bin` and power the board via USB.
3. Open Alchitry Loader in AlchitryLab v2, and program the board using the flash memory option.

## Tips for setting FPS and resoultion for HDMI Input
The HDMI input should be automtcially conifgured after it reads the EDID from the FPGA. To confirm it in Windows, go to **System > Display > Advanced Settings > Sletect Display "Qishi-SLI"**. The display info should be similar to the screenshot below.
![Screenshot 2025-04-17 201139](https://github.com/user-attachments/assets/5b7a5cb5-8982-4aa2-bfc6-cf27cb00fb06)


The **Active Signal Mode** is the actual setting of the HDMI signal, if it is not matching the desired resolution and FPS, please go to **System > Display > Advanced Settings > Adapter Properties > List All Modes** to manually set the correct mode.

## Specifications of FPGA Controller Modes

### 1. Pass-through with Top-Left Pixel Detection
- The FPGA functions as an HDMI pass-through capable of **720p@120Hz**. The PC is responsible for playing back the SLI patterns.
- The FPGA reads the **top-left pixel (TLP)** value of each frame.
- If the current frame has a different TLP value from the previous frame, the FPGA sends a pulse to trigger the camera shutter during the next VSYNC period.
- The host PC waits for confirmation that the camera is ready before playing the next frame.

### 2. Pass-through with SLI Pattern Generation
- The FPGA replaces the input HDMI frames with locally generated SLI patterns. If the HDMI input is absent, it simply creates the pattern locally.
- The fringe pattern is generated **on the fly by `pattern_gen.v`** (a resolution-adaptive DDS), not from precomputed ROMs. It measures the active region, sets a base period `b = ceil(F/288)`, sweeps the spatial frequencies `288b : 48b : 8b`, and samples a 4096-entry master cosine — so the fringe frequency **scales with the display resolution** and spans the field at any mode. `frq == 3` emits a flash (black/white) frame.
- Orientation (`SW[0]`: vertical vs. horizontal stripes), per-channel enables (`SW[3:1]`), and the runtime frame/frequency sequencing drive the generator.
- The FPGA increments the frame index and triggers the camera on VSYNC, as long as the camera signals that it is ready (by sending a rising edge).
- *Historical:* earlier builds drove the pattern from precomputed `LUT.coe` / `indexMap.coe` / `indexMapV.coe` ROMs produced by the `Matlab/` scripts (`LUT2coe.m`, `indexMapping.m`, `indexMappingV.m`). These were replaced by the on-the-fly `pattern_gen` to free ~1.7 MB of BRAM and support arbitrary resolutions; the scripts remain under `Matlab/` for reference.
### 3. Offline Mode
When the HDMI input is absent, the FPGA enters offline mode. This mode is similar to Mode #2, but the pattern is generated from the local 100 MHz oscillator instead of the recovered HDMI-Rx clock.

The offline output **pixel clock and timing are reconfigured at runtime to match the projector's EDID** (ported from the proven MimasA7-SLI design): an `MMCME2_ADV` is retuned over DRP (`drp_clkgen13`/`drp_recfg`) and the video timing generator is driven from the same curated mode table, so the FPGA drives the projector at whatever resolution/frame rate it advertises.

> **Offline output resolution ceiling — ~85 MHz pixel clock.** The supported modes
> are a curated table (`mode_table.vh`), all at or below an **85 MHz pixel-clock
> ceiling** set by what the output TMDS serializer can drive (5× serializer clock
> ≈ 425 MHz). The top mode is 1024×768@75; failsafe is 640×480@60. This ceiling is
> inherited verbatim from the Mimas A7 (a −1 50T part). The Au V2 is a faster **−2**
> grade, so its serializer/BUFG ceiling is higher (≈120 MHz pixel); the table can be
> extended above 85 MHz (e.g. 1280×1024@60, 1080p60) once validated on this board.
>
> Note: this ceiling applies to the **offline** (FPGA-generated) path only. The
> **pass-through** path locks the PC's HDMI clock with an **×15 recovery MMCM**
> (`BANDWIDTH = HIGH`, so it tracks the GPU's spread-spectrum clock and holds lock),
> giving a **~40–90 MHz** pixel-clock window. The served EDID advertises the in-window
> modes (the 800×600 and 1024×768 families); **1024×768@75 (78.67 MHz) pass-through is
> HW-validated**. 640×480@75 (31.5 MHz) sits below the ×15 lock floor and is not served.
## GPIO pin assignments
| Camera Interface  | FPGA Pins | DB9 Pins | Purpose                                         | I/O (from FPGA's POV)             |
|------------|-----------|----------|-------------------------------------------------|-----------------------------------|
| Line 1 (Cam1)    | A23     | 5        | Trigger the camera                              | Output                            |
| Line 2  (Cam1)     | A35     | 9        | Mode (1 local patterns, 0 pass-through)     | Input                             |
| Line 3  (Cam1)      | A29     | 4        | First frame of the pattern                      | Output                            |
| Line 4  (Cam1)     | A17     | 8        | Camera is ready for the next trigger            | Input                             |
| Line 1 (Cam2)    | A24     | 6        | Trigger the camera                              | Output                            |
| Line 2  (Cam2)     | A36     | 2        | Mode (1 local patterns, 0 pass-through)     | Input                             |
| Line 3  (Cam2)      | A30    | 3        | First frame of the pattern                      | Output                            |
| Line 4  (Cam2)     | A18     | 7        | Camera is ready for the next trigger            | Input                             |
| GND        | G     | 1        | Ground                                          | -                                 |




| Other  Signals |  FPGA Pins | Function                            |
|------------|----------|----------------------------------------|
|3.3V  |  V+ / DN /2.5 (Ctrl Bank)    | 3.3 V reference  |
|5V  |  R  (Ctrl Bank)   |  5 V reference |
| SW[3]          |A12     | Enable (1) / Disable(0) the Red channel        |
| SW[2]           |A11     | Enable (1) / Disable(0) the  Green channel      |
| SW[1]           |A6     | Enable (1) / Disable(0) the  Blue channel       |
|SW[0]          |A5     | 0 for vertical stripes, 1 for horizontal stripes |

## LED indicators
| LED (Index) | Indication                                                      |
|-------------|------------------------------------------------------------------|
| 7           | VSYNC                                                           |
| 6           | HSYNC                                                           |
| 5           | VYSNC Polarity (1 for postive, 0 for negative)                       |
| 4           | On if hdmi_rx_clk is detected, off for offline mode (using oscillator clock)|
| 3           | 0 for SLI pattern, 1 for desktop dispaly (black screen if in offline mode)                     |
| 2           | On if camera trigger is ready                                                   |
| 1           | On if the current frame is the first frame of the pattern                                                   |
| 0           | Trigger Output                                                       |
## Directory Structure
<pre>
├── README.md           # Overview of the repository  
├── Au2_SLI.zip   # Archive of the source Vivado 2024.1 project  
├── Bitsrteam/          # Final bitstream files, the optomaML1080.bin is working for 720p@60Hz for the specific projetcor.
├── Matlab/             # .m scripts and output files  
├── src_1/              # Source HDLand Matlab code  
└── constr_1/           #  Xlinx Design Constarint  
</pre>

## Licensing

Building an HDMI pass-through is a foundational element of this project. For this, I adapted the design by [hamsternz](https://github.com/hamsternz/Artix-7-HDMI-processing/tree/master) (MIT License).

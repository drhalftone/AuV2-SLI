# Vimba Camera FPGA-Triggered LCG Timing Guide

This document describes the camera timing and GPIO handshake required for FPGA-triggered LCG (Liquid Crystal Grating) structured light scanning with Allied Vision (Vimba) cameras.

## Overview

The FPGA controls both the projector pattern sequence and the camera triggers. The camera captures one frame per projector pattern, synchronized via GPIO lines. Two applications use this pipeline:

- **LAU3DVideoRecorder** — full 3D scanning with DFT phase computation (async callback capture)
- **LAUMultiPathRecorder** — raw frame recording for debugging/analysis (synchronous capture)

## Camera Settings

| Setting | Value | Notes |
|---------|-------|-------|
| AcquisitionMode | MultiFrame | Camera knows exact frame count |
| AcquisitionFrameCount | N (e.g. 28) | 24 depth + 4 flashing for 8-8-8 |
| TriggerMode | On | External trigger from FPGA |
| TriggerSource | Line0 | FPGA sends rising edges on Line0 |
| TriggerActivation | RisingEdge | |
| TriggerDelay | 17167 us | 1/60s + 500us (see below) |
| ExposureMode | Timed | |
| ExposureTime | 8333 us | Fixed at 1/120s in FPGA mode |
| PixelFormat | Mono12 | 12-bit LSB-aligned, no bit shifting |
| LineDebounceDuration | 10 us | Noise filter on Line0 trigger input |

## GPIO Wiring

| Camera Line | Direction | Function |
|-------------|-----------|----------|
| Line0 | Input | Frame trigger from FPGA |
| Line1 | Output | Start/stop signal to FPGA |
| Line3 | Output | FrameTriggerWait — tells FPGA camera is ready |

## GPIO Setup (setSynchronization)

```
Line0: Input, LineInverter=false
Line1: Output, LineSource=Off, LineInverter=false (starts low)
Line3: Output, LineSource=FrameTriggerWait, LineInverter=false
```

## Capture Sequence

### LAU3DVideoRecorder (Async Callbacks)

```
1. Queue all frames with FrameDoneXYZGCallback
2. AcquisitionStart for all cameras
3. Line1: false -> true  (reset FPGA counter, start patterns)
4. FrameTriggerWait on Line3 paces each trigger automatically
5. Callbacks fire as frames arrive, filling depth/color buffers
6. After all frames received:
   - AcquisitionStop
   - Line1: true -> false (stop FPGA)
```

### LAUMultiPathRecorder (Synchronous)

```
1. VmbCaptureStart
2. Queue N pFrames (nullptr callback)
3. AcquisitionStart
4. Line1: false -> true  (reset FPGA counter, start patterns)
5. VmbCaptureFrameWait for each frame, copy to depth buffer
6. AcquisitionStop
7. Line1: true -> false (stop FPGA)
8. VmbCaptureEnd + VmbCaptureQueueFlush
```

## Critical Timing Issues

### Spurious First Trigger

When Line1 transitions from low to high, PCB crosstalk between Line1 and Line0 causes a spurious trigger. The camera captures a frame of the projector's black/idle pattern.

**Solution:** A trigger delay of 17167us (one 60Hz frame + 500us margin) delays the actual exposure past the spurious trigger. By the time the camera exposes, the FPGA has advanced to the first real pattern.

### Frame Ordering

Frames must be queued BEFORE Line1 goes high. If frames are queued after the FPGA starts, the first triggers have no buffers to land in, causing missed or out-of-order frames. This was a bug in the original code where Line1/Line3 toggling happened before frame queuing.

### FrameTriggerWait Polarity

Line3 outputs the FrameTriggerWait signal with LineInverter=false. The FPGA watches this line and only sends the next trigger when the camera signals it is ready. This prevents triggers from arriving faster than the camera can read out frames, which is critical at full resolution (4112x2176).

### No Bit Shifting for Mono12

Vimba Mono12 delivers 12-bit pixel data LSB-aligned in 16-bit words (range 0-4095). The DFT filter uses signed 16-bit multiplication (`_mm_mulhi_epi16`), so values must stay within the signed range (0-32767). The old Prosilica code shifted bits left by 4 to fill 16 bits (range 0-65520), which caused values above 32767 to be misinterpreted as negative, corrupting the DFT phase computation.

**Rule:** Do NOT shift Mono12 or Mono10 pixel data. Only Mono8 data needs conversion when copied into unsigned short buffers (shift left by 4).

## Projector Frame Rate

The trigger delay and exposure are tuned for a 60Hz projector:

- Trigger delay: 17167us = 1/60s + 500us
- Exposure: 8333us = 1/120s (half the frame period to avoid capturing during pattern transitions)

If the projector runs at a different rate, scale these values accordingly:
- Trigger delay = 1/fps + 500us
- Exposure = 1/(2*fps)

## Pattern Sequences

For PatternEightEightEight with SchemeFlashingSequence:
- 24 depth frames (3 frequencies x 8 phase steps)
- 4 flashing sequence frames (for calibration)
- Total: 28 frames per scan

The vimbaFrameMapping list maps callback frame indices to interleaved channel positions in the depth and color memory objects. Positive indices go to depth, negative indices go to color.

# AuV2-SLI host — camera ↔ FPGA linearisation tool

A minimal Qt app that coordinates a **Basler USB camera** with the **Alchitry Au V2
SLI FPGA** to measure the projector response and build an **8-bit intensity
correction (linearisation) table**, then upload it to the FPGA over USB.

The FPGA renders the *linearised* sinusoid on the fly: it reads the cosine amplitude
from its pattern table and passes it through the uploaded correction table —
`out = corr[cos_sample]` (see `../ctrl/sli_lut.v`, TARGET `0x02`). The correction is
**intensity→intensity**, so it is resolution-independent; the resolution-dependent
cosine period stays in the FPGA's pattern LUT.

## What got ported

Brought over from `LAU3DVideoRecorder/LAUMultiPathRecorder` (bare minimum only):

| File | Role |
|------|------|
| `laumemoryobject.{h,cpp}` | core buffer / TIFF data type (unchanged) |
| `laubaslerusbcamera.{h,cpp}` | Basler USB capture + mean-pixel measurement (unchanged) |
| `lautonecorrectionwidget.{h,cpp}` | builds the inverse-response (tone) curve (unchanged) |

New, written for this repo:

| File | Role |
|------|------|
| `lauauboard.{h,cpp}` | **USB interface to the Au** — `0xA5` register R/W + correction-table upload (TARGET `0x02`). Protocol mirrors `../tools/uart_ctrl.ps1` / `../ctrl/uart_ctrl.v`. |
| `lauslicalibrationdialog.{h,cpp}` | wires camera → tone curve → upload; full-screen ramp window |
| `main.cpp`, `AuV2SLIHost.pro` | entry point + qmake project |

The old Mojo uploader (`laumojoboardwidget`, `laulookuptablewidget`, `qcustomplot`)
was **not** ported — it spoke a different serial protocol and baked the linearised
sinusoid into the pattern table. This design keeps the pattern cosine in the FPGA and
uploads only the 256-byte correction curve.

## Build

Prerequisites: Qt 5.15+/6 (with the **serialport** module), **libtiff**, and the
**Basler pylon** SDK. Edit the include/lib paths in `AuV2SLIHost.pro` to match your
install (the Windows block assumes `C:/usr/Tiff` and `pylon 8`).

```
qmake AuV2SLIHost.pro
make                 # or nmake / jom on Windows
```

Build without a camera (UI + board upload only): `qmake CONFIG+=nobasler`.

## Workflow

1. **Connect the board** — pick the FT2232 COM port, *Connect*. Confirms `ID=0x48`.
2. **Connect the camera** — opens the first Basler USB camera.
3. **Show ramp window** — pick the projector screen; a full-screen gray field appears.
4. **Run linearisation sweep** — for gray 0…255 the app shows the level, waits the
   *settle/display latency*, grabs one frame, and records the camera's mean pixel,
   producing the inverse-response curve in the tone widget.
5. **Upload correction to FPGA** — sends the 256-byte table (`A5 5B 02 …`). The Au
   replies `K`; the projected fringes are now linearised.

`Reset correction (identity)` restores `corr[i]=i` (no linearisation). `Save/Load
(.tcc)` persist a curve.

## Notes / to verify on hardware

- **Trigger topology is hardware-dependent.** With *HDMI/line trigger* checked the
  camera waits for its Line1 trigger (projector/Au VSYNC); unchecked, the camera
  drives its own output line. Match this to how the Basler trigger lines are wired to
  the Au `cam_pace` GPIO, and tune the *settle/display latency* to the projector lag.
- The camera config in `laubaslerusbcamera.cpp` (ROI, binning, 10-bit mono, exposure)
  is inherited unchanged from the source project — adjust for your sensor/projector.
- The FPGA `lut→corr` read path is two cascaded async RAM reads; confirm it closes
  timing in synthesis (noted in `../ctrl/sli_lut.v`).

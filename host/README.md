# AuV2-SLI host tools

Two things live here, and they speak to the board over **different transports**.

## 1. Python tools over the Ft+ (USB 3) — the current interface

Everything the host does with the merged design goes through
[`ftlink.py`](ftlink.py), which implements the `0xA5` control plane and the camera
opcodes over D3XX. Register and opcode reference: [`../FTPLUS_API.md`](../FTPLUS_API.md).

| Tool | What it does |
|---|---|
| `ftlink.py` | The transport. `send_bytes` (0xA5 protocol), `send_word` (camera opcodes), register and table read/write |
| `usb_speed.py` | **Run this first when anything looks slow.** Reports the enumerated USB speed *and* measured throughput — a USB 2 cable in a USB 3 port looks identical from the outside and costs 4x |
| `max_exposure.py` | Asks the FPGA the longest exposure usable at the current frame rate, and optionally applies it |
| `measure_vsync_period.py` | Display frame period and its jitter, at 10 ns (genlock G0) |
| `measure_trigger_latency.py` | Trigger to exposure-start delay and its jitter, from the sensor's own monitor pin |
| `measure_exposure_gap.py` | Sweeps exposure to find the constant inter-exposure gap and the readout floor |
| `test_m6c_silicon.py` | The full control-plane test over D3XX — registers, all three tables, EDID |
| `test_m6a_ctlpath.py`, `test_m6a_under_load.py`, `test_m6b_reply.py` | Control-plane bring-up, command and reply directions |
| `test_3b_cameraidle.py` | M3/3b — idle the camera, prove HDMI does not notice |
| `soak.py`, `stress_attack.py` | Long-run and adversarial tests |
| `read_cam_status.py`, `dump_edid.py`, `read_mode.py`, `force_mode.py`, `upload_corr.py` | Point tools over the serial port |

### Why several of these read over the serial port on purpose

Port A is not legacy. It is the **independent witness**: a test that measures the
camera over the same link whose traffic it is disrupting cannot distinguish the two.
This matters concretely — the Ft+ reply path currently goes silent whenever the
frame stream stops, so anything that wedges or idles the camera also stops USB 3
readback. See the known limitation in [`../FTPLUS_API.md`](../FTPLUS_API.md).

## 2. The Qt linearisation app — NOT yet migrated

A minimal Qt app that coordinates a **Basler USB camera** with the SLI FPGA to
measure the projector response, build an **8-bit intensity correction
(linearisation) table**, and upload it over USB.

> **It still speaks `QSerialPort` at 115200 baud.** Moving it to D3XX is the
> outstanding half of merge milestone M7 — the soak half passed, the host migration
> did not start. Everything below describes the app as it stands.

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

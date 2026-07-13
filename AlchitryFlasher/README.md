# Alchitry Au Flasher

A small Windows GUI that **downloads an FPGA bitstream from GitHub and flashes it
onto an Alchitry Au V2 board over USB** — and, if needed, installs the Alchitry
loader for you first. It is a friendly front-end around Alchitry's own
command-line loader; you click through four numbered steps instead of typing
commands.

> ### 📌 What this flashes
> This is the end-user installer for **this** repo's bitstream,
> [`../Bitstream/Au2_SLI.bin`](../Bitstream) — so a non-developer can program an
> Alchitry Au V2 without Vivado or the command line.
>
> It also serves as the **reference model** for a future one-click flasher for the
> **Numato Mimas A7**: the whole framework carries over and only the *loader
> backend* changes. See
> **[Adapting this to the Mimas A7](#adapting-this-to-the-mimas-a7)** at the bottom.

---

## Files in this folder

| File | What it is |
|------|------------|
| `AlchitryFlasher.cmd` | **Double-click this to start.** A tiny launcher that runs the PowerShell script with the right options (STA mode, execution-policy bypass, hidden console). |
| `AlchitryFlasher.ps1` | The actual application (Windows PowerShell + WinForms). |
| `AlchitryFlasher.README.md` | This document. |

Keep the `.cmd` and `.ps1` together in the same folder — the launcher looks for
the script next to itself. You can move the pair anywhere.

> **First launch:** Windows SmartScreen may warn *"Windows protected your PC"*
> because the script isn't code-signed. Click **More info → Run anyway**.

---

## What it does, in four steps

The window is laid out as a top-to-bottom checklist:

1. **Step 1 — Get the loader (one-time setup).** Installs Alchitry Labs V2 (which
   contains the loader) or points at an existing copy.
2. **Step 2 — Plug the board in, then detect it.** Confirms the board is connected
   and the USB driver works.
3. **Step 3 — Choose what to flash.** Pick the bitstream, board type, and whether
   to write to flash (permanent) or RAM (temporary).
4. **Step 4 — Flash the board.** Downloads, verifies, and programs.

Every control has a **(?)** button that opens a detailed local help page.

---

## The bitstream (`.bin`) we are loading

A **bitstream** is the compiled configuration for the FPGA — the binary that
defines the actual digital circuit the chip becomes when powered. These come from
the **[drhalftone/AuV2-SLI](https://github.com/drhalftone/AuV2-SLI)** repository.

### What the design does

`AuV2-SLI` is a **structured-light illumination (SLI) system** — a 3-D scanning
setup. An Alchitry Au V2 (Artix-7 FPGA) drives a DLP projector and synchronizes it
with camera modules so that patterned light can be projected and captured frame by
frame. The phase images the cameras capture are sent to a host PC for 3-D
reconstruction. In brief, the FPGA can:

- Pass HDMI video from a host PC through to a projector, emitting an edge-paced camera
  trigger (one trigger per camera FrameTriggerWait rising edge).
- Replace the incoming video with **locally generated SLI patterns** that vary in
  spatial and temporal frequency (driven by lookup tables on the FPGA).
- Fall back to an **offline mode** using the board's own oscillator when no HDMI input
  is present. The output timing is **EDID-driven**: the FPGA reads the connected
  display's EDID, picks the best mode it can generate (highest refresh, then highest
  pixel count), and retunes its pixel clock over DRP to match. The offline path is
  capped by what the output TMDS serializer can drive (~85 MHz pixel clock), so the
  top offline mode is **1024×768@75**, with **640×480@60** as the failsafe.
- Report status over its USB serial port (COM), and answer host commands — including
  reading back the connected display's **raw EDID** and **which mode it chose** from it.
- Talk to the cameras over GPIO using the Vimba trigger/ready protocol (trigger out,
  start/stop in, FrameTriggerWait in; + a debug first-frame marker).

### The bitstream

| File | Use it when |
|------|-------------|
| **`Au2_SLI.bin`** | The full SLI design (Bank-B remap for the LauCameraTrigger stack board; runs an idle LED slider when nothing is connected; USB control protocol + display EDID read-back). |

### Integrity

Each `.bin` is checked against a known **SHA-256** hash after download. A mismatch
**aborts the flash**, so a corrupted or tampered file never reaches the board:

```
Au2_SLI.bin  10AC984610979D1B3B0B9EF0B37AA9C4BE9477024E648EAABE8C75221A29ACF0
```

---

## The tools we are using

### Alchitry Labs V2 / `Alchitry.exe` (the loader)

The board is programmed by **`Alchitry.exe`**, the command-line tool that ships
inside **[Alchitry Labs V2](https://alchitry.com/alchitry-labs/)**. PowerShell
cannot speak the board's USB programming protocol directly, so this is the engine
that does the real work. The GUI simply runs:

```
Alchitry.exe load --bin "<file>" --board <AuV2|Au+|Au|Cu> [--flash]
```

The Flasher installs Alchitry Labs V2 from the official GitHub release
([alchitry/Alchitry-Labs-V2](https://github.com/alchitry/Alchitry-Labs-V2)). Two
install methods are offered in Step 1:

| Method | Size | Admin? | Notes |
|--------|------|--------|-------|
| **Portable ZIP** *(default)* | ~418 MB | No | Bundles its own Java runtime. Extracted under your user profile. No Start-menu entry, no driver setup. |
| **Installer EXE** | ~0.7 MB | Yes | A small web-installer that does a normal Start-menu install and can set up the USB driver. Prompts for administrator. |

> **Note:** the older standalone `alchitry-loader` does **not** support the Au V2,
> which is why this tool uses Alchitry Labs V2's `Alchitry.exe` instead.

### FTDI USB interface

The Alchitry Au talks to the PC through an **FTDI FT2232H USB chip** (USB ID
`0403:6010`). Windows enumerates it and installs its own **FTDI driver**
automatically (it shows up as *"USB Serial Converter A/B"* and a COM port). On
Windows that **stock FTDI driver is the correct one** — Alchitry Labs V2's loader
talks to the Au over FTDI's **D2XX** interface.

> ⚠️ **Do not "fix" the driver with Zadig.** Replacing the FTDI driver with
> **WinUSB or libusbK** (the usual advice for libusb-based tools) **breaks** Au V2
> detection — the loader then reports *"No devices detected."* If you've already done
> it, revert: Device Manager → the **Interface 0** device → *Uninstall device*
> (✓ delete the driver software) → **Action → Scan for hardware changes**.

#### "No devices detected" but the board is plugged in

When **Detect Boards** finds nothing, the tool checks whether the FT2232H is on the USB
bus at all and tells you which problem you have:

- **Board present on USB, not recognized** → almost always an **outdated loader**. The Au V2
  needs **Alchitry Labs 2.0.52+**; older builds (e.g. a `2.0.0-ALPHA` whose `--board` only
  lists `Au/Au+/Cu`) don't know the Au V2 and find nothing *on any driver*. Fix it in **Step 1**:
  choose **Portable ZIP** → **Install** (gets 2.0.52), then Detect again.
- **Board not on the bus** → a cable (must be a **data** cable, not charge-only), USB port/hub,
  or power problem.

> This replaces an earlier "Fix USB driver" button that swapped the driver to libusbK — that
> was a misdiagnosis (it actually *prevents* detection on the Au V2) and has been removed.

### Board type (`--board`)

The Au V2 hardware programs as **`AuV2`** — that is the default in Step 3.
(Verified on loader tag **2.0.52**: `load --list` reports "Alchitry Au V2", and
`--board Au+` fails with *"No board of type Alchitry Au+ found!"*.) The box is
editable; use **Detect Boards** to confirm what your loader version reports.

### Flash vs RAM

| Mode | Flag | Behaviour |
|------|------|-----------|
| **Flash (persistent)** | `--flash` | Writes to configuration flash. Survives power cycles; the board boots this design every time. Slower. |
| **RAM (temporary)** | `--ram` | Loads straight into the FPGA. Fast, ideal for testing, but lost on power-off. (Note: loader 2.0.52 needs the explicit `--ram` flag — omitting both flags loads nothing.) |

---

## How the script works under the hood

- **Downloading.** Files stream to disk with a live byte-percentage progress bar,
  so the window stays responsive during large transfers.
- **Verifying (SHA-256).** Bitstreams are checked against the hashes above;
  downloaded tools are checked against **GitHub's published `digest`** for the
  release asset. A bad hash aborts the operation.
- **Caching.** Everything is cached so repeat runs are fast:
  - The 418 MB tool archive is kept and hash-verified — re-installing won't
    re-download it.
  - Extracted tools are cached **by version tag**; a completed install writes a
    `.installed` marker so a half-finished extraction is never trusted.
  - Bitstreams are reused if a cached copy still passes its hash check.
- **Extraction.** The tool archive is unzipped entry-by-entry while pumping the UI,
  with the progress bar tracking extraction — no more "Not Responding" window.
- **Loading.** The loader runs as a child process; its output is streamed live into
  the black activity-log pane.

### Where files are stored

Everything lives under your user profile (no admin needed):

```
%LOCALAPPDATA%\AlchitryFlasher\
├── downloads\   bitstreams + transient loader output
├── cache\       downloaded tool archives (zip / installer exe)
├── tools\<tag>\ extracted Alchitry Labs, keyed by version (+ .installed marker)
└── help\        the (?) HTML help pages
```

To reset everything (force fresh downloads), click **Uninstall / clean up** at the
top-right of the activity log — it reports how much space it will free, asks you to
confirm, then deletes the whole `%LOCALAPPDATA%\AlchitryFlasher` tree (downloads,
cache, the extracted portable Alchitry Labs, and the help pages). You can also just
delete the folder by hand.

> **Note:** Uninstall / clean up does **not** remove an Alchitry Labs that you
> installed with the **Installer EXE** method — that is a normal system install;
> remove it from Windows **Settings → Apps → Installed apps**.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Window says **"Not Responding"** during install | Older builds froze while unzipping. The current script extracts with a live progress bar; if it ever appears stuck, give it a minute — large extraction is still work in progress. |
| **Detect Boards** finds nothing | If the board is plugged in, it's almost always an **outdated loader** — the Au V2 needs Labs **2.0.52+**. Install it via Step 1 → Portable ZIP, then Detect. Keep the **stock FTDI driver** (don't Zadig it). If the chip isn't on the bus at all, it's a cable/port/power problem. |
| Loader rejects the board name | The Au V2 uses `AuV2` (verified on 2.0.52); `Au+`/`Au`/`Cu` are for older boards. Run **Detect Boards** to see the exact name your version expects. |
| **SmartScreen** blocks the launcher | **More info → Run anyway** (the script is unsigned). |
| Flash succeeds but design doesn't persist | You used **RAM (temporary)** — re-flash with **Flash (persistent)** selected. |
| Want to start clean | Click **Uninstall / clean up** (top-right of the log), or delete `%LOCALAPPDATA%\AlchitryFlasher`. |

---

## Credits & links

- **Bitstream / SLI design:** [drhalftone/AuV2-SLI](https://github.com/drhalftone/AuV2-SLI)
  (the HDMI pass-through is adapted from
  [hamsternz/Artix-7-HDMI-processing](https://github.com/hamsternz/Artix-7-HDMI-processing), MIT).
- **Loader / IDE:** [Alchitry Labs V2](https://alchitry.com/alchitry-labs/) ·
  [alchitry/Alchitry-Labs-V2](https://github.com/alchitry/Alchitry-Labs-V2)
- **Hardware:** [Alchitry Au](https://alchitry.com/) (Xilinx Artix-7 FPGA)

---

## Adapting this to the Mimas A7

This tool is the template for a future **MimasA7 Flasher**. The architecture is
deliberately backend-agnostic: download → verify (SHA-256) → cache → run a loader.
Reusing it for the Numato Mimas A7 mostly means swapping out **Step 1** (which tool
gets installed) and **Step 4** (the command that programs the board).

### What stays the same
- The whole **GUI shell**: the four-step layout, `(?)` help pages, the streaming
  activity log, and the busy/disable handling.
- **Downloading** with a live progress bar (`Get-FileWithProgress`).
- **SHA-256 verification** (`Test-Sha256`) and **caching** (`Get-CachedFile`,
  the `%LOCALAPPDATA%` store, the `.installed` marker).
- **Live process output streaming** (`Invoke-Loader`).

### What has to change

| Piece | Alchitry Au (this tool) | Mimas A7 (to build) |
|-------|-------------------------|---------------------|
| Bitstream source | `drhalftone/AuV2-SLI` raw `.bin` | `drhalftone/MimasA7_SLI` → `Bitstream/MimasA7_SLI.bin` |
| Bitstream SHA-256 | hard-coded in `$BinChoices` | recompute for `MimasA7_SLI.bin` |
| Loader tool | `Alchitry.exe` (Alchitry Labs V2) | **see options below** |
| Programming command | `load --bin <f> --board AuV2 --flash` | depends on the chosen loader |
| Board / device flags | `--board`, `--device` | board profile / cable for the chosen loader |
| USB driver | FTDI (via Alchitry installer) | FTDI FT2232H on the Mimas (driver setup likely needed) |

### Candidate loader backends for the Mimas A7

The Mimas A7 does **not** use `Alchitry.exe`. Pick one backend and wrap it the same
way `Invoke-Loader` wraps the Alchitry CLI:

1. **openFPGALoader** *(recommended for a lightweight installer)* — a small,
   open-source, cross-platform tool that programs many FTDI-based boards. A
   command roughly like `openFPGALoader -b <board> -f Bitstream/MimasA7_SLI.bin`
   writes the SPI flash. No Vivado, no multi-GB install — closest in spirit to
   what this tool does for the Au.
2. **Vivado batch (hw_server / JTAG)** — this repo already ships
   `program_jtag.tcl`; a flasher could call
   `vivado -mode batch -source program_jtag.tcl`. Most faithful to the existing
   build flow, but requires a full Vivado install (very large), so it's a poor fit
   for a "one-click for non-developers" tool.
3. **Numato's configuration utility / DFU** — Numato's own flashing path over the
   board's USB. Viable but less scriptable/portable than openFPGALoader.

### Suggested next steps
1. Confirm the Mimas A7 enumerates over USB and which backend can program it
   headlessly (try **openFPGALoader** first).
2. Copy this folder, rename to `MimasA7Flasher`, and:
   - point `$BinChoices` at `MimasA7_SLI.bin` + its real SHA-256,
   - replace `Install-AlchitryLabs` with an installer/locator for the chosen loader,
   - replace the `Invoke-Loader` argument string in the Step 4 handler with that
     loader's flash command,
   - update Step 3's board control (or drop it) to match the loader.
3. Keep the verification + caching code unchanged — it is board-independent.

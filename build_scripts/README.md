# build_scripts/

Historical Vivado build scripts, rescued from the (gitignored) `build/` directory
before it was deleted. They were the only copies in existence.

**These are not the current build.** The current builds are repo-root TCL scripts
that read straight from the git-tracked RTL and XDC, with no Vivado project and no
zip:

| Script | Part | Contents |
|---|---|---|
| [`build_merged.tcl`](../build_merged.tcl) | XC7A100T (Pt V2) | **The current design** — HDMI/SLI + camera + Ft+ in one bitstream |
| [`build.tcl`](../build.tcl) | XC7A35T (Au V2) | The original HDMI/SLI-only design |
| [`build_pt.tcl`](../build_pt.tcl) | XC7A100T | **Stale** — predates M2, no longer reads `cam_frame_ft.v` |

```
vivado -mode batch -source build_merged.tcl
```

Everything below predates that. They are **project-mode** scripts: they operate on
an unzipped Vivado project in `build/Au2_SLI` (regenerated from `Au2_SLI.zip`), so
they will not run as-is against a fresh checkout — `build/` no longer exists. They
are kept for reference: each documents how a particular experiment was built, and
several carry design notes in their headers.

| script | what it built |
|---|---|
| `build_stackb.tcl`  | Functional SLI build with Camera-1 + config switches remapped to Bank B (LauCameraTrigger stack board). Superseded — Bank B is now active in the tracked `Au2.xdc`, so root `build.tcl` produces this. |
| `build_pat.tcl`     | Offline pattern-generator build. |
| `build_outclk.tcl`  | Output-clock / EDID-auto experiments (Phase D). |
| `build_drpA.tcl`    | DRP clock-reconfiguration bring-up. |
| `build_uart.tcl`    | UART/telemetry build. |
| `build_port1.tcl`, `build_port2.tcl`, `build_port2b.tcl` | Port/pin-mapping experiments. |
| `elab_check.tcl`    | Elaboration-only syntax check (fast, no synthesis). |

# build_scripts/

Historical Vivado build scripts, rescued from the (gitignored) `build/` directory
before it was deleted. They were the only copies in existence.

**These are not the current build.** The current, reproducible build is the
repo-root [`build.tcl`](../build.tcl): it builds straight from the git-tracked
RTL and XDC, with no Vivado project and no zip:

```
vivado -mode batch -source build.tcl
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

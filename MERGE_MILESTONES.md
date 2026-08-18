# Merge milestones — HDMI + camera on one Pt V2

_Drafted 2026-08-17._ Target: one bitstream where the HDMI/SLI datapath and the camera
datapath run **independently**, all bulk data and control on the Ft+, diagnostics on the Pt.

Plan: [`PC_INTERFACE_PLAN.md`](PC_INTERFACE_PLAN.md) · Inventory: [`PC_INTERFACE_INVENTORY.md`](PC_INTERFACE_INVENTORY.md)

## How these milestones are written

Every milestone has a **proof that cannot be faked by a broken system**. That rule is not
decoration — this project keeps producing failures that look exactly like success:

- frames that were clean, fresh, and *every one at a different offset*
- a sensor triggering at a perfect 120.000 Hz while delivering **no pixels at all**
- a link measuring 192 fps with zero drops while silently corrupting half the data bus
- a status UART reporting healthy 120 Hz while the delivered rate had halved

So "it builds", "it looks right", and "the counter says so" are never pass criteria. Each
milestone names the measurement and the number it must produce.

---

## M0 — RESULT: PASS (2026-08-17)

Answered by accounting the two existing builds rather than building a skeleton.

**Clocking — 5 of 6 MMCM, one spare.**

| MMCM instance | Source | Purpose |
|---|---|---|
| `hdmi_MMCME2_BASE_inst` | `hdmi_input.vhd:248` | HDMI RX recovered clock |
| `MMCME2_ADV` | `drp_clkgen13.v:38` | reconfigurable offline output clock |
| `ref_clk_pll` | IP | 200 MHz IDELAY reference |
| `i_cam_mmcm` | `Au2_SLI.vhd:952` | camera 72 MHz `cam_clk_pll` |
| MIG internal | IP | DDR3 (+ the design's only PLLE2) |

Summing the two builds naively gives 7 MMCM against a budget of 6, but that
double-counts: `cam_frame_ft`'s `u_mmcm` produces only 200 MHz and a buffered
100 MHz, and `ref_clk_pll` already generates the 200 MHz reference, so it
collapses. Also fine: BUFG ~21/32, IDELAYCTRL 3/6, BUFR 2/24, BUFIO 2/24.

**Area — comfortable.** LUTs ~14k/63,400 (22%), FF ~12.9k/126,800, BRAM 3.5/135,
IOB ~155/285 (34 pins shared between the designs are the same signals).

**BUFR clock region — clear.** This was the real risk: `wordclk` is on a REGIONAL
buffer, so the camera cfifo is confined to that region's ~600 LUTRAM-capable
slices, and it already had to drop from `AW=10` (688 RAM64M) to `AW=9` (344).
`Au2_SLI` adds 376 LUT-as-memory — but all of it is the pattern-generation IPs
(`LUT`/`LUT_V`/`indexMap`/`indexMapV`) in the PIXEL domain. Its camera line
buffer is BRAM, not LUTRAM: `cam_line_buf.v:51` carries `ram_style = "block"`
with a SYNCHRONOUS read, so inference actually works there. Nothing of
`Au2_SLI`'s competes for the camera's region.

> Worth noting the contrast, because it confirms the LUTRAM finding from the
> opposite direction: the same `ram_style = "block"` attribute works on
> `cam_line_buf` and is inert on `cam_async_fifo`. The only difference is the
> registered read. That is also the fix path if the camera FIFO ever needs depth.

### M0 also corrected the plan

**`Au2_SLI` instantiates the wrong LVDS receiver for silicon.** Its Phase-2 chain
uses `cam_lvds_rx` — documented at `Au2_SLI.vhd:495` as "BUFIO + BUFR /5 — no
IDELAY / clk200 needed" and "proven bit-exact in **tb_cam_decode**", a
SIMULATION testbench. The standalone camera build uses `cam_lvds_rx_idelay` +
`cam_eye_scan`, and that is the one proven on hardware: at 720 Mbps IDELAYE2
eye-centring is REQUIRED, and without it an isolated bit drops in a way that
looks exactly like bad solder.

So work item 5 in the plan — "keep `Au2_SLI`'s receiver, already integrated" — is
**wrong**. The merged design must keep the standalone camera's IDELAY receiver.
`Au2_SLI` already provides `clk200`, so this costs no extra clocking.

**Still unproven:** these are static counts. A real place-and-route confirms them,
which M1 delivers as a side effect. Nothing here needs a skeleton build of its own.

---

## M1 — RESULT: PASS (2026-08-17)

The merged build vehicle: `build_merged.tcl` + `constrs_1/imports/RTL/pt_ftplus_merged.xdc`,
top still `Au2_SLI`, Ft+ pins present and held at a safe idle, camera idle.

**Hardware proof, on the Pt with Hd V2 stacked:**

```
telemetry present, N = 0033/0034 counting        ok
read ID reg 0x00 == 0x48                         ok
15 passed, 0 failed          (test_silicon.py)
mode_idx 2 (1024x768@75), edid_ok False          ok -- correct failsafe, no display
```

**Build proof:**

| | M0 predicted | M1 measured |
|---|---|---|
| Bonded IOB | ~109 (65 HDMI + 44 Ft+) | **109** |
| MMCM | 4 here, 5 with MIG at M2 | **4 of 6** |
| BUFG | — | 14 of 32 (`ft_clk` now a real domain) |
| Timing | — | **WNS +1.339, WHS +0.064, 0 failing of 12,743** |

### The first M1 build was green and proved nothing

Tristating the Ft+ bus and leaving its inputs unread is correct, but the optimiser
then TRIMS THE PORTS. The first build placed **5 of 44** Ft+ pins — bonded IOB
65 → 70, not ~109 — and every `ft_data[*]` came back as "unconnected or has no
load". Nothing failed: timing was clean, `No ports matched` was zero, the build
reported success. The only visible symptom was the IOB count.

A place-and-route that never places the pins cannot say the merged pinout is
sound, which was half the reason to build M1 at all. Fixed with a **pin-liveness
probe**: the Ft+ inputs are reduced to one word, registered in the `ft_clk` domain
and held by `DONT_TOUCH`. Output side stays tristated, so nothing is driven at the
FT601. Rebuild: 109 IOB, zero trim warnings.

> Same shape as the failures this project keeps producing — a green result that is
> not measuring what you think. Worth remembering for M2: **an idle interface can
> optimise away, and an interface that optimised away is not an interface you have
> tested.**

**Also confirmed:** the `build_pt.tcl` `GENERATE_SYNTH_CHECKPOINT` guard is load-bearing.
This build hit the same Vivado 2025.2.1 error on THREE IPs (`indexMap`, `indexMapV`,
`ref_clk`) rather than one; unguarded it would have killed the build after all the
IP work was already done.

**Not yet proven at M1:** the MIG is not instantiated, so M0's 5-of-6 MMCM
prediction is only partly exercised, and the Ft+ OUTPUT path has no driver so its
`set_output_delay` constraints have no paths to check. Both land at M2.

---

## Milestones

| # | Milestone | Proof (all must hold) | Effort | Risk |
|---|---|---|---|---|
| **M0** | **Clocking + resource feasibility** | A skeleton top instantiating *both* clock trees + MIG places & routes. CMT count ≤ 6. The `BUFR` clock region still has room for the camera cfifo at its current depth. No function required. | S | **high** |
| **M1** | **Merged top builds; HDMI alive, camera idle** | HDMI telemetry `N` counting VSYNC edges. `test_silicon.py` 15/15. Offline mode selects correctly. Camera held in reset. | M | med |
| **M2** | **Camera alive in the merged top** | With HDMI running concurrently: `ring_check` clean + fresh + `ldrop` static, `row_align_check` 10/10 at zero shift, capture locked at 120.000 Hz (±2 cycles), `cfifo_ovf = 0`. | L | **high** |
| **M3** | **Independence proven** | The four-way test below. This is the actual requirement, and it is the milestone most likely to be skipped. | M | **high** |
| **M4** | **Unified diagnostics on Port A** | One ASCII line carries both subsystems. The 1 Mbaud 32-hex stream is gone. **A deliberately wedged camera still reports its state on Port A.** | S | low |
| **M5** | **Control replies on Port B** | Register read over FT601 returns `ID = 0x48`. Frames stay **byte-exact** while control traffic runs flat out — replies and frames demux without either corrupting the other. | M | med |
| **M6** | **Full control plane on Port B** | `test_silicon.py` equivalent 15/15 over D3XX: register round-trips, 720/1280/256-byte table uploads reading back identical, EDID readback. Port A is now TX-only. | M | med |
| **M7** | **Host migration + soak** | Qt app and all tools on D3XX. ≥30 min continuous: `ldrop` static, no HDMI mode changes, no `cfifo_ovf`, frame rate flat. | L | med |

S ≈ hours · M ≈ a day · L ≈ multiple days

---

## M3 — what "independent" has to mean, testably

"Both run at once" is not independence. Independence is that **neither subsystem's failure
changes the other's behaviour.** Four directed tests, all must pass:

| Test | Action | HDMI must | Camera must |
|---|---|---|---|
| 3a | Unplug the HDMI source mid-capture | fall back to offline mode | keep delivering, `ldrop` static |
| 3b | Unplug/idle the camera, or hold it in reset | keep VSYNC counting, mode unchanged | — |
| 3c | **Wedge the camera** (exposure > 8300 µs) | keep VSYNC counting, mode unchanged | stop — and say so on Port A |
| 3d | Force an HDMI mode change (`MODEFORCE`) | switch modes cleanly | **`ldrop` must not move** |

3c and 3d are the ones that matter. 3c uses a failure we can reproduce on demand — over-exposing
wedges the sensor and needs an FPGA reconfigure to clear — and asks whether HDMI notices.
3d asks the reverse: whether re-timing the display disturbs a running capture. A shared
reset, a shared MMCM, or MIG bandwidth contention would show up in exactly these two.

---

## What invalidates the approach

Stop and re-plan, rather than push through, if:

| Condition | Found at | Why it is fatal |
|---|---|---|
| CMTs exceed 6, or the `BUFR` region cannot hold the cfifo | M0 | The camera's `wordclk` is on a **regional** buffer; its FIFO is already at `AW=9` because that region has ~600 LUTRAM-capable slices and the FIFO wanted 688. HDMI logic landing there has nowhere to go. |
| MIG bandwidth cannot carry capture + HDMI concurrently | M2 | Writer 360 MB/s burst + reader 196 MB/s already uses a real share of 1600 MB/s peak. If HDMI also needs DDR, re-budget before building. |
| `ldrop` moves during M3d | M3 | Means the two datapaths are coupled through the memory controller or a clock. Independence is the requirement, not a nice-to-have. |
| Control replies corrupt frames at M5 | M5 | Byte-exactness is not negotiable — this project has already shipped a build that streamed at full rate while corrupting half the bus. |

---

## Sequencing notes

- **M0 before anything.** Cheapest milestone, highest information, and the only one that can
  invalidate the whole design. Do not build M1 on an unverified clock budget.
- **M1–M3 are the merge.** M4–M7 are the transport move and can be deferred: an interim
  merged build can keep control on Port A and still pass M1–M3.
- **M4 pairs with M2/M3.** Unified diagnostics is how M2 and M3 get debugged — bring it
  forward if the camera side proves stubborn.
- **Keep the standalone builds building throughout.** They are the recovery path when the
  merged design's single control link is down, and the reference when a merged result looks
  wrong.

## Carried-forward rules

| Rule | Why |
|---|---|
| Exposure clamped ≤ 8280 µs at 120 Hz | Past it the rate halves; at 8300 µs delivery stops and the sensor wedges until reconfigured. |
| Camera status never lives only on Port B | Port B goes silent in exactly the failure you need to diagnose. |
| Payload format read from `h[6]`, never assumed | Misreading it paints a plausible wrong picture rather than failing. |

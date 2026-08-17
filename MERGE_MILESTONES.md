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

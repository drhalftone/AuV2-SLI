# Genlock milestones — camera exposure locked to the projected frame

_Drafted 2026-08-24._ Target: one exposure per projected frame, at a rate the user selects
from what the display offers, with a programmable delay from the outgoing vsync to the
exposure — and a camera that keeps running when the display goes away.

Predecessor: [`MERGE_MILESTONES.md`](MERGE_MILESTONES.md) · Roadmap context: [`ROADMAP.md`](ROADMAP.md) §6.5

## How these milestones are written

Same rule as the merge campaign: every milestone has a **proof that cannot be faked by a
broken system**. This project keeps producing failures that look exactly like success —
a sensor triggering at a perfect 120.000 Hz while delivering no pixels at all, a link
measuring 192 fps with zero drops while corrupting half the data bus, a reply packet with a
correct header carrying the wrong bytes. "It builds", "it looks right" and "the counter says
so" are never pass criteria.

---

## What changed since the roadmap said this was blocked

`ROADMAP.md` §6.5 records the blocker as physical:

> **THE ACTUAL BLOCKER: vsync is not on this FPGA.** There is no HDMI in the camera design
> at all [...] the camera stack runs on the **Pt V2**; the HDMI passthrough work is on the
> **Au V2**, a separate physical board. So before any of the above matters, a sync signal has
> to physically reach the Pt V2 — over the DF40 stacking connector or a flying lead.

**The merge campaign dissolved that.** M1–M6 put both datapaths in one bitstream. In the
current top, `in_vsync`, `out_vsync`, `vsync` and `local_vsync` are internal signals in the
same architecture that instantiates `i_cam_frame_ft`. This is an internal port, not a pin.
No wire, no board revision, no DF40 pass-through decision.

Two things already exist and must be reused rather than rebuilt:

| Already there | Where | Note |
|---|---|---|
| `out_vsync`, the vsync sent to the projector | produced by `pixel_pipe`, feeds `hdmi_io` as `vga_vsync` | this is the master clock for genlock |
| a vsync-paced trigger generator | `pixel_pipe.v`, drives `trig` for the DB9 cameras | **carries a known defect — see G1** |
| mode selection from EDID | `mode_select`, reported at regs `0x20`–`0x2A` | `0x21 REFR` is the chosen refresh rate |
| user override of the mode | `MODEFORCE`, reg `0x14` | this *is* "the user picks the rate" |
| a way to stop the camera on demand | opcode 6 (M3b hook, 2026-08-24) | fault injection for G4 |
| a way to drop the HDMI link on demand | `LINKCTL`, reg `0x15`, self-timed | fault injection for G4 |

`pixel_pipe.v` even carries the comment `in_vsync => vsync_Pos, -- for postion tracking and
camera control`. The intent predates the merge; what was missing was the two subsystems
being on the same chip.

---

## Decisions already taken

**Lock to `out_vsync` — what is SENT to the projector, not what arrives.** The projector is
the thing being synchronised to. `in_vsync` is the source's timing and is irrelevant to when
a pattern is actually on the screen.

**1:1 — one exposure per projected frame.** No divider, no multiplier. The user picks the
frame rate from what the display supports; the camera follows.

**120 Hz is the ceiling.** This FPGA will never drive the projector faster. Two consequences
fall out and both are load-bearing:

- **The fixed exposure clamp stays valid.** The ≤ 8280 µs clamp is tied to an 8.33 ms period.
  It only needed to become dynamic to survive rates *above* 120 Hz, where a commanded
  8280 µs would exceed the whole frame. Below 120 Hz the period only grows, so a constant
  clamp is safe at every lockable rate. **Do not "improve" this into a live computation** —
  the failure mode it guards is a sensor that wedges until reconfigured.
- **The free-run fallback rate equals the maximum locked rate.** `TRIG_CY = 833333` is already
  120.000 Hz, so losing sync can never speed the camera into a region it cannot sustain.
  This is a safety property obtained for free. Preserve it.

**Cross into `clk100` as early as possible.** `out_vsync` lives in the pixel-clock domain,
whose frequency *changes with the video mode*. A delay counter there would need recomputing
for every mode. Crossing to the fixed 100 MHz domain first makes a delay expressed in
microseconds the same constant at every mode, at 10 ns quantisation — nothing against an
8.33 ms frame. Keep the pixel-clock side to edge detection and nothing else.

---

## Milestones

| # | Milestone | Proof (all must hold) | Effort | Risk |
|---|---|---|---|---|
| **G0** | **Pick the edge, prove it is trustworthy** | Measured rate and edge-to-edge jitter over ≥ 30 s, reported over the control plane, for `out_vsync` **in both offline and passthrough modes**. Decision recorded *with the numbers*. No RTL function. | S | **high** |
| **G1** | **Sync reaches the camera; nothing changes** | `cam_frame_ft` gains `ext_sync` plus an edge counter readable over the control plane. Camera still free-runs at 120.000 Hz, every M2 metric unchanged, WNS ≥ 0. **Includes the `pixel_pipe` `rdy_cnt` fix.** | S | low |
| **G2** | **1:1 lock across the usable range** | Frame period tracks vsync at every mode in the table, not `TRIG_CY`. One exposure per projected frame — none missed, none doubled, verified by count over ≥ 10⁴ frames. `ldrop` static, `cfifo_ovf = 0`, frames byte-exact. Exposure clamp verified at the slowest and fastest modes. Build-time assertion that no mode in the table exceeds 120 Hz. | M | **high** |
| **G3** | **Programmable delay** | A register sets the vsync→exposure delay in µs. Measured shift matches commanded across the **full range including delays greater than one frame period**. Range ≥ 0–50 ms. | M | med |
| **G4** | **Graceful degradation** | The four cases below. This is the milestone most likely to be skipped and the one that matters. | M | **high** |
| **G5** | **Lock quality measured, not assumed** | vsync→`frame_start` phase error sampled over ≥ 30 min; distribution reported; no drift. | S | med |
| **G6** | **Regression + soak, genlock on** | M3a–M3d and the M7 soak re-run locked. See the M3d restatement below. | L | med |

S ≈ hours · M ≈ a day · L ≈ multiple days

---

## G0 — why picking `out_vsync` does not retire the jitter question

Choosing the master settles *which signal*. It does not settle *whether that signal is good
enough*, because *`out_vsync`'s quality is mode-dependent*:

| Output mode | Pixel clock source | Expectation |
|---|---|---|
| offline | local clock | clean — offline SXGA is already rock-solid |
| passthrough | recovered from the HDMI input, via `clk_selector` | **suspect** |

The standing evidence is `passthrough-sxga-jitter`: 1280×1024 passthrough blacks the
projector from recovered-clock jitter at 540 MHz, while the same mode offline is stable. A
master derived from that clock does not produce a visibly jittery picture — it produces a
sensor whose exposure timing wanders, and you would look for that in the image and in the
sensor before suspecting the clock.

**So G0 measures both modes, and a bad passthrough number is a legitimate outcome:** genlock
may be an offline-mode feature until the jitter-clean output clock work lands.

---

## G1 — the defect that stops being deferrable

`pixel_pipe.v` paces `trig` from vsync today using an **accumulating** pending count
(`rdy_cnt` increments on a `rdy` rising edge, decrements on vsync). The replacement is
already identified — the MimasA7 `cam_pace.v` pattern: a **1-deep** pending flag, fresh-ready
arming, and de-glitch.

Under 1:1 locking this stops being housekeeping. An accumulator turns any brief sync gap
into a **burst of catch-up triggers** the moment sync returns — which at 120 Hz means
several exposures inside one frame period, which the sensor cannot serve. The result would
present as a sensor fault, and would be looked for in the sensor.

Fix it at G1, before anything depends on the trigger being correct.

---

## G3 — the delay must span more than one frame, and here is why

Locking to what is *sent* is correct. The projector does not display it then. DLP and LCD
projectors carry real pipeline latency — frequently one to two frames, and it changes with
their processing mode. Calibrating the camera against the *projected* pattern may therefore
need a delay of 16–25 ms at 120 Hz: expose during frame N+1 or N+2 relative to the vsync
locked to.

Consequences for the design:

- Size the delay register for **0 to at least 50 ms** (≥ 23 bits at 100 MHz).
- The delay must be allowed to **exceed one frame period** rather than wrapping at the
  boundary. A register that silently wraps cannot reach the setting the projector requires,
  and the symptom — a pattern that never lines up — says nothing about the cause.
- G3's proof must include a delay greater than one frame period, not only the sub-frame case.

**Open method question:** measuring the projector's actual latency most plainly means
sweeping the delay while watching a projected pattern in the live viewer. That makes a
repeatable optical path — lens mount, fixed geometry — a prerequisite for *calibrating*
genlock, even though it is not one for building it.

---

## G4 — the requirement that contradicts M3, stated honestly

M3 exists to prove **neither subsystem's failure changes the other's behaviour.** Genlock
*deliberately couples them.* G4 is where the permitted coupling is defined, rather than
allowed to emerge.

| Case | Camera must | Injected with |
|---|---|---|
| HDMI source unplugged mid-capture | fall back to free-run at 120.000 Hz within a bounded number of frames; `ldrop` bounded and **reported**; never wedge | `LINKCTL` reg `0x15` |
| HDMI mode change | re-lock automatically; `ldrop` bounded; no wedge | `MODEFORCE` reg `0x14` |
| No sync at power-up | free-run immediately — **never wait for an edge** | power-up with no display |
| Sync returns | re-lock automatically, and say so on Port A | release `LINKCTL` |

Both fault-injection mechanisms already exist, and the M3b hook (opcode 6) covers the
reverse direction. No new test hardware is required.

**Carried-forward rule, new:** *free-run fallback is not optional, and the camera must never
block waiting for a sync edge.* A camera that stalls because a display was unplugged is a
worse product than one that is not genlocked at all.

---

## M3d, restated for a genlocked system

M3d's original criterion was **`ldrop` must not move during an HDMI mode change**. That
cannot survive genlock unchanged: re-timing the display now necessarily re-times the camera.

> **Restated:** during a mode change `ldrop` MAY move, by a **bounded** amount that is
> **reported**, after which the camera re-locks and `ldrop` goes static again. An unbounded
> or silent disturbance still fails.

This is a deliberate relaxation of a milestone that already passed. It is recorded here so
that the change is a decision rather than a drift.

---

## What invalidates the approach

| Condition | Found at | Why it is fatal |
|---|---|---|
| `out_vsync` jitter in passthrough exceeds the sensor's trigger budget | G0 | The master is not fit to lock to. Re-time against a clean local clock, or restrict genlock to offline mode, before building G2. |
| The camera cannot hold 1:1 at a mode the application needs | G2 | The 1:1 decision has to be revisited, not worked around. |
| `ldrop` moves *unboundedly* during a mode change | G4/G6 | The coupling is unacceptable as designed — see the M3d restatement, which bounds it rather than removing it. |
| The delay range cannot reach the projector's latency | G3 | The feature does not do the job it exists for. |

---

## Sequencing notes

- **G0 before anything.** Cheapest milestone, highest information, and the only one that can
  invalidate the design. Do not build G2 on an unmeasured master.
- **G1 is the build vehicle.** Plumbing with no behaviour change, so G2 lands in a project
  that already places and routes — the same trick M1 played for the merge.
- **Fix `pixel_pipe`'s `rdy_cnt` at G1**, not later. See above.
- **G4 pairs with G2.** Bring it forward if locking proves stubborn: the degradation paths
  are how a half-working lock gets debugged without a reconfigure between attempts.
- **Keep free-run working throughout.** It is the recovery path and the reference when a
  locked result looks wrong.

## Carried-forward rules

| Rule | Why |
|---|---|
| Free-run fallback is not optional; never block on a sync edge | A display is an external object that will be unplugged. |
| Exposure clamp stays a constant ≤ 8280 µs | Safe at every rate ≤ 120 Hz. Past 8300 µs the sensor wedges until reconfigured. |
| The free-run rate must equal the maximum locked rate | Losing sync then cannot demand a rate the sensor cannot sustain. |
| Lock is proved by measured phase, never by "the rate looks right" | A trigger at a perfect 120.000 Hz has already, in this project, coexisted with zero pixels delivered. |
| Camera status never lives only on Port B | Unchanged from the merge, and reinforced 2026-08-24: the Ft+ reply path is currently silent when the frame stream stops. |

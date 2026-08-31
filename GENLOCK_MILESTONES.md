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

## What the application actually requires — answered 2026-08-24

Four questions were open and are now settled. They change what several milestones
are for, so they are recorded before the table rather than inside it.

### The projector is a DLP with sequential RGB LEDs, and there are real gaps between the colours

> "DLP projects use flashing RGB LEDs but there are substantial gaps between these
> colours. So we need to capture the entire frame but there is substantial latitude
> to dither the start time relative to the vertical sync."

**This reframes the whole campaign.** The requirement is COVERAGE, not alignment:

- **The exposure must span the entire projected frame.** Light arrives as separate
  colour flashes; a partial exposure captures an arbitrary subset of them and
  measures the wrong intensity. So the operating point is max exposure, right at
  the ceiling — which is where the sensor's ~5.3 µs start jitter lives.
- **But start-time alignment has latitude.** So THE 5.3 µs JITTER IS NOT A PROBLEM.
  It was the single biggest open worry and the answer retires it. No sensor-clock
  trimming, no line-time investigation, unless something else demands it.

**What DOES matter, and it is a different problem than "latency compensation".**
The sensor cannot integrate for 100% of the frame — a measured 44.1 µs of Frame
Overhead Time and reset is unavoidable. So roughly 50 µs of every frame is NOT
captured, and the question becomes **where that blind window lands**:

| Blind window lands in | Result |
|---|---|
| a dark gap between colour flashes | nothing is lost — the frame is fully captured |
| the middle of a colour flash | that colour is clipped, and the measured intensity is wrong |

**So the delay register's primary job is to park the blind window inside a colour
gap.** Compensating the projector's pipeline latency is the secondary job. That is
a more concrete requirement than "align to the pattern", and it is measurable.

### Passthrough needs sync too

Genlock is NOT an offline-only feature. **G0's passthrough measurement is a real
gate**, and the SXGA passthrough jitter already on record is a live risk to the
whole approach rather than an academic one.

### Several frame rates will be used in production

Whatever the projector supports. Two consequences:

- **G3's per-mode delay table is REQUIRED, not speculative.** The reserved payload
  bits get used. A single global delay would be wrong at every mode but one — and
  the colour-field structure of a DLP commonly changes with mode, so the blind
  window would need re-parking anyway.
- **G2's "across the usable range" stops being a robustness check** and becomes the
  actual requirement.

---

## Milestones

| # | Milestone | Proof (all must hold) | Effort | Risk |
|---|---|---|---|---|
| **G0** | **Pick the edge, prove it is trustworthy** | Measured rate and edge-to-edge jitter over ≥ 30 s for `out_vsync` **in both offline and passthrough modes**. Passthrough is IN SCOPE, so this is a genuine gate. Decision recorded *with the numbers*. | S | **high** |
| **G1** | **Sync reaches the camera; nothing changes** | `ext_sync` into `cam_frame_ft` + edge counter. Camera still free-runs at 120.000 Hz, every M2 metric unchanged, WNS ≥ 0. **Includes the `pixel_pipe` `rdy_cnt` fix.** | S | low |
| **G2** | **1:1 lock at EVERY mode that will be used** | Frame period tracks vsync at every production mode, not `TRIG_CY`. One exposure per projected frame — none missed, none doubled, over ≥ 10⁴ frames. `ldrop` static, `cfifo_ovf = 0`, frames byte-exact. Exposure clamp verified at the slowest and fastest. | M | **high** |
| **G3** | **Per-mode delay table, and the blind window parked in a colour gap** | A delay per mode index, live, range ≥ 0–50 ms and allowed to exceed one frame. **Proof is photometric, not just temporal**: with the delay set, the captured intensity of a flat field must be independent of which mode is running, i.e. no colour is clipped. | M | **high** |
| **G4** | **Graceful degradation** | The four cases below. Most likely to be skipped, and the one that matters. | M | **high** |
| **G5** | **Full-frame coverage verified, not assumed** | The captured intensity of a flat field matches the intensity captured at a deliberately shorter exposure scaled up, to within the noise floor — proving no colour flash is being clipped. Held over ≥ 30 min and across a mode change. | M | med |
| **G6** | **Regression + soak, genlock on** | M3a–M3d and the M7 soak re-run locked. See the M3d restatement below. | L | med |

S ≈ hours · M ≈ a day · L ≈ multiple days

---

## G0/G1 BLOCKER FOUND 2026-08-31: the genlock master fires ONCE PER LINE on
## negative-vsync modes

**`ext_sync => out_vsync` (Au2_SLI.vhd:1346) is the RAW output vsync, with no
polarity normalisation.** `hdmi_input` forces `raw_vsync` low through the video data
period, so on a NEGATIVE-vsync source -- where the idle level is HIGH -- it collapses
every active line and springs back at each horizontal blanking. One false rising edge
PER ACTIVE LINE.

MEASURED on hardware, merged build, pass-through, via the G1 edge counter 0x58/0x59:

    800x600@60   (+vsync)   ext_sync =      62.0 Hz    one edge per frame   OK
    1024x768@60  (-vsync)   ext_sync = ~43,000 Hz      768 active lines x 60 Hz

The same defect makes the G0 period counter (`vs_meas => out_vsync`) report the LINE
period on those modes, which is what exposed it:

    | mode        | vpol | G0 mean period | true line period |
    |-------------|------|----------------|------------------|
    | 1280x720@60 |  +   | 16666.61 us, span 10 ns (CLEAN) | -- |
    | 800x600@60  |  +   | 16579.14 us, span 10 ns (CLEAN) | -- |
    | 640x480@60  |  -   | 31.77 us       | 31.78 us         |
    | 1024x768@60 |  -   | 20.66 us (min) | 20.68 us         |

Both negative modes report their line period to two decimals. This is an INSTRUMENT
and MASTER fault, not display jitter -- the +vsync modes sit at the 10 ns measurement
floor, same as offline.

**EIGHT OF FOURTEEN curated modes have VPOL = 0** and are therefore affected:
800x600@120, 640x480@120, 640x480@75, 1024x768@70, 640x480@72, 1280x800@60,
1024x768@60, 640x480@60. Safe: 1024x768@75, 800x600@75, 800x600@72, 1280x720@60,
800x600@60, 1280x1024@60.

**WHY THIS MATTERS MORE THAN A BROKEN GAUGE.** G2 requires "one exposure per projected
frame -- none missed, none doubled, at EVERY mode that will be used". On any -vsync
mode the camera would be triggered at ~46 kHz. G2 would have failed there and the
symptom would have looked like a camera or trigger-pacing fault, not a sync-polarity
one.

**ROOT CAUSE, and it is already known.** `Au2_SLI.vhd` learns polarity as
`if (blank = '0') then VPolarity <= vsync; end if;` -- it samples during ACTIVE VIDEO,
which is exactly the window `hdmi_input` has just forced to 0, so `VPolarity` can
never learn 1 and `vsync_Pos` is never corrected. `video_meas` works around this
INTERNALLY (see `eb0265a`: sample during blanking, hold through active video, vote on
duty). Neither `ext_sync` nor the G0 period counter received that fix.

**FIX DIRECTION.** Holding sync through the video data period instead of forcing it low
makes `VPolarity` learnable at the source and fixes every consumer at once. That exact
change was written and reverted on 2026-08-31 because it did not fix the 640x480 black
screen (that was `alignment_detect`) -- but it is the correct fix for THIS, and it is a
proven no-op for +vsync modes, where holding 0 is identical to forcing 0.

**G0 IS THEREFORE PART-PASSED:** pass-through at +vsync modes is measured and CLEAN
(10 ns floor, equal to offline). Pass-through at -vsync modes cannot be measured until
the master is polarity-correct. The micro-HDMI cable blocker below is GONE.

---

## G0 — RESULT SO FAR: offline PASSES, passthrough not yet measured

**Offline, 2026-08-24** — `host/measure_vsync_period.py COM6 --seconds=34`:

```
  ran 35.1 s, 14 windows, 0 read failures
  mean period      : 9718.508 us  (102.8965 Hz)
  AGGREGATE across the whole run: min 9718.500  max 9718.510  span 0.010 us
```

**10 ns of total excursion — the measurement floor of a 100 MHz counter.** The
offline master is as clean as this instrument can resolve, and there is no slow
drift: the aggregate across every sampled window equals the per-window figure.

THE AGGREGATE IS THE NUMBER, NOT A WINDOW. min/max reset every status window
(~0.5 s ≈ 50 frames), so a slow wander would sit entirely inside per-window
figures that each look tiny. Coverage caveat: reading nine registers at
115200 baud takes ~2.5 s, so this sampled 14 windows spread across 35 s rather
than every window.

**CONSEQUENCE FOR THE WHOLE CAMPAIGN: the master is not the limiting term.** The
sensor's own exposure-start jitter is 5.3 µs at long exposures — five hundred
times larger. So lock quality will be set by the CAMERA, not the display, and G3's
delay register can be specified against a stable reference.

**Still outstanding: passthrough.** Blocked on micro-HDMI cables. See below for
why it is not a formality.

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

## G1 — RESULT: PLUMBING PASSES 2026-08-24, `rdy_cnt` fix still outstanding

```
  ext_sync edge count : 898 -> 1569   (+671 in 6.51 s)
  implied edge rate   : 103.13 Hz
  measured vsync rate : 102.90 Hz      ratio 1.0022
  camera fps before/after : 120.4 / 119.9
  0x3A 0x3F / 0x3F     ldrop 0 / 0     WNS +0.082 ns
```

`out_vsync` now reaches `cam_frame_ft` as `ext_sync`, with a free-running 16-bit
edge counter at regs `0x58`/`0x59`. The camera still free-runs on `TRIG_CY` and
every M2 metric is unchanged — the signal arrives and is deliberately ignored.

**THE RATE IS THE PROOF, NOT THE MOVEMENT.** The counter runs at 103 Hz, not 120.
Had the port been mis-wired to `trig0` the counter would still have incremented and
still looked like a pass; checking the rate against the independently measured vsync
period is what shows the RIGHT signal arrived.

**Timing recovered on the way.** The max-exposure multiplier had taken WNS from
+0.082 to +0.014 ns as a single combinational chain — subtract, 24x15 multiply,
compare. Pipelined into three stages it is back to +0.082 ns. The value is read over
a 115200 baud link and changes once per status window, so the latency costs nothing.
G1's own proof requires WNS >= 0, so this had to land first or a timing failure
would have been unattributable.

### Still outstanding within G1: the `pixel_pipe` `rdy_cnt` fix

Deliberately not in this build. It is a behavioural change to EXISTING WORKING
logic, and nothing depends on that path yet, so it deserves its own build where a
regression is attributable. **G1 is not complete until it lands.**

### Found on the way, and it matters for G4

**`MODEFORCE` force-off does NOT restore the power-up mode** — it leaves whatever
was last forced. Confirmed: power-up gives 102.9 Hz; after forcing indices and
clearing `force_en` the board sat at 75 Hz; a reflash restored 102.9 Hz. G4's
"mode change -> automatic re-lock" has to survive that.

Unresolved loose end: `0x20` reported index 2 while the board was demonstrably
running 102.9 Hz, so the reported index and the active timing disagreed.

---

## G3 — TRIGGERS STACK. The delay costs latency, not rate.

_Design settled 2026-08-31, before building. The first implementation got this
wrong; the correction is below and the reasoning is the point._

### What the AuV2 had to do, and why

The AuV2 handshake is **serialising**. `pixel_pipe`'s `pending` is 1-deep and is
cleared by the vsync that consumes it, so **at most one trigger is ever
outstanding**. If exposure plus delay ran past the next vsync the camera was not
ready, `hold` stayed high, no trigger went out, and the pattern was projected
again unchanged -- the sequence advanced every second or third frame.

That was not a defect. The FPGA was driving an external AVT camera over a DB9 and
**could not see when it would next be ready**, so waiting was the only safe
policy. The cost was that the SLI sequence ran slower than the display.

### Why this system does not have to

Two things changed, and together they remove the need to wait:

1. **The FPGA SETS both numbers.** Exposure is opcode 1, delay is opcode 7. It does
   not have to DISCOVER readiness -- it can compute it. There is nothing to wait for.
2. **The sensor is in pipelined global shutter**, integrating the next frame while
   reading out the current one. G0's own note depends on this: the integration start
   "snaps to a line boundary whenever integration begins during a readout -- which in
   pipelined mode is always."

So triggers may be **stacked**: issue one per projected frame, with several in
flight, and advance the SLI pattern at the **full HDMI rate**.

### The constraint is EXPOSURE, not exposure + delay

A delay applied uniformly to every trigger shifts PHASE, not PERIOD. The trigger
period stays the HDMI period however large the delay is. So the only rate condition
is the one that was always there:

    exposure + 54.1 us  <=  HDMI frame period      <- must hold
    delay                                          <- free, MAY exceed a frame

54.1 us is `GAP_TICKS` (44.1 us, measured) + `MARGIN_TICKS` (10 us) from
`usb_link.v`, and it is exactly what the max-exposure register already computes:
`usable = vsync_period - RESERVE`, scaled by 27962/2^20 = 0.026667 to turn 10 ns
ticks into 375 ns exposure units. It is why the clamp is 8280 us at 120 Hz -- one
period minus 54 us.

**Delay buys latency. It does not cost frame rate.**

### THE FIRST IMPLEMENTATION FORBADE EXACTLY THIS

The delay engine built earlier on 2026-08-31 re-armed on every vsync:

    if (xs_rise) begin dly_cnt <= gl_dly; dly_run <= 1'b1; end

with the comment "a fresh frame edge RE-ARMS unconditionally [...] otherwise a
mis-set delay would quietly halve the trigger rate". The guard is real but the
policy is backwards: with `gl_dly` longer than one frame, each vsync restarts the
countdown before it expires and **the trigger never fires at all**. Guarding
against a mis-set delay was chosen over supporting the actual design intent.

### The correct structure: several delays in flight

One countdown cannot represent N outstanding triggers. Push an absolute fire-time
per vsync instead:

  * a free-running tick counter (10 ns) as the time base;
  * on each `xs_rise`, push `now + gl_dly` into a small FIFO;
  * fire the trigger when the head entry is reached, and pop it.

Depth covers the worst case delay/period ratio: 167 ms of delay at 120 Hz needs 20
entries, so **32 is ample**. An overflow means the delay exceeds what the FIFO can
hold and must be reported, not silently dropped -- a lost trigger is a lost frame
of the scan.

### The consequence to design around is BOOKKEEPING, not timing

With D larger than one frame period, the capture of pattern *k* arrives while
pattern *k + floor(D/T)* is on the screen. The frames are all there and the rate is
full; only the PAIRING moves. The host must offset frame index against pattern
index by `floor(D/T)`.

**Expose the outstanding-trigger count in a register** so that offset is READ, not
assumed. An assumed offset that is wrong by one produces a phase map that looks
plausible and is wrong everywhere -- the exact failure class this project keeps
producing.

---

## G3 — the delay register, respecified for a DLP

**Opcode 7.** Payload `[27:24]` = **mode index**, `[23:0]` = delay in microseconds.

The per-mode table is now REQUIRED — several rates will be in production, and a DLP's
colour-field structure commonly changes with mode, so one global value would be wrong
at every mode but one. The mode index keys off `0x20 MODE`, which already exists.

| | |
|---|---|
| Units | µs directly — 24 bits reaches 16.7 s, far beyond the 0–50 ms needed |
| Range | ≥ 0–50 ms, and it **must be allowed to exceed one frame period** |
| Latency | **Live** — effective at the next frame boundary, no re-arm |
| Readback | per-mode, via the camera status word |

### What the delay is actually FOR — two jobs, and the first one is new

**Job 1: park the 44 µs blind window in a dark gap between colour flashes.** The
sensor cannot integrate 100% of a frame; a measured 44.1 µs of FOT and reset is
unavoidable. On a sequential-RGB DLP that window either lands in a gap between
colour flashes — costing nothing — or inside a flash, clipping that colour and
corrupting the measured intensity. **This is the requirement that sets the delay.**

**Job 2: compensate the projector's pipeline latency.** One to two frames typical,
and it changes with processing mode. This is why the range must exceed one frame
period: "expose during frame N+2" is a legitimate setting.

### Calibrating it needs no extra hardware, and the machinery already exists

**Sweep a SHORT exposure across the frame and photograph the projector's own colour
timing.** Below ~2775 µs the exposure-start jitter is 10 ns, so a short exposure
stepped through the frame by the delay register is a **sampling oscilloscope for
light**: plot captured intensity against delay and the colour flashes and the gaps
between them appear directly.

Then set the delay so the blind window sits in a gap, and switch to full exposure.

Two things make this work that were not designed for it: the delay register moves
the exposure with 10 ns precision, and monitor0 reports exactly when integration
happened, so the light measurement and the timing measurement come from the same
frame. **Repeat per mode** — that IS the per-mode table, measured rather than
guessed.

### Negative delay is unnecessary

Because the delay may exceed a frame, "2 ms before vsync N" is just "6.33 ms after
vsync N−1".

---

## G5 — why the proof is photometric, not temporal

G5 was originally "vsync→`frame_start` phase error over 30 min". **That measures the
wrong thing for this application.** The requirement is that the camera captures the
WHOLE frame, and a perfectly stable phase that happens to clip a colour flash would
pass a phase-error test while producing wrong intensities.

So the proof is: **a flat field captured at full exposure must match the same field
captured at a deliberately shorter exposure and scaled up**, within the noise floor.
If a colour is being clipped, the full-exposure capture comes up short and the ratio
breaks. Held over ≥ 30 min and across a mode change.

Phase stability is still worth recording — the instrument exists and costs nothing —
but it is evidence, not the criterion.

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
| `out_vsync` jitter in passthrough exceeds what the SENSOR's frame period can absorb | G0 | **Still fatal, and now unavoidable: passthrough is confirmed in scope.** Restricting genlock to offline mode is no longer an acceptable fallback, so a bad number here forces the output-clock cleanup before G2. |
| ~~The camera's own exposure-start jitter is too large~~ | — | **RETIRED 2026-08-24.** The application has "substantial latitude to dither the start time", so the measured 5.3 µs is not a constraint. No sensor-clock trimming needed. |
| **The 44 µs blind window cannot be placed in a colour gap at some mode** | G3 | The frame is not fully captured at that mode and intensities are wrong. This is the new central risk, and it is photometric rather than temporal. |
| The camera cannot hold 1:1 at a mode the application needs | G2 | The 1:1 decision has to be revisited, not worked around. |
| `ldrop` moves *unboundedly* during a mode change | G4/G6 | The coupling is unacceptable as designed — see the M3d restatement, which bounds it rather than removing it. |
| The delay range cannot reach the projector's latency | G3 | The feature does not do the job it exists for. |

---

## Cleanup before G2 — agreed 2026-08-24

Not genlock milestones; debts from the merge campaign that G2 would otherwise trip
over. Doing them first was a deliberate choice.

| Item | Why it must precede G2 |
|---|---|
| **`pixel_pipe` `rdy_cnt` accumulator** | G1 is not complete without it. It accumulates pending triggers, so any gap in the sync becomes a BURST of catch-up triggers the moment sync returns — several exposures inside one frame period, which the sensor cannot serve. It will present as a sensor fault and be looked for in the sensor. Replacement is the MimasA7 `cam_pace.v` pattern: 1-deep pending, fresh-ready arming, de-glitch. |
| **Camera does not resume after opcode 6** | The fault-injection hook for G4 is unusable for repeatable testing until release actually restarts the camera. Suspected `stream_go` firing into `cam_boot_stage1` during its 2FF reset sync. |
| **Ft+ reply path dies when the stream stops** | G4 deliberately stops and disturbs the camera. If USB 3 readback dies in exactly those states, G4 is debugged blind. Port A covers it, but the defect also retracts M6c's "Port A is TX-only". |

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

# Projector profiling — when is each LED actually on?

_Drafted 2026-09-03._ Target: for a given projector and video mode, a measured table and
plot of **when each colour LED delivers light into the projection lens**, on a time axis
where the rising edge of `out_vsync` is t = 0.

Predecessor: [`GENLOCK_MILESTONES.md`](GENLOCK_MILESTONES.md) · Hardware:
[`LauPythonCamera_Pt_Stack/mech/gen_projector_lens_cap.py`](LauPythonCamera_Pt_Stack/mech/gen_projector_lens_cap.py)

## Why this exists

`README.md` §3 states the open question in one line:

> **Not yet proven:** where the exposure actually lands optically inside the projected frame.

`GENLOCK_MILESTONES.md` then makes two milestones depend on the answer. **G3** requires the
camera's ~50 µs blind window to be parked *in a dark gap between colour flashes*, and **G5**
requires proof that no colour flash is being clipped. Neither can be designed, let alone
proven, without knowing where the flashes are. Both are currently marked **high risk**, and
this is the reason.

The DLP does not fire R, G, B once per frame. It pulses each LED **several times per frame in
an interleaved order**, so "the red slot" is not a single interval — it is a set of them, and
the set changes with video mode. That structure is the thing being measured.

## The quantity being measured

For each colour c and each mode, the function

    E_c(t) = light of colour c entering the projection lens, t microseconds after out_vsync

reduced to a list of `(t_on, t_off)` intervals plus the curve they came from.

**What the sensor at the pupil actually sees, and why it is the right quantity.** The cap
holds a bare sensor ~12.7 mm in front of the lens, on the optical axis. Light reaches it only
if it passed *through* the projection lens — that is, only if the LED was on **and** the DMD
mirror was in the on state. Mirrors in the off state dump their light elsewhere.

That coupling is a feature, not a confound:

| Projected field | What the sweep returns |
|---|---|
| full white | the union of all LED slots — the raw colour sequence |
| saturated red | the red slots alone |
| saturated green / blue | likewise |

**So a monochrome sensor separates the colours,** by changing what is projected rather than
by filtering what is received. The NOIP1SN1300A is mono and that is not a limitation here.

It also means the measured quantity is *emitted light*, not *LED drive current* — which is
what genlock and structured light actually care about.

---

## Method: three instruments, because one cannot be trusted

This repo's rule is that a milestone needs a proof that cannot be faked by a broken system.
A single sweep cannot meet it, so the plan runs the same measurement through paths that fail
differently.

### M-A. Delay sweep — the primary instrument

Static field, short exposure, sweep the genlock delay across the frame; the ROI mean at each
delay traces out E(t).

- Delay: **opcode 7**, `{gl_en, 3'b0, delay[23:0]}`, **10 ns ticks**, read back `0x5A`–`0x5C`.
- Exposure: **opcode 1**, **375 ns units**, read back `0x40`/`0x41`.
- The delay is allowed to exceed one frame period and triggers stack (32 outstanding), so the
  sweep can run past 1 T without special handling.

**Resolution is set by the exposure width, not by the clock.** The delay quantum is 10 ns and
the exposure quantum is 375 ns, but what the measurement returns is E(t) **convolved with a
boxcar the width of the exposure**. Choose the exposure to be the step size and the profile
is smeared by exactly one step.

**The jitter works in our favour here, for once.** `FTPLUS_API.md` records exposure-start
jitter as 0.01 µs below ~2775 µs exposure, rising to ~5.3 µs above it. Profiling wants *short*
exposures, which sit in the 10 ns regime. The 5.3 µs figure that dominates the genlock
campaign does not apply to this measurement.

### M-B. Cumulative exposure sweep — the cross-check

Hold delay at 0 and sweep the **exposure** from 0 to one frame period. The signal is then
∫E dt from vsync to t; its derivative is E(t).

This matters because it exercises a **different register and a different engine**. M-A leans
entirely on the genlock delay path; M-B does not use it at all. If the delay engine has a
systematic offset, M-A alone cannot see it and M-B will disagree. Agreement between two paths
that fail differently is the proof; the derivative's noise penalty is the price.

Bounded by the max-usable-exposure clamp at `0x53`/`0x54`
(`vsync_period − 44.1 µs − 10 µs`), which covers one frame at any lockable rate.

### M-C. Photodiode on a scope — ground truth

A photodiode and load resistor at the pupil, scope triggered on `out_vsync`. Independent of
the FPGA, the sensor and both engines above, with bandwidth far beyond anything the camera
can reach.

**M-C is the arbiter, not the instrument.** It is cheap, it is fast, and it settles any
disagreement between M-A and M-B — but it samples one point in the pupil, cannot be
automated across modes as easily, and does not produce the per-pixel data the rest of the
system consumes. Build it early; do not build the campaign on it.

---

## Traps — the failures that will look like success

Each of these produces a clean, plausible, wrong plot.

**1. Saturation reads as a wide flash.** The bare sensor sits in the undiverged beam. A
clipped pulse has a flat top and vertical edges — it looks *more* like a clean LED slot than
the truth does. **Un-fakeable check: halve the exposure and the entire profile must scale by
2.** Wherever it does not, that region was clipped.

**2. Do not control the level with the projector's brightness or eco setting.** On a DLP those
change LED duty and PWM structure — they modify the thing being measured. Attenuate with the
reduced-area field (above), ND film, or a shorter exposure. Record which was used.

**2b. The output LUT / correction tables must be in a known state.** The emitted code is not
the pattern code if something has been left in the correction path — `test-silicon-is-destructive`
records `test_silicon` leaving test data in the corr/LUT tables and visibly scrambling display
colours until the bitstream is reloaded. A "saturated red" field that is quietly being
gamma-mapped to 78% red still produces a plausible-looking profile. **Verify the LUT is
identity (or known) before P3, and never profile after running `test_silicon` without a
reload.**

**3. The projector may not be phase-locked to `out_vsync` at all.** If it has an asynchronous
scaler or frame buffer, its light output free-runs and equivalent-time sampling averages the
structure away — yielding a smooth, low-contrast, entirely fictitious curve. **This is P0 and
it gates everything.**

**4. Eight of fourteen modes have a broken t = 0 today.** `GENLOCK_MILESTONES.md` records
`ext_sync` firing once per *line* on negative-vsync modes, from `VPolarity` being sampled
during active video. Until that fix lands, profiling on those modes measures against a
reference that ticks ~43 kHz. Work in +vsync modes first; the affected list is in that doc.

**5. Frame repeats hide a second LED cycle.** A 60 Hz input on a projector running 120 Hz
internally shows each frame twice. **Sweep at least 2 frame periods**, or a doubled sequence
will be read as a single one.

**6. Known sensor and capture behaviours that will corrupt the mean if ignored:**

| Behaviour | Source | Handling |
|---|---|---|
| top rows permanently damaged | `sensor-damaged-top-of-image` | exclude from the ROI; fix the ROI once and record it |
| first frame after trigger saturates | `triggered-mode-capture` | discard the first frame of every burst |
| exposure lands one frame late | `FTPLUS_API.md` opcode 1 | discard after every exposure change |
| DDR holds the old burst until re-armed | `cam_ctl.py` | re-arm at every sweep point |
| passthrough SXGA clock jitter | `passthrough-sxga-jitter` | profile **offline** first; passthrough is P6 |

**7. `0x5E` (triggers outstanding) is the pairing offset — read it, never assume it.**
`README.md` §3 says so explicitly, and it differs exactly when the display has gone away.

---

## The source: a purpose-built offline bitstream _(decided 2026-09-03)_

**A new `.bin` carrying custom SLI patterns, run in offline mode.** This is the right call and
it removes the plan's biggest external dependency:

- **Offline `out_vsync` is the cleanest reference in the system** — mean period 9718.508 µs,
  span **0.010 µs** over 35 s, the 100 MHz measurement floor. Nothing else in the plan is
  measured against a better clock.
- **No PC, no HDMI source, no EDID negotiation, no recovered clock.** The
  `passthrough-sxga-jitter` risk simply does not apply, and trap 4 (vsync polarity) is a
  passthrough-side defect that offline sidesteps.
- **The field becomes a controlled variable rather than whatever a PC happened to send.**

### What the pattern set has to contain

| Pattern | Why it is needed |
|---|---|
| **BLACK** (0,0,0) | Dark reference. Gives the sensor pedestal, stray light, and any LED leakage through "off" mirrors. **Subtract it from every other measurement** — an emission profile that has not had a dark frame subtracted is a profile with an unknown floor. |
| **WHITE** (255,255,255) | The union of all LED slots — the raw colour sequence. |
| **RED / GREEN / BLUE** | 255 in one channel, 0 in the others. The per-colour slots, and the whole basis of P4. |
| **WHITE at reduced area** | See below — this is the attenuator. |
| _optional_ MID-GREY (128) | Reveals whether PWM structure changes with level. Cheap to include, out of scope to analyse. |

### Two requirements that are easy to miss

**1. The pattern must be frozen — bit-identical on every frame.** Equivalent-time sampling
assumes every frame is the same frame. If the SLI generator advances phase per frame, the
sweep would average across *different* patterns and return a smooth, meaningless curve — the
same failure mode as trap 3, from a different cause. The profiling bitstream needs the phase
advance held, not just slowed.

**2. Field selection must be live, not a reload.** P3–P6 change field many times per session.
A selector register or opcode turns that into a write; a bitstream reload per field turns a
15-minute mode into an afternoon.

### The reduced-area field is a better attenuator than ND film

This falls out of the geometry and is worth taking. Every DMD pixel illuminates the *whole*
pupil, so a bare sensor at the pupil sees flux proportional to the **fraction of mirrors in
the on state**, regardless of where they are. A centred white patch covering 10% of the
raster therefore attenuates by ~10× — **while leaving the LED timing untouched**, because the
LEDs fire on their own schedule either way.

That is a purely digital, FPGA-controlled, instantly-switchable ND filter, and it costs one
more pattern.

**But it must be proven, not assumed.** Some projectors run dynamic contrast or adaptive LED
current that changes the sequence with content. **The check: the timing measured at 100% area
and at 10% area must be identical.** If it is, the attenuator is sound and dynamic contrast is
not interfering. If the two disagree, that is a genuine finding — disable dynamic contrast and
re-measure, and treat every result taken before that as suspect.

---

## Milestones

| # | Milestone | Proof (all must hold) | Effort | Risk |
|---|---|---|---|---|
| **PB** | **The profiling bitstream** | Offline build emitting BLACK / WHITE / R / G / B / reduced-area WHITE, **selectable live**. Proof: the phase advance is genuinely frozen — two frames captured seconds apart are **byte-identical**, not merely similar. Each field verified at the FPGA output (not by eye) to be the intended code, with the LUT in a known state. `out_vsync` period at `0x4A`–`0x52` matches the intended rate. | M | med |
| **P0** | **The projector is phase-locked to `out_vsync`** | With a static white field and a short exposure at a **fixed** delay, the per-frame ROI mean is stable over ≥ 10³ frames. Two delays — one chosen inside a flash, one in a gap — give a large, **stable** ratio. A drifting or beating mean means the projector free-runs: stop and re-plan around M-C. | S | **high** |
| **P1** | **A photometric operating point that provably does not clip** | No ROI pixel at the 10-bit rail and none on the floor, **at the brightest point in the frame**. Halving the exposure halves the mean to within noise. A BLACK frame captured and subtracted. ROI (excluding the damaged rows) and attenuation both recorded. **Reduced-area attenuation validated: 100% and 10% area give the same timing.** | S | med |
| **P2** | **t = 0 is trustworthy for the mode under test** | `0x4A`–`0x52` reports the **frame** period, not the line period. `0x5D` shows `gl_en` and `gl_live`. `0x5E` read, not assumed. `0x5F` genlock FIFO overflows = 0 across a whole sweep. | S | med |
| **P2b** | **Complement sweep — the delay, measured directly** | Exposure at max, delay swept over a **full** frame period, **run separately for red, green, blue and white**. Proof: the same global peak on an up-sweep, a down-sweep and a delay-randomised pass — agreement across all three separates a real peak from drift. Superposition holds: the white curve's modulation matches the sum of the three primaries'. Plateau width reported per primary as the timing margin. | S | med |
| **P3** | **First emission profile — white, one mode** | Sweep ≥ 2 frame periods. Two independent sweeps agree within noise. **M-B reproduces every edge to within one exposure quantum.** The integral over one frame equals a single full-frame long exposure of the same field. | M | **high** |
| **P3b** | **Impulse response — latency, frame repeats, and contamination** | One saturated primary frame followed by four black frames, swept coarsely. Proof: the impulse is found at a repeatable offset across ≥ 3 runs; the four black frames are measured, not assumed dark; and the impulse's own flash structure matches the P3 static profile for the same colour. A mismatch means content-adaptive processing and invalidates P3. | M | **high** |
| **P4** | **Colour separation** | Profiles for saturated R, G and B. **Superposition: R + G + B must reconstruct the white profile within noise.** A shortfall is a real finding (a white-boost or clear segment), not a rounding error — record it, do not tune it away. | M | med |
| **P5** | **The on/off table and the plot** | `(t_on, t_off)` per colour with vsync at t = 0, plus the curve. **Edges must not move when the exposure width is changed** (e.g. 5 µs vs 20 µs) — an edge that moves is a threshold artifact, not a measurement. Machine-readable table alongside the plot. | M | med |
| **P6** | **Mode dependence, and passthrough** | The table repeated across the production modes, +vsync first. Passthrough vs offline compared at one common mode. Per-colour total on-time reported against frame period. | M | med |
| **P7** | **The profile is stable enough to be worth storing** | Cold start to 60 min: how far do the edges move? Either they hold within a stated bound, or the drift is characterised and the profile carries a warm-up caveat. | S | low |

S ≈ hours · M ≈ a day · L ≈ multiple days

---

## P2b in detail — the complement sweep _(added 2026-09-03)_

**Set the exposure to its maximum and sweep the delay. The peak is the answer.**

### Why the peak is the right delay

At max exposure the integration window covers the whole frame except the 54.1 µs the sensor
is blind for. So the reading is not the light collected — it is **the total minus the light
missed**:

    measured(d) = E_total − E_blind(d)

`E_total` is a constant. Every bit of variation across the sweep comes from `E_blind(d)`, the
energy that happened to fall in the blind window at that delay. Therefore:

| The curve | The blind window is | |
|---|---|---|
| at its **maximum** | in the darkest gap | ✅ **this is the G3 condition** |
| at its **minimum** | on the brightest part of a flash | worst case — a colour is clipped |

This is a better route to the number than reconstructing E(t) and then reasoning about it. It
measures **the actual production configuration** — real exposure, real delay engine — and the
quantity it optimises is the one that matters, with no intermediate model to be wrong.

It also yields the emission structure for free, inverted and smoothed by a 54.1 µs boxcar.
Not a substitute for the fine map, but often enough to see the shape.

### The plateau width is the most useful number on the plot

If the widest dark gap is **wider** than 54.1 µs, there is a *range* of delays where
`E_blind = 0` exactly, and the curve is **flat-topped**. That plateau is not a defect of the
measurement — it is the timing margin, and its width is:

    plateau width = gap width − 54.1 µs

Set the delay at the **centre of the plateau**, not at whichever sample happens to read
highest. And treat the width as a budget: the exposure-start jitter in this regime is
**5.3 µs**, not the 0.01 µs of the short-exposure sweep, because max exposure is far above
the ~2775 µs knee. A 200 µs plateau absorbs that comfortably; a 10 µs plateau does not.

**If there is no plateau — only a rounded peak — then no gap is as wide as 54.1 µs, and G3
is unachievable at that exposure and mode.** That is a decisive, actionable result: shorten
the exposure until a plateau opens, and accept the coverage cost. Getting that answer in one
short sweep is the strongest argument for running this early.

### The one thing that will fool it: drift, not noise

The modulation is small. With the blind window at 54.1 µs against a per-frame LED on-time of
roughly 2800 µs for a single primary, the full swing is only

    54.1 / 2800  ≈  2 %          (≈ 1 % on a white field)

Statistical noise is not the problem — averaging 256 ROI pixels and a few tens of frames puts
the noise floor far below a 2% swing. **Slow drift is the problem.** A 0.5% wander in LED
output or sensor response over a 45-second sweep is a quarter of the entire signal, and it
arrives as a smooth slope that looks exactly like real structure.

Three defences, all cheap:

- **Sweep up, then down.** If the peak moves between the two, that displacement is drift.
- **Run one pass in randomised delay order.** Drift then scatters as noise instead of
  masquerading as a trend, and a peak that survives randomisation is real.
- **Interleave a fixed reference delay** between test points and report the difference.

### Sweep the whole period — do not stop at the first peak

The projector pulses each LED **several times per frame**, so there are several gaps and
therefore several peaks, of differing width and depth. Stopping at the first rise-and-fall
finds *a* gap, not *the* gap.

Sweep the full frame period, take the **global** maximum, and where two peaks are close in
height **prefer the wider plateau over the taller spike** — margin is worth more than a
fractionally lower `E_blind`.

### Run it one primary at a time — the primaries do not share their gaps

**A gap in red is not a gap in green.** Red is dark largely *because* green and blue are
firing, so the delay that parks the blind window perfectly against an all-red field can drop
it squarely onto a green flash. A single sweep on one colour cannot see that.

So the sweep is run four times — **red, green, blue, and white** — and the four curves are
read together:

| Curve | What its peaks mean |
|---|---|
| **Red / green / blue** | where the blind window misses *that* primary |
| **White** | where it misses **all three at once** — the only gaps a full-colour pattern can use |
| **All four overlaid** | the usable delay, and when none exists, **which colour is in the way** |

That last row is the reason to do it per primary rather than just measuring white. A white
sweep with no plateau tells you the problem exists; the per-primary curves tell you its name
— "red and green share a gap at 3.2 ms, but blue fires straight through it" is something you
can act on. A flat white curve on its own is not.

**Three practical consequences.**

**1. Per-primary contrast is about twice as good.** A single primary is on roughly a third of
the frame, so its modulation is ~54.1/2800 ≈ 2%, against ~1% for white where the on-time is
much longer. The primaries are the easier measurement, and white is the one most at risk from
the drift described above.

**2. The attenuation must be identical across all four runs.** The exposure is pinned at
maximum — that is the entire point of the experiment — so the *only* level control left is
optical or reduced-area attenuation. Set it once, for the **brightest** primary (usually
green) so nothing clips, and do not touch it again. Red and blue will read lower and noisier;
that is the price of a comparison that stays valid. Changing attenuation between runs breaks
superposition and quietly invalidates the cross-check.

**3. Superposition is a free un-fakeable check.** The energy landing in the blind window is
additive, so across the whole sweep

    E_blind,white(d)  ≈  E_blind,R(d) + E_blind,G(d) + E_blind,B(d)

If the four measured curves do not satisfy that, something is wrong — a white boost or clear
segment in the projector, a changed attenuation, or drift between runs. Same check as P4, and
it costs nothing beyond doing the runs.

### How to read the three curves — overlap, not coincidence

Each primary's curve is close to a **square wave**, not a bump. Red is genuinely off for the
whole stretch between red flashes, so `E_blind,R = 0` across all of it — red's plateau spans
the green and blue flashes. Green's spans red's and blue's. **The three plateaus are roughly
anti-correlated and each is much wider than the delay you are looking for.**

So the three peaks are *not* in the same place, and looking for a common peak location will
either find nothing or find a coincidence that means nothing. The correct reading:

> **Find where all three plateaus overlap.** That intersection is the all-dark gap — the only
> place the blind window misses every colour at once.

Two equivalent ways to compute it, and they should agree:

    best delay  =  argmax over d of   min( R̂(d), Ĝ(d), B̂(d) )      ← the three curves, each normalised
    best delay  =  argmax over d of   white(d)                      ← the physical sum, no normalisation needed

The white curve is the physically correct combination, because in production the camera
integrates all colours and the energy it misses is the plain sum. **Use white for the
decision; use the three primaries to understand it.** Normalisation matters in the first
form and is easy to get wrong — the primaries have different LED outputs — which is another
reason to let white carry the decision.

**The width of the overlap is the real margin**, and it is much narrower than any single
primary's plateau:

    usable margin = all-dark gap − 54.1 µs

**The likely disappointing outcome, stated in advance.** If the all-dark gap is narrower than
54.1 µs, the three plateaus never overlap enough and **no delay avoids every colour**. Then
the job changes from "find the gap" to "choose what to sacrifice": minimise the total missed
energy, and let the per-primary curves tell you which colour is being clipped and by how
much. That is a legitimate result, not a failed measurement — but it must be reported as a
compromise rather than as a peak.

### The dips are more informative than the peaks

Because each curve is nearly a square wave, its **downward excursions locate that primary's
flashes** directly. Three complement sweeps therefore hand you an inverted, 54.1 µs-smoothed
emission map for each colour — the substance of P3–P5 — as a by-product of the measurement
that answers the delay question.

That is worth knowing before committing to the fine sweeps: **P2b may make P3–P5 confirmatory
rather than essential**, and the fine sweeps are then best spent only on the regions the
complement curves show to be interesting.

### The result that would make this much easier

**If the SLI patterns use only one primary, only that primary's gaps constrain the delay** —
and single-primary gaps are far wider than the gaps in the union, because two thirds of the
frame becomes parking space. The blind window would go from barely fitting to trivially
fitting.

That is a system-design lever, not just a measurement detail, and it is worth knowing which
way it points before committing to full-colour patterns. It is added to the open questions.

### Where it sits in the campaign

Cheap enough to run as soon as P1 and P2 pass, and it may answer the whole G3 question on its
own. Four sweeps instead of one, still well under an hour. The fine mapping in P3–P5 stays
valuable for two reasons: it is the diagnostic when this sweep finds no adequate plateau, and
it is what turns "this delay works" into a per-mode table that can be reasoned about.

---

## P3b in detail — the impulse response _(added 2026-09-03)_

**One frame of a saturated primary, then four black frames, repeated.** Sweep the delay
coarsely across the resulting five-frame cycle.

### Why the static sweep cannot answer this

With a flat field every frame is identical, so emission is periodic with period T and a
reading at delay *d* is **ambiguous modulo T** — there is no way to tell whether the light
arriving in that window came from the frame just sent, the one before it, or the one before
that. Latency is exactly the quantity that ambiguity hides. Making one frame in five
different gives the sequence a period of 5T and a unique origin to measure from.

### It breaks the inner loop's averaging assumption — and the fix is better than the obvious one

P3's inner loop averages consecutive frames because every frame is the same. **That is no
longer true.** With a five-frame cycle, consecutive camera frames sample five *different*
phases, so averaging them straight would blend the impulse into the black frames and produce
a flat, meaningless result.

The obvious fix is to trigger the camera only on the impulse frame — a divide-by-five. It
works, but it throws away 80% of the frames and needs delays out to 41.7 ms.

**The better fix: keep triggering every frame and bin by phase.** The camera fires once per
projected frame at delay *d*; the frame at cycle phase *p* therefore samples absolute time
`p·T + d` after the impulse's vsync. So sweeping *d* over **one** frame period and sorting
each reading into one of five bins reconstructs the whole 5T timeline:

| | Obvious (÷5 trigger) | Phase binning |
|---|---|---|
| Delay range needed | 0 … 41.7 ms | 0 … 8.33 ms |
| Frames used | 1 in 5 | all |
| Steps at 50 µs | 833 | 167 (× 5 bins) |
| Capture time | identical | identical |
| Systematics | five different delay values | **one delay value for all five phases** |

The last row is the real win: all five phases are measured at the *same* delay register
setting, so any delay-dependent systematic cancels out of the comparison between them.

This needs the FPGA to tag each mean with its cycle phase — a few bits alongside the value.

### Sweep it coarse first

Latency is a bulk property. A **500 µs** first pass is 17 steps — about **23 s** — and is
enough to find which phase the impulse lands in. Only then is a 50 µs pass (~3.7 min) worth
running, and only over the phase that contains it.

### What it returns, beyond latency

**The four black frames are the second measurement, and arguably the more important one.**
They are not padding — they are where contamination shows up:

- **Latency.** Where the light appears relative to the impulse's vsync. In offline mode this
  is FPGA-output-to-photons, which is precisely the quantity genlock needs.
- **Frame repeats.** If the impulse appears *twice*, the projector is running its panel at a
  multiple of the input rate. That changes what "one projected frame" means everywhere else
  in this plan.
- **Cross-frame contamination.** If the black frames are not black, the projector is bleeding
  the impulse forward — temporal dithering, motion processing, or slow settling. **For
  structured light this is the highest-consequence result in the whole campaign**, because it
  means every pattern is polluted by its predecessor. Listed as out-of-scope earlier in this
  document; this experiment answers it for free, so it is now in scope.

### The trap this experiment walks straight into

**The content is now 80% black, which is exactly what triggers dynamic contrast.** A
projector with adaptive LED current or dynamic black will drive this sequence differently
from the static flat field in P3 — different LED duty, possibly a different colour sequence.

**So the cross-check is mandatory, not optional: the impulse frame's own flash structure must
match the P3 static profile for the same colour.** If it does not, content-adaptive
processing is active, and it invalidates P3 as well as this test. Disable it and re-measure
both.

### "Latency" needs a definition before it can be a number

The impulse frame does not emit light at an instant. It emits the **same multi-flash burst**
every other field does — several LED pulses spread across a good fraction of a frame period.
So "when the light appears" is a question about an extended waveform, and the answer changes
by hundreds of microseconds depending on which feature is chosen:

| Definition | Value | Use when |
|---|---|---|
| **First photon** — leading edge of the first flash | earliest | this is what genlock needs: it is when the projected frame starts existing |
| **Centroid** — energy-weighted mean of the burst | later, by ~half the burst | most robust to noise and to a slow leading edge; best for comparing runs |
| Peak | arbitrary | avoid — moves with which flash happens to be brightest |

**Report first-photon as the latency and centroid as the check.** The two must differ by a
stable amount across runs; if that separation wanders, the burst shape itself is changing and
the dynamic-contrast question above is already answered in the wrong direction.

Note that the leading edge is measured through the sweep's own resolution — a 500 µs coarse
pass locates it to ±500 µs, which is enough to identify *which frame* the light belongs to
but not enough to state a latency. Refine with the 50 µs pass over that frame only.

### Sizing the cycle

Four black frames gives 4T ≈ 33 ms of unambiguous guard at 120 Hz, which covers a latency of
up to about four frames — comfortable for typical projectors. **If the impulse lands in the
last guard frame, the cycle is too short and the result may be aliased** — lengthen the black
run and re-run rather than reporting the number.

---

## Cost estimate

At 120 Hz, two frame periods is 16.67 ms. Sweeping that at 20 µs steps is 833 points. With a
burst of 8 frames per point and ~205 ms of re-arm turnaround (`scan_latency.py`), a point
costs ~270 ms:

    833 points x 0.27 s  ~=  3.7 min per colour per mode
    4 fields (W,R,G,B)   ~=  15 min per mode
    6 modes              ~=  1.5 h of capture

Well inside a day, and the step size is the knob if it needs to be finer.

---

## What this rig also answers, later

Deliberately **out of scope here** — noted so the fixture is not rebuilt for them:

- **Cross-frame contamination.** Does the projector blend frame N with N−1 (motion
  interpolation, temporal dithering)? For structured light this is arguably the highest-risk
  unknown in the system, and the same rig answers it by alternating fields.
- **Radiometric response.** Input code → emitted energy, and whether PWM structure changes
  with grey level.
- **Direct G3/G5 closure.** Once the gap map exists, the delay that parks the blind window is
  read off it rather than searched for.

---

## Open questions

1. ~~Can the offline pattern generator emit a saturated primary flat field?~~
   **Answered 2026-09-03: a new bitstream with custom SLI patterns, run offline.** See above.
2. Which offline frame rates will the profiling bitstream emit? The colour sequence is
   expected to change with rate, so this sets the size of P6.
3. Does the negative-vsync polarity fix land before P6, or does P6 ship offline-only?
   (Offline sidesteps the defect; it only bites if passthrough is compared.)
4. Which projector is the reference unit — and is it the one production will use?
5. **Do the production SLI patterns use all three primaries, or one?** This decides which
   P2b curve is authoritative. A single-primary pattern only has to dodge that primary's
   flashes, and those gaps are far wider than the union's — it could turn a marginal parking
   problem into a comfortable one. See P2b.

---

## Seeing it before measuring it

`host/plot_frame_timeline.py` draws both halves on one axis and lets the delay and exposure
be slid against each other. The **sensor rows are real** — 44.1 µs gap, the
`T − 44.1 − 10` clamp, the 375 ns and 10 ns quanta, the jitter knee. Its computed max
exposure of 8279.2 µs at 120 Hz independently reproduces the fabric's own ≤ 8280 µs clamp,
which is a small but real check that the model matches the hardware.

**The LED rows in it are invented** and labelled as such on the page. `--profile` swaps them
for the measured table once P5 produces one; the script refuses a profile whose frame period
does not match the axis rather than stretching a measurement onto the wrong time base.

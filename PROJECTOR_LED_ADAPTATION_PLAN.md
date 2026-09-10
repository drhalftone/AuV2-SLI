# Holding the projector's LED drive still across an SLI sequence

_Discussion note, 2026-09-09. Nothing here is built._ Observed on another machine:
the **Optoma ML750ST** raises its LED output to brighten the image. Not yet
reproduced on this bench.

**Scope: the projected SLI pattern sequence only**, and within it **the high-frequency
fringes are the crucial ones** — they set measurement precision. Not room light, not
the desktop, not anything the projector sees outside a scan.

**Working hypothesis:** LED drive is set by the **mean R, G and B pixel value of the
projected frame**. If every frame in the sequence carries the same per-channel mean,
the LED drive never moves and the projector is once again the static, memoryless map
the linearization assumes.

That hypothesis is the whole plan. It converts an analogue problem — "the projector
is adaptive" — into an arithmetic one: **compute the mean of every frame we send, and
make them equal.** The mean of a frame we generate is known exactly, in advance,
without measuring anything.

---

## 1. Why constant mean is sufficient

For N-step phase shifting with a per-frame gain `γ_k` and offset `β_k`:

    I_k = γ_k·(A + B·cos(φ − 2πk/N)) + β_k

The estimator `φ̂ = atan2(Σ I_k sin θ_k, Σ I_k cos θ_k)` factors out any `γ` **common
to all k**, and `Σ sin θ_k = Σ cos θ_k = 0` kills any `β` common to all k. Absolute
brightness and absolute black level are both irrelevant to phase.

**Only frame-to-frame variation hurts.** We do not need to know `γ`, model it, or hold
it at any particular value — only to keep it the same across the 8 phase frames.

### 1.1 The three frequencies do not matter equally

This is what decides how much of the rest of this document is worth doing.

| | role | what an error costs |
|---|---|---|
| **hi** (36×) | carries the depth measurement | phase error → **depth error, directly** |
| mid (6×) | unwraps hi | only has to pick the right **integer** fringe order |
| lo (1×) | unwraps mid | only has to pick the right **integer** fringe order |

The coarse frequencies feed a `round()`. With a ratio of 6 between levels, the
unwrapping step is

    Φ_next = φ_next + 2π·round[ (6·Φ_this − φ_next) / 2π ]

and the rounding lands on the right integer as long as the error in `Φ_this` stays
below about **π/6 ≈ 0.52 rad (30°)**. That is a loose tolerance — roughly an order of
magnitude looser than anything the high frequency needs. Below it, coarse-frequency
error costs *nothing at all*; above it, the fringe order jumps and the result is
catastrophic. The failure is a cliff, not a slope.

So the question to ask of every effect below is not "does it perturb the phase?" but
**"does it perturb the high frequency, or does it push a coarse phase over 30°?"**

---

## 2. What the projector is actually fed today

From `sources_1/imports/RTL/pattern_gen.v`:

- **28-frame sequence**: 3 frequencies × 8 phases = 24 fringe frames, then 4 flash
  frames (`frq_use == 3`, camera-paced seq 24..27). Order is lo (0-7), mid (8-15),
  **hi (16-23)**, flash (24-27), wrap.
- **Fringe pixels**: `mcos[]`, a 4096-entry ROM of `round(255·(0.5 + 0.5·cos))`.
  Mean over a **full period** is 127.5 — exactly the target.
- **Phase step**: `frm·512`, an exact 1/8 of the master period, applied as a ROM
  address offset. The 8 steps are exactly equal — which §9 leans on.
- **Periods**: `P_lo = 288·ceil(F/288)`, `P_mid = P_lo/6`, `P_hi = P_lo/36`, where
  `F = vact` (orient=0) or `hact` (orient=1). The 288 exists so all three periods are
  multiples of 8, making the 1/8 phase shift an integer pixel count (no-banding rule,
  README §13.1).
- **Flash frames**: full-field `0x00` / `0xFF`, alternating, **bypassing the
  radiometric LUT** so the projector emits true black and true white.
- **Channel enables**: `out_red = rgb_sel[2] ? pat_out : 8'h00`. A disabled channel is
  driven to zero, not left alone.

---

## 3. Where the mean is not constant today

### 3.1 Partial periods — and the good news

`P_lo = 288·ceil(F/288)` forces **P_lo ≥ F**, so the coarse pattern covers less than
one full period. The frame mean then depends on *which part* of the cosine is on
screen, and the phase shift moves exactly that. For a field spanning `F/P = M + f`
periods,

    mean(φ) = 127.5 + 127.5·A·cos(πf + φ),     A = |sin(πf)| / (π·(M+f))

`A = 0` when `f = 0`; any partial period gives a phase-dependent mean:

| F | freq | P | periods across F | mean swing | frame mean range |
|---|---|---|---|---|---|
| 1024 | lo | 1152 | 0.889 | ±15.6 | 111.9 .. 143.1 |
| 1024 | mid | 192 | 5.333 | ±6.6 | 120.9 .. 134.1 |
| 1024 | **hi** | 32 | **32.000** | **0.0** | **127.5 (exact)** |
| 1280 | lo | 1440 | 0.889 | ±15.6 | 111.9 .. 143.1 |
| 1280 | mid | 240 | 5.333 | ±6.6 | 120.9 .. 134.1 |
| 1280 | **hi** | 40 | **32.000** | **0.0** | **127.5 (exact)** |

_Predicted from the RTL, not measured._

**The high frequency is already exact.** 32 whole periods across the field at both
resolutions, so every one of its 8 phase frames has an identical mean of 127.5 — and
therefore an identical LED drive, which then cancels exactly per §1. The frequency
that carries the measurement is the one frequency this design already gets right.

The reason is worth keeping in mind, because it is the general rule: **with an integer
number of periods across the field, a phase shift is a cyclic permutation of the
pixels.** A cyclic permutation preserves the histogram exactly, hence the mean exactly
— *for any pointwise function of the pattern, including whatever we later load into
`corr[]`.* Integer periods buy mean-invariance permanently and for free.

So the ±12 % swing lands on **lo**, and ±5 % on **mid** — the two frequencies that only
have to pick an integer. What matters is whether that pushes them past the ~30 °
unwrapping cliff of §1.1.

A gain that varies sinusoidally with the phase index — which is exactly what a
mean-driven LED does here, since the mean varies as `cos(πf + φ_k)` — is the worst
case, because it aliases straight onto the phase-shift basis. Expanding the estimator,
the surviving term adds a fixed vector of magnitude `ε·A` against a signal of `B`, so

    phase error ≲ ε · (A/B) radians

with `ε` the relative gain swing and `A/B` the inverse fringe modulation at the camera:

| ε | A/B = 1 | A/B = 2 | A/B = 3 |
|---|---|---|---|
| 0.02 | 0.020 | 0.040 | 0.060 |
| 0.05 | 0.050 | 0.100 | 0.150 |
| 0.10 | 0.100 | 0.200 | 0.300 |
| 0.20 | 0.200 | 0.400 | 0.600 |

against a tolerance of 0.52 rad. The unknown is `ε`: a ±12 % mean swing produces a
gain swing of `0.12 × η`, where `η` is the projector's gain response per unit relative
mean — **not yet measured**, and the single number this section turns on. Unless `η`
is large, lo and mid sit comfortably inside the cliff and §3.1 costs nothing.

### 3.2 The LUT moves the mean

`mean(corr[cos]) ≠ corr[mean(cos)]`. The cosine's values are arcsine-distributed and
`corr[]` is deliberately nonlinear, so the projected mean after correction is not 127.5
even when the raw pattern's is.

This is *not* a phase problem — by §3.1 it is common to all 8 hi frames, and common
gain cancels. It is an **operating-point** problem: it moves the fringe away from the
mean the calibration sweep was taken at, and it makes the LUT self-referential, since
the curve changes the APL that sets the `γ` the curve was built to invert. Cheap to
fix (§5), so fix it.

### 3.3 The flash frames are a 0 %↔100 % excursion inside the sequence

Seq 24..27 are full-field `0x00` and `0xFF` — the two most extreme frames possible —
inside the sequence we are trying to hold still, and they bypass the LUT as well.

The sequence order works in our favour: the **hi block (16-23) sits immediately before
the flash and eight-plus frames after it**, so it is the block furthest from the
disturbance. The frames that eat the settling transient are lo's, which per §1.1 can
absorb far more error. Whether any of it still reaches hi depends entirely on the
settling time, which is §11 step 4.

### 3.4 Channel enables change the per-channel mean

Disabling a channel drives it to `0x00`, so that channel's mean goes to zero. Under a
per-channel-mean hypothesis a green-only scan is a completely different LED operating
point from an RGB scan. **Never change `rgb_en` mid-sequence**, and give each channel
combination its own calibration.

---

## 4. Fixing the fringe frames — probably nothing to do

Given §3.1, this section is now **contingent**, not planned. The high frequency needs
no fix. Do any of the following only if step 5 of §11 shows lo or mid actually
approaching the unwrapping cliff.

The condition for zero wobble is `F/P ∈ ℤ` at all three frequencies. With the 1:6:36
ratios and `P_lo = 288b`, that holds **exactly when F is a multiple of 288** — then
`b = F/288`, `P_lo = F`, and the three frequencies span 1, 6 and 36 periods:

    F ∈ { 288, 576, 864, 1152, 1440, 1728, ... }

`1152 × 864` is notable: **both** axes are multiples of 288 (288·4 and 288·3), so it
works in either orientation with no RTL change. The catch is that it only helps if the
projector displays it **without scaling** — any rescaling resamples the fringe and
breaks both the integer-period property at the DMD and the no-banding rule. The
ML750ST's native panel size therefore decides whether this option exists at all.

Options, cheapest first:

1. **Do nothing.** The default, and the likely outcome. Justified as soon as step 5
   shows the lo/mid phase error is well inside 30°.
2. **Pick a field size that is a multiple of 288**, if the projector shows one 1:1.
   Zero code change.
3. **Ballast the mean.** Leave the fringe region alone; add a border outside the
   measurement FOV whose level is set per phase frame to cancel the computed wobble,
   holding the frame mean at 127.5. Keeps the fringe math and no-banding untouched;
   costs projected area and a small RTL block.
4. **Change the frequency ratios** so integer periods are achievable at the native
   size — e.g. 1:8:32 is exact at F = 1280 (P = 1280/160/40). A real RTL change
   touching the `INC6`/`INC36` derivation and the unwrapping arithmetic. Hard to
   justify unless 2 and 3 are both unavailable.

---

## 5. Fixing the LUT's mean shift

Emitted light is what the LUT linearizes, so the fringe's DC level and amplitude are
free design choices. That freedom is enough to pin the projected mean.

Instead of `corr[a]`, load `corr'[a] = corr[α·a + δ]` with `α`, `δ` chosen so

    Σ_a p(a) · corr'[a] = 127.5

where `p(a)` is the known arcsine distribution of the master ROM's values over a full
period. A 1-D root-find on the host, done once when the table is built. **Zero hardware
change** — still a 256-byte table through the same `lut_din`/`lut_dout` seam. Costs a
small loss of modulation depth, which is a real cost at the high frequency where `B` is
already reduced by MTF, so keep the trim as small as the arithmetic allows.

---

## 6. Fixing the flash frames

The flash frames serve two jobs — albedo/texture capture, and historically a per-frame
trigger. Separate them.

- **Trigger**: use the top-left-pixel path (`aa181ba`) instead of a full-field flash.
  One pixel in 1.31 M is a mean change of ~0.0002 levels — negligible.
- **Texture**: a genuine full-white frame is an irreducible mean excursion. Choices:
  (a) leave the ordering as it is and rely on the hi block already being furthest from
  it (§3.3), discarding the first N lo frames after the wrap, N from the measured
  settling time; (b) a mid-gray texture frame at mean 127.5, scaled, trading albedo SNR
  for a pinned mean; (c) shrink the flash to the measurement ROI and ballast the
  surround, keeping texture at full white over the region that matters.

(a) is free and may well be enough. (c) is the same ballast block as §4 option 3.

---

## 7. The dark window — a reference that costs no screen area

At **~8000 µs after the trigger the RGB output is dark**, and a 5 µs gated exposure
there reads only background light. This is a free reference channel: reserve a slot in
*time* rather than a patch in *space*, so every captured frame can be paired with a
sample reporting the projector's state independent of the pattern.

Which quantity it reports depends on what the window is:

- **LEDs off** → ambient + optics leakage + camera floor: a per-frame **offset (`β`)
  reference**.
- **LEDs lit, mirrors parked** → scales with drive current: a direct per-frame **gain
  (`γ`) monitor**, which is far more valuable — it measures `η` (§3.1) directly.

**The test:** change frame content enough to move the drive and see whether the 8 ms
reading tracks it. Tracks → gain monitor. Flat → offset reference.

Practical constraints on the gate:

- **Map the whole 16.7 ms**, not just the one delay — width, edges, and whether dark
  windows recur (frame repeat would put several per frame).
- **Window width must exceed 5 µs plus trigger jitter**, or captures clip into a lit
  segment and read high.
- Position may move with input mode, frame rate or brightness mode; re-find it after
  any projector setting change.
- RGB segments are separated in time, so gating at other delays measures **per-channel**
  drive independently — how §3.4 gets tested.

**Do not use short gates to measure the fringe itself.** The DMD builds gray by PWM
within each colour segment, so a 5 µs sample lands on one instant of a mirror's binary
duty cycle and reports mirror state, not gray level. Short gate for the dark window,
full-frame integration for anything measuring a level.

---

## 8. The calibration sweep — complementary half-screen

Replace the flat-field ramp. Split the screen and show **level `g` on one half and
`255 − g` on the other**, sweeping `g`.

    (g + (255 − g)) / 2 = 127.5    for every g

The frame mean is pinned **exactly**, at every step, by construction — and pinned at
127.5, where the master cosine ROM already sits, so the calibration operating point and
the fringe operating point coincide by design. That is precisely what the flat-field
sweep got wrong. Both halves are exactly equal in pixel count for a left/right split of
1280 or a top/bottom split of 1024, so the mean is exact, not approximate.

Measure a centred ROI in each half: the near half traces `d(g)` forward, the far half
traces `d(255 − g)` — the same curve backwards. Average into one LUT.

**Refinements that matter:**

- **Half the sweep suffices for coverage.** At step `g` the halves show `g` and
  `255 − g`, so `g = 0..127` already covers all 256 levels. The full `0..255` gives
  real 2× averaging, not extra coverage.
- **Normalize each ramp before averaging, not after.** The ROIs sit at different field
  positions with different optical gains (short-throw falloff, screen gain vs angle,
  camera vignetting):

      near ROI:  s_N·d(g)       + β_N
      far  ROI:  s_F·d(255 − g) + β_F

  The far ramp is *reversed in g*, so averaging raw values with `s_N ≠ s_F` folds a
  reversed copy onto a forward one and injects a systematic **S-shaped distortion**.
  Two-point normalize each ROI's ramp to its own range first — each half sweeps the
  full range, so each has a valid min and max — then average. This is **not** what
  `toneCorrectionCurve()` does today; it normalizes once, globally, at the end.
- **Verify the reindex against an asymmetric input.** Averaging requires flipping one
  ramp, and a flip or off-by-one yields a smooth, plausible curve that symmetry will
  not expose — with a deliberate left-right flip already present in
  `toneCorrectionCurve()` (`lautonecorrectionwidget.cpp:163`) for it to compose badly
  with. Test with a known-asymmetric curve.
- **Sample the dark window at every step.** Two captures per step: a full-frame
  exposure for the ramp, a 5 µs gate at 8 ms for the LED state. A flat gated reading
  across the sweep confirms the mean hypothesis *in the same data that produced the
  LUT* — the sweep validates its own premise at no extra cost.
- **Weight the fit toward the high frequency's working range.** The hi fringes are
  attenuated by projector and camera MTF at a 32-pixel period, so they occupy a
  narrower band around mid-gray than a full-swing ramp suggests. Accuracy at the ends
  of the curve matters less than accuracy where the hi fringe actually lives.
- **Bound the stray light.** A half-black/half-white field throws scattered light into
  the dark half — flare, screen inter-reflection, room bounce — scaling with `g`. That
  biases the **toe** of the curve. Keep both ROIs centred in their halves, away from
  the seam, and measure it: compare the dark ROI against a full-field-black reading.
  The dark-window monitor does **not** catch this, since stray light exists only while
  mirrors are on.
- **`corr[]` must be identity during the sweep**, or we measure `corr∘d` instead of `d`.

**The residual risk is mean vs. histogram.** The complementary split holds the mean
fixed, but its histogram is two spikes that spread monotonically apart as `g` runs
0→255: a flat field at `g = 128`, half-black/half-white at `g = 255`. If the LED
control looks at anything beyond the mean — peak white, a percentile, contrast — the
drive tracks `g` monotonically and that artifact lands in the LUT looking exactly like
projector gamma. Same failure as the flat-field sweep, new disguise. The dark-window
sample is what catches it; if the drive does move, the surround must match the fringe's
*histogram*, not just its mean.

---

## 9. Verification — and a prior question about the LUT

The tone curve is not evidence; a contaminated sweep also produces a smooth, plausible
curve. The real test is in the phase. Project linearized fringes on a **flat plane**,
unwrap, fit and subtract the plane, and read the residual ripple. Report peak-to-peak
phase ripple in radians and equivalent depth error in mm at the working standoff, **at
the high frequency** — that is the number that matters.

Before investing in any of §8, though, there is a question worth settling, because it
may shrink the whole job:

> **How much does 8-step phase shifting already reject the projector's gamma?**

For N-step shifting with exactly equal steps, a harmonic of order `m` in the response
perturbs the phase **only when `m ≡ ±1 (mod N)`**. At N = 8 that means harmonics
2, 3, 4, 5 and 6 — the dominant ones for any smooth gamma curve — **cancel outright**,
and the leading residual is the 7th and 9th. The design already guarantees the exact
equal steps this relies on (`frm·512`, §2).

So the 8-step scheme may be far more gamma-tolerant than a 3-step one, and the
linearization LUT may be correcting something already suppressed to the 7th harmonic.
Measure the flat-plane ripple **with `corr[]` at identity** first. If the residual is
already inside the depth budget, §8 is optional and this document ends at §5.

If it is not, run the ripple test four ways — no correction; the old flat-field curve;
the complementary-split curve; and split-curve plus any §4 fix — and keep the number as
the standing acceptance metric.

---

## 10. Provenance

Every saved `.tcc` should record: projector model, serial, firmware; display mode,
brightness mode, and every adaptive setting; LED hours; warm-up elapsed; input
resolution and whether the projector scaled it; `rgb_en` and `orient`; camera exposure
and both ROIs; and the FPGA bitstream. Any `.tcc` taken before this is understood
should be treated as contaminated by `γ(g)` and not trusted.

---

## 11. Order of work

Measurement first, and cheapest-decisive first.

1. **Reproduce the original observation here**, with every projector setting written
   down. Confirm it is adaptive behaviour and not a display-mode difference.
2. **Flat-plane ripple at the high frequency with `corr[]` = identity** (§9).
   _Gate: if it is already inside the depth budget, most of this document is optional.
   This is the cheapest measurement that can cancel the most work, so do it first._
3. **Characterize the dark window** (§7): map the full 16.7 ms, get width, edges and
   recurrence, and settle gain-monitor vs offset-reference.
4. **Test the mean hypothesis** — frames of equal mean but different histograms (flat
   127.5, complementary split, sinusoid) read through the monitor. _Gate: if drive
   tracks histogram rather than mean, §8's surround needs redesign first._
5. **Measure the settling time.** Step the mean, watch the monitor per frame. Sets the
   sweep dwell and how far the flash transient reaches into the fringe blocks (§3.3).
6. **Measure `η`** across the 8 lo-frequency phase frames (§3.1) and compare the
   implied phase error against the 30° cliff. Decides whether §4 has anything to do —
   expected answer: no.
7. Only then build the complementary-split sweep (§8) and the mean-trimmed LUT (§5).

Steps 1–6 are measurement. No RTL or host changes are proposed until step 6 has a
number attached.

---

## Open questions

- Is the 8 ms dark window LEDs-off or mirrors-parked? (Decides §7.)
- What is `η`, the gain response per unit relative mean? (Decides §4 entirely.)
- Is the LED drive really mean-driven, or histogram/peak-driven? (§8's residual risk.)
- Is it per-colour-channel? Temporal gating at the R/G/B segments answers this.
- Is the 8-step harmonic rejection already enough to make the LUT unnecessary? (§9.)
- What is the ML750ST's native panel size, and is any 288-multiple mode shown 1:1?
- Does the adaptation key off the HDMI input signal or the internally scaled image, and
  does it differ between pass-through and offline mode?

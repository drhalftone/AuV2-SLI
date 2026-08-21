# Two-point calibration — where it could live, and what it would cost

_Discussion note, 2026-08-21. Nothing here is built._ Captured so the reasoning
survives to whenever this gets picked up.

The question: apply a two-point (offset + gain) correction to the camera, and can
the correction be embedded in the bitstream?

    corrected = (raw - dark[x,y]) * gain[x,y]

---

## 1. The memory arithmetic decides the shape of the answer

| | |
|---|---|
| Pixels | 1280 × 1024 = **1,310,720** |
| Per-pixel offset + gain, 8 bits each | **2.62 MB** of coefficients |
| Offset only, 8 bits | 1.31 MB |
| Artix-7 100T block RAM | 135 × RAMB36 = 4,860 Kbit = **607 KB** |

**Full per-pixel maps are about 4× too large for on-chip memory**, and that is
before anything else claims a BRAM. Offset-only is still double. So per-pixel
correction cannot be a BRAM lookup on this part, at this resolution, in any
arrangement.

> Note the merged design currently uses **3.5 of 135 BRAMs**. The constraint is
> not that BRAM is busy — it is that there is nowhere near enough of it.

That leaves three real options.

---

## 2. Option A — per-pixel, coefficients in DDR3

Possible: there is 256 MB and a working MIG. The cost is bandwidth, and the
budget is already interesting.

| Traffic | MB/s |
|---|---|
| Capture writer (during the 4.55 ms burst) | 360 |
| Reader feeding USB at 120 Hz | 197 |
| **Coefficient read, per-pixel, during the burst** | **~360** |
| Total against a 1600 MB/s theoretical bus | **~900** |

Feasible on paper at ~56% utilisation, but DDR3 does not deliver its theoretical
peak once reads and writes interleave — turnaround penalties are real, and this
design has already been bitten by a sustained-rate shortfall that looked like a
transient (the cfifo overflow behind the rolling-frame bug).

It also puts a third master on the MIG, and **M3 exists precisely because
subsystems that share the memory controller can stop being independent.** Test
3d (`ldrop` must not move during an HDMI mode change) would need re-running with
real weight behind it.

Verdict: possible, but it is a substantial project and it spends the margin in
the place where this design's failures have historically lived.

---

## 3. Option B — column-wise (or row+column), coefficients in BRAM

If the dominant artifact is **column fixed-pattern noise** — usually the case on
CMOS, from per-column amplifier offsets — the table collapses:

| | Entries | Size |
|---|---|---|
| Column offset + gain | 1280 × 2 | **~2.5 KB** = 0.4% of BRAM |
| Row + column, separable | (1280 + 1024) × 2 | ~4.6 KB |

**This is essentially free**, and the project already has the mechanism: the SLI
control plane uploads a 720-byte row LUT (`0x00`), a 1280-byte column LUT
(`0x01`) and a 256-byte intensity correction (`0x02`) over the `0xA5` protocol,
with `host/upload_corr.py` driving it. Same transport, same framing, already
verified byte-exact by `test_silicon.py`.

Verdict: the natural fit, IF the FPN is columnar. That is a measurement, not an
assumption — see §6.

---

## 4. Option C — do it on the host

On the host it is a numpy expression over 1.31 Mpix: a couple of milliseconds
per frame, full per-pixel maps, no FPGA resources, and trivially changeable when
calibration drifts with temperature, exposure or gain.

**It costs no bandwidth to skip.** Corrected 10-bit is the same number of bits as
raw 10-bit, so doing the correction in the FPGA saves nothing on the link. The
FPGA version only wins if you correct *and then* reduce bit depth, or if
something on-chip needs corrected pixels.

Worth weighing against the viewer's measured budget: the render path is ~9.7 ms
per frame today, so a 2–3 ms correction is not free at 120 fps, but it is also
not where the time goes.

---

## 5. "Embedded in the bin" — two separate things

| | Feasible? | Consequence |
|---|---|---|
| Correction **logic** in the bitstream | Yes, easily | 8 parallel multiply-adds at the kernel stage, 36 M kernels/s. 8 of 240 DSPs. Must sit BEFORE the 10-bit packing. |
| Correction **data** baked in as ROM init | Yes for small tables | **Recalibration becomes a Vivado rebuild** rather than a host command |

Since calibration drifts with temperature, exposure and gain, baking the
coefficients in is the wrong trade. Upload them over the existing control link
and keep the bitstream fixed.

One datapath detail: `(raw − dark) × gain` wants headroom. Correcting in 10 bits
and rounding back to 10 loses precision at the ends; a 12-bit intermediate then
rounding is the usual answer. That interacts with the packed-10 format, which
assumes exactly 10 bits per pixel.

---

## 6. Two things to settle BEFORE choosing

**Characterise the FPN first.** Per-pixel and per-column need completely
different solutions — DDR3 and a serious project versus 2.5 KB of BRAM. A dark
frame and a flat field, differenced and examined column-wise versus pixel-wise,
answers it in an afternoon and decides everything above.

**Apply the CDS/sequencer program first.** `sources_1/imports/RTL/cam_cds_rom.v`
holds Avnet's `vita_cds_seq` table, committed but **not yet wired in**. 91 of its
104 entries are registers 384–474 — the sensor's CDS / sequencer TIMING PROGRAM —
and it has never been uploaded, so the pixel array has been running on power-on
defaults throughout all of this work, including the 120 Hz bring-up.

That block materially affects fixed-pattern noise. **Calibrating around a
misconfigured sensor means the coefficients encode a problem that could have been
removed instead.** Fix the configuration, re-measure the FPN, and the calibration
question may look different — or partly answer itself.

---

## 7. Suggested order

1. Wire in `cam_cds_rom.v` and compare FPN before/after. It is a wholesale rewrite
   of sensor timing registers, so it wants its own bring-up rather than being
   folded into other work.
2. Capture a dark frame and a flat field; decide whether the residual FPN is
   per-pixel or columnar.
3. If columnar → Option B, following the existing uploadable-LUT pattern.
   If per-pixel → start on the host (Option C) and only move it into DDR3 if
   there is a concrete reason the host cannot own it.

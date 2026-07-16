# PYTHON 1300 Receiver Chain — Fresh-Eyes RTL Review

Conclusions from an independent, adversarial review of the camera RTL, written into the repo
so they aren't lost in chat. Two passes:

1. **Phase 2 integration diff** (commit `3dad19d`) — 4 reviewers over the clocking rewire,
   the boot/SPI arbitration, `cam_line_buf`, and the build/XDC.
2. **The pre-existing receiver chain** — 4 reviewers, one per module (`cam_lvds_rx`,
   `cam_align`, `cam_sync_decode`, `cam_spi_master`).

**Method.** Each reviewer started fresh (no author bias), read the *prior* code and the
primary sources — the datasheet extract `docs/datasheets/python1300.txt`, the OVC reference
decoder `docs/reference/ovc_python_decoder.v`, and `CAMERA_SENSOR_PROTOCOL.md` — then judged
the RTL against them, and specifically hunted for **what the testbenches do not exercise**.
Every finding below was re-verified against the actual code before being recorded.

---

## Headline

**The receiver chain's clean-input logic is correct in every module.** The things most feared
were checked against independent references and hold:

- **`cam_sync_decode` de-interleave** (the "scrambled-but-looks-like-an-image" trap): the
  alternating-parity kernel mapping is proven bit-for-bit against **both** the golden model
  **and** the OVC reference decoder's `UNSWAP_KERNELS`. Sync-code constants match datasheet §5.
- **`cam_spi_master`**: verified against the **datasheet text itself** (§ SPI, L1428–1513),
  including the asymmetric miso launch edge pinned by `ts_miso = tsck/2 − 10` — not merely
  self-consistent with the co-authored model.
- **`cam_lvds_rx`**: ISERDES reset sequencing (synchronous to CLKDIV, master+slave released
  together), master/slave cascade wiring, and the MSB-first bit-order "identity" all correct.
- **`cam_align`**: 8-consecutive-TRAIN lock, no false-lock on a rotation (`0x3A6` has no
  rotational symmetry — proven), 1-wordclk bitslip pulse, per-lane isolation, `aligned` held.

So the fast-written logic is **sound**. The exposure is **not** in the logic — it is in
bring-up robustness, silicon timing, observability, and test coverage. In priority order:

---

## P1 — SILICON: the 720 Mbps LVDS capture is unconstrained, and the interface is *mesochronous*

**This is the one that can stop capture from working at all, and nothing in the build or the
sim would warn you.**

- `cam_lvds_rx` samples each lane directly on the BUFIO bit clock with `IOBDELAY("NONE")` (no
  `IDELAYE2`), and `Au2_pt.xdc` has a `create_clock` on `cam_clkout` but **no `set_input_delay`**
  on the data/sync lanes. Vivado therefore does **zero setup/hold analysis** on the 1.389 ns
  bit window — timing closes "met" while saying nothing about whether data is captured.
- The datasheet (`python1300.txt`) is explicit about why this matters:
  - L360–361: *"fserclock … Clock output for **mesochronous** signaling."*
  - L760: the output clock is *"skew aligned to the output data channels."*
  - L372: *"Channel to channel skew (**Training pattern allows per channel skew correction**) —
    50 ps."*

  **Mesochronous** = same frequency, *fixed but unknown phase* between clock and data. The
  receiver is **required** to dynamically align each channel, and the sensor gives you the
  training pattern (reg 116 `0x3A6` on data, reg 126 on sync) precisely to do per-channel
  **skew/eye correction**. Static `set_input_delay` cannot close a mesochronous interface — the
  correct structure is dynamic phase alignment.

- `bitslip` (in `cam_align`) only rotates the **word boundary**; it can never fix a **sub-bit
  sampling-phase** error. So a lane whose data edge lands near the sample edge (from the fixed
  mesochronous phase + up to 50 ps inter-channel skew + PCB/package mismatch) will fail to
  lock, or lock and drop out under temperature/voltage drift.

**Why the sim hides it:** `python1300_lvds_model.v` places each `clock_out` edge in the *dead
center* of the data eye (race-free by construction) and never drives the `SKEW_*` params off
zero. The bit-clean model is *structurally incapable* of exposing this. `tb_cam_decode` passing
is not evidence about the mesochronous margin.

**Required fix (a hardware-bring-up subsystem, not a blind edit):**
1. Add `IDELAYE2` (VAR_LOAD) on all 5 data/sync lanes, `ISERDESE2` set to `IOBDELAY="IFD"`
   (sample the delayed `DDLY`), a per-clock-region `IDELAYCTRL` with a 200 MHz `REFCLK` (the
   design already generates `clk200`), tied by an `IODELAY_GROUP`.
2. A per-lane **eye-centering** step: sweep the IDELAY tap while the sensor transmits the
   training pattern, find the widest run of stable-`0x3A6` taps, and park at its center. This
   is the "per channel skew correction" the datasheet intends — it runs *before* `cam_align`'s
   bitslip word-alignment.
3. `set_max_delay`/`set_min_delay` (or the ISERDES dynamic-phase methodology) to constrain what
   remains.

**Why it is NOT implemented in this pass:** its correctness can only be judged on real silicon
(you are centering an eye that a bit-clean sim does not have), and validating it in simulation
first requires **making the model realistically edge-aligned with injectable skew** — at which
point the *current* no-IDELAY receiver would (correctly) fail. Building and committing an
IDELAY sweep blind would be exactly the "compiles but unverified" failure this review exists to
prevent. It is specified here as the **#1 task for the #12 bench bring-up.**

---

## P2 — ROBUSTNESS: no recovery from real link imperfection

On hardware, sync codes are occasionally corrupted (that is why the sensor sends CRC) and
clocks hiccup. The chain assumes neither ever happens.

- **`cam_sync_decode` has no mid-frame resync.** `S_LINE` handles only `IMG`/`BL`/`LE`
  (verified, lines 105–109); `LS` and `FS` are ignored. A single garbled/dropped `LE` leaves
  the FSM in `S_LINE`, ignores the next line's `LS`, keeps incrementing `kcol` across the line
  boundary — merging lines, **overflowing the 11-bit column counter** (wraps at 2048), and
  corrupting the rest of the frame until the next `frame_start`. A garbled `LS` drops a line and
  offsets row counting. (OVC handles `LS` as an unconditional line-restart *and* has a
  `state_rst` watchdog; this has neither.)
  *Fix:* in `S_LINE`, treat an unexpected `LS` as a line-restart and `FS`/`FE` as an abort; add
  a stuck-state watchdog.
- **`cam_lvds_rx` + `cam_align` are one-shot.** `serdes_rst` saturates and never re-asserts;
  `cam_align` latches `A_DONE`/`locked` and never re-aligns. A clock hiccup or PLL-unlock
  permanently corrupts alignment until FPGA reconfiguration.
  *Fix:* a link-loss detector (word off-`TRAIN` for N idle cycles → re-arm both the ISERDES
  reset and the per-lane align FSM).

---

## P3 — OBSERVABILITY: blind during bring-up

- `lane_locked` / `lane_failed` (`cam_align`, 5 bits each) are tied to `open` at the top. If a
  lane can't lock, `aligned` never asserts, `cam_sync_decode` stays held in reset, and **nothing
  indicates which lane or that a timeout occurred** — a silent hang.
- CRC words are **consumed but never validated** (`cam_sync_decode` `S_LINE_END`), so corrupt
  pixels are emitted with no error signal. §5.2 calls CRC "a free, self-checking correctness
  signal worth wiring up."
  *Fix:* surface `lane_locked`/`lane_failed` + an align-timeout + a CRC-error flag to a UART
  status register (e.g. alongside the reg-0x39 boot status).

---

## P4 — TEST COVERAGE: why all of the above is invisible today

Every testbench uses bit-clean golden models — zero skew, no jitter, dead-center sampling,
32-wide images, a single clean frame, no sync-code corruption, no dead lanes:

- **The 11-bit `kbase` fix is itself untested** — `tb_cam_decode`'s 32-wide line (4 kernels)
  never reaches the wrap an 8-bit counter would have hit. The 160-kernel parity alternation and
  the column-overflow path are unverified.
- `tb_cam_align` is a **single fixed point** — it may lock in 0 bitslips and exercise none of
  the slip/settle/convergence path; the "locks from 4 phases + 0.9 ns skew" claim is not
  reproducible from anything in the tree. The `lane_failed`/`MAX_SLIP` give-up path and per-lane
  independence are never exercised (the model emits `TRAIN` on every lane, identically).
- `cam_lvds_rx`: `SKEW_*` params exist but are never driven off zero; jitter, cold-start
  transient, and per-lane independent bitslip are untested (the no-IDELAY choice is validated by
  a TB structurally unable to exercise it).
- `cam_spi_master`: start-while-busy and reset-mid-transfer are correct by inspection but
  untested; miso setup/hold margin is never stressed (model drives miso idealized).
- `cam_sync_decode`: no full 1280-wide line, no corrupted/missing sync codes, no multi-frame,
  no `in_black`/CRC checks.

*Direction:* make the golden LVDS model **mesochronous with injectable per-lane skew + jitter**,
add a full-width multi-frame decode test with sync-code corruption injection, and sweep
`tb_cam_align` across all 10 phases + a dead-lane case. These need no RTL change and would give
the P1/P2 assumptions real evidence — but several are best written *with* the board so the
hardening is validated against real corruption rather than guesses.

---

## Fixed in this pass (safe, verified)

- **`cam_line_buf` synthesizability** (Phase 2 review) — 8-write-port RAM → one 64-bit word per
  kernel. `tb_cam_line` 34/0.
- **`clk100` global buffer** (Phase 2 review) — whole domain onto one BUFG; WNS −17 ns → +2 ns.
- **Boot status stickiness, full-line `kbase` (11-bit), reset-on-boot-GO, build-script
  isolation/retry** — see commit `859db91`.
- **`cam_lvds_rx` header** reconciled with its (correct, required) ISERDES reset.
- **`cam_sync_decode` `in_black`** cleared at `line_start` (was stale from the prior black line;
  the current consumer sidestepped it via `kvalid`, so latent, not live).

## Status

| Area | Verdict |
|---|---|
| Clean-input logic (all 4 modules) | ✅ correct, cross-checked against datasheet + OVC reference |
| P1 LVDS mesochronous capture / IDELAY | ⬜ **specified; #1 bench-bring-up task** — do not implement blind |
| P2 resync / link-loss re-arm | ⬜ real; fix at or just before bring-up |
| P3 lane / CRC observability | ⬜ small; recommended before bench |
| P4 test realism | ⬜ ongoing; several best done with the board |

# Per-frame headers — design

_Drafted 2026-08-31. Field set agreed: frame index, exposure, trigger delay, and a
vsync-derived timestamp with a host-commanded epoch reset. Two items in §7 are still
my recommendation rather than your decision._

## 1. What exists today

Every packet on the Ft+ IN pipe is already **32 bytes of header + payload**, as two
128-bit words (`cam_frame_ft.v`, state `R_HDR`):

| word | bits | field |
|---|---|---|
| 0 | `[31:0]` | `MAGIC` — `"SLI0"` a video frame, `"SLI1"` a control reply |
| 0 | `[63:32]` | `frame_idx` |
| 0 | `[95:64]` | `{NROW[15:0], NCOL[15:0]}` |
| 0 | `[127:96]` | `{ldrop[17:0], nf_run/slot}` — scan length and padded-frame count |
| 1 | `[31:0]` | `FBYTES` — payload bytes |
| 1 | `[55:32]` | `FBYTES/4` — payload words (24 bits; the value is 409,600) |
| 1 | `[63:56]` | **`tlp`** — top-left pixel of the HDMI frame that triggered this capture |
| 1 | `[95:64]` | **format** — `3` = dense packed 10-bit (4 px in 5 bytes) |
| 1 | `[127:96]` | `~MAGIC` — inverse, so a lost host can resynchronise |

**Why `tlp` lives in word 1 and not the format word.** In pass-through mode the PC
owns the pattern sequence, and a GPU is not a real-time system: it may hold a frame
for an extra refresh or deliver the next one late, and nothing in the video signal
says which happened. `pixel_pipe.v` already samples the top-left pixel of every
incoming HDMI frame and triggers the camera when it changes (`TLP_THRESH = 4` there
rejects GPU dither). Carrying the sampled VALUE in the header lets the host match a
captured frame back to the pattern actually displayed instead of trusting its own
play order.

It went into the spare top byte of `FBYTES/4` because that field is pure redundancy
— the host can divide `FBYTES`. The format word was the obvious alternative and is
the wrong choice: several host tools compare it with `==` or `in (1,3)`, so widening
it would break them silently. Word 0 has only two spare bits.

The value crosses from the HDMI pixel clock to `ui_clk` as a toggle + 3FF + sample,
the same shape as `fs_tog` — not a bare 2FF on the bus, because a torn byte would
make the host match a frame to the WRONG pattern, which is worse than reporting none.
Reads 0 when nothing drives the HDMI input, and on the standalone camera build where
the wire does not exist.

Two of its choices are principles worth preserving, not just details:

* **The format field exists because assuming was tried and failed.** A host that
  assumes rather than switches on it "will read packed bytes as u16 and produce a
  convincing-looking wrong image."
* **`ldrop` is in the header, not only on the status UART**, so a bad frame can be
  *attributed* to a real drop instead of guessed at.

## 2. Why extend it

**A frame does not currently say which projected frame it belongs to.**

Tolerable when the camera was triggered one-for-one with the pattern advance.
Untenable now that triggers **stack**: with a delay longer than a frame, the capture
of pattern *k* arrives while pattern *k + floor(D/T)* is on screen. Every frame is
present and the rate is full — only the PAIRING moves.

The host currently reconstructs that pairing from a delay register, a frame period
and an outstanding count. On 2026-08-31 that reconstruction path produced three
wrong numbers in one afternoon:

* the outstanding counter read **10 when it should have read 0** — a skippable
  equality test on the fire-time, which put every trigger a 167.8 ms counter-wrap
  late *while looking perfectly rate-locked*;
* it then read **0 when it should have read 3**, but only when the delay was an exact
  multiple of the frame period (push and pop colliding in one cycle);
* and the camera's frame-period register **cannot represent anything below
  68.67 Hz**, so a genlocked rate was un-decidable from it — 59.896 Hz and 6.16 Hz
  fit the same reading.

None failed loudly. Each produced a plausible number. An offset wrong by one yields
a phase map that looks right and is wrong everywhere.

**THE RULE:** _anything the host would otherwise infer about a frame is carried in
that frame._ Not read from a register afterwards — by then it describes a different
frame.

## 3. The timestamp: vsync-derived, host-resettable

Two options were considered. **Free-running since power-up** gives an epoch only that
board knows. **Zeroed on a projected frame boundary** gives an epoch defined by the
video signal — and because HDMI passes *through* each camera, every camera in a daisy
chain observes the **same vsync**. One broadcast reset and their timestamps are
directly comparable, with no clock distribution and no host round-trip.

That is the multi-camera and active-stereo case working by construction rather than
by post-hoc alignment, so it is the one to build.

It is carried as **two fields, not one**, because that makes it do double duty:

    vsync_seq[31:0]      which projected frame   (zeroed by the epoch reset)
    tick_in_frame[23:0]  10 ns ticks since that frame's vsync

The timestamp is `vsync_seq x frame_period + tick_in_frame`. But the same two fields
*are* the pairing: "which projected frame did this exposure belong to" stops being
reconstructed and is simply stated.

**Widths.** `vsync_seq` at 32 bits is 414 days at 120 Hz. `tick_in_frame` at 24 bits
spans 167.8 ms, covering frame rates down to ~6 Hz, and shares units with `gl_dly`
and the vsync period so nothing needs converting.

**CAVEAT, and it must be measured before cross-camera timestamps are trusted at fine
resolution:** each pass-through hop adds latency, so camera *N* sees the shared vsync
slightly later than camera 1. Small and constant — phase FIFO plus serialiser — but
real, and a per-hop offset.

## 4. The epoch reset command

A new Ft+ opcode, alongside the existing capture controls:

    opcode 8:  {4'd8, 27'd0}     arm an epoch reset

Arming zeroes `vsync_seq` and `tick_in_frame` **on the next `ext_sync` rising edge**,
not immediately — so the epoch is a frame boundary, which is the whole point, and so
every camera given the command before that edge lands on the same one.

`epoch_valid` in the header flags whether a reset has been applied since power-up.
The host must be able to tell "epoch is defined" from "counting from configuration",
rather than assuming the command arrived.

**`frame_idx` is NOT zeroed by this.** It stays continuous so it remains a drop
detector across the reset. Two indices with different epochs is a small cost against
losing the one field that proves nothing was lost.

## 5. Layout — format 4

The first 32 bytes stay **byte-identical**. A second 32 bytes is appended and
`format` goes `3 -> 4`. A host that only knows format 3 reads the first 32 bytes and
the payload exactly as before.

| word | bits | field | notes |
|---|---|---|---|
| 2 | `[31:0]` | `vsync_seq` | zeroed by the epoch reset |
| 2 | `[55:32]` | `tick_in_frame` | 10 ns ticks since that vsync |
| 2 | `[63:56]` | `pattern_idx` | `{frq[1:0], fra[2:0]}` — see §7 |
| 2 | `[79:64]` | `exposure` | 375 ns units, **as used for this frame** |
| 2 | `[103:80]` | `gl_dly` | 10 ns ticks in effect at capture |
| 2 | `[111:104]` | `trig_out` | triggers outstanding when this one was issued |
| 2 | `[119:112]` | `hflags` | `{epoch_valid, gl_en, gl_live, orient, …}` |
| 2 | `[127:120]` | `hdr_ver` | `1` — versions the second block independently |
| 3 | `[15:0]` | `pat_period` | **fringe period in pixels for THIS frame**; 0 when `frq=3` (flash) |
| 3 | `[95:16]` | reserved | zero; room for §8's deferred fields |
| 3 | `[127:96]` | `~MAGIC` | so the second block validates on its own |

### Why the PERIOD and not just the frequency index

`frq` is an index, not a frequency. `pattern_gen` solves the period **per mode at
runtime** from the active size:

    F     = active size in the varying direction
              orient=0 -> Vactive (rows)      orient=1 -> Hactive (columns)
    b     = ceil(F / 288)
    P_lo  = 288*b      P_mid = 48*b      P_hi = 8*b        (exact 1 : 6 : 36)

So the same `frq = 1` is a **different spatial frequency at every resolution**. A host
deriving it would need `orient` -- which lives in `sli_ctrl[0]`, a register, not the
header -- plus a replicated `ceil` division, and would have to assume neither changed
during the scan. `P_lo` is re-solved on a mode change, so a scan spanning one
silently changes fringe period with nothing in the data saying so.

`orient` therefore also moves into `hflags`: without it the period is ambiguous about
which axis it applies to, and the phase map cannot be interpreted at all.

**`frq = 3` is not a fringe.** It selects a flat full-field FLASHING block for
texture/albedo capture (sequence entries 24..27). `pat_period` is 0 there, and a host
must switch on `frq` rather than divide by a period of zero.

## 6. The hard part is WHEN the fields are sampled

Not the bytes — the latching.

**Every field above must be captured at TRIGGER time and travel with the frame.** A
header assembled when the frame is emitted would describe the *current* state, not
the state that produced those pixels. With triggers stacked, those differ by
`floor(D/T)` frames — which is precisely the error the header exists to remove. It
would be the same staleness bug in a new place.

So the per-frame record is pushed alongside the existing fire-time entry in the
genlock FIFO, and popped with it. The FIFO is already depth 32 and already carries
one entry per outstanding trigger; this widens each entry rather than adding a
structure.

Consequence: **the FIFO becomes the single source of per-frame provenance.** An
overflow there is no longer just a lost trigger, it is a frame whose metadata is
unknown — worth reporting distinctly from a plain drop.

## 7. My recommendations, not yet your decisions

**`pattern_idx` should ride along.** `vsync_seq` identifies the projected *frame*;
`pattern_idx` identifies what was *on* it. They differ whenever a pattern is held
across more than one frame — exactly what the AuV2 handshake did when the camera was
not ready. One byte to remove a class of ambiguity I cannot rule out from here.

**The epoch reset should not zero `frame_idx`**, for the reason in §4.

## 8. Deliberately deferred

Word 3 is reserved for these rather than built now:

* `t_rise` / `t_int` — measured trigger-to-integration delay and achieved
  integration length, per frame rather than as a windowed min/max.
* `mode_idx`, `refresh`, `vs_period` — what was being projected, so a scan spanning a
  mode change is not silently inconsistent.
* `health` — `cfifo_ovf`, `ufifo_ovf`, `aligned`, `calib` **at capture**. Sticky flags
  read later cannot distinguish "overflowed at power-up" from "overflowing now".
* `roi_id` — 3 bits. Moves to word 2 if ROIs are actually used, because an untagged
  windowed frame cannot be attributed at all.
* `payload_crc` — the most invasive item, and the only one that guards against silent
  corruption. This project has shipped a build that streamed at full rate while
  corrupting half the data bus, and on 2026-08-31 found a source constant altered by
  one digit. Worth revisiting.

## 9. Cost

32 -> 64 bytes against a 1,638,400-byte payload: **0.004%**. Throughput is not the
question; §6 is.

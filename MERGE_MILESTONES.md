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

## M2 — RESULT: PASS (2026-08-21)

The whole camera datapath now runs inside `Au2_SLI`, concurrently with HDMI, in
one bitstream.

```
HDMI      N = 0033/0034 counting     test_silicon.py: 15 passed, 0 failed
          read_mode works after the camera has been streaming
CAMERA    200.6 MB/s  25 CLEAN / 0 padded  119.5 fps  ldrop 0  10/10 ALIGNED
BUILD     WNS +0.082  WHS +0.052  0 failing of 45,070   MMCM 5 of 6
```

`cam_frame_ft` is instantiated WHOLE rather than re-plumbed, which keeps the
proven IDELAY receive chain proven. `Au2_SLI`'s own chain (`cam_lvds_rx` ->
`cam_align` -> `cam_sync_decode` -> `cam_line_buf`) and its `i_cam_mmcm` are
deleted, not commented out.

**Sensor ownership resolved.** Two boot sequencers both drove
`cam_sck/mosi/ss_n/reset_n/trigger`; two drivers on one pin is an elaboration
error, not a bench surprise. Independent operation means the camera must come up
WITHOUT the host asking, so `cam_boot_stage1` wins and `usb_link`'s camera
outputs are `open`. Registers `0x30`-`0x39` therefore no longer reach the sensor
-- re-exposing them as STATUS is M4. Inputs stay connected, so `CAM_MON` is honest.

### Four things M2 had to find the hard way

**1. An attribute that documented an intention the tool never honoured.**
`hdmi_input.vhd` said its IDELAYCTRL was "tied to the delay instances by the
IODELAY_GROUP attribute", and `deserialiser_1_to_10.vhd` does tag its IDELAYE2
cells -- but the attribute was NEVER DECLARED on the controller. That worked only
because it was the design's ONLY IDELAYCTRL, so Vivado associated implicitly. The
merged design has three (HDMI, camera, MIG) and DRC rejected it outright. This is
the SECOND decorative attribute found in this codebase, after `ram_style="block"`
on `cam_async_fifo`; both had no effect and neither failed until something else
changed. **An RTL-wide grep for attributes is worth doing.**

**2. Deriving the build script from the wrong parent.** `build_merged.tcl` came
from `build_pt.tcl` (HDMI), so it inherited nothing from `build_cam_ft.tcl`
(camera) -- including the tri-state multicycle on the Ft+ enables. Timing failed
at **-2.269 ns** on `doe_reg[*] -> ft_data[*]`, a problem already solved once at
some cost ("failed at -2.983 ... still -1.209 after the enables were replicated
into the IOB T flops"). Ported the multicycle AND `phys_opt_design`, which the
camera build had added because "small violations kept needing RTL changes".
The guard now ERRORS rather than warns: an unapplied constraint leaves the Ft+
bus untimed, which is exactly how this project once shipped a build streaming at
full rate while corrupting half the data bus.

**3. MMCM saturation was the real timing failure, and directives could not fix it.**
Taking `cam_frame_ft` wholesale brought its 200/100 MHz MMCM, which duplicates
`ref_clk_pll` -- pushing the design to **6 of 6 MMCM**. Timing stuck at -0.868 ns,
and the cause was **clock INSERTION DELAY**: `ft_clk` needed **6.1 ns of a 10 ns
period** to reach the FT601 registers, because saturated clock regions forced that
logic away from its own pins. Timing-focused directives made it slightly WORSE
(-0.919, 26 failing vs 18) -- a useful negative result, proving the problem was
structural rather than effort-limited. Adding `EXT_CLK` to `cam_frame_ft` so it
takes `clk200`/`clk100` from the parent dropped it to **5 of 6** and timing closed
at **+0.082** with NO pblock required. M0 predicted 5 of 6; taking the module
whole is what departed from that.

> Removing the MMCM removes its `LOCKED`, which gates the IDELAYCTRL and MIG
> resets -- and a glitch on the latter aborts DDR calibration, previously seen as
> two builds with identical logic where one always calibrated and one never did.
> Replaced with a settle counter clocked BY THE SUPPLIED CLOCK: it cannot advance
> unless that clock is genuinely running, and being synchronous its release
> cannot glitch. `EXT_CLK` defaults to 0, so the standalone build is untouched.

**4. Calling a transient a fault.** The first `ring_check` on the merged build
returned zero bytes and was treated as "the camera is broken". It was a transient
right after the RAM load; a single retry showed frames flowing. A diagnostic
bitstream was built on the strength of one failed read. **Retry before diagnosing.**

### CAM_DIAG

A build-time switch routing the camera's 1 Mbaud status word to `usb_tx` in place
of the SLI telemetry. It made the datapath legible in one shot -- and made the
zero-byte read explicable, since `ufifo_empty=0` with `txe=1` is an IDLE LINK
(frames queued, host not reading), not a dead one.

It is a stopgap: it trades one interface for the other. M4 should surface
`calib`/`aligned`/`streaming`/`cap`/`cfifo_ovf`/`ldrop` as read registers so both
work at once. Defaults to 0.

---

## M3 — PARTIAL: 3c and 3d PASS (2026-08-21)

The two tests that carry the weight are done. Both directions of coupling are
now ruled out.

**3d — HDMI mode change during capture: PASS**

```
before : MODE=0x82 REFR=75    ldrop=0
after  : MODE=0x8D REFR=60    ldrop=0      <- the mode really changed
406 frames captured, 294 of them after the force
ldrop min..max : 0..0
```

`MODEFORCE` retunes the output pixel clock by RECONFIGURING A LIVE MMCM over
DRP, 75 Hz -> 60 Hz, while the camera streamed. Not one kernel lost. Test:
`ft_usb_video/host/test_3d_modechange.py`.

**3c — wedged camera: PASS**

```
camera:  119.8 -> 60.7 fps    (exactly halved: sensor skipping every other trigger)
HDMI:    N = [52, 51, 52] still counting
         MODE unchanged, REFR unchanged, control plane alive (ID = 0x48)
```

Exposure pushed past the 8280 us cliff, which is the most severe camera failure
we can produce on demand -- it needs an FPGA reconfigure to clear. HDMI did not
notice. Test: `ft_usb_video/host/test_3c_camerawedge.py`.

### The first run of 3c was inconclusive because the TEST was wrong

It counted frames with a 40 MB read cap -- about 24 frames -- so a healthy camera
and a half-speed one both saturated the cap and reported the same number. It
declared "the camera did not break" against a camera that had in fact halved.

Fixed by measuring **rate over a fixed wall-clock window** instead: frames are
contiguous and fixed-size, so bytes/frame-size is the rate and cannot be capped
into agreeing with itself. The corrected run showed 119.8 -> 60.7 fps
immediately.

> Third time in this project that a MEASUREMENT, not the hardware, produced the
> wrong answer -- after the correlation that locked onto the kernel pattern, and
> the frame counter that reported extraction rate as camera rate. **Check what
> the number is actually measuring before believing what it says.**

### Still outstanding

| | Blocked on |
|---|---|
| **3a** — unplug the HDMI source mid-capture | No HDMI source is connected (`S=0`, `edid_ok=False`). Needs a PC on the Hd RX port. |
| **3b** — camera idle / held in reset | Needs either an RTL hook or physically unstacking the camera. |
| 3c's reporting half | The camera "says so on Port A" needs M4 -- `calib`/`aligned`/`cfifo_ovf` are still not exposed as registers. |

---

## M4 — RESULT: PASS (2026-08-21)

Camera health is now readable over the Pt's USB control link, **without taking
Port A away from the SLI control plane**.

```
0x3A alive  = 0x3F   stw=1 rd_busy=1 calib=1 aligned=1 streaming=1 cap=1
0x3B health = 0x10   cfifo_ovf=0 ufifo_ovf=0 ufifo_empty=0 txe=1
0x3C ldrop  = 0
0x3E period = 599,984 cycles => 120.00 Hz
0x40 expo   = 1600 units = 600 us

test_silicon.py: 15 passed, 0 failed     <- simultaneously
```

New read registers, alongside the existing camera block:

| addr | contents |
|---|---|
| `0x3A` | `{stw[2:0], rd_busy, calib, aligned, streaming, cap}` — *is it alive* |
| `0x3B` | `{cfifo_ovf, ufifo_ovf, ufifo_empty, txe, 0000}` — *is it healthy* |
| `0x3C`/`0x3D` | `ldrop` lo/hi — static means no kernels lost |
| `0x3E`/`0x3F` | frame period / 16, in 72 MHz wordclk cycles |
| `0x40`/`0x41` | `exposure0` lo/hi, 375 ns units |

Host tool: `host/read_cam_status.py`. This supersedes `CAM_DIAG`, which could
only ever show one interface at a time.

**The status crosses clock domains ON A TOGGLE, not continuously.** 64 bits
moving from the camera's `ui_clk` into `clk100_g` can TEAR -- some bits from the
old value, some from the new -- and a torn status word is worse than none,
because it reads as a plausible state that never existed. The source holds each
snapshot stable ~200 ms and flips a toggle; the parent captures only on that
edge.

### It closed M3/3c's reporting half — and taught something

3c now passes in full:

```
camera degraded            : 120.5 -> 60.8 fps
HDMI VSYNC still counting  : True      mode unchanged, control plane alive
camera REPORTS it on Port A: True (120.00 -> 475.54 Hz, 296% off)
```

But note WHICH field reports it. **The status flags do not move**: `0x3A` stays
`0x3F` with `calib`/`aligned`/`streaming`/`cap` all still 1, because from the
datapath's point of view nothing is wrong — it is the SENSOR misbehaving
upstream. Only the frame period betrays it, and it does so by going to a
nonsensical **475 Hz**, not to zero: `frame_start` intervals become ERRATIC
rather than absent.

> Consequence for any host that monitors this: check the period for
> PLAUSIBILITY, not for a drop. The first version of the 3c criterion looked
> only for the rate to fall and scored a clear report as a miss. A monitor that
> alarms on "rate decreased" would sit silent through this failure.

---

## M5 — RESULT: PASS (2026-08-21), plus a real robustness bug found and fixed

**Control replies now share the frame pipe, inserted only at frame boundaries.**

```
sent 5 requests, captured 956.3 MB
frames  : 583        malformed packets: 0
replies : 5  seq=[4,5,6,7,8]  marker=0x48 x5
frame_idx contiguous : True
```

Every packet on the wire is now `32-byte header + payload`, the magic saying
which kind (`SLI0` frame, `SLI1` reply) and the header stating the length. Both
carry the magic's inverse in `h[7]`, so a host that loses its place resynchronises
rather than drifting.

**Replies are emitted only from `R_IDLE`**, the reader's between-frames state, so
a frame is never interrupted and the host's fast path — read a header, consume
exactly `FBYTES` — is untouched. That matters: bytes/frame-size is how the true
delivered rate is measured, and mid-frame insertion would break it.

The reply check sits BEFORE the new-frame check on purpose. If the camera stops,
the reader parks in `R_IDLE` forever, and that is exactly when control must still
answer. A reply path gated on frames existing would go silent in the one
situation that needs it.

> `rpl_pend` is a FLAG, NOT A QUEUE: 400 rapid requests produced ~4 replies.
> Right semantics for a status read, but N requests do not guarantee N replies.

### The stress test found a real bug: a truncated upload wedged the control plane

Deliberate hostility was shrugged off. What broke it was a **well-formed command
that simply stopped early** — also the one a real host does by accident.

```
--> begin 1280-byte upload, send 5 bytes, abandon
    read ID attempt 1..4 : NO ANSWER      (indefinitely)
--> feed the remaining 1,275 bytes
    read ID : 0x48
```

`uart_ctrl`'s receive FSM had **no inter-byte timeout**: once collecting payload
it counted bytes and would not leave until it had them all, swallowing everything
after — including well-formed register reads. Ctrl-C during `upload_corr.py` does
exactly this, and the host has no way to know the cure is 1,275 filler bytes.

Fixed with a **50 ms inter-byte watchdog**. At 115200 a byte is ~87 µs, so a 50 ms
gap inside a frame is unambiguous. Armed only outside `S_SYNC` so an idle link is
never disturbed; applied AFTER the state machine so it overrides any branch; and
deliberately silent, since the sender is gone and an error byte would only
desynchronise the next host to connect. Verified at six abandonment points
(1, 2, 5, 40, 700, 1279 bytes) — all recover.

### Full battery — 18 phases, both ports, all survived

| Port A | Port B |
|---|---|
| 8 KB random garbage | undefined opcodes 6–15 |
| 500 bad checksums | boundary payloads on every opcode |
| truncated commands | truncated 1–3 byte words |
| undefined opcodes | 400 rapid re-arms |
| read+write sweep of all 256 addresses | 400 reply-request floods |
| **truncated / abandoned uploads** | D3XX open/close churn |
| oversized upload, SYNC embedded in payload | abandoned mid-transfer reads |
| writes to the read-only EDID table | + both ports hammered simultaneously |

The control plane answered in every phase and telemetry never stopped once.

### Three TEST bugs, each of which produced a confident wrong answer

1. **A patch that silently did not apply.** `str.replace` on a non-matching anchor
   is a no-op, not an error — the counter landed, the code that ACTED on it did
   not. Build succeeded, timing clean, behaviour unchanged. The edits that used
   `assert old in s` caught their problems instantly; the ones without did not.
2. **Probing before the wire drained.** 700 bytes takes 61 ms to transmit at
   115200; the test waited 100 ms and probed while bytes were still going out,
   reporting 4-of-6 for a fix that worked 6-of-6.
3. **Conflating obedience with damage.** Legal-but-extreme commands change the
   camera's CONFIGURATION. Measuring fps without restoring first reported seven
   phases as failures when the camera was doing exactly what it was told.

### Build script

Added a post-route checkpoint (Vivado died mid-build once) and switched to
`route_design -directive Explore` after the default router crashed **twice at the
identical point** — Phase 5.1 Global Iteration, exit 116, no error text.
Identical failures rule out random instability.

> Third thing `build_merged.tcl` has needed that it did not inherit from
> `build_pt.tcl`, after the tri-state multicycle and the checkpoint. A fourth
> should prompt a proper diff against `build_cam_ft.tcl` rather than more patching.

Timing: WNS +0.082, WHS +0.056, 0 failing of 45,868.

---

## M7 soak — RESULT: PASS (2026-08-21)

30 minutes continuous, both subsystems running, watched over two independent
paths: the camera through the FRAME STREAM's structure, HDMI through the Pt
control link.

```
ran 30.0 min   211,942 frames   347.25 GB
frame_idx gaps    : 0        not one boundary lost
ldrop             : 0        not one kernel lost, all 30 minutes
cfifo_ovf/ufifo   : 00       never set
MODE              : 0x82 stable, VSYNC counting throughout
rate              : 119.8 -> 117.5, then flat 117.5-117.9 for 29 minutes
```

**This is the first test in the project longer than 10 seconds.** Everything
prior — `ring_check` 6 s, `row_align_check` 10 s, each stress phase a second or
two — was structurally blind to thermal drift, to rare events (a `cfifo`
overflow once every few minutes would never land in a 6 s window), and to slow
leaks. None of those appeared.

### Two readings worth keeping

**Delivered 117.8, captured 120.** 117.8 x 1.638 MB = 193.0 MB/s, matching the
measured throughput exactly — so this is HOST-limited delivery, not a camera
fault. `ldrop` at 0 proves nothing was lost; the ring skips ~2 frames/sec when
the host cannot take them, which is its designed behaviour.

> Note the limitation this exposes: **`frame_idx` counts frames the reader
> SERVED, not frames the sensor captured.** Deliberately skipped frames are
> therefore invisible to it. `ldrop` catches lost kernels; skipped frames are a
> separate counter (`w_skip`) that is not currently exposed.

**`alive` alternates 0x3F / 0x2F.** Bit 4 is `rd_busy` — the reader caught
between frames when sampled. Normal. The check masks to the low nibble
(`calib`/`aligned`/`streaming`/`cap`), which held `0xF` throughout.

### The run reported FAILED, and the test was wrong

`malformed: 1`, reached at the one-minute mark and never incremented again across
the remaining 29 minutes and 347 GB. That is the parser joining a stream already
in progress: the accumulator starts wherever the pipe happens to be, so the
opening bytes are mid-packet by definition. The framing resynchronises on the
next magic — which is what it is for — but the first walk scored one malformed
packet.

Fixed by not counting until the first valid packet is seen. Confirmed with a
2-minute run: `malformed: 0`, PASS.

> Fourth measurement bug in this project to produce a confident wrong answer,
> after the correlation that locked onto the kernel pattern, the frame counter
> that reported extraction rate as camera rate, and the stress test that
> conflated obedience with damage. **The instrument is as likely to be wrong as
> the thing being measured.**

---

## M6a — RESULT: PASS (2026-08-21): control commands over the Ft+

M6 moves the `0xA5` control plane off the Pt's UART and onto the FT601, so the
delivered system is reached only through the Ft+. M6a is the **command**
direction alone; M6b adds replies.

**Why the stream rides opcode 0.** The OUT pipe delivers 32-bit *words* whose top
nibble is a camera opcode. Packing protocol bytes raw would let the control
stream **alias into camera commands** — a byte landing at `0x1?` in that position
fires opcode 1 and silently rewrites the exposure. So the bytes ride opcode 0,
which was undefined, three at a time with an explicit count:
`{4'd0, count[3:0], byte2, byte1, byte0}`. Opcodes 1–5 are untouched and no byte
pattern can reach them. 75% efficiency — for a 1,283-byte upload that is 428
words, nothing on a 232 MB/s link.

**The proof is deliberately one-directional:** write a register over USB3, read it
back over the *serial* link. That isolates the command path completely, with no
dependency on the reply path M6b still has to build.

### First run failed, and both the RTL and the test were wrong

Scored 2 of 4. Register `0x13` never changed; nothing crashed and serial stayed
healthy, so it looked like bytes were not arriving at all.

**The RTL bug — a registered read on a first-word-fall-through FIFO delivers every
byte twice.** `cam_async_fifo` is FWFT: `rd_data` is combinational on the read
pointer, so the byte is on the bus whenever `!empty`, and `rd_en` only advances
the pointer. The crossing did

```verilog
always @(posedge clk) ctlf_rd <= !ctlf_empty;   // WRONG
assign ctl_valid = ctlf_rd;
```

`rd_en` is computed from the **previous** cycle's `empty`, so it stays asserted
for one cycle after the FIFO drains — with the stale last byte still on
`rd_data`. The 5-byte command reached `uart_ctrl` as
`A5 A5 57 57 13 13 AB AB ck ck`: SYNC, then `A5` read as an opcode, rejected,
resynced, and the write never executed. The pop and the valid must be the *same*
cycle: `assign ctl_valid = !ctlf_empty;`.

> The doubling is invisible from the outside. A path that delivers nothing and a
> path that delivers everything twice fail *identically* at the register — both
> just leave it unchanged.

**The test bug — two of the four "passes" were vacuous.** The values alternated
`0xAB, 0x00, 0x5A, 0x00`, and writing `0x00` to a register already reading `0x00`
passes whether or not the write happened. The real score was 0 of 4. Test values
are now all distinct and non-zero, and the test asserts the register does not
already hold the value it is about to write.

> Fifth measurement bug in this project to produce a confident wrong answer. This
> one did not invent a failure — it *understated* one, which is the more
> dangerous direction.

Ruled out along the way, in this order: register `0x13` is writable and reads
back correctly over serial (so the register is fine); `ft601_sync_rx` takes
exactly one word per bus-turnaround window of ~15 cycles, so `cmd_valid` pulses
can never arrive back-to-back and the unpacker cannot be clobbered mid-word; and
every `uart_ctrl` state is a single-cycle transition on `rx_valid`, so one byte
per clock is safe.

### Results, after the fix

`host/test_m6a_ctlpath.py` — 4 of 4 distinct non-zero values written over USB3
and read back over serial. But that runs on an **idle pipe**, which is the easy
case, so it is not on its own sufficient.

`host/test_m6a_under_load.py` — commands injected **while the frame stream
runs**. This is where a fault would actually land: `ft601_sync_rx` has to take
the shared DATA bus away from the transmitter, and it does that by gating the TX
with `rx_hold`. The earlier version of exactly that handshake dropped `bus_oe`
one cycle before `rx_hold` reached the TX's WR# flop, so the FT601 latched a
garbage word while `ft_data` floated — and the damage showed up in the *frame
stream*, not in the command.

```
    streamed 25.7 s   4.92 GB   3,002 frames   117.0 fps
    commands sent 34, applied 34
    frame_idx gaps 0   malformed 0
    ldrop 0 -> 0   cfifo_ovf 0  ufifo_ovf 0   alive 0x3F
```

117.0 fps against the soak's host-limited 117.8 — the control traffic costs
nothing measurable, which is expected: 34 commands is 68 words against ~3,000
frames of payload.

> Timing unchanged at **WNS = +0.082 ns**. Vivado crashed twice on the way to
> this bitstream — once silently after synthesis (`EXIT=127`, no bitstream, no
> error), once mid-synthesis with a real `EXCEPTION_ACCESS_VIOLATION` — with
> 60 GB of 96 GB free. Two crashes at *different* stages of an unchanged flow is
> the tool-instability signature, not a design fault; the third attempt went
> straight through.

**Still open:** M6b (replies from a byte FIFO, variable length) and M6c (tables +
EDID, the `test_silicon` equivalent over D3XX). Until M6b lands the Pt UART is
still required to read anything back.

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

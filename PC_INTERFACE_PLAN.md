# PC interface plan — diagnostics on the Pt, everything else on the Ft

_Drafted 2026-08-17. Inventory of what exists today: [`PC_INTERFACE_INVENTORY.md`](PC_INTERFACE_INVENTORY.md)._

## Three projects, not two

| Project | Top | Boards | Control path | Status |
|---|---|---|---|---|
| **Standalone HDMI** | `Au2_SLI` | Pt + Hd V2 | Port A, `0xA5` @ 115200 | works — 15/15 bring-up, 2026-08-17 |
| **Standalone camera** | `cam_frame_ft` | Pt + Ft+ + camera | Port B opcodes | works — 120.000 Hz locked, in flash |
| **Merged** (this plan) | new | Pt + Hd V2 + Ft+ + camera | **Port B** | to build |

**The merged project always has the Ft+.** That single constraint removes the objection to
putting all control on Port B: the case it would have stranded — a Pt with no Ft+ stacked —
is served by the *standalone HDMI project*, which keeps its Port A control plane untouched.

The standalone builds are therefore not legacy. They are the bring-up and recovery path:
if the FT601 wedges in the merged design (we hit that today — it needed a force-kill and an
FPGA reconfigure), loading the standalone HDMI bitstream restores control over the Pt's own
UART. Keep both building.

## The split, in the merged project

| | Port A — Pt FT2232H (`COM6`) | Port B — Ft+ FT601Q (D3XX) |
|---|---|---|
| Role | **out-of-band diagnostics** | **control + bulk data** |
| Direction | FPGA → PC, streaming only | bidirectional |
| Carries | unified HDMI + camera status | frame stream, all commands, tables, EDID |
| Rate | 115200 | ~232 MB/s measured |

Port A becomes a console: one writer, no control, still reporting when the data path is
dead. Port B becomes the application's single link.

This resolves the only contested pin in the system (`AA21 = usb_tx`) by construction —
after the merge exactly one source drives it.

---

## Work items

| # | Item | What | Effort | Risk |
|---|---|---|---|---|
| 1 | **Unify diagnostics on Port A** | One status generator emitting both HDMI and camera fields. Keep the ASCII `KEY=VAL` shape — greppable, and every existing eye is trained on it — extended with the camera's `stw/aligned/calib/cfifo_ovf/ldrop/period/expo_cur`. Retire the 1 Mbaud 32-hex word. | S | low |
| 2 | **Reply path on Port B** | The OUT pipe is fire-and-forget today; control needs replies. Interleave **typed packets** into the IN stream: reuse the 32-byte header with a distinct magic so the host demuxes frames from replies. Avoids FT601 multi-channel mode entirely. | M | med |
| 3 | **Move the `0xA5` engine to Port B** | Re-bind `uart_rx`/`uart_ctrl`/`usb_link` to the FT601 byte stream. Protocol, registers and tables unchanged — only the transport moves. | M | med |
| 4 | **Sensor ownership** | Two boot sequencers today: SLI reg `0x39` (host-initiated) and `cam_boot_stage1` (automatic at power-up). Independent operation wants automatic, so `cam_boot_stage1` owns SPI + `cam_reset_n`; reg `0x39` becomes status-only and `0x30`–`0x36` a mailbox that must wait for `ready`. | M | **high** |
| 5 | **Collapse the LVDS receiver** | Both tops instantiate one. **Keep the STANDALONE camera's `cam_lvds_rx_idelay` + `cam_eye_scan`, NOT `Au2_SLI`'s `cam_lvds_rx`.** M0 caught this: the SLI chain is the no-IDELAY variant, proven only in simulation (`tb_cam_decode`), and 720 Mbps requires IDELAYE2 eye-centring on silicon. `Au2_SLI` already supplies `clk200`, so it costs no extra clocking. | S | med |
| 6 | **Merge the tops** | `Au2_SLI` (VHDL) stays top; add DDR3/MIG + ring writer + FT601 streaming as components. Camera pins already match — 34 shared, all the same signals. | L | med |
| 7 | **Clocking audit** | HDMI MMCMs + camera MMCM + MIG + the `BUFR` regional clock against 6 CMTs. The `BUFR` clock-region limit already forced the cfifo to `AW=9`; adding HDMI logic to that region needs checking before it bites again. | S | **high** |
| 8 | **Host migration** | `lauauboard.cpp`, `test_silicon.py`, `dump_edid.py`, `read_mode.py`, `force_mode.py`, `upload_corr.py` gain a D3XX transport for the merged target. `cam_ctl.py` opcodes fold into the register map. The serial transport stays for the standalone HDMI project. | L | med |

S ≈ hours · M ≈ a day · L ≈ multiple days

Item 8 is a *transport* change, not a protocol change. If the `0xA5` framing is factored
behind a byte-stream interface on the host side, every tool gets both targets for the cost
of one abstraction — and the standalone projects keep working unchanged.

---

## Post-merge message map

| Port | Dir | Carries |
|---|---|---|
| A | ← | Unified status: HDMI `S/V/T/F/M/R/N/D/P/O` + camera `stw/aligned/calib/cfifo_ovf/ldrop/period/expo` |
| A | → | *(nothing)* |
| B | ← `0x82` | Frame stream (`MAGIC` header + payload) **and** control replies (distinct magic) |
| B | → `0x02` | `0xA5` control: registers, tables, EDID readback, camera commands |

Camera opcodes 1–4 become registers, gaining what they never had: a reply, and therefore an
acknowledgement that is not a counter arriving on a different port.

---

## Ordering

1. **Item 7 first.** A CMT or clock-region blocker invalidates the whole merge; find it
   before building anything on top. Cheapest item, highest information.
2. **Item 1 next.** Unified diagnostics is independently useful and lands as soon as the
   merged top exists — it is how items 4–6 get debugged.
3. **Items 5, 6, 4** — collapse the receiver, merge the tops, then settle sensor ownership.
   Item 4 last: it is the one most likely to need bench iteration.
4. **Items 2, 3, 8** — the transport move. Deferrable without touching any RTL from 4–6:
   the merged design can ship an interim build with control still on Port A, then move.

---

## Rules this must not break

| Rule | Why |
|---|---|
| Exposure clamped ≤ 8280 µs at 120 Hz | Past it the rate halves; at 8300 µs delivery stops and the **sensor wedges** — recoverable only by FPGA reconfigure. Whichever port owns exposure must clamp. |
| Camera status must not live only on Port B | When the pipeline wedges, Port B goes silent. Item 1 is what preserves a signal in exactly that case. |
| Payload format is read, never assumed | Header word `h[6]`: `2` = 10-bit in u16, `3` = packed. Misreading it paints a plausible wrong picture rather than failing. |
| HDMI runs with no camera attached, and vice versa | That is what "independent" means. Camera LVDS inputs float when absent, and a floating input can self-oscillate. |
| The standalone builds keep building | They are the recovery path when the merged design's single control link is down. |

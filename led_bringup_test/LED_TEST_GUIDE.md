# Stack-Board LED Continuity Test — Guide

A bring-up smoke test for the **LauCameraTrigger_Alchitry_Stack** board. The 8 user
LEDs on the Alchitry Au V2 mirror the 8 Bank-B signals that travel up through the
stack, so you can verify every camera GPIO and DIP-switch line with your eyes.

```
   ┌──────────────────────────────────────┐
   │  Stack board   (DF40 plugs, face down)│  ← DIP switch SW1 + MASTER/SLV JSTs
   ├──────────────────────────────────────┤
   │  Hd V2         (passes Bank B through)│
   ├──────────────────────────────────────┤
   │  Au V2         (FPGA + the 8 LEDs)    │  ← reads Bank B, lights the LEDs
   └──────────────────────────────────────┘
```

The FPGA drives **nothing** on the signal pins — all eight are **inputs**. It only
reads each line and lights the matching LED. Pure continuity check.

---

## The 8 LEDs on the Au V2

LEDs sit in a single row on the Au V2 board, `led0` … `led7`. Each lights when its
line is driven **HIGH**; all are **OFF at rest** (internal pull-downs).

```
            CAMERA GPIO  (MASTER JST)          DIP SWITCH  (SW1)
        ┌───────────────────────────┐   ┌───────────────────────────┐

 LED:   ● led0   ● led1   ● led2   ● led3   ● led4   ● led5   ● led6   ● led7

 SIG:  CAM_READY CAM_TRIG CAM_PAT  CAM_MODE  HVSV     BLUE     GREEN    RED

 BALL:   R11      R16      R10      R15       K5       N16      E6       M16

 DF40:    27       28       29       30       33       34       35       36

 JST:    pin5     pin2     pin4     pin3      —        —        —        —
 SW1:     —        —        —        —       SW1-1    SW1-2    SW1-3    SW1-4
```

> `CAM_PAT` = `CAM_PATTERN`. JST pins are on the **MASTER** connector.

---

## Mapping table

| LED  | Signal        | Au ball | DF40 pin | Driven by                       |
|------|---------------|:-------:|:--------:|---------------------------------|
| led0 | `CAM_READY`   | R11     | 27       | Camera GPIO → MASTER JST pin 5  |
| led1 | `CAM_TRIG`    | R16     | 28       | Camera GPIO → MASTER JST pin 2  |
| led2 | `CAM_PATTERN` | R10     | 29       | Camera GPIO → MASTER JST pin 4  |
| led3 | `CAM_MODE`    | R15     | 30       | Camera GPIO → MASTER JST pin 3  |
| led4 | `SW_HVSV`     | K5      | 33       | DIP SW1-1                       |
| led5 | `SW_BLUE`     | N16     | 34       | DIP SW1-2                       |
| led6 | `SW_GREEN`    | E6      | 35       | DIP SW1-3                       |
| led7 | `SW_RED`      | M16     | 36       | DIP SW1-4                       |

Pins from `ROADMAP.md §5.2` (Bank A low → Bank B high remap).

---

## How to run the test

1. **Stack the boards:** Stack board → Hd V2 → Au V2. Plug the camera cable into
   the **MASTER JST**.
2. **Flash** `led_test.bin` via `..\AlchitryFlasher\AlchitryFlasher.cmd`
   (Step 3 → choose **RAM / temporary** so a power-cycle restores your normal image).
3. **Camera lines (led0–led3):** in the **Pylon viewer**, configure all four camera
   GPIO lines as **outputs (push-pull)** and toggle each one. Push-pull cleanly
   overrides the internal pull-downs.
   - led0 ON ⇒ `CAM_READY` continuity good. led1 ⇒ `CAM_TRIG`, led2 ⇒ `CAM_PATTERN`,
     led3 ⇒ `CAM_MODE`.
4. **DIP switches (led4–led7):** flip each of SW1's four positions.
   - One orientation ties the net to **+3V3** (LED **on**), the other to **GND**
     (LED **off**). SW1-1 ⇒ led4 (HVSV), SW1-2 ⇒ led5 (BLUE), SW1-3 ⇒ led6 (GREEN),
     SW1-4 ⇒ led7 (RED).

### Pass / fail

- **LED follows its line** → that ball + the full DF40 → Hd → Au path is good. ✅
- **LED never lights** → open/continuity fault on that net (check the DF40 solder
  joint, the JST crimp, or the switch).
- **LED stuck on** → short to +3V3, or a wrong/bridged net.

---

## Notes

- This test uses an **all-pull-down** constraint for clean "high = on" semantics.
  The *functional* `Au2.xdc` Bank-B remap should instead use the mixed
  pull-up/down from `ROADMAP.md §5.2` (safe SLI defaults).
- Build/flash details and the source are in [`README.md`](README.md);
  RTL is [`led_test_top.v`](led_test_top.v), pins are [`led_test.xdc`](led_test.xdc).

# Stack-board LED bring-up smoke test

Lights the **8 user LEDs on the Alchitry Au V2** from the 8 Bank-B signals coming
up through the **LauCameraTrigger_Alchitry_Stack** board (DF40 → Hd pass-through →
Au). Use it to verify a freshly fabbed batch: drive a line high, its LED turns on.

## LED map

| LED  | Signal        | Au ball | DF40 pin | Source                         |
|------|---------------|---------|----------|--------------------------------|
| led0 | `CAM_READY`   | R11     | 27       | MASTER JST pin 5 (camera out)  |
| led1 | `CAM_TRIG`    | R16     | 28       | MASTER JST pin 2 (camera **in**) |
| led2 | `CAM_PATTERN` | R10     | 29       | MASTER JST pin 4 (camera **in**) |
| led3 | `CAM_MODE`    | R15     | 30       | MASTER JST pin 3 (camera out)  |
| led4 | `SW_HVSV`     | K5      | 33       | SW1-1 (DIP)                    |
| led5 | `SW_BLUE`     | N16     | 34       | SW1-2 (DIP)                    |
| led6 | `SW_GREEN`    | E6      | 35       | SW1-3 (DIP)                    |
| led7 | `SW_RED`      | M16     | 36       | SW1-4 (DIP)                    |

All inputs have an internal **pull-down**, so every LED is **off at rest** and
lights only when its line is driven high. (This is bring-up-friendly and is *not*
the functional pull scheme — the real `Au2.xdc` remap should use the mixed
pull-up/down from `ROADMAP.md §5.2`.)

## How to test

- **DIP switches (led4–led7):** SW1 is SPDT — one orientation ties the net to
  +3V3 (LED on), the other to GND (LED off). Flip each of the 4 positions and
  watch led4–led7 toggle.
- **Camera lines (led0–led3):** plug the GPIO cable into the **MASTER JST**.
  - `CAM_MODE` (led3) and `CAM_READY` (led0) are **camera outputs** — set those
    Alvium GPIO lines high and the LEDs follow.
  - `CAM_TRIG` (led1) and `CAM_PATTERN` (led2) are **camera inputs** (the camera
    won't drive them). To exercise their LEDs, either jumper the JST pin to +3V3,
    or set the camera's GPIO line direction to output and drive it.
  - If a camera-output line stays dark, check the Alvium line is configured
    **push-pull / active-driving** (a weak internal pull-down can't see an
    open-drain high).

## Build

From this folder, with the project's Vivado on PATH:

```
vivado -mode batch -source build.tcl
```

Produces `led_test.bit` (JTAG/RAM load) and `led_test.bin` (for the
`AlchitryFlasher` GUI). The design is pure routing — build is well under a minute.

GUI alternative: create a new RTL project for part `xc7a35tftg256-2`, add
`led_test_top.v` + `led_test.xdc`, set `led_test_top` as top, Generate Bitstream.

## Flash

Use `../AlchitryFlasher/AlchitryFlasher.cmd` (Step 3 → pick `led_test.bin`,
choose **RAM / temporary** for a smoke test), or the Alchitry loader directly.
RAM-loading means a power-cycle returns the board to its previous flash image.

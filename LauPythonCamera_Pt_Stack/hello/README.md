# `hello/` — "is the PYTHON 1300 actually there?"

> **STATUS: milestone #5 PASSED 2026-08-10** (reg 0 = `0x50D0`, 40/40). The staged
> bitstreams here remain the bring-up ladder and the recovery path when the merged
> design misbehaves. `cam_boot_stage1.v` is NOT historical — the merged build
> instantiates it directly as `i_cam_frame_ft/u_boot`, so it owns the sensor SPI,
> reset and 72 MHz reference in the shipping design.

The bring-up bitstream for **[`CAMERA_RTL_PLAN.md`](../../CAMERA_RTL_PLAN.md) milestone #5**, the
red hardware gate: **reset the sensor, read register 0 over SPI, and check it against the chip ID
`0x50D0`.**

> Reading `0x50D0` from register 0 is the whole hardware gate. It requires no sensor clock, no PLL,
> no LVDS and no NDA material. It proves the power tree came up, the DF40 pin map and the stack
> pass-through are right, `reset_n` released, and our SPI master works — in one transaction.
> — [`CAMERA_SENSOR_PROTOCOL.md`](../../CAMERA_SENSOR_PROTOCOL.md) §2

## Why this is standalone and not `Au2_SLI`

The full design reaches the same register through the UART mailbox, but it also brings up HDMI, an
MMCM tree, the LVDS receiver and the boot sequencer. If the read fails in there, the failure has a
hundred possible causes. Here it has almost none: a counter, an SPI shift register, six pins.

## What it does — and what it deliberately does not

- It **never writes any register.** `cam_spi_master`'s `rw` input is tied to a constant `0`, so the
  design is *physically incapable* of a write. That is what makes it safe to stack on an Au V2:
  register 112 (LVDS power-up) can never be written, so the sensor's LVDS drivers stay off and
  `dout0±` never drives the Au's non-3.3 V-tolerant bank ([`CAMERA_IO_MAP.md`](../../CAMERA_IO_MAP.md)
  §8.2). It is a structural guarantee, not a rule someone has to remember.
- It **never drives `clk_pll`** — held low. No clock is needed, and never starting one means never
  stopping one while the sensor is out of reset, the failure mode `CAMERA_SENSOR_PROTOCOL.md` §6
  warns about.
- On the Pt it **touches no bank-13 pin**, so its safety does not depend on the VBSEL_A strap.

## Reading the result

LEDs, left to right (`led[7]` … `led[0]`):

| LED | Meaning |
|---|---|
| `led[7]` | **Heartbeat**, ~1.5 Hz. Bitstream loaded and clocking. Always blinks. |
| `led[6]` | **PASS** — the last read returned `0x50D0`. |
| `led[5]` | **FAIL** — the last read returned something else. |
| `led[4]` | **MISO live** — `miso` has been seen both high *and* low. **Dark = the line never moved:** open circuit, no sensor power, or wrong pin. |
| `led[3:0]` | Low nibble of the value read (`0x0` on a good part). |

`led[4]` is the bit that separates *"nothing is connected"* from *"something is connected but
wrong"* — the two failures that otherwise look identical.

The UART (**115200 8N1**, onboard FT2232, `COM6` on this host) prints the full story twice a second:

```
reg0=50D0 mon=0 PASS
reg0=0000 mon=0 FAIL      <- miso stuck low  (led[4] dark)
reg0=FFFF mon=0 FAIL      <- miso stuck high (led[4] dark)
reg0=1234 mon=0 FAIL      <- something is answering, but it is not a PYTHON 1300
```

It re-reads continuously rather than latching one result, so a marginal connection shows up as a
flickering PASS instead of a one-shot answer you have to trust.

## Files

| File | |
|---|---|
| `cam_hello_core.v` | All the logic. Board-agnostic; both bitstreams run this. |
| `pt_cam_hello.v` / `.xdc` | Alchitry **Pt V2** wrapper (XC7A100T-FGG484). Has a user reset pin. |
| `au_cam_hello.v` / `.xdc` | Alchitry **Au V2** wrapper (XC7A35T-FTG256). No reset pin; POR only. |
| `cam_probe.v` / `pt_cam_probe.xdc` | **Diagnostic build** for when the hello bitstream says FAIL. See below. |
| `tb_pt_cam_hello.v` | Self-checking testbench — **22 checks, 0 errors**. |
| `build_hello.tcl` | Builds both boards. |
| `run_sim.tcl` | Runs the testbench. |

Reused unchanged from the main tree: `cam_spi_master.v`, `uart_tx.v`, and (for simulation)
`sim/python1300_spi_model.v`.

## Build and run

```sh
# simulate first -- it tests the broken-hardware cases too
vivado -mode batch -source run_sim.tcl

# both boards, or -tclargs au / -tclargs pt for one
vivado -mode batch -source build_hello.tcl
```

The build gates itself on setup **and** hold timing, DRC, every pin landing on the ball its XDC
asked for, and no port straying into a forbidden bank.

## Flashing

**Check which board is actually connected first — the two bitstreams are not interchangeable.**
An A35T bitstream sent to a Pt (or vice versa) transfers over JTAG and prints `Done` *without
configuring the silicon*.

```sh
ALCHITRY="$LOCALAPPDATA/AlchitryFlasher/tools/2.0.52/bin/alchitry.exe"

"$ALCHITRY" load --list          # -> "Detected 1 Alchitry Au V2"  (or Pt V2)

# Au V2
"$ALCHITRY" load --bin build/au_cam_hello.bin --board AuV2 --ram
# Pt V2
"$ALCHITRY" load --bin build/pt_cam_hello.bin --board PtV2 --ram
```

`--ram` is temporary and reverts on a power cycle — the right choice while bring-up is in progress.
`--flash` makes it persistent.

## `cam_probe` — when the answer is FAIL

```sh
vivado -mode batch -source build_hello.tcl -tclargs probe
"$ALCHITRY" load --bin build/cam_probe.bin --board PtV2 --ram
```

`reg0=FFFF` cannot distinguish *sensor unpowered* / *stuck in reset* / *open trace* / *wrong ball* /
*bad solder joint*. `cam_probe` splits that space using signals that **do not depend on SPI working**.
It prints one line per ~200 ms cycle:

```
rlo=0 rhi=0 reg0=0000 miso=L- pul=10
```

| Field | Meaning |
|---|---|
| `pul=` | **The important one.** Tri-states `ss_n`/`reset_n`/`trigger0-2` with no internal pull and reads back *the board's own* resistors, as `{ss_n, reset_n, trig[2:0]}`. **`10` = the network is present and powered** → the board is mated, its 3.3 V rail is up, and those five balls land where the XDC says. `00` = nothing there. |
| `miso=` | Whether `miso` was seen `L` / `H` while selected. |
| `rlo=`/`rhi=` | `monitor` sampled with `reset_n` low, then high. Differing values mean `reset_n` reaches a part that responds to it. |
| `reg0=` | Last value read from register 0. |

**The XDC fits internal PULLDOWNs on `miso` and `monitor`, and that is the whole trick.** Neither
net has a board pull (README pull table: `—`), so without one a floating pin and a driven pin are
indistinguishable — a floating input parks high and reads as a confident, entirely meaningless `1`.
Force a pulldown and any remaining `1` was *actually driven*.

> **Blind spot:** `mosi`, `sck`, `miso` and `clk_pll` are the four bank-14 signals with **no**
> external pull, so the tri-state trick cannot validate them — and three of those are the SPI bus.
> Those need a DMM or scope at the sensor end. They do share connector J4 with the five it can check.

## `cam_pinwalk` — the DMM instrument for the board → sensor hop

```sh
vivado -mode batch -source build_hello.tcl -tclargs walk
"$ALCHITRY" load --bin build/cam_pinwalk.bin --board PtV2 --ram
```

`cam_probe` proves everything up to the camera board but is blind to `mosi`, `sck`, `miso` and
`clk_pll` — the four bank-14 signals with no board pull, three of which are the SPI bus. Those need
a meter at the sensor end, and this makes it a one-person job.

Every output idles at its safe level; once every 2 s exactly one flips, in rotation, and the
matching LED lights.

| LED / step | Signal | Sensor pin | Idle | Driven |
|---|---|---|---|---|
| 0 | `mosi` | 2 | 0 V | 3.3 V |
| 1 | `sck` | 4 | 0 V | 3.3 V |
| 2 | `clk_pll` | 25 | 0 V | 3.3 V |
| 3 | `reset_n` | 46 | 0 V | 3.3 V |
| 4 | `ss_n` | 47 | 3.3 V | **0 V** *(inverted)* |
| 5–7 | `trigger0/1/2` | 41/42/43 | 0 V | 3.3 V |

Meter one sensor pin, watch which LED is lit when it moves:

- **changes on its own step** → that wire is good end to end.
- **never changes** → **open** between the DF40 and that sensor pin.
- **changes on someone else's step** → those two nets are **bridged**, or the pin map is wrong, and
  the lit LED names the culprit. This is the case a continuity beep-test tends to miss.

Idle levels match the board's own pull resistors, so nothing is disturbed. During the `ss_n` step
select goes active but `sck` is static — with no clock edge the sensor cannot shift anything in.
There is no SPI master in this design at all, so no register can be written.

## What each board can and cannot prove

The Au V2 can run this test in full, because the sensor's SPI is asynchronous to its system clock.
What it cannot do is receive a pixel, ever — on the Au the seven LVDS pairs scatter across three
banks at three fixed voltages and the forwarded bit clock lands where it can never be 2.5 V. That is
not fixable in RTL. See `CAMERA_IO_MAP.md` §8.2 and §8.4.

| | Au V2 | Pt V2 |
|---|---|---|
| Power tree up and correctly sequenced | ✅ | ✅ |
| DF40 pin map + stack pass-through | ✅ | ✅ |
| `reset_n` releases; SPI works both ways | ✅ | ✅ |
| Bank 13 @ 2.5 V, `DIFF_TERM`, SRCC/BUFIO, even-row routing | ❌ | ✅ (milestone #12) |
| ISERDES receiver, training, real pixels | ❌ | ✅ (milestone #12) |

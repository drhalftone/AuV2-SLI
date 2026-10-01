// ESP32-S3-MINI-1U-N8 pin map for the LauEsp32Config_Pt_Stack card.
// Every number here is read from the schematic netlist (kicad-cli sch export netlist), U1 pads
// -> nets, on 2026-10-01. Change the schematic first, then this file.
#pragma once

// ---- JTAG, through U2 (SN74LVC125A), rev A straps (README §3, SCHEMATIC.md §4)
#define PIN_JTAG_TCK        4   // U1 pad 8   ESP_TCK  -> U2 1A -> R10 0R -> J3 pin 49
#define PIN_JTAG_TDI        5   // U1 pad 9   ESP_TDI  -> U2 3A -> R12 0R -> J3 pin 43
#define PIN_JTAG_TDO        6   // U1 pad 10  ESP_TDO  <- U2 4Y <- R13 33R <- J3 pin 45 (R3 10k pull-down)
#define PIN_JTAG_TMS        7   // U1 pad 11  ESP_TMS  -> U2 2A -> R11 0R -> J3 pin 47
#define PIN_JTAG_OE_N       15  // U1 pad 19  JTAG_OE_N, all four /OE; R2 10k pull-up = buffer OFF by default

// ---- FPGA control (open-drain through 2N7002s; the Pt has the pull-ups)
#define PIN_FPGA_DONE       16  // U1 pad 20  ESP_DONE <- R6 1k <- J3 pin 39. INPUT ONLY: driving it low holds off startup
#define PIN_PROG_EN         17  // U1 pad 21  Q1 gate (R4 10k pull-down) -> pulls FPGA_PROGRAM_B (J3 41) low
#define PIN_RESET_EN        18  // U1 pad 22  Q2 gate (R5 10k pull-down) -> pulls FPGA_RESET (J3 37) low

// ---- microSD, SDMMC 4-bit through the GPIO matrix (10k pull-ups R31-R35 on CMD/D0-D3)
#define PIN_SD_CLK          36  // U1 pad 32  via R30 33R
#define PIN_SD_CMD          35  // U1 pad 31
#define PIN_SD_D0           37  // U1 pad 33
#define PIN_SD_D1           38  // U1 pad 34
#define PIN_SD_D2           33  // U1 pad 28
#define PIN_SD_D3           34  // U1 pad 29
#define PIN_SD_CD_N         47  // U1 pad 27  detect switch, R36 pull-up. Polarity UNVERIFIED on hardware

// ---- status LED: LED_STATUS -> R7 1k -> D1 -> GND, so active HIGH
#define PIN_LED             48  // U1 pad 30

// ---- fabric side channel (README §3b) -- wired, unused by this firmware yet
#define PIN_FAB_IRQ         8
#define PIN_FAB_IO3         9
#define PIN_FAB_CS          10
#define PIN_FAB_MOSI        11
#define PIN_FAB_SCK         12
#define PIN_FAB_MISO        13
#define PIN_FAB_IO2         14
#define PIN_FAB_SPARE       21

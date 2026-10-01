# ESP32 card firmware — bitstream library + MCP server

_Drafted 2026-10-01, before the boards exist. It **compiles clean** (ESP-IDF v5.5.5, 0 warnings in
`main/`), and the `.bit` parser's logic was checked against six real build outputs. Nothing has
run on hardware: §5 lists what has to be proven first._

Implements [`../SD_BITSTREAM_LIBRARY.md`](../SD_BITSTREAM_LIBRARY.md): a library of `.bit` images
on the microSD card, and an MCP server over WiFi that lists them and loads one into the Pt V2's
FPGA over JTAG. **Configuration RAM only**: the QSPI boot flash is never written, and nothing loads
at power-up. A power cycle, or `restore_flash_image`, always brings back the camera image.

## 1. Layout

Written in Dr. Lau's house style from his Qt C++ (boxed copyright header, `#ifndef LAUXXX_H`
guards, the three-line `/****/` banner before every function, ALL-CAPS comments, `lau`-prefixed
files and camelCase names, `...Flag` booleans, underscore-free macros, `return (x);`). C has no
classes, so `LAUFpga::load()` is written `lauFpgaLoad()`. Keep new code in the same style.

| File | Job |
|---|---|
| `main/laupins.h` | every GPIO, read from the schematic netlist |
| `main/laujtag.c` | bit-banged JTAG through U2; U2 is enabled only while shifting, so the Pt's FT2232H owns the bus otherwise |
| `main/laufpga.c` | UG470 JTAG configuration flow, IDCODE/part check, PROGRAM_B, DONE, the one-user JTAG lock |
| `main/laubitfile.c` | `.bit` header parser (pure C) |
| `main/laulibrary.c` | SDMMC 4-bit mount, list / sidecar / sha256 / delete / atomic install |
| `main/laufetch.c` | `fetch_bitstream`: HTTP(S) download straight to the card |
| `main/laumcp.c` | JSON-RPC: `initialize`, `ping`, `tools/list`, `tools/call`, notifications |
| `main/lauserver.c` | `POST /mcp`, `PUT /bitstreams/<name>.bit\|.json`, `PUT /ota`, bearer token, Origin refusal |
| `main/launetwork.c` | WiFi station, mDNS `<hostname>.local` |
| `main/main.c` | boot order, token, LED, OTA rollback guard |
| `tools/serve_bitstreams.py` | serve build folders to the card, writing sha256/commit sidecars |
| `tools/mcp_smoke.py` | bring-up test that speaks MCP the way Claude Code does |

## 2. Build

ESP-IDF v5.5.5 is installed at `C:\esp\esp-idf` (it needs Python ≥ 3.9; this machine's default
`python` is 3.8, so put 3.12 first):

```powershell
$env:Path = "C:\Users\dllau\AppData\Local\Programs\Python\Python312;C:\Users\dllau\AppData\Local\Programs\Python\Python312\Scripts;" + $env:Path
. C:\esp\esp-idf\export.ps1
cd LauEsp32Config_Pt_Stack\firmware
idf.py menuconfig        # LAU ESP32 card -> WiFi SSID / password / hostname
idf.py build
```

`sdkconfig` holds the WiFi password and is gitignored; `sdkconfig.defaults` has no secrets.

## 3. First flash (out of the stack)

The card has no USB connector. Wire a cut USB cable to the top-face pads (README §6.0):

| Pad | Wire |
|---|---|
| TP1 `+3V3` | 3.3 V bench supply (**not** USB 5 V) — only when the card is out of the stack |
| TP2 `GND` | ground, and the USB cable's ground |
| TP3 `USB_DP` / TP4 `USB_DN` | USB D+ (green) / D− (white) |
| TP5 `EN` | momentary to GND = reset |
| TP6 `BOOT` (GPIO0) | hold to GND while releasing EN = download mode |

Then `idf.py -p COMx flash monitor`. The console prints the endpoint and the **bearer token** at
every boot (generated once, kept in NVS). After this, updates go over WiFi:

```
curl -X PUT --data-binary @build/lau_esp32_card.bin -H "Authorization: Bearer <token>" http://lau-esp32.local/ota
```

New firmware that cannot reach the network within 120 s rolls itself back to the previous image.

## 4. Use from Claude Code

```
claude mcp add --transport http lau-esp32 http://lau-esp32.local/mcp --header "Authorization: Bearer <token>"
```

The just-in-time loop: build → `python tools/serve_bitstreams.py ../../build_pt` → ask Claude to
`fetch_bitstream` the printed URL → `load_bitstream`. `restore_flash_image` undoes it.

Tools: `list_bitstreams`, `get_fpga_status`, `load_bitstream`, `restore_flash_image`,
`fetch_bitstream`, `delete_bitstream`, `get_card_info`.

## 5. Bring-up — what is assumed, not proven

Do these in order, with the **Pt's USB cable unplugged** (the FT2232H shares the JTAG lines):

1. **JTAG reaches the FPGA.** `get_fpga_status` must show `jtag_chain_length: 1` and IDCODE
   `0x?3631093` (XC7A100T). Chain length 0 = U2 not driving, TDO open, or the board is rev B (move
   the straps, SCHEMATIC.md §4).
2. **INIT_COMPLETE is IR-capture bit 4.** Taken from UG470's IR capture layout; `INIT_B` is not on
   J3, so this is the only view of INIT. If loads fail with "INIT_COMPLETE never set", check this
   bit position first.
3. **Bitstream bit order.** Each byte is shifted MSB first (the order openFPGALoader and xc3sprog
   use for `.bit` over JTAG). A wrong order fails with DONE low, not with damage — then
   `restore_flash_image`.
4. **TCK rate.** Bit-banged with `LAU_JTAG_HALF_PERIOD_SPIN = 4`; measure TCK on a scope and the
   load time (`seconds` in the result). If IDCODE reads are unstable, raise it in menuconfig.
   Moving the data shift onto the SPI peripheral (README §5's 10 MHz) is the planned speed-up.
5. **SD card-detect polarity.** Unverified; `get_card_info` reports the raw `SD_CD_N` level so it
   can be read with and without a card. The mount does not depend on it.
6. **`restore_flash_image`** must bring DONE back within ~1 s with a valid image in the Pt's flash.

`mcp_smoke.py` runs steps 1, 5 and 6 and the protocol checks (wrong token → 401, notification →
202) in one go.

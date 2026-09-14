# microSD bitstream library — addendum to the ESP32 element card

_Started 2026-09-13. Nothing is built. This extends `README.md` in this folder and
**reverses two of its decisions** (§6.1 bitstream storage, and — if §2 here is
accepted — the §6.4/§8 module choice). The module switch is **not yet decided**;
see §8._

---

## 1. What this adds

**A microSD socket on the card, holding a library of precompiled bitstreams, and
an MCP server on the ESP32 that reports the library and loads any image in it on
request.**

An MCP client — Claude Code on the bench LAN — can then ask the camera what images
it carries, what each one is, and load one, without a PC toolchain in the loop.
Everything in `README.md` §2 still holds: images go into **configuration RAM
only**, and the QSPI boot flash is never written.

This settles `README.md` §6.1. That section leaned toward "stream over WiFi and
never store — no stale image to load by accident." The SD card stores images, so
the stale-image risk has to be handled by checks instead (§5.3).

## 2. It breaks the C3's GPIO budget

`README.md` §3b.1 puts the ESP32-C3-MINI-1U at **15 of 15 GPIOs** and calls that
over budget in practice. microSD needs more:

| Function | Pins |
|---|---|
| Existing allocation (`README.md` §3b.1) | 15 |
| microSD over SPI: CS, SCK, MOSI, MISO | 4 |
| Card detect (optional) | 1 |
| **Total** | **19–20** |

The C3 also has **no SDMMC host peripheral**; SPI mode is its only option.

| Option | Consequence |
|---|---|
| **A. Move to ESP32-S3-MINI-1U** — recommended | Far more GPIOs, a real SDMMC host (1- or 4-bit), native USB, optional PSRAM. Larger footprint. |
| B. Keep the C3; share the J3 SPI pins between the SD card and the fabric | The loaded bitstream owns those FPGA pins. A JIT image that drives them corrupts SD access — the opposite of "a bad image costs a reboot and nothing else." **Rejected.** |
| C. Keep the C3; give up the fabric SPI link | Loses the ESP32↔fabric data path of `README.md` §3b. |

**Speed is not the reason for the S3.** A 2.46 MB image reads from SD in a second
or two even over SPI, and the JTAG load at 10 MHz TCK takes ~2 s regardless
(`README.md` §5). The reason is pin count, and that the card keeps acquiring jobs.

### 2.1 What moving to the S3 changes — CONFIRM FROM THE DATASHEET

The S3 datasheet is not in `docs/`. The items below are from memory and **must be
checked against Espressif's ESP32-S3-MINI-1/1U datasheet** before they are relied on:

| Item | C3-MINI-1U (confirmed, `README.md`) | S3-MINI-1U (to confirm) |
|---|---|---|
| Footprint | 13.2 × 12.5 mm | ~15.4 × 15.4 mm |
| Height | 2.4 ± 0.15 mm | believed 2.4 mm — **re-derive §6.3's 1.725 mm clearance** |
| Native USB D−/D+ | GPIO18 / GPIO19 | GPIO19 / GPIO20 |
| Download-mode strap | GPIO9 | GPIO0 |
| Other strapping pins | GPIO2, GPIO8 | GPIO3, GPIO45, GPIO46 |

`README.md` §6.0's edge-pad table changes with it: `USB_D+`/`USB_D−` move to the
S3's USB pins, and the boot pad moves from `GPIO9` to `GPIO0`. The antenna
reasoning of §6.4 is unchanged — take the `-1U` wired-antenna variant for the same
reasons.

## 3. Socket placement and access

**The SD card is a library filled over WiFi, not a user-facing slot.** Load it in
bulk on the bench; after that the ESP32 adds and removes images itself (§6). This
keeps faith with the card's reason for existing — a sealed unit — because nothing
in the field needs to reach the socket.

If field access is wanted after all, the socket's insertion edge has to face the
enclosure's one port window, with the same constraint as the §6.0 pads.

**Height.** microSD sockets are typically **~1.3–2.0 mm**; pick one and read its
drawing. That fits the 4.125 mm top-face gap beside the module. **Do not put it on
the bottom face** — the Hd+'s top-side heights are still unmeasured
(`README.md` §6.3).

**Power.** SD cards draw on the order of 100 mA during writes. Trivial against the
4 A 3V3 rail, but give the socket its own local decoupling as `README.md` §6.2
already requires for the radio.

## 4. Design rule: no automatic load at power-up

`README.md` §2's guarantee is that **power-cycling restores the shipped camera
image** from QSPI flash. A "load the default SD image at boot" feature silently
removes that guarantee.

**Rule: the ESP32 never loads a bitstream from SD without an explicit request.**
If an autoload is ever added, it is off by default, set deliberately, and
cancellable — e.g. skipped if `DONE` is already high, so it never overwrites the
flash image on a camera that booted normally.

The complement to this is `restore_flash_image` (§6.2): **pulsing `PROGRAM_B`
(J3 pin 41) makes the FPGA reconfigure from its QSPI flash**, which undoes a bad
JIT image without a power cycle.

## 5. Library format

### 5.1 Store `.bit`, not `.bin`

Every build script already writes both (`build.tcl:57`, `build_pt.tcl:142`,
`build_merged.tcl:240`, `build_pt_hdmi.tcl:275`). The `.bin` is a `write_cfgmem`
**flash image**; the `.bit` is what JTAG configuration wants, and its header is
self-describing:

| Field | Contents |
|---|---|
| `a` | design name, with `UserID` / `Version` |
| `b` | part name — `7a100tfgg484` for this project (`xc7a100tfgg484-2`, `build_pt.tcl`) |
| `c` | build date |
| `d` | build time |
| `e` | length of the configuration data that follows |

So the ESP32 can describe any image on the card by parsing ~100 bytes, with no
sidecar required and no build-script change.

### 5.2 Optional sidecar JSON

For what the header cannot carry, a file of the same stem sits beside each image:

```
/bitstreams/
    sli_merged.bit
    sli_merged.json
    pt_hdmi.bit
    pt_hdmi.json
```

```json
{
  "description": "Merged SLI + camera, projector LED drive moved with picture",
  "git_commit": "0beb8a4",
  "sha256": "…",
  "source_script": "build_merged.tcl"
}
```

A sidecar per image, rather than one `manifest.json`, cannot drift out of sync
when a file is added or deleted by hand. The build scripts can write it as a final
step once this is real.

**Filesystem:** FAT32 with long filenames (ESP-IDF FATFS over SDMMC or SDSPI).
At ~2.5 MB per image, capacity is not a constraint — a 32 GB card holds thousands.

### 5.3 Stale-image guards — replacing "never store"

Storing images brings back the risk `README.md` §6.1 wanted to avoid. Handle it
with checks the firmware performs on **every** load:

1. **Part check.** Read the JTAG IDCODE; compare against the `.bit` header's `b`
   field. XC7A100T is `0x?3631093` — mask the top (version) nibble. Refuse on
   mismatch.
2. **Integrity check.** If a sidecar carries `sha256`, verify before clocking
   anything into the part.
3. **Completion check.** Read `DONE` (J3 pin 39) after the load and report it.
   `INIT_B` is not on J3 (`README.md` §3), so `DONE` is the only completion signal.

## 6. The MCP server

### 6.1 Transport

**Streamable HTTP, stateless, plain JSON responses** — no SSE, no sessions. That is
a valid MCP server and keeps the firmware to `initialize`, `tools/list`,
`tools/call` and `ping`.

- Endpoint `http://<name>.local/mcp`, advertised over **mDNS**.
- **Bearer token** checked on every request. The client adds it with
  `claude mcp add --transport http <name> http://<name>.local/mcp --header "Authorization: Bearer …"`.
- **LAN only.** A claude.ai connector would need public HTTPS and OAuth, which is
  not worth carrying on this part.

### 6.2 Tools

| Tool | Inputs | Does |
|---|---|---|
| `list_bitstreams` | — | name, size, part, build date/time, description, git commit, for every image on the card |
| `get_fpga_status` | — | `DONE`, IDCODE, name of the last image this ESP32 loaded (unknown after the FPGA boots from flash) |
| `load_bitstream` | `name` | runs the §5.3 checks, loads over JTAG, returns `DONE` and elapsed time |
| `restore_flash_image` | — | pulses `PROGRAM_B`; FPGA reloads its QSPI image |
| `fetch_bitstream` | `url`, optional `name` | ESP32 downloads an image (and its sidecar) from the build machine onto the card |
| `delete_bitstream` | `name` | removes an image and its sidecar |
| `get_card_info` | — | SD capacity, free space, image count |

**`load_bitstream` blocks until done.** At ~2 s that is shorter than most HTTP
timeouts; there is no need for async jobs or progress notifications. Refuse a
second load while one is running.

### 6.3 Bitstream upload does NOT go through a tool call

A 2.5 MB image as base64 inside a JSON-RPC message is ~3.3 MB of text that the
ESP32 must buffer and decode — well beyond its SRAM without PSRAM, and slow over
the model's context either way. Two paths instead:

- **Pull:** `fetch_bitstream(url)` — the ESP32 streams from an HTTP server on the
  build machine straight to the card. Preferred: the JIT loop becomes *build →
  serve the output directory → one tool call*.
- **Push:** a plain `PUT /bitstreams/<name>.bit` endpoint beside `/mcp`, same bearer
  token, streamed to the card in chunks. For `curl` from a script.

### 6.4 Relationship to XVC

`README.md` §5's XVC mode stays. It is for Vivado and hardware debug; this library
is for loading known images by name. They share the JTAG pins, so the firmware must
**refuse an MCP load while an XVC client is connected**, and vice versa.

### 6.5 Alternative interface: micro-ROS

**The library and its guards (§4, §5) do not change; only the front door does.**
Instead of — or beside — the MCP server, the card can be a **ROS 2 node** using
micro-ROS, which has an ESP-IDF component (`micro_ros_espidf_component`) and
supports both the C3 and the S3.

**Architecture.** micro-ROS does not speak DDS on the ESP32 itself. It speaks
**XRCE-DDS over UDP** (WiFi) to a **micro-ROS agent** running on a host, and the
agent represents it in the ROS 2 graph. So unlike §6.1, **a host process is always
in the loop.**

**The same tools, as ROS 2 interfaces:**

| §6.2 tool | ROS 2 interface | Why that kind |
|---|---|---|
| `list_bitstreams` | service | request/reply |
| `get_card_info` | service | request/reply |
| `get_fpga_status` | **topic**, published at ~1 Hz | state other nodes can subscribe to without polling |
| `load_bitstream` | **action** | ~2 s, reports progress (bits shifted), cancellable; result carries `DONE` and elapsed time |
| `restore_flash_image` | service | |
| `delete_bitstream` | service | |
| `fetch_bitstream` | service taking a URL | **the image still travels over HTTP** — see below |

These need a small interface package, e.g. `lau_bitstream_interfaces`
(`srv/ListBitstreams`, `action/LoadBitstream`, `msg/FpgaStatus`), built **both**
into the micro-ROS firmware library and into the host workspace. Custom interfaces
require rebuilding the micro-ROS library — a firmware-build step, not a runtime one.

**What micro-ROS does badly here — confirm before choosing it:**

- **No bitstream transfer over ROS.** XRCE-DDS works in small, statically
  allocated buffers; a 2.5 MB message is out of the question. §6.3's reasoning
  holds even more strongly: the image moves over HTTP, and ROS only names it.
- **`list_bitstreams` on a large library** can exceed the default message size.
  Page the reply, or raise the transport MTU and use reliable (fragmenting)
  streams.
- **Action server support in `rclc`** is newer than services and topics. Verify it
  against the ROS 2 distro in use; if it is lacking, fall back to a service plus a
  progress topic.
- **No descriptions for a model.** ROS interfaces are typed but carry no
  natural-language purpose. An LLM reaching the card through a ROS→MCP bridge
  (rosbridge-based MCP servers exist) sees names and types only — keep interface
  and field names self-explanatory.

**When to choose which:**

| | MCP on the ESP32 (§6.1) | micro-ROS |
|---|---|---|
| Host software needed | none | micro-ROS agent |
| Reachable by Claude Code | directly | through a ROS→MCP bridge |
| Reachable by other robot software | HTTP only | native ROS 2 graph |
| Long operations | blocking call | action, with feedback and cancel |
| Time sync with the rest of the rig | none | agent session time sync |
| Worth it when | the camera stands alone | the rig is becoming a ROS system — arms, turntables, other sensors |

**They are not exclusive.** Both front ends can call the same library code in one
firmware image, provided the JTAG lock of §6.4 covers **all three** clients — MCP,
micro-ROS, and XVC. Build MCP first (no host dependency, §6.1 is already specified)
and add micro-ROS when a ROS graph exists to join.

## 7. Firmware outline

| Piece | Source |
|---|---|
| WiFi, mDNS, HTTP server | ESP-IDF `esp_wifi`, `mdns`, `esp_http_server` |
| JSON-RPC / MCP | `cJSON`, hand-written dispatch — four methods |
| SD + filesystem | `sdmmc` (S3) or `sdspi` (C3), `fatfs` |
| `.bit` header parser | ~50 lines, hand-written |
| JTAG shift | SPI peripheral driving TCK/TDI at 10 MHz (`README.md` §5) |
| OTA with rollback | `esp_https_ota`, A/B partitions (`README.md` §6.0) |
| micro-ROS front end (optional, §6.5) | `micro_ros_espidf_component`, `rclc`; custom `lau_bitstream_interfaces` |

## 8. Open items

| | |
|---|---|
| **Module: C3 → S3-MINI-1U** | **DECISION PENDING.** Recommended (§2). Blocks the schematic. |
| S3 datasheet figures (§2.1) | fetch into `docs/`, confirm height, footprint, USB and strap pins |
| microSD socket part | not chosen; read its height from the drawing |
| Field access to the slot | proposed **no** — filled over WiFi (§3) |
| Sidecar JSON written by build scripts | not started; `.bit` header alone is enough to begin |
| micro-ROS front end | **alternative, not scheduled** — add when the rig joins a ROS 2 graph (§6.5) |
| Rev B JTAG pin move | inherited from `README.md` §3 — confirm board revision first |

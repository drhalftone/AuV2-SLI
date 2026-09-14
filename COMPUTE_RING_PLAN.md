# Compute ring — camera stacks as LLM-addressable FPGA compute

_Drafted 2026-09-14. Nothing is built._ Long-term goal, not scheduled work. Target: a ring of
Pt V2 stacks that an LLM can hand data to for processing, reached from a host PC over the Ft+
and the ESP32's WiFi, and interconnected node to node over the HDMI ports.

Builds on: [`LauEsp32Config_Pt_Stack/README.md`](LauEsp32Config_Pt_Stack/README.md) (ESP32
element card) · [`LauEsp32Config_Pt_Stack/SD_BITSTREAM_LIBRARY.md`](LauEsp32Config_Pt_Stack/SD_BITSTREAM_LIBRARY.md)
(bitstream library + MCP server) · [`PC_INTERFACE_PLAN.md`](PC_INTERFACE_PLAN.md) (Ft+ link) ·
[`README.md`](README.md) §5–§6 (HDMI TX/RX and their ceilings)

---

## 1. The goal in one paragraph

Each camera stack is also a **compute node**: an XC7A100T with 256 MB of DDR3L, a USB 3 link
to the host (Ft+), a WiFi control link (ESP32), and one HDMI transmitter and one HDMI
receiver. Wire each node's HDMI **out** to the next node's HDMI **in** and the stacks form a
unidirectional ring. An LLM, through MCP, names a **kernel** (a precompiled partial bitstream)
and a **dataset** (a reference, not the bytes); the host moves the data in over the Ft+, the
ring carries it between nodes, and results come back to the host.

## 2. One node

| Resource | Figure | Source |
|---|---|---|
| FPGA | XC7A100T-2FGG484 — 101,440 LUTs, 126,800 FFs, 240 DSP48E1, 135 BRAM36 | `build_pt.tcl`; Xilinx 7-series product table |
| Memory | 256 MB DDR3L | `CAMERA_RTL_PLAN.md` |
| MMCMs | 6 on the part; **5 already used** by the merged camera + SLI design | `MERGE_MILESTONES.md` M0 |
| Host link | Ft+ FT601Q, **~232 MB/s measured** | `PC_INTERFACE_PLAN.md` |
| Control link | ESP32 WiFi, ~4 MB/s | `LauEsp32Config_Pt_Stack/README.md` §3b.1 |
| Node-local side channel | ESP32 ↔ fabric SPI on J3 29–36, ~8–10 MB/s | same, §3b |
| Ring link | HDMI TX → next node's HDMI RX, one direction per cable | §4 |

**The MMCM count is the first budget to watch.** A ring node needs a recovery MMCM on its
receiver and a clean generated clock on its transmitter — the same two clocking jobs the SLI
pass-through already does. Adding the ring *beside* the full camera + SLI design leaves one
spare MMCM. See §5.

## 3. Architecture: control plane and data plane are different wires

```
                       ┌──────────────── host PC ────────────────┐
                       │  cluster MCP server · job scheduler     │
                       │  data in/out · ring health              │
                       └──┬──────────┬──────────┬──────────┬─────┘
             USB 3 (Ft+)  │          │          │          │   ~232 MB/s each, both ways
             WiFi (ESP32) ┆          ┆          ┆          ┆   ~4 MB/s, control only
                       ┌──┴──┐    ┌──┴──┐    ┌──┴──┐    ┌──┴──┐
                  ┌───►│node0├───►│node1├───►│node2├───►│node3├───┐
                  │    └─────┘HDMI└─────┘HDMI└─────┘HDMI└─────┘   │
                  └───────────────────────  HDMI  ────────────────┘
```

| Plane | Carries | Wires |
|---|---|---|
| **Control** | MCP calls, job submission, kernel loads, health, errors | ESP32 WiFi; also the Ft+ control path (`PC_INTERFACE_PLAN.md` Port B) |
| **Data** | datasets, intermediate results, outputs | Ft+ (host ↔ node), HDMI (node → node) |

### 3.1 The LLM never carries the data

A tool call holds kilobytes; WiFi is ~4 MB/s. So MCP calls **name** data and never contain it:

```
submit_job(kernel="sobel_3x3", input="host:/data/scan_042/", output="host:/results/scan_042/")
```

The host resolves `input`, pushes it over the Ft+, and the ring does the rest. This is
`SD_BITSTREAM_LIBRARY.md` §6.3's rule — "bitstreams do not go through a tool call" —
generalised to everything.

## 4. HDMI as the ring link

### 4.1 Why it fits

- **The directionality matches.** Each stack has exactly one TX and one RX. A one-way ring is
  the topology HDMI gives for free; a bus or a mesh would need more ports.
- **Both ends are ours.** No projector, no EDID negotiation, no standard timing required.
  Blanking can be cut to the minimum the receiver needs for character alignment, and the pixel
  clock picked for the link rather than for a display.

### 4.2 How fast — ESTIMATES, NOT MEASUREMENTS

TMDS carries **8 payload bits per channel per pixel clock** during active video; blanking
carries only control tokens (the TX is DVI-D — no data islands; commit `0c7a263`).

| Pixel clock | Basis | Active-video payload |
|---|---|---|
| 74.25 MHz, 1280×720@60 standard timing | **highest mode pass-through RX is verified at** (`README.md` §6) | 1280×720×60×24 = 1.33 Gb/s = **~166 MB/s** |
| 74.25 MHz, minimal blanking | same RX rate, blanking cut | ≤ 3×8×74.25 MHz = **≤ 223 MB/s** |
| 108 MHz, minimal blanking | offline TX ceiling (×5 = 540 MHz, `README.md` §6) | ≤ **324 MB/s** — **RX not proven at this rate** |
| ~120 MHz | ~600 MHz OSERDES/BUFG ceiling | ≤ 360 MB/s — theoretical |

**Per hop, the ring is in the same class as the Ft+.** Treat every row as arithmetic until R1
measures it.

### 4.3 Store-and-forward, not cut-through

`README.md` §6 records that **1280×1024 pass-through was attempted and dropped**: recovering
108 MHz and re-serialising it never held sync, because the recovered clock carried the GPU's
spread spectrum and the jitter cleaner only reached "mostly perfect."

A ring that forwards on the recovered clock would stack that jitter hop after hop. So each node:

1. receives on its **recovered** clock,
2. crosses into its **own clean** clock through an elastic FIFO (or DDR3 for large buffers),
3. retransmits from the clean clock.

Jitter then resets at every hop, and the source clock is an FPGA MMCM with no spread spectrum,
which is a better input to the next receiver than a GPU ever was. This also makes every node a
natural point to consume, transform or inject packets.

### 4.4 The link layer — DVI has no error detection

TMDS is DC-balanced line coding. It has **no CRC and no FEC**; a bit error silently corrupts a
byte. The ring needs its own layer on top of the pixels:

| Field | Purpose |
|---|---|
| Sync word | packet boundary inside the active-video byte stream |
| Destination, source | node addressing (`0xFF` = broadcast) |
| Sequence number | loss and duplicate detection, per source |
| Length, type | payload framing |
| Payload | data |
| CRC32 | integrity |

**NAKs cannot go backwards on a one-way ring.** A retransmit request either travels the rest of
the way around, or is reported out of band — Ft+ or WiFi to the host, which tells the sender.
The out-of-band path is simpler and the host already sees every node; prefer it.

**The frame boundary is a free ring clock.** Every node already observes the same vsync in a
daisy chain (`FRAME_HEADER_PLAN.md` §3). In the ring each hop's frame start is a heartbeat:
a node that stops seeing frames on its RX knows its upstream neighbour or cable is down.

## 5. OPEN DECISION: the HDMI ports already drive the projector

In the SLI rig, HDMI **in** comes from the PC and HDMI **out** goes to the projector. A node
cannot feed both the projector and the next node from one transmitter.

| Option | What it means | Cost |
|---|---|---|
| **(a) Per-node mode** | a node boots as *camera* or as *compute*; the ring shell replaces the SLI datapath | a node cannot scan and compute at once — but it frees the SLI design's MMCMs (§2) |
| (b) Data inside the video | packets in reserved rows of real frames, passed through to the projector | visible or cropped rows; payload limited to those rows; ties link rate to the projected mode |
| (c) Compute stacks carry no projector | dedicated compute nodes, camera nodes stay as they are | more hardware; cleanest link |

**Not decided.** (a) is the natural fit with the rest of this plan — reconfiguration (§6) is
already the mechanism that changes what a node is — and it resolves the MMCM budget. (b) is the
only option that lets a projecting camera also be in the ring.

## 6. Reconfiguring a node without breaking the ring

**A node reloading its bitstream over JTAG stops forwarding HDMI, and the whole ring goes down
for the duration.** With kernels swapped per job, that is unacceptable.

### 6.1 Static shell + reconfigurable partition (DFX)

7-series parts support partial reconfiguration (Xilinx: **Dynamic Function eXchange**):

| Region | Contents | Changes |
|---|---|---|
| **Static shell** | HDMI ring RX/TX + link layer, Ft+ interface, DDR3 MIG, ESP32 SPI slave, `ICAPE2` loader, node registers | only by full reconfiguration |
| **Reconfigurable partition** | the compute kernel, behind a fixed streaming interface to the shell | per job |

The ring, the host link and the DDR stay up while the kernel changes.

**Check before designing:** whether DFX needs a separate license in the Vivado version in use.
It was once a paid option and is believed to be included now — confirm.

### 6.2 Load kernels through the fabric, not JTAG

The ESP32 streams the partial bitstream over the **J3 SPI side channel** into the static shell,
which writes it through **`ICAPE2`**. JTAG is not touched and the shell never stops. Partial
bitstreams are also much smaller than full images, so the load is quicker.

JTAG from the ESP32 (`LauEsp32Config_Pt_Stack/README.md` §5) remains the path for **full**
reconfiguration: installing or replacing the shell, and recovery.

### 6.3 The boot image changes meaning

`LauEsp32Config_Pt_Stack/README.md` §2 makes power-cycling restore "the shipped image" from QSPI
flash, and treats the flash as never written. For the ring to come up at power-up, **the shell
must be what boots from flash** on a compute node. That is a one-time, deliberate flash write
on the bench — the §2 rule still holds for everything the ESP32 and MCP can do in the field.

## 7. When a node fails

A physical HDMI ring has **no bypass**: one dead node or unplugged cable stops the loop.

- **Detection:** RX frame loss (§4.4) at the downstream node, reported over Ft+ or WiFi.
- **Recovery:** the host sees every node on USB 3, so it can **re-route around the break
  through itself** — forward from the node before the gap to the node after it over Ft+ —
  at the cost of host round-trips.
- **Decide early** whether that degraded mode is required, or whether a broken ring simply
  fails the job.

## 8. MCP: two tiers

| Tier | Runs on | Tools | Already specified |
|---|---|---|---|
| **Node** | each ESP32 | health, kernel library, load kernel, restore flash image, FPGA status | `SD_BITSTREAM_LIBRARY.md` §6 |
| **Cluster** | host PC | `list_nodes`, `list_kernels`, `submit_job`, `job_status`, `get_result`, `ring_health` | not yet |

**The cluster server lives on the host** because the datasets and the Ft+ links are there. An
ESP32 head node could aggregate the others over WiFi, but it would be scheduling data it can
never touch.

A job, as the cluster server sees it:

1. resolve the kernel on every node the job uses; load where missing (node tier, §6.2);
2. push input over Ft+ to the ingress node;
3. the ring carries it — as a **pipeline** (each node one stage, data flows once around) or
   **scatter/gather** (chunks addressed to nodes, results forwarded to the egress node);
4. pull the result over Ft+; verify; report by reference.

The one-way ring suits the pipeline form best: every hop is the next stage.

## 9. What this changes in the ESP32 card now

| Item | Change | Where |
|---|---|---|
| Module | **C3 → S3 becomes stronger.** The SPI side channel stops being optional — it is the kernel-load path (§6.2) — and the C3 cannot spare its pins. | `SD_BITSTREAM_LIBRARY.md` §2 |
| Library contents | full images **and partial kernels**; sidecar metadata records the **shell ID** a kernel fits | `SD_BITSTREAM_LIBRARY.md` §5.2 |
| Stale-image guard | a kernel whose shell ID does not match the running shell is refused, like a part mismatch | `SD_BITSTREAM_LIBRARY.md` §5.3 |
| MCP rule | tools carry references, never datasets | `SD_BITSTREAM_LIBRARY.md` §6.3 |

## 10. Milestones

Written the way [`MERGE_MILESTONES.md`](MERGE_MILESTONES.md) writes them: **every milestone has a
proof a broken system cannot produce.** "It builds", "it looks right" and "the counter says so"
are not pass criteria.

| # | Milestone | Proof |
|---|---|---|
| **R0** | Accounting, no build: MMCM and resource budget of a shell; §5 decision; DFX license confirmed | a table of every clock and region with numbers, and the decision recorded here |
| **R1** | Two nodes, one cable: offline TX → RX carrying PRBS in active video | BER checker reports **zero errors over ≥ 10¹² bits** — and reports **exactly N** when N errors are deliberately injected at the TX. A checker that cannot count injected errors has not proven anything. |
| **R2** | Link layer: framing, sequence numbers, CRC32 | injected bit errors produce exactly that many CRC failures; a cable pulled mid-stream is reported as loss, with the sequence gap matching the outage |
| **R3** | Three-node store-and-forward ring | a packet with a known hash circulates **1,000 times** and returns bit-exact; per-hop latency and end-to-end throughput measured against R1's single-hop rate |
| **R4** | Host ingress/egress over Ft+ | ≥ 10 GB in at node 0, out at node k, **SHA-256 equal**; throughput measured end to end |
| **R5** | DFX: shell + one partition, kernel loaded ESP32 → SPI → `ICAPE2` | a kernel swap **during** R3 traffic loses **zero** packets, and the partition's kernel-ID register changes |
| **R6** | Cluster MCP server | an LLM submits a job by reference; the output's hash equals a **software reference implementation** of the same kernel run on the host |

## 11. Open questions

| | |
|---|---|
| **§5 HDMI sharing** | per-node mode, data in video, or projector-free compute nodes |
| Ft+ on every node, or gateways only | every node is simplest; the host then needs one USB 3 root per few nodes |
| RX rate above 74.25 MHz | untested; a clean FPGA-sourced clock may hold where the GPU's did not (§4.3) |
| DFX license | confirm for the Vivado version in use |
| Degraded ring | re-route through the host (§7), or fail the job |
| Kernel interface | the fixed streaming interface between shell and partition — width, back-pressure, DDR access |

# Intel Warranty Claim: Core i9-14900KF (Raptor Lake instability)

Prepared 2026-10-07. The test logs are in this same folder.

---

## 1. Still to collect before filing

- [x] **Proof of purchase.** `invoice_904666187.pdf` (in this folder)
  - Seller: **B&H Photo-Video**, 420 Ninth Ave, New York, NY 10001 (Kentucky@bhphoto.com, 212-239-7760 ext. 7745)
  - Invoice date: **2024-03-06**
  - Invoice number: **222025773**, order number **904666187**, PO **7500418059**
  - Item: **Intel Core i9-14900KF**, B&H SKU IN80715149KF, Intel part number **BX8071514900KF** (boxed retail, so the claim goes **directly to Intel**)
  - Price: $539.00
  - Bought together with the ASUS ROG STRIX Z790-F GAMING WIFI motherboard (serial R3M0CS10R937R7Z), $270.02
  - Billed to: **University of Kentucky** (Accounts Payable); shipped to Dan Lau, 512 Administration Dr #453, Lexington, KY 40506
- [x] **CPU serial number (from the invoice):** **U3D34N2300659**
- [ ] **Batch number (FPO).** Printed on the box label and on top of the CPU, e.g. `X3xxxxxx`. `__________`
- [ ] **Check the box label** against the invoice serial number above, if you still have the box.
- [x] **University property.** The university paid for this CPU, but the PC has no UK asset tag (it was assembled from components), so nothing needs to be tracked for the swap.

**Warranty status:** bought 2024-03-06. Boxed CPUs come with a 3-year warranty, which Intel extended by 2 years for 13th/14th gen, so coverage runs to about **2029-03-06**. It is **in warranty**.
- [ ] **Photos.** The box label, and the top of the CPU if you take the cooler off.
- [ ] **Shipping address and phone number** for the replacement.

---

## 2. The system

| Item | Value |
|---|---|
| CPU | Intel Core i9-14900KF, Family 6 Model B7 Stepping 1 (CPUID 0xB0671) |
| Cores | 24 physical (8P + 16E), 32 logical |
| Motherboard | ASUS ROG STRIX Z790-F GAMING WIFI |
| BIOS (now) | 3202, dated 2026-05-06 |
| BIOS (before 2026-10-07) | 2402, dated 2024-06 |
| Microcode (now) | **0x133** |
| Microcode (before 2026-10-07) | 0x125 |
| Memory | 96 GB DDR5 |
| GPU | AMD Radeon RX 7900 XTX |
| OS | Windows 11 Pro 64-bit |
| Storage | Intel RAID0 volume |

---

## 3. Symptoms (since about 2026-09)

The machine silently produces wrong data when it writes. None of the failures could be reproduced on demand:

- **Corrupt git objects** when writing to disk. Seven in one session on 2026-09-29/30, and more on 2026-10-06 and 2026-10-07. Errors included `incorrect data check` and `serious inflate inconsistency` from zlib, `failed to unpack compressed delta`, and `malformed mode in tree entry`.
- **A rewrite of a corrupt object was itself corrupt** (2026-10-07). Writing the same data again came out clean.
- **Python crashes:** `SystemError: unknown opcode` (a corrupt `.pyc` file), a CPython segfault, and a `TypeError` that disappeared when the identical command was run three more times.
- **Single-digit corruption** in written files.
- GitHub rejected the bad objects on push, and fresh clones were always clean. The corruption happens on this machine, not in the files' sources.

---

## 4. Troubleshooting done

| Date | Step | Result |
|---|---|---|
| ~2026-10-06 | Memory test | **Passed.** RAM ruled out as the main suspect. |
| 2026-10-07 | BIOS updated 2402 → 3202, microcode 0x125 → **0x133** (includes Intel's Vmin Shift fixes 0x129 / 0x12B / 0x12F) | Confirmed in Windows |
| 2026-10-07 | Power limits checked with HWiNFO64 8.54 | **PL1 = PL2 = 253 W**, matching Intel's spec for the i9-14900K/KF |
| 2026-10-07 | Intel Processor Diagnostic Tool 4.5, run 1 | PASS |
| 2026-10-07 | Intel Processor Diagnostic Tool 4.5, run 2 | **FAIL: FMA3 test** |

### IPDT run 2 in detail (16:51–16:53, 2026-10-07)

| Module | Result |
|---|---|
| Genuine Intel | PASS |
| Brand String | PASS |
| Cache | PASS |
| MMX/SSE | PASS |
| Integrated Memory Controller | PASS |
| Prime Number (Parallel_PrimeNum) | PASS |
| AVX2 / AES / PCLMULQDQ (Parallel_FP) | PASS |
| Floating Point (Parallel_FP) | PASS |
| **FMA3 (Parallel_Math)** | **FAIL** |
| **Overall** | **Fail** |

Sensor peaks during the run, from HWiNFO64:

| Sensor | Peak | Comment |
|---|---|---|
| CPU package power | 145 W | well under the 253 W limit |
| Vcore | 1.46 V | |
| CPU package temperature | 96 °C | below Tjmax (100 °C); one brief thermal-throttle flag on one P-core |
| Power-limit exceeded flags | none | |

**Conclusion:** the CPU gives wrong FMA3 results on Intel's latest microcode, at Intel's default power limits, with no overclock. Together with the intermittent data corruption, this matches the 13th/14th gen Vmin Shift degradation that Intel acknowledged, and Intel extended the warranty on affected processors to 5 years.

---

## 5. Evidence in this folder

| File | What it is |
|---|---|
| `TESTRESULTS.TXT` | IPDT summary, overall **Fail** |
| `TestResults_Full.txt` | full IPDT log |
| `FMA3_Parallel_Math_1_Results.txt` / `Parallel_Math_1_Results.txt` | the failing test |
| `System_Info.txt` | system information recorded by IPDT |
| `invoice_904666187.pdf` | B&H invoice, proof of purchase (2024-03-06) |
| `math_capped_5000/` | full IPDT **PASS** with the 4.8 GHz cap on (shows the fault depends on clock speed; don't lead with this in the claim) |
| `*_Results.txt` (the rest) | the passing modules |

Optional extra evidence to add:
- [ ] A screenshot of the IPDT window showing **FAIL**
- [ ] A screenshot of HWiNFO showing microcode 0x133 and PL1/PL2 = 253 W
- [ ] A second IPDT failure, or an OCCT / y-cruncher error, to show the problem repeats

---

## 6. How to file

1. Go to **https://supporttickets.intel.com** and sign in or create an account.
2. Open a **warranty** request for the **Intel Core i9-14900KF**.
3. Enter the batch number, the ATPO/serial number, the purchase date and the seller from section 1.
4. Paste the summary below, and attach the IPDT logs, the receipt and the photos.
5. Intel normally confirms by email, then sends an RMA number and shipping instructions. Pack the CPU in its original clamshell or an anti-static box.
6. Ask whether they offer an **advance replacement** (a new CPU shipped before you return the old one) to cut downtime. Intel has offered this for some 13th/14th gen claims.

### Summary to paste into the ticket

> My Intel Core i9-14900KF (boxed, BX8071514900KF, serial U3D34N2300659, purchased 2024-03-06 from B&H Photo-Video, invoice 222025773) has become unstable, producing silent data corruption (corrupt files on write, zlib data-check errors, Python interpreter crashes with invalid opcodes) that cannot be reproduced on demand. RAM has passed a memory test. On 2026-10-07 I updated the BIOS (ASUS ROG STRIX Z790-F, BIOS 3202) to microcode 0x133 and confirmed Intel default power limits (PL1 = PL2 = 253 W) with no overclocking. On this configuration, Intel Processor Diagnostic Tool 4.5 **failed the FMA3 test** (all other modules passed). Peak package power during the test was 145 W and peak temperature 96 °C. I believe this processor is affected by the 13th/14th gen Vmin Shift instability and request a replacement under the extended 5-year warranty. IPDT logs attached.

---

## 6a. Returning the CPU

Yes, the faulty CPU goes back to Intel; the warranty is a swap. Intel says which options apply when it approves the claim:

| Option | How it works | Downtime |
|---|---|---|
| Standard | Ship the CPU to Intel first; they send the replacement after receiving and checking it | days to a couple of weeks without a CPU |
| **Advance replacement (ask for this)** | Intel ships the new CPU first; return the old one within the stated period (usually about 30 days); Intel holds a credit card until it arrives | almost none |

- **Ask for advance replacement in the ticket.** You can keep working on the capped CPU (section 7) until the new one arrives.
- Some 13th/14th gen claims have been offered a refund or a different model instead. Your choice if offered.
- A CPU stores no user data, so there's nothing to wipe.
- Packing: clean off the thermal paste; use the original clamshell or anti-static packaging inside a padded box. Photograph the box label (batch and serial numbers) first and keep the box.
- Shipping: normally you pay to send the CPU to Intel and Intel pays for the replacement's shipping. Confirm against the RMA email.

---

## 7. Until the replacement arrives

- Treat everything this PC writes as suspect. Run `git fsck --full` after every commit or fetch, and check large binaries by reading them back.
- Produce fab outputs and important builds on another machine, or generate them twice and compare.
- **Temporary fix, applied 2026-10-07:** a Windows clock cap. The hidden power-plan settings *Maximum processor frequency* (PROCFREQMAX and PROCFREQMAX1) are set to 5000 MHz on the ASUS "GameTurbo (High Performance)" plan (`da59d749-aede-466a-b5a1-4a2c7c53723f`). HWiNFO shows the P-cores holding at **4800 MHz**.
  - With the cap on, the IPDT Math module (which includes FMA3) **passed 4 of 4 runs**: three from the command line (17:17–17:20; their logs were overwritten by the next run) and one **full IPDT run in the tool window that passed overall** (17:22–17:25; logs in `math_capped_5000/`). Without the cap, the full run failed 1 of 2.
  - **Armoury Crate or a power-plan switch can silently drop the cap.** If HWiNFO shows P-cores above 4800 MHz, reapply it.
  - **Remove it once the replacement is in:**
    `powercfg /setacvalueindex da59d749-aede-466a-b5a1-4a2c7c53723f SUB_PROCESSOR 75b0ae3f-bce0-45a7-8c89-c9611c25e100 0`
    Then repeat with `...e101`, run both again with `/setdcvalueindex`, and finish with `powercfg /setactive da59d749-aede-466a-b5a1-4a2c7c53723f`.
- A firmer alternative is in the BIOS: disable Thermal Velocity Boost and Turbo Boost Max 3.0, and set the P-core ratio to Sync All Cores at 55 (or 53).
- **Don't undervolt** (a degraded chip needs more voltage, not less), and don't add voltage either (it speeds up the wear).
- Before shipping the CPU: note your BIOS settings, keep the cooler mounting hardware, and clean the thermal paste off the CPU.

---

## 8. Claim log

| Date | Event |
|---|---|
| 2024-03-06 | CPU + motherboard bought from B&H (invoice 222025773) |
| 2026-10-07 | BIOS/microcode updated; IPDT FMA3 failure found; evidence collected; receipt found |
| 2026-10-07 | Intel warranty check needs the batch number (FPO) as well as the serial; FPO still to read off the box |
| 2026-10-07 | Temporary 5000 MHz Windows cap (P-cores at 4.8 GHz); IPDT Math passed 4/4 |
| | Ticket filed: number `__________` |
| | RMA approved: RMA number `__________` |
| | CPU shipped: tracking `__________` |
| | Replacement received |
| | Replacement passes IPDT + OCCT; corruption stopped |

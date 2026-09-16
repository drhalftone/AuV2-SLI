"""Generate the ESP32 element card schematic, BOM and parts table from one parts list.

    python gen_schematic.py

Writes, next to this script:
    LauEsp32Config_Pt_Stack.kicad_sch   KiCad 10 schematic (net-label style, one sheet)
    LauEsp32Config_Pt_Stack.kicad_pro   minimal project file
    production/bom.csv                  JLCPCB assembly BOM (assembled parts only)
    production/bom_full.csv             every part, incl. DNP straps, with MPN + qty

Why generated: the parts list below is the single source of truth for the drawing AND
the BOM, so a part cannot be in one and not the other, and every assembled part must
carry an LCSC code or the script refuses to write (see build_bom). Symbols are copied
from the KiCad 10 stock libraries; every footprint is checked to exist on disk.

Design decisions are argued in SCHEMATIC.md; the ones that shape the netlist:
  * S3-MINI-1U + microSD (SD_BITSTREAM_LIBRARY.md 2, decided 2026-09-16)
  * Pt V2 rev A JTAG order, with DNP rev B strap positions (README.md 3)
  * true pass-through: DF40 plugs on the bottom, 4.0 mm receptacles on top (README.md 6.5)
"""
import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = "LauEsp32Config_Pt_Stack"
KICAD = "C:/Users/drhal/AppData/Local/Programs/KiCad/10.0/share/kicad"
LOCAL_FP = {"LauEsp32": os.path.join(HERE, "LauEsp32.pretty")}
NS = uuid.UUID("5b0e3f5e-6a55-4c7e-9d0e-2f1e5a1c0e32")
ROOT_UUID = str(uuid.uuid5(NS, "root"))

# --------------------------------------------------------------------------- s-expr
TOK = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def parse(text):
    stack = [[]]
    for t in TOK.findall(text):
        if t == "(":
            stack.append([])
        elif t == ")":
            x = stack.pop()
            stack[-1].append(x)
        else:
            stack[-1].append(t)
    return stack[0]


def dump(e, ind=0):
    if not isinstance(e, list):
        return e
    if all(not isinstance(x, list) for x in e):
        return "\t" * ind + "(" + " ".join(e) + ")"
    head = [x for x in e if not isinstance(x, list)]
    out = "\t" * ind + "(" + " ".join(head)
    for x in e:
        if isinstance(x, list):
            out += "\n" + dump(x, ind + 1)
    return out + "\n" + "\t" * ind + ")"


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def U(*key):
    return str(uuid.uuid5(NS, "/".join(map(str, key))))


def num(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


# --------------------------------------------------------------------------- libraries
_libs = {}


def libfile(name):
    if name not in _libs:
        path = f"{KICAD}/symbols/{name}.kicad_sym"
        _libs[name] = parse(open(path, encoding="utf-8").read())[0]
    return _libs[name]


def raw_symbol(lib, name):
    for e in libfile(lib)[1:]:
        if isinstance(e, list) and e[0] == "symbol" and e[1] == q(name):
            return e
    raise KeyError(f"{lib}:{name} not in stock KiCad library")


def flat_symbol(lib_id):
    """Library symbol as embedded in a schematic: derived symbols flattened."""
    lib, name = lib_id.split(":")
    sym = raw_symbol(lib, name)
    ext = [x for x in sym if isinstance(x, list) and x[0] == "extends"]
    if not ext:
        body = [x for x in sym[2:]]
    else:
        parent = flat_symbol(f"{lib}:{ext[0][1].strip(chr(34))}")
        pname = parent[1].split(":")[1].strip('"')
        child_props = {x[1]: x for x in sym if isinstance(x, list) and x[0] == "property"}
        body = []
        for x in parent[2:]:
            if isinstance(x, list) and x[0] == "property" and x[1] in child_props:
                body.append(child_props.pop(x[1]))
            elif isinstance(x, list) and x[0] == "symbol":
                body.append(["symbol", x[1].replace(pname, name, 1)] + x[2:])
            else:
                body.append(x)
        body[0:0] = list(child_props.values())
    return ["symbol", q(lib_id)] + body


def symbol_pins(lib_id):
    """{(unit, pin_number): (x, y, angle)} in symbol coordinates (Y up)."""
    sym = flat_symbol(lib_id)
    name = lib_id.split(":")[1]
    pins = {}
    for sub in sym:
        if not (isinstance(sub, list) and sub[0] == "symbol"):
            continue
        m = re.match(re.escape(name) + r"_(\d+)_(\d+)$", sub[1].strip('"'))
        unit = int(m.group(1))
        for p in sub:
            if isinstance(p, list) and p[0] == "pin":
                at = next(y for y in p if isinstance(y, list) and y[0] == "at")
                nr = next(y for y in p if isinstance(y, list) and y[0] == "number")[1].strip('"')
                pins[(unit, nr)] = (float(at[1]), float(at[2]), float(at[3]))
    return pins


def footprint_exists(fp):
    lib, name = fp.split(":")
    base = LOCAL_FP.get(lib, f"{KICAD}/footprints/{lib}.pretty")
    return os.path.exists(os.path.join(base, name + ".kicad_mod"))


# --------------------------------------------------------------------------- parts
FP_R = "Resistor_SMD:R_0402_1005Metric"
FP_C0402 = "Capacitor_SMD:C_0402_1005Metric"
FP_C0805 = "Capacitor_SMD:C_0805_2012Metric"

# value -> (footprint, LCSC, MPN). All JLCPCB Basic parts, checked by check_jlc_parts.py.
PASSIVE = {
    "10k": (FP_R, "C25744", "0402WGF1002TCE"),
    "33R": (FP_R, "C25105", "0402WGF330JTCE"),
    "1k": (FP_R, "C11702", "0402WGF1001TCE"),
    "100n": (FP_C0402, "C1525", "CL05B104KO5NNNC"),
    "1u": (FP_C0402, "C52923", "CL05A105KA5NQNC"),
    "10u": (FP_C0805, "C15850", "CL21A106KAYNNNE"),
    "22u": (FP_C0805, "C45783", "CL21A226MAQNNNE"),
}

parts = []   # dicts: ref, lib_id, value, fp, lcsc, mpn, at, nets{(unit,pin): net}, dnp, bom
notes = []   # (x, y, text, size)


def part(ref, lib_id, value, fp, lcsc, mpn, at, nets, dnp=False, bom=True, comment=None):
    parts.append(dict(ref=ref, lib_id=lib_id, value=value, fp=fp, lcsc=lcsc, mpn=mpn, at=at,
                      nets=nets, dnp=dnp, bom=bom, comment=comment or value))


def passive(ref, value, at, top, bottom, dnp=False, comment=None):
    fp, lcsc, mpn = PASSIVE[value]
    lib = "Device:R" if ref.startswith("R") else "Device:C"
    part(ref, lib, value, fp, lcsc, mpn, at, {(1, "1"): top, (1, "2"): bottom}, dnp=dnp,
         comment=comment)


def note(x, y, text, size=2.0):
    notes.append((x, y, text, size))


# ---- DF40 pass-through: plug (bottom, mates the board below) + receptacle (top, board above)
def bank_net(bank, n):
    # 80-pin sites: GND on pins = 1,2 (mod 6); every other pin is an FPGA I/O passed through.
    return "GND" if n % 6 in (1, 2) else f"BANK{bank}_{n}"


def control_net(n):
    fixed = {
        29: "FAB_SCK", 31: "FAB_MOSI", 34: "FAB_MISO", 36: "FAB_CS",
        30: "FAB_IO2", 32: "FAB_IO3", 33: "FAB_IRQ", 35: "FAB_SPARE",
        37: "FPGA_RESET", 39: "FPGA_DONE", 41: "FPGA_PROGRAM_B",
        43: "J3_P43", 45: "J3_P45", 47: "J3_P47", 49: "J3_P49",
        38: "VBSEL_A", 40: "VBSEL_B", 42: "A1V8", 44: "AVP", 46: "AVN", 48: "AREF", 50: "AGND",
    }
    if n in fixed:
        return fixed[n]
    if n <= 16:
        return "+3V3" if n % 2 else "RAW"
    return "GND"   # 17-28


DF40 = {
    "80DP": ("LauEsp32:Hirose_DF40C-80DP-0.4V_2x40-1MP_P0.4mm", "C294544", "DF40C-80DP-0.4V(51)"),
    "80DS": ("LauEsp32:Hirose_DF40HC(4.0)-80DS-0.4V_2x40_P0.4mm", "C5254351",
             "DF40HC(4.0)-80DS-0.4V(51)"),
    "50DP": ("LauEsp32:Hirose_DF40C-50DP-0.4V_2x25-1MP_P0.4mm", "C424645", "DF40C-50DP-0.4V(51)"),
    "50DS": ("LauEsp32:Hirose_DF40HC(4.0)-50DS-0.4V_2x25_P0.4mm", "C5338326",
             "DF40HC(4.0)-50DS-0.4V(51)"),
}

for ref_p, ref_s, bank, x in (("J1", "J4", "A", 45.72), ("J2", "J5", "B", 147.32)):
    for ref, kind, y, side in ((ref_p, "80DP", 91.44, "BOTTOM, plug"), (ref_s, "80DS", 223.52, "TOP, receptacle")):
        fp, lcsc, mpn = DF40[kind]
        nets = {(1, str(n)): bank_net(bank, n) for n in range(1, 81)}
        part(ref, "Connector_Generic:Conn_02x40_Odd_Even", f"DF40 Bank {bank} ({side})", fp, lcsc, mpn,
             (x, y), nets, comment=f"{mpn} Bank {bank} {side}")
    note(x - 20, 30, f"Pt V2 Bank {bank} -- pass-through\n{ref_p} plug (bottom) <-> {ref_s} receptacle (top)\npin n <-> pin n, no connection to this card", 2.0)

for ref, kind, y, side in (("J3", "50DP", 76.2, "BOTTOM, plug"), ("J6", "50DS", 172.72, "TOP, receptacle")):
    fp, lcsc, mpn = DF40[kind]
    nets = {(1, str(n)): control_net(n) for n in range(1, 51)}
    part(ref, "Connector_Generic:Conn_02x25_Odd_Even", f"DF40 Control ({side})", fp, lcsc, mpn,
         (254.0, y), nets, comment=f"{mpn} Control {side}")
note(234, 30, "Pt V2 J3 'Control' -- pass-through + taps\nJTAG pins 43/45/47/49 are REV DEPENDENT:\nnets J3_P43..J3_P49, strapped at R10-R17", 2.0)

# ---- U1 ESP32-S3-MINI-1U-N8
esp = {
    "3": "+3V3", "45": "ESP_EN",
    "4": "ESP_BOOT",                  # GPIO0  strap: low at reset = download mode
    "12": "ESP_FAB_IRQ",              # GPIO8
    "13": "ESP_FAB_IO3",              # GPIO9  FSPIHD  (IO_MUX)
    "14": "ESP_FAB_CS",               # GPIO10 FSPICS0
    "15": "ESP_FAB_MOSI",             # GPIO11 FSPID
    "16": "ESP_FAB_SCK",              # GPIO12 FSPICLK
    "17": "ESP_FAB_MISO",             # GPIO13 FSPIQ
    "18": "ESP_FAB_IO2",              # GPIO14 FSPIWP
    "25": "ESP_FAB_SPARE",            # GPIO21
    "8": "ESP_TCK",                   # GPIO4
    "9": "ESP_TDI",                   # GPIO5
    "10": "ESP_TDO",                  # GPIO6
    "11": "ESP_TMS",                  # GPIO7
    "19": "JTAG_OE_N",                # GPIO15
    "20": "ESP_DONE",                 # GPIO16
    "21": "PROG_EN",                  # GPIO17
    "22": "RESET_EN",                 # GPIO18
    "23": "USB_DN", "24": "USB_DP",   # GPIO19 / GPIO20
    "28": "SD_D2",                    # GPIO33
    "29": "SD_D3",                    # GPIO34
    "31": "SD_CMD",                   # GPIO35
    "32": "SD_CLK",                   # GPIO36
    "33": "SD_D0",                    # GPIO37
    "34": "SD_D1",                    # GPIO38
    "27": "SD_CD_N",                  # GPIO47
    "30": "LED_STATUS",               # GPIO48
    "39": "ESP_TXD0", "40": "ESP_RXD0",
}
esp_nc = ["5", "6", "7", "26", "35", "36", "37", "38", "41", "44"]  # GPIO1,2,3,26,39-42,45,46
esp_nets = {(1, p): n for p, n in esp.items()}
esp_nets.update({(1, p): None for p in esp_nc})
esp_nets.update({(1, str(p)): "GND" for p in [1, 2, 42, 43] + list(range(46, 66))})
part("U1", "RF_Module:ESP32-S3-MINI-1U", "ESP32-S3-MINI-1U-N8", "LauEsp32:ESP32-S2-MINI-1U",
     "C2980299", "ESP32-S3-MINI-1U-N8", (386.08, 91.44), esp_nets)
note(345, 20, "U1 ESP32-S3-MINI-1U-N8: 8 MB flash, no PSRAM,\n"
     "external antenna connector. S3-MINI-1 shares the S2-MINI-1\n"
     "land pattern (KiCad's S3 symbol uses the S2 footprint).\n"
     "GPIO3/45/46 are STRAPS, left floating on purpose.\n"
     "GPIO1/2/26/39-42 spare.", 1.6)

# decoupling + bulk (README 6.2: local bulk for WiFi TX transients) + EN RC (datasheet 9)
for i, (ref, val) in enumerate((("C1", "22u"), ("C2", "22u"), ("C3", "10u"), ("C4", "100n"))):
    passive(ref, val, (345.44 + i * 10.16, 162.56), "+3V3", "GND")
passive("R1", "10k", (391.16, 162.56), "+3V3", "ESP_EN")
passive("C5", "1u", (401.32, 162.56), "ESP_EN", "GND")
note(340, 180, "C1-C4 at U1 pin 3.  R1/C5: EN RC delay, 10k/1uF per datasheet 9.", 1.6)

# ---- U2 SN74LVC125A: JTAG isolation. /OE pulled high => buffers OFF until firmware enables.
lvc = "74xx:74LVC125"
U2 = {
    (1, "1"): "JTAG_OE_N", (1, "2"): "ESP_TCK", (1, "3"): "JTAG_TCK",
    (2, "4"): "JTAG_OE_N", (2, "5"): "ESP_TMS", (2, "6"): "JTAG_TMS",
    (3, "10"): "JTAG_OE_N", (3, "9"): "ESP_TDI", (3, "8"): "JTAG_TDI",
    (4, "13"): "JTAG_OE_N", (4, "12"): "JTAG_TDO", (4, "11"): "ESP_TDO",
    (5, "14"): "+3V3", (5, "7"): "GND",
}
for unit, (x, y) in {1: (482.6, 50.8), 2: (482.6, 83.82), 3: (482.6, 116.84), 4: (482.6, 149.86),
                     5: (533.4, 50.8)}.items():
    part("U2", lvc, "SN74LVC125APWR", "Package_SO:TSSOP-14_4.4x5mm_P0.65mm", "C7813",
         "SN74LVC125APWR", (x, y), {k: v for k, v in U2.items() if k[0] == unit},
         bom=(unit == 1))
    parts[-1]["unit"] = unit
passive("C6", "100n", (533.4, 83.82), "+3V3", "GND")
passive("R2", "10k", (546.1, 83.82), "+3V3", "JTAG_OE_N", comment="10k JTAG_OE_N pull-up")
passive("R3", "10k", (558.8, 83.82), "ESP_TDO", "GND", comment="10k ESP_TDO pull-down")
note(500, 20, "U2: JTAG buffer. R2 holds /OE HIGH, so the ESP32 is OFF the JTAG bus through its own\n"
     "boot and whenever firmware has not claimed it -- the Pt's USB/FT2232H path wins by default\n"
     "(README 4.2). R3 parks ESP_TDO while the buffer is off.", 1.6)

# ---- JTAG straps: rev A populated (33R source termination), rev B positions DNP.
# Rev A (Pt V2 schematic sheet 3): 43 TDI, 45 TDO, 47 TMS, 49 TCK
# Rev B = Au V2 order (AuSchematic.pdf sheet 1): 43 TMS, 45 TCK, 47 TDI, 49 TDO
straps = [
    ("R10", "JTAG_TCK", "J3_P49", False), ("R11", "JTAG_TMS", "J3_P47", False),
    ("R12", "JTAG_TDI", "J3_P43", False), ("R13", "J3_P45", "JTAG_TDO", False),
    ("R14", "JTAG_TCK", "J3_P45", True), ("R15", "JTAG_TMS", "J3_P43", True),
    ("R16", "JTAG_TDI", "J3_P47", True), ("R17", "J3_P49", "JTAG_TDO", True),
]
for i, (ref, a, b, dnp) in enumerate(straps):
    rev = "B (DNP)" if dnp else "A"
    passive(ref, "33R", (477.52 + i * 12.7, 208.28), a, b, dnp=dnp, comment=f"33R JTAG strap rev {rev}")
note(465, 228, "JTAG STRAPS -- populate R10-R13 for Pt V2 rev A (as built), OR R14-R17 for rev B.\n"
     "NEVER BOTH: that shorts two JTAG lines together.  Rev B moves JTAG to the Au V2 order.", 1.6)

# ---- PROGRAM_B and RESET: open-drain via N-FET, gate pulled down, so no ESP32 boot glitch
# can reconfigure or reset the FPGA. Both nets have pull-ups on the Pt (README 4.1).
for ref, rg, gate, drain, x in (("Q1", "R4", "PROG_EN", "FPGA_PROGRAM_B", 482.6),
                                 ("Q2", "R5", "RESET_EN", "FPGA_RESET", 520.7)):
    part(ref, "Transistor_FET:2N7002", "2N7002", "Package_TO_SOT_SMD:SOT-23", "C8545", "2N7002",
         (x, 266.7), {(1, "1"): gate, (1, "2"): "GND", (1, "3"): drain})
    passive(rg, "10k", (x - 12.7, 274.32), gate, "GND", comment=f"10k {gate} gate pull-down")
passive("R6", "1k", (546.1, 266.7), "FPGA_DONE", "ESP_DONE", comment="1k DONE series")
note(465, 290, "Q1/Q2 pull PROGRAM_B / Reset low only when the ESP32 drives the gate high.\n"
     "R4/R5 keep them OFF while GPIOs float at boot -- a bitstream is never lost to a reset glitch.\n"
     "R6: DONE is read-only; 1k limits current if the GPIO is ever misconfigured as output.", 1.6)

# ---- Fabric SPI side channel (README 3b): 33R series at the ESP32 end
fab = ["SCK", "MOSI", "MISO", "CS", "IO2", "IO3", "IRQ", "SPARE"]
for i, s in enumerate(fab):
    passive(f"R{20 + i}", "33R", (345.44 + i * 12.7, 223.52), f"ESP_FAB_{s}", f"FAB_{s}",
            comment=f"33R FAB_{s} series")
note(335, 244, "ESP32 <-> fabric SPI on J3 29-36 (README 3b). SCK/MOSI/MISO/CS/IO2/IO3 on the\n"
     "S3's IO_MUX FSPI pins, so quad SPI at 80 MHz is available without the GPIO matrix.\n"
     "Banks 14 and 34 are 3.3 V (bank 13 is the only 2.5 V bank) -- confirm VCCO before first use.", 1.6)

# ---- microSD, SDMMC 4-bit
part("J7", "Connector:Micro_SD_Card_Det2", "microSD Molex 104031-0811",
     "LauEsp32:microSD_HC_Molex_104031-0811", "C585350", "1040310811", (383.54, 330.2),
     {(1, "1"): "SD_D2", (1, "2"): "SD_D3", (1, "3"): "SD_CMD", (1, "4"): "+3V3",
      (1, "5"): "SD_CLK_C", (1, "6"): "GND", (1, "7"): "SD_D0", (1, "8"): "SD_D1",
      (1, "9"): "GND", (1, "10"): "SD_CD_N", (1, "SH"): "GND"})
passive("R30", "33R", (345.44, 381.0), "SD_CLK", "SD_CLK_C", comment="33R SD_CLK series")
for i, n in enumerate(["SD_CMD", "SD_D0", "SD_D1", "SD_D2", "SD_D3", "SD_CD_N"]):
    passive(f"R{31 + i}", "10k", (358.14 + i * 12.7, 381.0), "+3V3", n, comment=f"10k {n} pull-up")
passive("C7", "10u", (439.42, 381.0), "+3V3", "GND")
passive("C8", "100n", (449.58, 381.0), "+3V3", "GND")
note(335, 300, "J7 microSD, 1.42 mm tall -- TOP face only (SD_BITSTREAM_LIBRARY 3).\n"
     "10k pull-ups on CMD/DAT0-3 per the SD spec. Detect switch 9-10 -> SD_CD_N.", 1.6)

# ---- status LED
# Red, not green: every green 0603 at JLCPCB is Extended (feeder fee); KT-0603R is Basic.
part("D1", "Device:LED", "Red", "LED_SMD:LED_0603_1608Metric", "C2286", "KT-0603R",
     (492.76, 330.2), {(1, "2"): "LED_A", (1, "1"): "GND"})
passive("R7", "1k", (477.52, 337.82), "LED_STATUS", "LED_A", comment="1k LED")

# ---- pad field (README 6.0 / SD_BITSTREAM_LIBRARY 2.1): no USB receptacle. Not in BOM.
pads = [("TP1", "+3V3"), ("TP2", "GND"), ("TP3", "USB_DP"), ("TP4", "USB_DN"), ("TP5", "ESP_EN"),
        ("TP6", "ESP_BOOT"), ("TP7", "ESP_TXD0"), ("TP8", "ESP_RXD0")]
for i, (ref, n) in enumerate(pads):
    part(ref, "Connector:TestPoint", n, "TestPoint:TestPoint_Pad_D1.0mm", "", "", (530.86 + i * 12.7, 355.6),
         {(1, "1"): n}, bom=False)
note(520, 320, "Pad field for first flash / recovery -- place at the enclosure's open face.\n"
     "USB_DP/DN = GPIO20/19 (native USB-Serial-JTAG). ESP_BOOT = GPIO0: hold low + pulse EN.\n"
     "TP1-TP8 are bare copper: no part, not in the BOM or CPL.", 1.6)

# power flags (the DF40 +3V3/GND pins are passive, so something must say the rail is driven)
part("#FLG01", "power:PWR_FLAG", "PWR_FLAG", "", "", "", (304.8, 30.48), {(1, "1"): "+3V3"}, bom=False)
part("#FLG02", "power:PWR_FLAG", "PWR_FLAG", "", "", "", (317.5, 30.48), {(1, "1"): "GND"}, bom=False)

note(20, 520, "LauEsp32Config_Pt_Stack -- ESP32 wireless programmer, Pt V2 element card.\n"
     "GENERATED by gen_schematic.py -- edit the script, not this sheet. Design notes: SCHEMATIC.md.\n"
     "Every assembled part carries an LCSC code; check_jlc_parts.py verifies code, MPN and stock.", 2.2)


# --------------------------------------------------------------------------- emit
def prop(name, value, x, y, hide=False, justify=None):
    e = ["property", q(name), q(value), ["at", num(x), num(y), "0"]]
    if hide:
        e.append(["hide", "yes"])
    eff = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if justify:
        eff.append(["justify", justify])
    e.append(eff)
    return e


def label(net, x, y, angle, key):
    just = {0: "left", 90: "left", 180: "right", 270: "right"}[angle]
    return ["label", q(net), ["at", num(x), num(y), str(angle)],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", just, "bottom"]],
            ["uuid", q(U("label", *key))]]


def power_symbol(net, x, y, outward, key, pwr_index):
    lib_id = "power:GND" if net == "GND" else "power:+3V3"
    body = 270 if net == "GND" else 90
    rot = (outward - body) % 360
    ref = f"#PWR{pwr_index:03d}"
    return lib_id, ["symbol", ["lib_id", q(lib_id)], ["at", num(x), num(y), str(int(rot))],
                    ["unit", "1"], ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
                    ["dnp", "no"], ["uuid", q(U("pwr", *key))],
                    prop("Reference", ref, x, y, hide=True), prop("Value", net, x, y, hide=True),
                    prop("Footprint", "", x, y, hide=True), prop("Datasheet", "", x, y, hide=True),
                    ["pin", q("1"), ["uuid", q(U("pwrpin", *key))]],
                    ["instances", ["project", q(PROJECT), ["path", q("/" + ROOT_UUID),
                                                          ["reference", q(ref)], ["unit", "1"]]]]]


def build_schematic():
    used_libs, items, pwr = set(), [], [0]
    for p in parts:
        lib_id, unit = p["lib_id"], p.get("unit", 1)
        used_libs.add(lib_id)
        if p["fp"] and not footprint_exists(p["fp"]):
            raise SystemExit(f"{p['ref']}: footprint {p['fp']} does not exist")
        pins = symbol_pins(lib_id)
        sx, sy = p["at"]
        unit_pins = {k: v for k, v in pins.items() if k[0] in (0, unit)}
        # every pin on this unit must be assigned a net or an explicit None (no-connect)
        missing = [k[1] for k in unit_pins if (unit, k[1]) not in p["nets"] and (0, k[1]) not in p["nets"]
                   and not (k[0] == 0 and unit != 1)]
        if missing and not p["ref"].startswith("#"):
            raise SystemExit(f"{p['ref']} unit {unit}: pins with no net: {missing}")
        is_power_flag = lib_id == "power:PWR_FLAG"
        for (u, nr), (px, py, pa) in unit_pins.items():
            net = p["nets"].get((unit, nr), p["nets"].get((0, nr)))
            x, y = sx + px, sy - py
            outward = int(pa + 180) % 360
            key = (p["ref"], unit, nr)
            if is_power_flag:
                lid, sym = power_symbol(net, x, y, 270 if net == "GND" else 90, key, 900 + len(items))
                used_libs.add(lid)
                items.append(sym)
            elif net is None:
                items.append(["no_connect", ["at", num(x), num(y)], ["uuid", q(U("nc", *key))]])
            elif net in ("GND", "+3V3"):
                pwr[0] += 1
                lid, sym = power_symbol(net, x, y, outward, key, pwr[0])
                used_libs.add(lid)
                items.append(sym)
            else:
                items.append(label(net, x, y, outward, key))
        # designator placement: outside the body so it never sits on pin names or labels
        ys = [sy - py for (_, _), (_, py, _) in unit_pins.items()] or [sy]
        if lib_id.startswith("Device:") or lib_id.startswith("Connector:TestPoint"):
            rx, ry, vy = sx + 2.54, sy - 1.27, sy + 1.27
        else:
            rx, ry, vy = sx - 5.08, min(ys) - 5.08, min(ys) - 2.54
        props = [
            prop("Reference", p["ref"], rx, ry, justify="left", hide=p["ref"].startswith("#")),
            prop("Value", p["value"], rx, vy, justify="left", hide=p["ref"].startswith("#")),
            prop("Footprint", p["fp"], sx, sy, hide=True),
            prop("Datasheet", "", sx, sy, hide=True),
            prop("LCSC", p["lcsc"], sx, sy, hide=True),
            prop("MPN", p["mpn"], sx, sy, hide=True),
        ]
        in_bom = "yes" if (p["bom"] or p.get("unit", 1) != 1) and not p["ref"].startswith("#") and p["lcsc"] != "" else "no"
        if p["ref"].startswith("TP"):
            in_bom = "no"
        items.append(
            ["symbol", ["lib_id", q(lib_id)], ["at", num(sx), num(sy), "0"], ["unit", str(unit)],
             ["exclude_from_sim", "no"], ["in_bom", in_bom], ["on_board", "no" if p["ref"].startswith("#") else "yes"],
             ["dnp", "yes" if p["dnp"] else "no"], ["uuid", q(U("sym", p["ref"], unit))]] + props +
            [["pin", q(nr), ["uuid", q(U("pin", p["ref"], nr))]] for (u, nr) in unit_pins] +
            [["instances", ["project", q(PROJECT), ["path", q("/" + ROOT_UUID),
                                                    ["reference", q(p["ref"])], ["unit", str(unit)]]]]])
    for i, (x, y, text, size) in enumerate(notes):
        items.append(["text", q(text), ["exclude_from_sim", "no"], ["at", num(x), num(y), "0"],
                      ["effects", ["font", ["size", num(size), num(size)]], ["justify", "left", "top"]],
                      ["uuid", q(U("text", i))]])

    sch = ["kicad_sch", ["version", "20260306"], ["generator", q("gen_schematic.py")],
           ["generator_version", q("10.0")], ["uuid", q(ROOT_UUID)], ["paper", q("A1")],
           ["title_block", ["title", q("LauEsp32Config_Pt_Stack - ESP32 wireless programmer")],
            ["company", q("University of Kentucky")],
            ["comment", "1", q("Alchitry Pt V2 element card, pass-through. ESP32-S3-MINI-1U-N8 + microSD.")],
            ["comment", "2", q("JTAG straps: R10-R13 = Pt rev A (populated), R14-R17 = rev B (DNP).")],
            ["comment", "3", q("Generated by gen_schematic.py. All assembled parts: LCSC codes, JLCPCB PCBA.")]],
           ["lib_symbols"] + [flat_symbol(l) for l in sorted(used_libs)]] + items + [
           ["sheet_instances", ["path", q("/"), ["page", q("1")]]], ["embedded_fonts", "no"]]
    return dump(sch) + "\n"


def build_bom(out):
    groups = {}
    for p in parts:
        if not p["bom"] or p["ref"].startswith("#") or p["ref"].startswith("TP"):
            continue
        if not p["lcsc"]:
            raise SystemExit(f"{p['ref']} is assembled but has no LCSC code -- JLCPCB cannot place it")
        # group by orderable part, not by function: one BOM line per LCSC code (and DNP state)
        comment = p["value"] if p["lib_id"].startswith("Device:") else p["mpn"]
        key = (comment, p["fp"], p["lcsc"], p["mpn"], p["dnp"])
        groups.setdefault(key, []).append(p["ref"])

    def refkey(r):
        m = re.match(r"([A-Z]+)(\d+)", r)
        return (m.group(1), int(m.group(2)))

    rows = sorted(groups.items(), key=lambda kv: refkey(sorted(kv[1], key=refkey)[0]))
    os.makedirs(os.path.join(out, "production"), exist_ok=True)
    with open(os.path.join(out, "production", "bom.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("Comment,Designator,Footprint,LCSC Part #\n")
        for (comment, fp, lcsc, mpn, dnp), refs in rows:
            if dnp:
                continue
            f.write(f'"{comment}","{",".join(sorted(refs, key=refkey))}",{fp.split(":")[1]},{lcsc}\n')
    with open(os.path.join(out, "production", "bom_full.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("Comment,Designator,Footprint,Qty,MPN,LCSC Part #,Populate\n")
        for (comment, fp, lcsc, mpn, dnp), refs in rows:
            f.write(f'"{comment}","{",".join(sorted(refs, key=refkey))}",{fp.split(":")[1]},{len(refs)},'
                    f'"{mpn}",{lcsc},{"DNP" if dnp else "yes"}\n')
    return rows


if __name__ == "__main__":
    import sys
    OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else HERE
    with open(os.path.join(OUT, PROJECT + ".kicad_sch"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_schematic())
    pro = os.path.join(OUT, PROJECT + ".kicad_pro")
    if not os.path.exists(pro):
        with open(pro, "w", encoding="utf-8") as f:
            f.write('{\n  "meta": {\n    "filename": "%s.kicad_pro",\n    "version": 3\n  }\n}\n' % PROJECT)
    rows = build_bom(OUT)
    placed = sum(len(r) for k, r in rows if not k[4])
    print(f"schematic written; BOM: {len([1 for k, r in rows if not k[4]])} lines, {placed} placements, "
          f"{sum(len(r) for k, r in rows if k[4])} DNP")

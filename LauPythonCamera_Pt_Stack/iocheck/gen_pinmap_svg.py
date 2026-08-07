#!/usr/bin/env python3
"""Generate BRINGUP_PIN_VOLTAGE_MAP.svg from the real LauPythonCamera_Pt_Stack geometry.

Revision 2 (2026-08-07) — updated against bench measurements:
  * §2 complete: all five rails measured and in spec.
  * §4 resolved: the Pt leaves its I/Os in high-Z (configuration pull-ups OFF),
    so every conditional value collapses -- and 17 pins become unresolvable by voltage.
  * Readings are shown as the bench meter displays them: it has a measured
    +3.3 % gain error (scale factor 1.033), established against TP1/TP3/TP4/TP5.
"""
import re, io

PCB = r"C:\Users\dllau\Developer\AuV2-SLI\LauPythonCamera_Pt_Stack\LauPythonCamera_Pt_Stack.kicad_pcb"
OUT = r"C:\Users\dllau\Developer\AuV2-SLI\LauPythonCamera_Pt_Stack\BRINGUP_PIN_VOLTAGE_MAP.svg"

src = open(PCB, encoding="utf-8").read()

def span(t, st):
    d = 0; j = st
    while True:
        c = t[j]
        if c == '(': d += 1
        elif c == ')':
            d -= 1
            if d == 0: return t[st:j + 1]
        j += 1

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ---------------------------------------------------------------- extract
u1, fps = {}, {}
for m in re.finditer(r'\n\t\(footprint ', src):
    b = span(src, m.start() + 1)
    ref = re.search(r'\(property "Reference"\s+"([^"]+)"', b).group(1)
    at = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', b)
    fps[ref] = dict(x=float(at.group(1)), y=float(at.group(2)),
                    layer=re.search(r'\(layer "([^"]+)"', b).group(1))
    if ref != "U1": continue
    fx, fy, mech = fps[ref]['x'], fps[ref]['y'], 0
    for pm in re.finditer(r'\(pad "([^"]*)"', b):
        pad = span(b, pm.start())
        pat = re.search(r'\(at ([-\d.]+) ([-\d.]+)', pad)
        net = re.search(r'\(net (?:\d+ )?"([^"]*)"', pad)
        key = pm.group(1).strip()
        if not key:
            key = "MECH%d" % mech; mech += 1
        u1[key] = dict(x=fx + float(pat.group(1)), y=fy + float(pat.group(2)),
                       net=net.group(1) if net else "")

OHM = "\u03a9"
CAT = {
 "rail33cam": dict(c="#c62828", lbl="+3V3_CAM  rail",           v="3.40 V",  st="live",
                   band="true 3.293 V \u00b7 must equal TP4"),
 "rail33pix": dict(c="#ad1457", lbl="+3V3_PIX  rail",           v="3.41 V",  st="live",
                   band="true 3.301 V \u00b7 must equal TP5"),
 "rail18":    dict(c="#ef6c00", lbl="+1V8_CAM  rail",           v="1.86 V",  st="live",
                   band="true 1.804 V \u00b7 must equal TP3"),
 "pullup":    dict(c="#6a1b9a", lbl="CAM_SS_N  10k pull-UP",    v="3.40 V",  st="live",
                   band="the ONLY control pin that is high"),
 "gnd":       dict(c="#37474f", lbl="GND",                      v="0.000 V", st="zero",
                   band="11 pins \u00b7 also < 1 " + OHM + " to TP6, power off"),
 "ibias":     dict(c="#6d4c41", lbl="IBIAS_MASTER",             v="0.000 V", st="zero",
                   band="also 47 k" + OHM + " to TP6 through R1"),
 "ctrl":      dict(c="#2e7d32", lbl="control, 10k pull-DOWN",   v="0.000 V", st="weak",
                   band="reads zero even if the FPGA link is open"),
 "lvds":      dict(c="#1565c0", lbl="LVDS \u2194 FPGA bank 13", v="floats",  st="float",
                   band="drifts \u00b7 a meter returns no verdict"),
 "sout":      dict(c="#00838f", lbl="sensor output \u2192 FPGA", v="floats", st="float",
                   band="drifts \u00b7 a meter returns no verdict"),
}

def cat(net):
    if net == "+3V3_CAM": return "rail33cam"
    if net == "+3V3_PIX": return "rail33pix"
    if net == "+1V8_CAM": return "rail18"
    if net == "GND": return "gnd"
    if net == "IBIAS_MASTER": return "ibias"
    if net == "CAM_SS_N": return "pullup"
    if net in ("CAM_MISO", "CAM_MON0", "CAM_MON1"): return "sout"
    if net.startswith(("CAM_D", "CAM_CLKOUT", "CAM_SYNC", "CAM_LVDSCLK")): return "lvds"
    return "ctrl"

# ------------------------------------------------------------------ canvas
W, H = 2200, 1620
o = io.StringIO()
def w(s): o.write(s + "\n")
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"
SANS = "'Segoe UI', Inter, system-ui, -apple-system, sans-serif"
INK, MUTE, RULE = "#16202a", "#5c6b7a", "#c8d2dc"

w(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
  f'font-family="{SANS}">')
w(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
w(f'<text x="46" y="54" font-size="31" font-weight="700" fill="{INK}">'
  'PYTHON 1300 socket \u2014 expected voltmeter readings</text>')
w(f'<text x="46" y="82" font-size="15" fill="{MUTE}">'
  'LauPythonCamera_Pt_Stack \u00b7 pin-to-net map read directly from the .kicad_pcb '
  '\u00b7 rev 2, updated against the bench: \u00a72 measured and passed, \u00a74 resolved</text>')
w('<rect x="46" y="98" width="2108" height="56" rx="6" fill="#fff4e5" stroke="#f0b37e"/>')
w('<text x="62" y="120" font-size="14.5" fill="#8a5316" font-weight="600">'
  'CONDITIONS \u2014 socket EMPTY \u00b7 board mated to the Pt V2 \u00b7 Pt flash ERASED and '
  'power-cycled \u00b7 FPGA unconfigured, and its configuration pull-ups MEASURED OFF (\u00a74) '
  '\u00b7 black lead on TP6</text>')
w('<text x="62" y="142" font-size="14.5" fill="#8a5316" font-weight="600">'
  'VOLTAGES BELOW ARE WHAT YOUR METER DISPLAYS \u2014 it has a measured +3.3 % gain error '
  '(\u00d71.033). True design values are in the legend. A gain error does not affect 0.000 V.</text>')

# =================================================================== PANEL A
S, BX, BY = 12.0, 46, 210
def bx(mm): return BX + (mm - 100.0) * S
def by(mm): return BY + (mm - 60.0) * S
w(f'<text x="46" y="190" font-size="18" font-weight="700" fill="{INK}">'
  'A \u00b7 Board \u2014 all five rails measured, all in spec</text>')
outline = [(101.5,60),(153.5,60),(155,61.5),(155,66.5),(153.5,68),(151,68),(149.5,69.5),
           (149.5,95.5),(151,97),(153.5,97),(155,98.5),(155,103.5),(153.5,105),(101.5,105),
           (100,103.5),(100,61.5)]
w('<polygon points="' + " ".join("%.1f,%.1f" % (bx(x), by(y)) for x, y in outline) +
  '" fill="#eef3f7" stroke="#7d8fa0" stroke-width="1.8"/>')
for ref, half in (("J1", 8.28), ("J2", 8.28), ("J3", 5.28)):
    f = fps[ref]
    w(f'<rect x="{bx(f["x"]-half):.1f}" y="{by(f["y"]-1.9):.1f}" width="{2*half*S:.1f}" '
      f'height="{3.8*S:.1f}" rx="3" fill="none" stroke="#9aa9b8" stroke-width="1.3" '
      f'stroke-dasharray="6 4"/>')
    w(f'<text x="{bx(f["x"]):.1f}" y="{by(f["y"])+4:.1f}" font-size="12" fill="#7b8b9a" '
      f'text-anchor="middle" font-family="{FONT}">{ref} \u00b7 bottom</text>')
w(f'<rect x="{bx(126.094):.1f}" y="{by(72.094):.1f}" width="{19.812*S:.1f}" '
  f'height="{19.812*S:.1f}" rx="5" fill="#dde7ef" stroke="#4a5c6c" stroke-width="1.8"/>')
w(f'<rect x="{bx(131.6):.1f}" y="{by(77.6):.1f}" width="{8.8*S:.1f}" height="{8.8*S:.1f}" rx="3" '
  f'fill="#c3d3e0" stroke="#4a5c6c" stroke-width="1"/>')
w(f'<text x="{bx(136):.1f}" y="{by(81.6):.1f}" font-size="14" font-weight="700" fill="{INK}" '
  f'text-anchor="middle">U1</text>')
w(f'<text x="{bx(136):.1f}" y="{by(83.6):.1f}" font-size="11" fill="#4a5c6c" '
  f'text-anchor="middle">PYTHON 1300</text>')
w(f'<circle cx="{bx(126.094):.1f}" cy="{by(82.508):.1f}" r="4" fill="#c62828"/>')
w(f'<text x="{bx(126.094)+8:.1f}" y="{by(82.508)+4:.1f}" font-size="11.5" font-weight="700" '
  f'fill="#c62828">pin 1</text>')
for ref, lbl, col in (("U2","U2","#8b98a5"),("U3","U3","#8b98a5"),("U4","U4","#8b98a5"),
                      ("U5","U5","#8b98a5"),("L1","L1","#8b98a5"),
                      ("U6","U6","#0b6e4f"),("U7","U7","#0b6e4f")):
    f = fps[ref]; hw = 2.0 if ref == "L1" else 1.5
    w(f'<rect x="{bx(f["x"]-hw):.1f}" y="{by(f["y"]-hw):.1f}" width="{2*hw*S:.1f}" '
      f'height="{2*hw*S:.1f}" rx="2" fill="#ffffff" stroke="{col}" stroke-width="1.6"/>')
    w(f'<text x="{bx(f["x"]):.1f}" y="{by(f["y"])+4:.1f}" font-size="10.5" fill="{col}" '
      f'text-anchor="middle" font-family="{FONT}">{lbl}</text>')
w(f'<text x="{bx(107.5):.1f}" y="{by(96.5)+4:.1f}" font-size="11.5" font-weight="700" '
  f'fill="#0b6e4f">U6, U7 \u2014 your SOT-23s \u2713 both good</text>')

TPS = [("TP1","3.390 \u2713","#0b6e4f",0,26,"middle"), ("TP2","4.690 \u2713","#0b6e4f",-13,4,"end"),
       ("TP3","1.863 \u2713","#ef6c00",-13,-4,"end"), ("TP4","3.401 \u2713","#c62828",-13,20,"end"),
       ("TP5","3.410 \u2713","#ad1457",-13,-8,"end"), ("TP6","0.000","#37474f",-13,4,"end")]
for ref, v, col, dx, dy, anc in TPS:
    f = fps[ref]; cx, cy = bx(f["x"]), by(f["y"])
    w(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8" fill="#ffffff" stroke="{col}" stroke-width="2.8"/>')
    w(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.6" fill="{col}"/>')
    w(f'<text x="{cx+dx:.1f}" y="{cy+dy:.1f}" font-size="12.5" font-weight="700" fill="{col}" '
      f'text-anchor="{anc}" font-family="{FONT}">{ref} {v}</text>')
for ref, txt, col, dx, dy, anc in (("R10","VBSEL_A","#b00020",0,-16,"middle"),
                                   ("R11","VBSEL_B","#b00020",0,-34,"middle"),
                                   ("R5","\u00a74 \u2713 0.001 V \u2014 pull-ups OFF","#2e7d32",
                                    14,4,"start")):
    f = fps[ref]; cx, cy = bx(f["x"]), by(f["y"])
    w(f'<rect x="{cx-8:.1f}" y="{cy-5:.1f}" width="16" height="10" rx="1.5" fill="#ffffff" '
      f'stroke="{col}" stroke-width="2.2"/>')
    w(f'<text x="{cx+dx:.1f}" y="{cy+dy:.1f}" font-size="11.5" font-weight="700" fill="{col}" '
      f'text-anchor="{anc}" font-family="{FONT}">{txt}</text>')
w(f'<text x="{BX}" y="{by(105)+26:.1f}" font-size="12" fill="{MUTE}">'
  '55 \u00d7 45 mm \u00b7 dashed outlines = the bottom-side DF40 connectors facing the Pt '
  '\u00b7 all six test points are on the TOP layer</text>')

# =================================================================== PANEL B
CX, CY, K = 1440, 770, 26.0
def sx(mm): return CX + (mm - 136.0) * K
def sy(mm): return CY + (mm - 82.0) * K
w(f'<text x="820" y="190" font-size="18" font-weight="700" fill="{INK}">'
  'B \u00b7 The 48-pin socket \u2014 looking DOWN into it, empty</text>')
w(f'<rect x="{sx(125.3):.1f}" y="{sy(71.3):.1f}" width="{21.4*K:.1f}" height="{21.4*K:.1f}" '
  f'rx="12" fill="#f4f7fa" stroke="#9fb0c0" stroke-width="1.8"/>')
bd = 8.38
w(f'<rect x="{sx(136-bd):.1f}" y="{sy(82-bd):.1f}" width="{2*bd*K:.1f}" height="{2*bd*K:.1f}" '
  f'rx="6" fill="#e3ebf2" stroke="#4a5c6c" stroke-width="2.2"/>')
w(f'<rect x="{sx(136-4.6):.1f}" y="{sy(82-3.9):.1f}" width="{9.2*K:.1f}" height="{7.8*K:.1f}" '
  f'rx="4" fill="#d6e4ef" stroke="#5f7a92" stroke-width="1.4"/>')
w(f'<rect x="{sx(136-3.07):.1f}" y="{sy(82-2.46):.1f}" width="{6.14*K:.1f}" '
  f'height="{4.92*K:.1f}" fill="#b9cfe0" stroke="#5f7a92" stroke-width="1"/>')
w(f'<text x="{CX}" y="{CY-34:.1f}" font-size="18" font-weight="700" fill="{INK}" '
  f'text-anchor="middle">PYTHON 1300</text>')
w(f'<text x="{CX}" y="{CY-12:.1f}" font-size="12.5" fill="#4a5c6c" text-anchor="middle" '
  f'font-family="{FONT}">NOIP1SN1300A-QTI</text>')
w(f'<text x="{CX}" y="{CY+12:.1f}" font-size="12" fill="#5f7a92" text-anchor="middle">'
  '1280 \u00d7 1024 global shutter \u00b7 4.8 \u00b5m pixel</text>')
w(f'<text x="{CX}" y="{CY+30:.1f}" font-size="12" fill="#5f7a92" text-anchor="middle">'
  '6.14 \u00d7 4.92 mm active area</text>')
w(f'<text x="{CX}" y="{CY+120:.1f}" font-size="11.5" fill="#7d8b98" text-anchor="middle" '
  f'font-style="italic">pin 1 is mid-edge, not at a corner \u2014</text>')
w(f'<text x="{CX}" y="{CY+137:.1f}" font-size="11.5" fill="#7d8b98" text-anchor="middle" '
  f'font-style="italic">orient from the SW / NE mech pads</text>')
for k in ("MECH0", "MECH1"):
    p = u1[k]
    w(f'<rect x="{sx(p["x"])-12:.1f}" y="{sy(p["y"])-12:.1f}" width="24" height="24" rx="3" '
      f'fill="#8b98a5" stroke="#4a5c6c" stroke-width="1.2"/>')
w(f'<text x="{sx(u1["MECH0"]["x"])-20:.1f}" y="{sy(u1["MECH0"]["y"])+20:.1f}" font-size="11" '
  f'fill="#5c6b7a" text-anchor="end" font-family="{FONT}">mech pad SW</text>')
w(f'<text x="{sx(u1["MECH1"]["x"])+20:.1f}" y="{sy(u1["MECH1"]["y"])-10:.1f}" font-size="11" '
  f'fill="#5c6b7a" font-family="{FONT}">mech pad NE</text>')

def side_of(p):
    if abs(p["x"] - 126.094) < 0.2: return "W"
    if abs(p["x"] - 145.906) < 0.2: return "E"
    if abs(p["y"] - 91.906) < 0.2: return "S"
    return "N"

def glyph(gx, gy, st, col):
    if st == "live":
        return (f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="6.6" fill="{col}"/>')
    if st == "zero":
        return (f'<rect x="{gx-5.8:.1f}" y="{gy-5.8:.1f}" width="11.6" height="11.6" rx="1.6" '
                f'fill="#ffffff" stroke="{col}" stroke-width="2.5"/>')
    if st == "weak":
        return (f'<rect x="{gx-5.8:.1f}" y="{gy-5.8:.1f}" width="11.6" height="11.6" rx="1.6" '
                f'fill="#ffffff" stroke="{col}" stroke-width="2.5" stroke-dasharray="3 2.4"/>')
    return (f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="6.6" fill="#ffffff" stroke="{col}" '
            f'stroke-width="2.5" stroke-dasharray="3.2 2.7"/>')

PIN_L, GAP = 34, 18
W_NUM, W_NET, W_VOLT = 830, 842, 1103
E_NUM, E_NET, E_VOLT = 2144, 2110, 1777

for n in range(1, 49):
    p = u1[str(n)]; C = CAT[cat(p["net"])]; col = C["c"]
    side = side_of(p); px, py = sx(p["x"]), sy(p["y"])
    if side in ("W", "E"):
        sgn = -1 if side == "W" else 1
        x0 = px if side == "E" else px - PIN_L
        w(f'<rect x="{x0:.1f}" y="{py-8:.1f}" width="{PIN_L}" height="16" rx="2.5" fill="{col}"/>')
        gx, gy = px + sgn * (PIN_L + GAP), py
    else:
        sgn = -1 if side == "N" else 1
        y0 = py if side == "S" else py - PIN_L
        w(f'<rect x="{px-8:.1f}" y="{y0:.1f}" width="16" height="{PIN_L}" rx="2.5" fill="{col}"/>')
        gx, gy = px, py + sgn * (PIN_L + GAP)
    w(glyph(gx, gy, C["st"], col))
    net, volt = esc(p["net"]), esc(C["v"])
    if side == "W":
        w(f'<text x="{W_NUM}" y="{py+4.5:.1f}" font-size="13.5" font-family="{FONT}" fill="{MUTE}" '
          f'text-anchor="end">{n}</text>')
        w(f'<text x="{W_NET}" y="{py+4.5:.1f}" font-size="13.5" font-family="{FONT}" '
          f'fill="{INK}">{net}</text>')
        w(f'<text x="{W_VOLT}" y="{py+4.5:.1f}" font-size="13.5" font-family="{FONT}" fill="{col}" '
          f'font-weight="700" text-anchor="end">{volt}</text>')
    elif side == "E":
        w(f'<text x="{E_NUM}" y="{py+4.5:.1f}" font-size="13.5" font-family="{FONT}" fill="{MUTE}" '
          f'text-anchor="end">{n}</text>')
        w(f'<text x="{E_NET}" y="{py+4.5:.1f}" font-size="13.5" font-family="{FONT}" fill="{INK}" '
          f'text-anchor="end">{net}</text>')
        w(f'<text x="{E_VOLT}" y="{py+4.5:.1f}" font-size="13.5" font-family="{FONT}" fill="{col}" '
          f'font-weight="700">{volt}</text>')
    else:
        ty = (sy(72.094) - PIN_L - 32) if side == "N" else (sy(91.906) + PIN_L + 32)
        rot = -90 if side == "N" else 90
        w(f'<g transform="translate({px:.1f},{ty:.1f}) rotate({rot})">'
          f'<text x="0" y="4.5" font-size="13" font-family="{FONT}" fill="{INK}">'
          f'<tspan fill="{MUTE}">{n}</tspan>  {net}  '
          f'<tspan fill="{col}" font-weight="700">{volt}</tspan></text></g>')

p1 = u1["1"]
w(f'<circle cx="{sx(p1["x"])-PIN_L-GAP:.1f}" cy="{sy(p1["y"]):.1f}" r="12.5" fill="none" '
  f'stroke="#c62828" stroke-width="2.4"/>')
w(f'<text x="{W_NUM-26:.1f}" y="{sy(p1["y"])+4.5:.1f}" font-size="13" font-weight="700" '
  f'fill="#c62828" text-anchor="end" font-family="{FONT}">PIN 1 \u25b6</text>')
SIDEC = "#8493a2"
w(f'<text x="{sx(130.412)-56:.1f}" y="{sy(72.094)-PIN_L-30:.1f}" font-size="13" '
  f'font-weight="700" fill="{SIDEC}" text-anchor="end" letter-spacing="2">N \u00b7 31\u201342</text>')
w(f'<text x="{sx(130.412)-56:.1f}" y="{sy(91.906)+PIN_L+36:.1f}" font-size="13" '
  f'font-weight="700" fill="{SIDEC}" text-anchor="end" letter-spacing="2">'
  'S \u00b7 7\u201318 \u00b7 all LVDS</text>')
w(f'<text x="{sx(125.3):.1f}" y="{sy(71.3)-16:.1f}" font-size="13" font-weight="700" '
  f'fill="{SIDEC}" letter-spacing="2">W \u00b7 43\u201348, 1\u20136</text>')
w(f'<text x="{sx(146.7):.1f}" y="{sy(71.3)-16:.1f}" font-size="13" font-weight="700" '
  f'fill="{SIDEC}" text-anchor="end" letter-spacing="2">E \u00b7 19\u201330</text>')

# =================================================================== LEGEND
LX, LY = 46, 800
w(f'<text x="{LX}" y="{LY}" font-size="18" font-weight="700" fill="{INK}">'
  'C \u00b7 What can a voltmeter actually settle?</text>')
w(f'<rect x="{LX}" y="{LY+16}" width="730" height="196" rx="8" fill="#f7fafc" stroke="{RULE}"/>')
rows = [("live", "12", "READS A REAL VOLTAGE", "Verified. Must match its test point exactly."),
        ("zero", "12", "HARD 0.000 V", "Verified. Eleven grounds, plus IBIAS through R1."),
        ("weak", "7",  "READS 0.000 V \u2014 BUT PROVES LITTLE",
         "The pull-down gives 0.000 V whether or not the FPGA link is intact."),
        ("float", "17", "FLOATS \u2014 NO VERDICT",
         "Needs the \u00a76 resistance walk, or a walking-ones bitstream.")]
yy = LY + 48
for st, cnt, title, sub in rows:
    w(glyph(LX + 32, yy - 4, st, "#37474f"))
    w(f'<text x="{LX+58}" y="{yy}" font-size="13.5" font-weight="700" fill="{INK}">'
      f'<tspan fill="{MUTE}">{cnt} pins</tspan>  {esc(title)}</text>')
    w(f'<text x="{LX+58}" y="{yy+19}" font-size="12.5" fill="{MUTE}">{esc(sub)}</text>')
    yy += 47
w(f'<text x="{LX}" y="{LY+252}" font-size="15" font-weight="700" fill="{INK}">'
  'Net colours \u2014 what your meter should show</text>')
yy = LY + 296
for k in ["rail33cam", "rail33pix", "rail18", "pullup", "gnd", "ibias", "ctrl", "lvds", "sout"]:
    C = CAT[k]
    w(f'<rect x="{LX}" y="{yy-12}" width="28" height="15" rx="2.5" fill="{C["c"]}"/>')
    w(f'<text x="{LX+40}" y="{yy}" font-size="13" font-family="{FONT}" fill="{INK}">'
      f'{esc(C["lbl"])}</text>')
    w(f'<text x="{LX+322}" y="{yy}" font-size="13" font-family="{FONT}" fill="{C["c"]}" '
      f'font-weight="700">{esc(C["v"])}</text>')
    w(f'<text x="{LX+430}" y="{yy}" font-size="12" font-family="{FONT}" fill="{MUTE}">'
      f'{esc(C["band"])}</text>')
    yy += 28

# =================================================================== NOTES
NX, NY = 820, 1330
w(f'<rect x="{NX}" y="{NY}" width="1334" height="222" rx="8" fill="#f7fafc" stroke="{RULE}"/>')
w(f'<text x="{NX+22}" y="{NY+32}" font-size="15" font-weight="700" fill="{INK}">'
  'D \u00b7 Reading the drawing</text>')
notes = [
 ("\u00a74 is answered \u2014 the Pt leaves its I/Os in high-Z.",
  "R5 pad 1 measured 0.001 V against a hard 0.000 V on its ground pad, so the FPGA's configuration "
  "pull-ups are off. That fixes every value on this drawing, but it also means 17 pins no longer "
  "have anything driving them and cannot be resolved by voltage at all."),
 ("Eight pins read correctly even when they are broken.",
  "The seven dashed-square control pins and pin 47 all sit on on-board pull resistors. Their reading "
  "proves the resistor and the copper between it and the socket \u2014 not the link onward through J1 "
  "to the FPGA. Only a walking-ones bitstream proves that, because only it catches pin swaps."),
 ("Voltage alone cannot prove a socket joint.",
  "A contact merely resting near its pad still reads correctly through a 10 M" + OHM + " meter. "
  "Follow with the power-off resistance walk in \u00a76 of BRINGUP_DMM_CHECKLIST.md \u2014 that is now "
  "where most of the remaining proof lives. Pin 23 to pin 24 = 100 " + OHM + " through R2 is the one "
  "differential check a meter can make."),
 ("The gain error does not invalidate the comparisons.",
  "Rail pins matching their test point, the eleven grounds at zero, and the control pins agreeing "
  "with each other are all ratios or zeros \u2014 immune to a scale error. Swapping the meter battery "
  "is still worth doing before any absolute measurement matters."),
]
def wrap(tx, n):
    words, line, lines = tx.split(), "", []
    for wd in words:
        if len(line) + len(wd) + 1 > n:
            lines.append(line); line = wd
        else:
            line = (line + " " + wd).strip()
    lines.append(line)
    return lines
yy = NY + 60
for hd, tx in notes:
    lines = wrap(tx, 148)
    w(f'<text x="{NX+22}" y="{yy}" font-size="12.5" fill="{MUTE}">'
      f'<tspan font-weight="700" fill="{INK}">{esc(hd)}</tspan>  {esc(lines[0])}</text>')
    yy += 16
    for ln in lines[1:]:
        w(f'<text x="{NX+22}" y="{yy}" font-size="12.5" fill="{MUTE}">{esc(ln)}</text>')
        yy += 16
    yy += 7
w('</svg>')

open(OUT, "w", encoding="utf-8").write(o.getvalue())
print("wrote", OUT, len(o.getvalue()), "bytes")
import xml.dom.minidom as md
md.parse(OUT); print("XML OK")
from collections import Counter
print("states:", dict(Counter(CAT[cat(u1[str(n)]["net"])]["st"] for n in range(1, 49))))

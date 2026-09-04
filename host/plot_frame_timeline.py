"""plot_frame_timeline.py -- one time axis, vsync at t = 0, showing BOTH halves.

Writes a self-contained interactive HTML page and opens it.

    top rows     when each colour LED delivers light into the projection lens
    bottom rows  when the sensor is integrating, and when it is BLIND

The whole point of PROJECTOR_PROFILING_PLAN.md is that these two live on the same
axis. G3 asks for the sensor's blind window to be parked in a dark gap between
colour flashes; that is a statement about this picture, and it is much easier to
argue about as a picture than as a paragraph.

WHAT IS REAL HERE AND WHAT IS NOT. The sensor half is built from values measured
on this hardware and recorded in the repo:

  frame overhead / sensor gap  44.1 us    FTPLUS_API.md, "Max usable exposure"
  max usable exposure          T - 44.1 - 10 us   ditto (regs 0x53/0x54)
  exposure quantum             375 ns     opcode 1, read back 0x40/0x41
  genlock delay quantum        10 ns      opcode 7, read back 0x5A-0x5C
  exposure-start jitter        0.01 us below ~2775 us exposure, ~5.3 us above
  out_vsync period, offline    9718.508 us, span 0.010 us over 35 s

The LED half is ILLUSTRATIVE. Nobody has measured it yet -- that is milestones
P3-P5. It is drawn hatched and labelled so it cannot be mistaken for data. Feed
a real one in with --profile once P5 produces it.

ONE NUMBER IS DELIBERATELY ZERO. Trigger -> integration start is readable at regs
0x42-0x47 but no measured value is recorded anywhere in this repo, so the default
is 0.0 and the page says so. Do not let it quietly become "about right" -- read it
off the board and pass --trig-latency.

usage:
    plot_frame_timeline.py                        # 120 Hz, max exposure
    plot_frame_timeline.py --fps 102.8965         # the measured offline rate
    plot_frame_timeline.py --delay 1200 --exposure 4000
    plot_frame_timeline.py --profile measured.json    # once P5 exists
    plot_frame_timeline.py --no-open              # just write the file
"""
import argparse
import json
import os
import subprocess
import sys
import webbrowser

# ---- measured on this hardware; see the docstring for provenance ------------
SENSOR_GAP_US = 44.1        # frame overhead time + reset, measured
MAXEXP_MARGIN_US = 10.0     # the reserve subtracted by the fabric
EXPO_QUANTUM_US = 0.375     # opcode 1 unit
DELAY_QUANTUM_US = 0.010    # opcode 7 unit
JITTER_SHORT_US = 0.01      # exposure-start jitter below ~2775 us exposure
JITTER_LONG_US = 5.3        # ...and above it
JITTER_KNEE_US = 2775.0

# ---- ILLUSTRATIVE DLP sequence: 4 interleaved RGB cycles per frame ----------
# Fractions of one frame period. Replace with a measured profile via --profile.
# The gap widths are exactly the unknown that P3-P5 exists to measure, and they
# are the thing that decides whether G3 is easy or impossible.
ILLUSTRATIVE_CYCLES = 4
ILLUSTRATIVE_SLOTS = [        # (colour, start, end) within ONE cycle, normalised
    ("R", 0.010, 0.230),
    ("G", 0.260, 0.480),
    ("B", 0.510, 0.730),
    ("R", 0.760, 0.870),      # a second, shorter red -- DLPs commonly do this
]

COLORS = {"R": "#e5484d", "G": "#30a46c", "B": "#0090ff"}


def illustrative_profile(period_us):
    """Build the placeholder LED profile, in microseconds since vsync."""
    out = []
    cyc = period_us / ILLUSTRATIVE_CYCLES
    for i in range(ILLUSTRATIVE_CYCLES):
        base = i * cyc
        for name, a, b in ILLUSTRATIVE_SLOTS:
            out.append({"c": name, "t0": base + a * cyc, "t1": base + b * cyc})
    return out


def load_profile(path, period_us):
    """Load a measured profile: {"period_us": float, "slots":[{c,t0,t1},...]}."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    slots = d["slots"]
    for s in slots:
        if s["c"] not in COLORS:
            sys.exit("profile: unknown colour %r (want R, G or B)" % s["c"])
        if not (s["t1"] > s["t0"]):
            sys.exit("profile: slot %r is not forward in time" % s)
    src_period = d.get("period_us")
    if src_period and abs(src_period - period_us) > 1.0:
        sys.exit("profile was measured at a %.3f us frame period but --fps implies "
                 "%.3f us. Refusing to stretch a measurement onto the wrong axis; "
                 "pass the matching --fps." % (src_period, period_us))
    return slots


def gaps_between(slots, period_us):
    """Dark intervals -- where nothing at all is emitted. The parking spots."""
    if not slots:
        return [(0.0, period_us)]
    spans = sorted((s["t0"], s["t1"]) for s in slots)
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    if merged[0][0] > 0:
        out.append((0.0, merged[0][0]))
    for i in range(len(merged) - 1):
        out.append((merged[i][1], merged[i + 1][0]))
    if merged[-1][1] < period_us:
        out.append((merged[-1][1], period_us))
    return out


def build(args):
    period = 1e6 / args.fps
    max_expo = period - SENSOR_GAP_US - MAXEXP_MARGIN_US
    if max_expo <= 0:
        sys.exit("frame period %.1f us is shorter than the sensor's own %.1f us gap"
                 % (period, SENSOR_GAP_US + MAXEXP_MARGIN_US))

    expo = max_expo if args.exposure is None else args.exposure
    if expo > max_expo:
        sys.exit("exposure %.1f us exceeds the max usable %.1f us at %.4f Hz -- the "
                 "fabric would refuse this (regs 0x53/0x54) and an over-long exposure "
                 "wedges the sensor until reconfigure." % (expo, max_expo, args.fps))

    slots = (load_profile(args.profile, period) if args.profile
             else illustrative_profile(period))
    model = {
        "period": period,
        "maxExpo": max_expo,
        "expo": expo,
        "delay": args.delay,
        "trigLat": args.trig_latency,
        "sensorGap": SENSOR_GAP_US,
        "margin": MAXEXP_MARGIN_US,
        "expoQ": EXPO_QUANTUM_US,
        "delayQ": DELAY_QUANTUM_US,
        "jitterShort": JITTER_SHORT_US,
        "jitterLong": JITTER_LONG_US,
        "jitterKnee": JITTER_KNEE_US,
        "fps": args.fps,
        "slots": slots,
        "gaps": gaps_between(slots, period),
        "measured": bool(args.profile),
        "source": os.path.basename(args.profile) if args.profile else None,
        "colors": COLORS,
        "trigLatKnown": args.trig_latency != 0.0,
    }

    html = PAGE.replace("__MODEL__", json.dumps(model))
    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    print("wrote %s" % out)
    print("frame period   %.3f us  (%.4f Hz)" % (period, args.fps))
    print("max exposure   %.3f us  = T - %.1f - %.1f" % (max_expo, SENSOR_GAP_US,
                                                         MAXEXP_MARGIN_US))
    print("blind window   %.3f us  at the exposure shown" % (period - expo))
    print("LED profile    %s" % ("measured, from " + model["source"] if model["measured"]
                                 else "ILLUSTRATIVE -- not measured (P3-P5)"))
    if not model["trigLatKnown"]:
        print("trig->int      0.0 us assumed -- READ IT from regs 0x42-0x47")
    if args.open:
        open_in_chrome(out)
    return 0


def open_in_chrome(path):
    url = "file:///" + path.replace("\\", "/")
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in candidates:
        if os.path.exists(exe):
            subprocess.Popen([exe, url])
            print("opened in Chrome")
            return
    webbrowser.open(url)
    print("Chrome not found in the usual places -- opened the default browser")


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Frame timeline</title>
<style>
 :root{--bg:#0f1115;--fg:#e6e8eb;--dim:#9ba1a6;--line:#2a2f37;--card:#161a21}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
 .wrap{max-width:1400px;margin:0 auto;padding:24px}
 h1{font-size:20px;margin:0 0 4px}
 .sub{color:var(--dim);margin:0 0 18px}
 .warn{background:#3a2a12;border:1px solid #7a5a22;color:#ffd9a0;
       padding:10px 14px;border-radius:8px;margin:0 0 18px;font-size:13px}
 .ok{background:#12301f;border-color:#2a6a45;color:#a8e6c4}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
       padding:16px;margin-bottom:16px}
 .ctl{display:grid;grid-template-columns:180px 1fr 130px;gap:10px 14px;
      align-items:center}
 .ctl label{color:var(--dim)}
 .ctl output{font-variant-numeric:tabular-nums;text-align:right}
 input[type=range]{width:100%}
 svg{width:100%;height:auto;display:block;overflow:visible}
 .verdict{font-size:15px;padding:12px 14px;border-radius:8px;margin-top:4px}
 .hit{background:#3a1518;border:1px solid #7a2a30;color:#ffb3b8}
 .miss{background:#12301f;border:1px solid #2a6a45;color:#a8e6c4}
 table{border-collapse:collapse;font-size:13px;width:100%}
 td,th{text-align:left;padding:4px 12px 4px 0;color:var(--dim)}
 td b{color:var(--fg);font-variant-numeric:tabular-nums}
 code{background:#0b0d11;padding:1px 5px;border-radius:4px;font-size:12px}
</style></head><body><div class="wrap">
<h1>One frame, one time axis &mdash; t = 0 is the rising edge of <code>out_vsync</code></h1>
<p class="sub" id="sub"></p>
<div id="banner"></div>

<div class="card"><div class="ctl">
  <label for="d">Genlock delay (opcode 7)</label>
  <input type="range" id="d" min="0" step="1"><output id="dv"></output>
  <label for="e">Exposure (opcode 1)</label>
  <input type="range" id="e" min="1" step="1"><output id="ev"></output>
</div></div>

<div class="card"><svg id="svg" viewBox="0 0 1200 430"
   preserveAspectRatio="xMidYMid meet"></svg></div>

<div class="card"><div id="verdict" class="verdict"></div></div>

<div class="card"><table id="facts"></table></div>
</div>
<script>
const M = __MODEL__;
const W=1200, L=95, R=1175, TOP=34;
const SPAN = 2*M.period;                     // draw two frame periods
const X = t => L + (t/SPAN)*(R-L);
const rows = [
  {k:"R",   y:TOP+0,   h:26, label:"LED red"},
  {k:"G",   y:TOP+34,  h:26, label:"LED green"},
  {k:"B",   y:TOP+68,  h:26, label:"LED blue"},
  {k:"gap", y:TOP+106, h:18, label:"dark gaps"},
  {k:"exp", y:TOP+146, h:30, label:"sensor integrating"},
  {k:"bl",  y:TOP+186, h:30, label:"sensor BLIND"},
];
const el = id => document.getElementById(id);
const f = (v,n=1) => v.toLocaleString(undefined,{minimumFractionDigits:n,
                                                 maximumFractionDigits:n});

const dS=el('d'), eS=el('e');
dS.max = Math.round(M.period); dS.value = Math.round(M.delay);
eS.max = Math.round(M.maxExpo); eS.value = Math.round(M.expo);

function windows(delay, expo){
  // integration starts at delay + trigger->integration latency, repeats every T
  const out=[];
  for(let k=-1;k<=2;k++){
    const s = delay + M.trigLat + k*M.period;
    out.push({s:s, e:s+expo, bs:s+expo, be:s+M.period});
  }
  return out;
}

function overlapsFlash(delay, expo){
  // does the BLIND window clip any LED slot? that is the G3 question.
  const hits=[];
  for(const w of windows(delay,expo)){
    for(let k=0;k<2;k++){
      for(const s of M.slots){
        const t0=s.t0+k*M.period, t1=s.t1+k*M.period;
        const a=Math.max(w.bs,t0), b=Math.min(w.be,t1);
        if(b-a > 1e-6 && w.bs < SPAN && w.be > 0) hits.push({c:s.c, us:b-a});
      }
    }
  }
  const by={};
  for(const h of hits) by[h.c]=(by[h.c]||0)+h.us;
  return by;
}

function draw(){
  const delay=+dS.value, expo=+eS.value;
  el('dv').textContent = f(delay,0)+" µs";
  el('ev').textContent = f(expo,0)+" µs";
  let s='';
  // frame boundaries
  for(let k=0;k<=2;k++){
    const x=X(k*M.period);
    s+=`<line x1="${x}" y1="${TOP-12}" x2="${x}" y2="${TOP+232}" stroke="#4a5568"
         stroke-width="1.5" stroke-dasharray="4 4"/>`;
    s+=`<text x="${x+5}" y="${TOP-16}" fill="#9ba1a6" font-size="11">vsync ${
        k===0?"t=0":"+"+f(k*M.period,0)+" µs"}</text>`;
  }
  // row labels + baselines
  for(const r of rows){
    s+=`<text x="${L-10}" y="${r.y+r.h/2+4}" fill="#9ba1a6" font-size="12"
         text-anchor="end">${r.label}</text>`;
    s+=`<rect x="${L}" y="${r.y}" width="${R-L}" height="${r.h}" fill="#0b0d11"
         stroke="#232830"/>`;
  }
  const hatch = M.measured ? "" : `fill-opacity="0.85"`;
  // LED slots, two periods
  for(let k=0;k<2;k++) for(const sl of M.slots){
    const r=rows.find(q=>q.k===sl.c);
    const x0=X(sl.t0+k*M.period), x1=X(sl.t1+k*M.period);
    s+=`<rect x="${x0}" y="${r.y}" width="${Math.max(1,x1-x0)}" height="${r.h}"
         fill="${M.colors[sl.c]}" ${hatch} rx="2"/>`;
  }
  // dark gaps
  const gr=rows.find(q=>q.k==="gap");
  for(let k=0;k<2;k++) for(const g of M.gaps){
    const x0=X(g[0]+k*M.period), x1=X(g[1]+k*M.period);
    s+=`<rect x="${x0}" y="${gr.y}" width="${Math.max(1,x1-x0)}" height="${gr.h}"
         fill="#4a5568" rx="2"/>`;
  }
  // sensor
  const er=rows.find(q=>q.k==="exp"), br=rows.find(q=>q.k==="bl");
  for(const w of windows(delay,expo)){
    if(w.e>0 && w.s<SPAN){
      const x0=X(Math.max(0,w.s)), x1=X(Math.min(SPAN,w.e));
      s+=`<rect x="${x0}" y="${er.y}" width="${Math.max(1,x1-x0)}" height="${er.h}"
           fill="#8b5cf6" rx="3"/>`;
    }
    if(w.be>0 && w.bs<SPAN){
      const x0=X(Math.max(0,w.bs)), x1=X(Math.min(SPAN,w.be));
      s+=`<rect x="${x0}" y="${br.y}" width="${Math.max(1,x1-x0)}" height="${br.h}"
           fill="#f59e0b" rx="3"/>`;
    }
  }
  // axis
  s+=`<line x1="${L}" y1="${TOP+236}" x2="${R}" y2="${TOP+236}" stroke="#4a5568"/>`;
  for(let i=0;i<=8;i++){
    const t=i*SPAN/8, x=X(t);
    s+=`<line x1="${x}" y1="${TOP+236}" x2="${x}" y2="${TOP+242}" stroke="#4a5568"/>`;
    s+=`<text x="${x}" y="${TOP+258}" fill="#9ba1a6" font-size="11"
         text-anchor="middle">${f(t,0)}</text>`;
  }
  s+=`<text x="${(L+R)/2}" y="${TOP+280}" fill="#9ba1a6" font-size="12"
       text-anchor="middle">microseconds since out_vsync</text>`;
  el('svg').innerHTML=s;

  // verdict
  const by=overlapsFlash(delay,expo);
  const keys=Object.keys(by);
  const v=el('verdict');
  if(keys.length===0){
    v.className="verdict miss";
    v.innerHTML="<b>Blind window is parked in a dark gap.</b> No colour flash is "+
      "clipped at this delay and exposure &mdash; this is the G3 condition.";
  } else {
    v.className="verdict hit";
    v.innerHTML="<b>Blind window clips "+keys.map(k=>
      `${{R:"red",G:"green",B:"blue"}[k]} by ${f(by[k])} µs`).join(", ")+
      ".</b> That colour is under-measured; the captured intensity is wrong.";
  }

  const jit = expo > M.jitterKnee ? M.jitterLong : M.jitterShort;
  el('facts').innerHTML = `
   <tr><td>frame period</td><td><b>${f(M.period,3)} µs</b> (${f(M.fps,4)} Hz)</td>
       <td>blind window</td><td><b>${f(M.period-expo,1)} µs</b></td></tr>
   <tr><td>max usable exposure</td><td><b>${f(M.maxExpo,1)} µs</b>
       = T &minus; ${f(M.sensorGap,1)} &minus; ${f(M.margin,1)}</td>
       <td>duty (integrating)</td><td><b>${f(100*expo/M.period,1)} %</b></td></tr>
   <tr><td>exposure-start jitter</td><td><b>${f(jit,2)} µs</b>
       (${expo>M.jitterKnee?"above":"below"} the ~${f(M.jitterKnee,0)} µs knee)</td>
       <td>delay quantum</td><td><b>${f(M.delayQ,3)} µs</b></td></tr>
   <tr><td>trigger &rarr; integration</td><td><b>${M.trigLatKnown?
        f(M.trigLat,2)+" µs":"0.00 µs — ASSUMED, read regs 0x42–0x47"}</b></td>
       <td>exposure quantum</td><td><b>${f(M.expoQ,3)} µs</b></td></tr>`;
}

el('sub').textContent = "Top three rows: light into the projection lens. "+
  "Bottom two: what the sensor is doing. Slide the delay to move the blind "+
  "window relative to the flashes.";
const b=el('banner');
if(M.measured){
  b.className="warn ok";
  b.innerHTML="LED profile is <b>measured</b>, from <code>"+M.source+"</code>.";
} else {
  b.className="warn";
  b.innerHTML="<b>The LED rows are ILLUSTRATIVE, not measured.</b> Nobody has "+
   "measured this projector's colour sequence yet &mdash; that is milestones "+
   "P3&ndash;P5 in PROJECTOR_PROFILING_PLAN.md. The flash and gap positions "+
   "shown are invented to make the geometry visible. <b>The sensor rows are "+
   "real</b>, from values measured on this hardware. Do not read timing off "+
   "the coloured bars.";
}
dS.addEventListener('input',draw); eS.addEventListener('input',draw); draw();
</script></body></html>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fps", type=float, default=120.0,
                   help="projected frame rate (default 120.0; the measured offline "
                        "rate is 102.8965)")
    p.add_argument("--delay", type=float, default=0.0,
                   help="genlock delay, us (opcode 7)")
    p.add_argument("--exposure", type=float, default=None,
                   help="exposure, us (default: the max usable at this rate)")
    p.add_argument("--trig-latency", type=float, default=0.0,
                   help="trigger -> integration start, us. NOT MEASURED anywhere in "
                        "this repo; read it from regs 0x42-0x47 and pass it.")
    p.add_argument("--profile", help="measured LED profile JSON from P5")
    p.add_argument("--out", default="frame_timeline.html")
    p.add_argument("--no-open", dest="open", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()

"""gen_stack_assembly.py -- the real board stack, and the stack inside the enclosure.

Writes, under ../3dmodels/stack/:

    camera_pcb.step          the camera element, exported from its KiCad board
    esp32_card_pcb.step      the ESP32 element, exported from its KiCad board
    board_stack.step         all five boards, placed and mated, in ONE file
    camera_stack_enclosure.step   board_stack + lens box + base box, in ONE file

    z = 0       the CAMERA PCB TOP SURFACE -- the frame gen_lens_box.py and
                gen_base_box.py already use, so the case parts go in unmoved
    +X / +Y     KiCad top view, origin at U1, +y UP

=============================================================================
THE STACK, TOP TO BOTTOM
=============================================================================
    camera element    KiCad export        1.60
    Alchitry Pt V2    vendor STEP         1.97   <- Alchitry model, NOT 1.60
    Alchitry Ft+      vendor STEP         1.60
    ESP32 card        KiCad export        1.60   (in the old spacer's slot)
    Alchitry Hd       vendor STEP         1.60

The camera PCB top to Hd bottom is MEASURED at 24.50 mm (d6201f5). The boards
take 8.37 of that, leaving four DF40 gaps of 4.03 mm each -- against Hirose's
4.0 mm mated height for DF40C plug + DF40HC(4.0) receptacle. So the gaps are
solved from the measurement, not assumed, and the agreement with the
connector datasheet is the check.

=============================================================================
THE FRAMES ARE TIED BY THE DF40 SITES
=============================================================================
Every board carries its 80/80/50-pin DF40s at the same three sites. In each
source file those sites are at:

    Alchitry STEPs      (38, 4) / (38, 41) / (16.5, 41)     board at x 0..55, y 0..45
    KiCad exports       (138, -101) / (138, -64) / (116.5, -64)

and in the enclosure frame at (2, -19) / (2, 18) / (-19.5, 18). The translations
below map all three onto that, and --check re-measures every connector in the
written file rather than trusting them. A board rotated 180 degrees would put
the 50-pin site on the wrong end, which is what that check catches.

The Alchitry models are NOT committed (130 MB). Fetch them from
https://cdn.alchitry.com/docs/{Pt-V2/Alchitry Platinum v2,Ft-V2/FtPlus,Hd-V2/Hd}.step
into LauEsp32Config_Pt_Stack/docs/, where the ESP32 card work already put them.
"""
import argparse, os, subprocess, sys, time

from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_Editor
from OCP.TDF import TDF_LabelSequence, TDF_Label
from OCP.TDataStd import TDataStd_Name
from OCP.TopLoc import TopLoc_Location
from OCP.gp import gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
MODELS = os.path.normpath(os.path.join(HERE, "..", "3dmodels"))
OUTDIR = os.path.join(MODELS, "stack")
VENDOR = os.path.join(ROOT, "LauEsp32Config_Pt_Stack", "docs")
KICAD_CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"

CAMERA_PCB = os.path.join(ROOT, "LauPythonCamera_Pt_Stack", "LauPythonCamera_Pt_Stack.kicad_pcb")
ESP32_PCB = os.path.join(ROOT, "LauEsp32Config_Pt_Stack", "LauEsp32Config_Pt_Stack.kicad_pcb")

STACK_H = 24.50          # MEASURED camera PCB top -> Hd bottom, as gen_base_box.py

# Where each source file puts its own board. "top" is the board's top surface
# in that file's z; "t" its thickness; (dx, dy) takes its frame to ours.
KICAD_XY = (-136.0, 82.0)        # KiCad export: x - U1.x, y + U1.y  (STEP y = -KiCad y)
ALCH_XY = (-36.0, -23.0)         # Alchitry: x - 36, y - 23  (gen_base_box.py header)
BOARDS = [
    # name          source                                         top    t     xy
    ("camera",  os.path.join(OUTDIR, "camera_pcb.step"),          1.60, 1.60, KICAD_XY),
    ("pt_v2",   os.path.join(VENDOR, "Alchitry_Platinum_v2.step"), 0.00, 1.97, ALCH_XY),
    ("ft_plus", os.path.join(VENDOR, "FtPlus.step"),               1.52, 1.60, ALCH_XY),
    ("esp32",   os.path.join(OUTDIR, "esp32_card_pcb.step"),       1.60, 1.60, KICAD_XY),
    ("hd",      os.path.join(VENDOR, "Hd.step"),                   1.52, 1.60, ALCH_XY),
]
# U1 has no 3D model in KiCad; the repo's own datasheet-built package goes in
# instead. Its origin is the package centre on the seating plane, which sits
# flat in the Andon socket on the PCB top (gen_lens_box.py --seat-z 0.0).
SENSOR = ("sensor_U1", os.path.join(MODELS, "NOIP1SN1300A_LCC48.step"), (0.0, 0.0, 0.0))
CASE = [("lens_box", os.path.join(MODELS, "camera_lens_box.step")),
        ("base_box", os.path.join(MODELS, "camera_base_box.step"))]

# The three DF40 sites in OUR frame, for --check.
SITES = [(2.0, -19.0), (2.0, 18.0), (-19.5, 18.0)]


def kicad_export(pcb, out):
    if not os.path.exists(KICAD_CLI):
        sys.exit("kicad-cli not found at %s" % KICAD_CLI)
    r = subprocess.run([KICAD_CLI, "pcb", "export", "step", "-f", "--subst-models",
                        "-o", out, pcb], capture_output=True, text=True)
    if r.returncode or not os.path.exists(out):
        sys.exit("kicad-cli failed on %s:\n%s%s" % (pcb, r.stdout, r.stderr))


def new_doc():
    return TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))


def read(fn):
    if not os.path.exists(fn):
        sys.exit("MISSING %s\nSee the header of this file for where it comes from." % fn)
    doc = new_doc()
    r = STEPCAFControl_Reader()
    r.SetNameMode(True); r.SetColorMode(True); r.SetLayerMode(True)
    if r.ReadFile(fn) != IFSelect_RetDone or not r.Transfer(doc):
        sys.exit("could not read %s" % fn)
    return doc


def label_name(lab):
    a = TDataStd_Name()
    if lab.FindAttribute(TDataStd_Name.GetID_s(), a):
        return a.Get().ToExtString()
    return ""


def set_name(lab, text):
    TDataStd_Name.Set_s(lab, TCollection_ExtendedString(text))


def bbox(shape):
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b, True)
    return None if b.IsVoid() else b.Get()


class Assembly:
    """One XCAF document that other STEP files are cloned into, each placed."""

    def __init__(self, name):
        self.doc = new_doc()
        self.st = XCAFDoc_DocumentTool.ShapeTool_s(self.doc.Main())
        self.root = self.st.NewShape()
        set_name(self.root, name)
        self.keep = []                   # source docs must outlive the clone
        self.parts = {}

    def add(self, name, fn, dx, dy, dz, parent=None, drop=()):
        t = time.time()
        src = read(fn)
        self.keep.append(src)
        sst = XCAFDoc_DocumentTool.ShapeTool_s(src.Main())
        free = TDF_LabelSequence()
        sst.GetFreeShapes(free)
        # Optionally drop whole first-level components by name -- the Alchitry
        # models carry every copper and soldermask layer as a full-board solid,
        # which is most of their bulk and none of their mechanical content.
        take, dropped = TDF_LabelSequence(), []
        for i in range(1, free.Length() + 1):
            top = free.Value(i)
            kids = TDF_LabelSequence()
            if drop and sst.IsAssembly_s(top) and sst.GetComponents_s(top, kids, False):
                for k in range(1, kids.Length() + 1):
                    nm = label_name(kids.Value(k))
                    if any(d in nm.lower() for d in drop):
                        dropped.append(nm.split(":")[0])
                    else:
                        take.Append(kids.Value(k))
            else:
                take.Append(top)
        part = self.st.NewShape()
        set_name(part, name)
        if not XCAFDoc_Editor.Extract_s(take, part, False):
            sys.exit("XCAF extract failed for %s" % fn)
        self.parts[name] = (part, (dx, dy, dz), src)
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(dx, dy, dz))
        self.st.AddComponent(self.root if parent is None else parent, part,
                             TopLoc_Location(trsf))
        sys.stdout.write("  + %-10s %-28s at (%+8.3f, %+8.3f, %+8.3f)  %.1f s%s\n"
                         % (name, os.path.basename(fn), dx, dy, dz, time.time() - t,
                            "  (dropped %s)" % ", ".join(dropped) if dropped else ""))
        return part

    def write(self, fn):
        self.st.UpdateAssemblies()
        Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        # No p-curves: every receiving CAD rebuilds them, and writing them
        # roughly doubles a file this size.
        Interface_Static.SetIVal_s("write.surfacecurve.mode", 0)
        w = STEPCAFControl_Writer()
        w.SetNameMode(True); w.SetColorMode(True); w.SetLayerMode(True)
        if not w.Transfer(self.doc, STEPControl_AsIs):
            sys.exit("STEP transfer failed for %s" % fn)
        tmp = fn + ".tmp"
        if w.Write(tmp) != IFSelect_RetDone:
            sys.exit("STEP write failed for %s" % fn)
        os.replace(tmp, fn)


def place_boards(asm, gap, parent=None):
    """Add the five boards (and the sensor). Returns [(name, top, bottom)]."""
    out = []
    z_top = 0.0
    for i, (name, fn, src_top, t, (dx, dy)) in enumerate(BOARDS):
        asm.add(name, fn, dx, dy, z_top - src_top, parent,
                drop=("copper", "soldermask") if fn.startswith(VENDOR) else ())
        out.append((name, z_top, z_top - t))
        if name == "camera":
            asm.add(SENSOR[0], SENSOR[1], *SENSOR[2], parent=parent)
        z_top = z_top - t - gap
    return out


def df40s(src, off):
    """Every DF40 in a source document, as placed: [(kind, cx, cy, z0, z1)].

    Found by SIZE, not name -- the three vendors of these models name them three
    different ways. A DF40 footprint is ~3 mm wide and either ~12 (50-pin) or
    ~18 (80-pin) long; nothing else on these boards is that shape.
    """
    st = XCAFDoc_DocumentTool.ShapeTool_s(src.Main())
    found = []

    def walk(lab, loc):
        # Test the WHOLE component first: a connector model is often itself an
        # assembly of pins and housing, and its leaves are not connector-shaped.
        shp = st.GetShape_s(lab)
        b = None if shp.IsNull() else bbox(shp.Moved(loc))
        if b is None:
            return
        lx, ly = b[3] - b[0], b[4] - b[1]
        for n, span in ((50, (11.0, 13.2)), (80, (17.0, 19.0))):
            if span[0] <= lx <= span[1] and 2.5 <= ly <= 3.6 and b[5] - b[2] < 4.5:
                found.append((n, (b[0] + b[3]) / 2.0 + off[0], (b[1] + b[4]) / 2.0 + off[1],
                              b[2] + off[2], b[5] + off[2]))
                return
        kids = TDF_LabelSequence()
        if st.IsAssembly_s(lab) and st.GetComponents_s(lab, kids, False):
            for i in range(1, kids.Length() + 1):
                c = kids.Value(i)
                ref = TDF_Label()
                st.GetReferredShape_s(c, ref)
                walk(ref, loc.Multiplied(st.GetLocation_s(c)))

    free = TDF_LabelSequence()
    st.GetFreeShapes(free)
    for i in range(1, free.Length() + 1):
        walk(free.Value(i), TopLoc_Location())
    return found


def check_mating(asm, layout):
    """Each board's bottom plugs must land in the receptacles of the board below:
    same three sites, and the plug tip inside the receptacle body."""
    w = sys.stdout.write
    ok = True
    con = {}
    for name, top, bot in layout:
        _, off, src = asm.parts[name]
        found = df40s(src, off)
        plugs = [f for f in found if f[4] <= bot + 0.05]        # under the board
        recs = [f for f in found if f[3] >= top - 0.05]         # on top of it
        con[name] = (plugs, recs)
        for kind, lst in (("plug", plugs), ("recept", recs)):
            for n, cx, cy, z0, z1 in lst:
                site = min(SITES, key=lambda s: (s[0] - cx) ** 2 + (s[1] - cy) ** 2)
                err = ((site[0] - cx) ** 2 + (site[1] - cy) ** 2) ** 0.5
                if err > 0.30:
                    ok = False
                    w("  ** %s %s DF40-%d at (%.2f, %.2f) is %.2f mm off every site\n"
                      % (name, kind, n, cx, cy, err))
    w("connectors board     plugs below  receptacles above\n")
    for name, _, _ in layout:
        plugs, recs = con[name]
        w("           %-8s %5d        %5d\n" % (name, len(plugs), len(recs)))
    for (up, _, _), (lo, _, _) in zip(layout, layout[1:]):
        plugs, recs = con[up][0], con[lo][1]
        if len(plugs) != 3 or len(recs) != 3:
            ok = False
            w("  ** %s -> %s: %d plugs over %d receptacles, expected 3 and 3\n"
              % (up, lo, len(plugs), len(recs)))
            continue
        worst = None
        for n, cx, cy, z0, z1 in plugs:
            mate = [r for r in recs if r[0] == n and abs(r[1] - cx) < 0.3 and abs(r[2] - cy) < 0.3]
            if not mate:
                ok = False
                w("  ** %s DF40-%d plug at (%.2f, %.2f) has no receptacle under it on %s\n"
                  % (up, n, cx, cy, lo))
                continue
            ins = mate[0][4] - z0          # how far the plug tip is inside the receptacle
            worst = ins if worst is None else min(worst, ins)
        if worst is not None:
            w("           %-8s -> %-8s 3 sites matched, plug %.2f mm into receptacle%s\n"
              % (up, lo, worst, "" if 0.3 < worst < 2.0 else "  ** NOT SEATED"))
            ok = ok and 0.3 < worst < 2.0
    return ok


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stack-h", type=float, default=STACK_H,
                   help="MEASURED camera PCB top -> Hd bottom, mm")
    p.add_argument("--no-export", dest="export", action="store_false",
                   help="reuse the existing KiCad STEP exports")
    p.add_argument("--no-case", dest="case", action="store_false",
                   help="write board_stack.step only")
    args = p.parse_args()
    w = sys.stdout.write
    os.makedirs(OUTDIR, exist_ok=True)

    if args.export:
        w("exporting KiCad boards\n")
        kicad_export(CAMERA_PCB, BOARDS[0][1])
        kicad_export(ESP32_PCB, BOARDS[3][1])

    boards_t = sum(b[3] for b in BOARDS)
    gap = (args.stack_h - boards_t) / (len(BOARDS) - 1)
    w("stack      %.2f mm measured, boards %.2f, 4 gaps of %.3f (DF40 nominal 4.000)\n"
      % (args.stack_h, boards_t, gap))
    if abs(gap - 4.0) > 0.25:
        sys.exit("GAP %.3f IS NOT A DF40 STACK. Either --stack-h or a board thickness "
                 "is wrong." % gap)

    w("board_stack.step\n")
    asm = Assembly("board_stack")
    layout = place_boards(asm, gap)
    if not check_mating(asm, layout):
        sys.exit("CONNECTORS DO NOT MATE -- nothing written. See ** lines above.")
    fn = os.path.join(OUTDIR, "board_stack.step")
    asm.write(fn)
    w("  wrote %s (%.1f MB)\n" % (fn, os.path.getsize(fn) / 1e6))

    if args.case:
        w("camera_stack_enclosure.step\n")
        asm = Assembly("camera_stack_enclosure")
        stack = asm.st.NewShape()
        set_name(stack, "board_stack")
        # Boards go in as one sub-assembly so a viewer can hide the case.
        place_boards(asm, gap, parent=stack)
        asm.st.AddComponent(asm.root, stack, TopLoc_Location())
        for name, cfn in CASE:
            asm.add(name, cfn, 0.0, 0.0, 0.0)
        fn = os.path.join(OUTDIR, "camera_stack_enclosure.step")
        asm.write(fn)
        w("  wrote %s (%.1f MB)\n" % (fn, os.path.getsize(fn) / 1e6))

    w("\nlayout     board      top      bottom\n")
    for name, top, bot in layout:
        w("           %-8s %8.3f  %8.3f\n" % (name, top, bot))
    return 0


if __name__ == "__main__":
    sys.exit(main())

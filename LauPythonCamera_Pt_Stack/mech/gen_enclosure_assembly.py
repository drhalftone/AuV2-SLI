"""gen_enclosure_assembly.py -- both halves of the enclosure in ONE STEP file.

Writes ../3dmodels/camera_enclosure_assembly.step (+ .stl).

    z = 0       the CAMERA PCB TOP SURFACE (the frame both halves already use)

WHAT THIS IS. The lens box and the base box are separate parts and print
separately; this is the file you OPEN to check they meet. It contains every
solid of both halves at its true position, plus -- unless you say
--no-reference -- four plates standing in for the boards, so the sandwich can
be seen rather than trusted.

IT DOES NOT RE-DESCRIBE THE GEOMETRY. Nothing here re-derives a wall or a boss.
It runs gen_lens_box.py and gen_base_box.py, captures the StepFile each one
built, and REPLAYS their recorded prisms into a single file. Every solid is
therefore byte-for-byte the same geometry as the part you print; if this
assembly and the two half files ever disagree, that is a bug here and not a
difference in the design.

THE BOARD PLATES ARE REFERENCE GEOMETRY AND PARTLY ASSUMED. Only two numbers
about the stack are known: the 55 x 45 outline (read from KiCad) and the
MEASURED 19.00 mm from the camera PCB's top surface to the Hd+'s bottom. The
individual board thickness is NOT measured -- 1.60 mm is the usual value and is
assumed here, which then forces the pitch:

    pitch = (19.00 - 1.60) / 3 = 5.800 mm   ->  gap = 4.200 mm

The two OUTER faces are measured and correct. The two INTERMEDIATE boards sit
where that assumption puts them, so use them to check clearance, not to
dimension anything. They are named board_REF_* and are excluded from the STL.
--no-reference drops them.
"""
import argparse, importlib, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step
from gen_lens_holder import read_pcb

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "3dmodels", "camera_enclosure_assembly.step")

STACK_H = 19.00          # MEASURED camera PCB top -> Hd+ bottom
BOARD_T = 1.60           # ASSUMED -- see header
BOARDS = ["camera", "pt", "ftplus", "hd"]       # top to bottom
REF_RGB = (0.18, 0.42, 0.24)                    # green, obviously not a printed part


def capture(module_name, argv):
    """Run a generator and hand back the StepFile it built.

    sw.StepFile is swapped for a subclass that records every instance. Both
    generators do `import step_writer as sw` and therefore share this module
    object, so patching the attribute reaches them without either file being
    touched. They still write their own outputs, which is harmless: the
    generators are byte-reproducible, so re-running one rewrites exactly what
    was already there.
    """
    mod = importlib.import_module(module_name)
    made = []
    orig = sw.StepFile

    class Capturing(orig):
        def __init__(self, *a, **k):
            orig.__init__(self, *a, **k)
            made.append(self)

    sw.StepFile = Capturing
    old_argv, old_stdout = sys.argv, sys.stdout
    sys.argv, sys.stdout = argv, open(os.devnull, "w")
    try:
        mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 0
    else:
        rc = 0
    finally:
        sw.StepFile = orig
        sys.argv, sys.stdout = old_argv, old_stdout
    if rc:
        sys.exit("%s failed (exit %s); assembly not built.\nRun it on its own to see why."
                 % (module_name, rc))
    if not made:
        sys.exit("%s built no StepFile" % module_name)
    return made[0]


def replay(dst, src, prefix, expected):
    """Copy every recorded prism out of one StepFile into another."""
    rgb = dict((name, colour) for _, name, colour in src.solids)
    n = 0
    for name, pts, holes, z0, z1 in src.meshes:
        nm = "%s_%s" % (prefix, name)
        dst.prism(pts, z0, z1, nm, rgb.get(name, (0.5, 0.5, 0.5)), holes=holes)
        expected[nm] = ((abs(sw.signed_area(pts))
                         - sum(abs(sw.signed_area(h)) for h in holes)) * (z1 - z0))
        n += 1
    return n


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--open-face", action="append", default=["E"],
                   help="passed through to gen_base_box.py (default E)")
    p.add_argument("--stack-h", type=float, default=STACK_H)
    p.add_argument("--board-t", type=float, default=BOARD_T,
                   help="ASSUMED board thickness for the reference plates, mm")
    p.add_argument("--no-reference", dest="reference", action="store_false",
                   help="drop the four board_REF_* plates")
    p.add_argument("--timestamp", default="2026-08-25T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    args = p.parse_args()

    w = sys.stdout.write

    # The base box reads the lens box's STEP, so the lens box must exist first.
    lens = capture("gen_lens_box", ["gen_lens_box.py", "--bosses"])
    base_argv = ["gen_base_box.py"]
    for f in args.open_face:
        base_argv += ["--open-face", f]
    base = capture("gen_base_box", base_argv)

    step = sw.StepFile(
        "camera_enclosure_assembly",
        "Two-part printed enclosure -- lens box + base box, split at z=-1.60"
        + (" | board plates are REFERENCE, intermediate pitch assumed"
           if args.reference else ""),
        args.timestamp, tool="gen_enclosure_assembly.py")
    expected = {}

    n_lens = replay(step, lens, "lens", expected)
    n_base = replay(step, base, "base", expected)

    skip = []
    if args.reference:
        edge, _, _, u1 = read_pcb()
        xs = [v for e in edge for v in (e[0], e[2])]
        ys = [v for e in edge for v in (e[1], e[3])]
        bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
        mx0, my1 = bx0 - u1["x"], u1["y"] - by0
        mx1, my0 = bx1 - u1["x"], u1["y"] - by1
        cx, cy = (mx0 + mx1) / 2.0, (my0 + my1) / 2.0
        bw, bh = mx1 - mx0, my1 - my0
        pitch = (args.stack_h - args.board_t) / (len(BOARDS) - 1)
        for i, nm in enumerate(BOARDS):
            top = -i * pitch
            n = "board_REF_%s" % nm
            step.prism(sw.rect(cx, cy, bw, bh), top - args.board_t, top, n, REF_RGB)
            expected[n] = bw * bh * args.board_t
            skip.append(n)

    text = step.dumps()
    tries = sw.write_verified(OUT, text)
    w("wrote %s  (%d entities, %.1f kB)\n" % (OUT, step.entity_count, len(text) / 1024.0))
    if tries > 1:
        w("           ** read-back MISMATCHED %d time(s); rewritten until it matched.\n"
          "           ** See step_writer.write_verified -- this machine corrupts writes.\n"
          % (tries - 1))
    if args.stl:
        ntri, _ = step.write_stl(os.path.splitext(OUT)[0] + ".stl", skip=skip)
        w("wrote %s  (%d triangles%s)\n"
          % (os.path.splitext(OUT)[0] + ".stl", ntri,
             ", reference plates excluded" if skip else ""))

    zs = [z for _, _, _, z0, z1 in step.meshes for z in (z0, z1)]
    w("\nassembly   %d solids -- %d from the lens box, %d from the base box%s\n"
      % (len(step.meshes), n_lens, n_base,
         ", %d reference plates" % len(skip) if skip else ""))
    w("           z %.3f -> %.3f  (%.2f mm tall)\n" % (min(zs), max(zs), max(zs) - min(zs)))
    w("split      the two halves meet at z = -1.600, the camera PCB's bottom face\n")
    if args.reference:
        w("boards     4 x %.1f x %.1f x %.2f plates, pitch %.3f mm (gap %.3f)\n"
          % (bw, bh, args.board_t, pitch, pitch - args.board_t))
        w("           camera top %.2f | Hd+ bottom %.2f  <- both MEASURED\n"
          % (0.0, -args.stack_h))
        w("           ** the two INTERMEDIATE boards are placed from an ASSUMED\n"
          "           ** %.2f mm board thickness. Check clearance against them,\n"
          "           ** do not dimension from them.\n" % args.board_t)
    w("\nThese are REFERENCE bodies and the printed parts are the two half files:\n")
    w("    camera_lens_box.step   upper half\n")
    w("    camera_base_box.step   lower half\n")
    ok = check_step.validate(OUT, expected, out=open(os.devnull, "w"))
    w("volumes    %s\n" % ("OK" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

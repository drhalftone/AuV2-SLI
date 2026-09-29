"""gen_lens_cap_stand.py -- desk stand for projector_lens_cap_filter_tab.step.

Writes ../3dmodels/lens_cap_stand.step (+ .stl).

The cap (gen_projector_lens_cap.py --filter-dia 50 --slot-deg 90 --tab-w 32 --cbore-all) has a
tongue on its DOWN edge. This stand takes that tongue in a rectangular pocket and
puts the whole camera + filter + FPGA stack on a flat base, optical axis horizontal,
so the camera can look at a projector sitting some distance away on the same desk.

WHICH EDGE IS DOWN. The tab is on the cap's -y edge, the side of the stack that
carries only the one 80-pin DF40 (J2). The Hd+ HDMI and Ft+ USB-C ports are all on
the -x (west) edge, so they leave the stack SIDEWAYS, parallel to the desk. An
earlier revision (--slot-deg 0) put the tab on that west edge, which pointed the
cables straight down into the stand.

FRAME. Printing frame: Z up, Z=0 is the desk. It is the cap frame rotated
(x, y, z)_cap -> (x - axis_x, z - tab_mid_z, axis_y - y)_stand, so the tab drops in
along +Z. Print it as it sits (base down). Nothing overhangs, so no supports.

THE PART IS A GRAVITY-AND-FRICTION FIT. The tab is 4.0 mm thick in a 4.6 mm pocket
and 32.0 in a 32.6 pocket; there is no latch. The load is the stack hanging on one
side of the tab and the filter/skirt on the other, and the pocket walls resist the
resulting torque. Widen --clear to loosen, or shim with tape to tighten.
"""
import argparse, os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step
import gen_lens_holder as glh

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "3dmodels", "lens_cap_stand.step")
COL = {"base": (0.30, 0.32, 0.36), "block": (0.24, 0.26, 0.30)}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tab-w", type=float, default=32.0, help="cap tab width, mm")
    p.add_argument("--tab-t", type=float, default=4.0, help="cap tab thickness (= plate), mm")
    p.add_argument("--tab-len", type=float, default=16.0, help="cap tab length past the plate")
    p.add_argument("--tab-tip-from-axis", type=float, default=42.90,
                   help="distance from the optical axis to the tab tip, mm (printed by the cap generator)")
    p.add_argument("--engage", type=float, default=11.0, help="how deep the tab sits in the pocket, mm")
    p.add_argument("--clear", type=float, default=0.30, help="pocket clearance each side, mm")
    p.add_argument("--floor-gap", type=float, default=0.6, help="tab tip to pocket floor, mm")
    p.add_argument("--wall", type=float, default=8.0, help="wall around the pocket, mm")
    p.add_argument("--block-y", type=float, default=14.6,
                   help="block length along the camera axis (across the tab thickness), mm. "
                        "Kept thin: its rear face sits at cap z = 8 - block_y/2 = 0.7, just in "
                        "front of the camera PCB top (z = 0), so it cannot reach the stack.")
    p.add_argument("--rib-len", type=float, default=22.0,
                   help="front-only rib beyond the block, mm, to stiffen it against the "
                        "camera's fore/aft lever. It is on the FRONT (projector side) only, "
                        "because the stack hangs off the back")
    p.add_argument("--rib-w", type=float, default=30.0, help="rib width across the tab, mm")
    p.add_argument("--rib-h", type=float, default=35.0, help="rib height above the base, mm")
    p.add_argument("--base-x", type=float, default=80.0, help="base width (across the tab), mm")
    p.add_argument("--base-y-back", type=float, default=90.0,
                   help="base length behind the tab towards the camera, mm (stack + boards)")
    p.add_argument("--base-y-front", type=float, default=34.0,
                   help="base length in front of the tab towards the projector, mm (filter + skirt)")
    p.add_argument("--base-t", type=float, default=5.0)
    p.add_argument("--block-below", type=float, default=54.8,
                   help="solid under the pocket floor, mm (4.0 + 50.8 = 2 inch riser)")
    p.add_argument("--segments", type=int, default=48)
    p.add_argument("--timestamp", default="2026-09-23T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    a = p.parse_args()

    step = sw.StepFile("lens_cap_stand", "Desk stand for the filter lens cap tab",
                       a.timestamp, tool="gen_lens_cap_stand.py")
    exp = {}
    # X = across the tab (cap x), Y = along the camera axis (cap z), 0 at tab mid-thickness
    z0 = 0.0
    zb = z0 + a.base_t
    base = sw.rounded_rect((a.base_y_front - a.base_y_back) / 2.0 * 0, 0, a.base_x,
                           a.base_y_back + a.base_y_front, 4.0)
    # rounded_rect is centred; shift it so Y=0 is the tab plane
    yc = (a.base_y_front - a.base_y_back) / 2.0
    base = [(x, y + yc) for x, y in base]
    step.prism(base, z0, zb, "base", COL["base"])
    exp["base"] = abs(sw.signed_area(base)) * a.base_t

    zfloor = zb + a.block_below
    ztop = zfloor + a.floor_gap + a.engage
    bx = a.tab_w + 2 * a.clear + 2 * a.wall
    blk = sw.rect(0, 0, bx, a.block_y)
    step.prism(blk, zb, zfloor, "block_root", COL["block"])
    exp["block_root"] = abs(sw.signed_area(blk)) * (zfloor - zb)
    if a.rib_len > 0:
        rib = sw.rect(0, a.block_y / 2.0 + a.rib_len / 2.0, a.rib_w, a.rib_len)
        step.prism(rib, zb, zb + a.rib_h, "rib", COL["block"])
        exp["rib"] = abs(sw.signed_area(rib)) * a.rib_h
    pocket = sw.reverse(sw.rect(0, 0, a.tab_w + 2 * a.clear, a.tab_t + 2 * a.clear))
    step.prism(blk, zfloor, ztop, "block", COL["block"], holes=[pocket])
    exp["block"] = (abs(sw.signed_area(blk)) - abs(sw.signed_area(pocket))) * (ztop - zfloor)

    text = step.dumps()
    sw.write_verified(OUT, text)
    if a.stl:
        step.write_stl(os.path.splitext(OUT)[0] + ".stl")
    w = sys.stdout.write
    w("wrote %s\n" % OUT)
    tip = zfloor + a.floor_gap
    w("footprint  %.0f x %.0f mm, block top %.1f mm above the desk\n"
      % (a.base_x, a.base_y_back + a.base_y_front, ztop))
    w("pocket     %.1f x %.1f mm, %.1f deep; tab tip rests %.2f above the floor\n"
      % (a.tab_w + 2 * a.clear, a.tab_t + 2 * a.clear, a.engage + a.floor_gap, a.floor_gap))
    # the boards' down (-y) edge, read from the PCB like every other part here
    edge, _, _, u1 = glh.read_pcb()
    board_down = glh.OPTICAL_CENTER[1] - min(u1["y"] - y for e in edge for y in (e[1], e[3]))
    w("stack      block top is %.1f mm from the axis, the boards' down edge %.1f -> %.1f mm clear;\n"
      "           block rear face at cap z %.2f, in front of the camera PCB top\n"
      % (a.tab_tip_from_axis - a.engage, board_down,
         a.tab_tip_from_axis - a.engage - board_down, 8.0 - a.block_y / 2.0))
    w("cable      HDMI and USB-C leave the stack's -x edge sideways, parallel to the desk\n")
    w("AXIS       optical axis is %.1f mm above the desk (put the projector lens at this height)\n"
      % (tip + a.tab_tip_from_axis))
    ok = check_step.validate(OUT, exp, out=open(os.devnull, "w"))
    w("volumes    %s\n" % ("OK" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

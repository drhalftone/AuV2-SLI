#!/usr/bin/env python3
"""Generate the socket tile -- a square halo that covers the Andon socket's contacts.

Iteration 0 of the camera bracket. Deliberately just the tile:

  * a square plate large enough to cover the socket's exposed contact ring,
  * a square window in the middle for the PYTHON 1300 to sit in, and
  * two locating pins on the underside, on the board's Andon index holes.

Everything is parameterised, because almost every dimension here is a first guess that a
test print will move.

Coordinate system
-----------------
    origin      centre of U1 (the socket footprint) on the board
    z = 0       the **PCB top surface**
    +X / +Y     the KiCad top view of the board -- the same view as `gen_sensor_step.py`,
                so the two models share an X/Y frame

Note the Z datum differs from `gen_sensor_step.py`, whose z = 0 is the sensor's *seating
plane* inside the socket. The height of that plane above the PCB is published nowhere and
is still unmeasured, so the two models cannot yet be stacked in Z. See `README.md`.

Geometry that came off the real board rather than a catalogue -- `--check` re-reads it on
every run:

    index holes     2 x dia 1.60 NPTH at (-8.382, -8.128) and (+8.128, +8.382)
    socket body     16.764 mm square (the U1 F.Fab rectangle)
    contact pads    2.54 x 0.635 on a 9.906 mm row centre, so copper reaches +-11.176

Usage
-----
    python gen_socket_tile.py
    python gen_socket_tile.py --outer 24 --window 15.0 --thickness 2.0
"""

import argparse
import math
import os
import re
import sys

import step_writer as sw
import check_step
import gen_sensor_step as sensor

MM = "mm"

# --------------------------------------------------------------------------------------
# Board-derived constants. `check_against_pcb` re-derives all of these from the .kicad_pcb
# and fails if they have moved, so treat these as documentation of what is expected.
# --------------------------------------------------------------------------------------

INDEX_HOLES = ((-8.382, -8.128), (+8.128, +8.382))   # model frame (+y up), from U1
INDEX_HOLE_DIA = 1.60
SOCKET_BODY_XY = 16.764
SOCKET_HEIGHT = 2.90        # README section 7, Andon 680-48-SM, "2.90 mm (REF)"
PAD_REACH = 11.176          # outer edge of the socket's solder pads
PAD_LENGTH = 2.54           # pad length, so they run 8.636 .. 11.176 from centre
SENSOR_BODY_MAX = 14.42     # NOIP1SN1300A body, 14.22 +0.20
PCB_THICKNESS = 1.6         # README section 7 -- the index-pin protrusion question

COLORS = {
    "tile": (0.35, 0.55, 0.80),
    "pin": (0.25, 0.40, 0.62),
    "boss": (0.30, 0.47, 0.71),
}


# --------------------------------------------------------------------------------------
# The wire slots
# --------------------------------------------------------------------------------------

def slot_offsets():
    """Along-edge slot positions per side, taken from the sensor's own pin ring.

    Reusing `gen_sensor_step.pin_ring` rather than re-deriving the pitch is the point:
    the slots then line up with the sensor's castellations by construction, and inherit
    the cross-check that ring already carries against U1's footprint.
    """
    sides = {}
    for _, (side, along) in sorted(sensor.pin_ring().items()):
        sides.setdefault(side, []).append(along)
    for side in sides:
        sides[side].sort()
    assert sum(len(v) for v in sides.values()) == 48, sides
    return sides


def _rot90(pts, n):
    for _ in range(n % 4):
        pts = [(-y, x) for x, y in pts]
    return pts


def lower_pieces(outer_half, outer_r, win_half, win_r, off, width, segments=6):
    """The bottom layer, once the channels break out at the rim.

    A channel that reaches the outer edge cuts the bottom layer clean through, so the layer
    is not one ring but 48 separate pieces: eleven between the slots on each side, plus a
    piece wrapping each corner. Built once for the bottom edge and its corner, then rotated
    -- all four sides carry the same slot offsets, so one construction covers the part.
    """
    w = width / 2.0
    oc, wc = outer_half - outer_r, win_half - win_r
    canon = []
    for k in range(len(off) - 1):                       # between adjacent slots
        a, b = off[k] + w, off[k + 1] - w
        canon.append([(a, -outer_half), (b, -outer_half), (b, -win_half), (a, -win_half)])

    p = [(off[-1] + w, -outer_half), (oc, -outer_half)]  # around the corner
    p += sw.arc(oc, -oc, outer_r, -math.pi / 2, 0.0, segments)
    p += [(outer_half, off[0] - w), (win_half, off[0] - w), (win_half, -wc)]
    p += sw.arc(wc, -wc, win_r, 0.0, -math.pi / 2, segments)
    p += [(off[-1] + w, -win_half)]
    canon.append(sw.dedupe(p))

    return [_rot90(piece, n) for n in range(4) for piece in canon]


def window_outline(half, sides, depth, width, corner_r, corner_segments=6):
    """The window, counter-clockwise, with a slot cut at each sensor pin position.

    Slots protrude OUTWARD -- away from the window centre, into the tile's material -- by
    `depth`. Depth is what distinguishes the two layers of the tile: a shallow groove down
    the window wall above, and a channel running out across the bottom face below.
    """
    def edge(side, reverse_):
        pts, d = [], half + depth
        for along in sides.get(side, []):
            a, b = along - width / 2.0, along + width / 2.0
            if side == "S":
                pts += [(a, -half), (a, -d), (b, -d), (b, -half)]
            elif side == "N":
                pts += [(a, half), (a, d), (b, d), (b, half)]
            elif side == "W":
                pts += [(-half, a), (-d, a), (-d, b), (-half, b)]
            else:
                pts += [(half, a), (d, a), (d, b), (half, b)]
        if reverse_:
            pts.reverse()
        return pts

    c = half - corner_r
    out = [(-c, -half)]
    out += edge("S", False)
    out += [(c, -half)]
    out += sw.arc(c, -c, corner_r, -math.pi / 2, 0.0, corner_segments)
    out += edge("E", False)
    out += [(half, c)]
    out += sw.arc(c, c, corner_r, 0.0, math.pi / 2, corner_segments)
    out += edge("N", True)
    out += [(-c, half)]
    out += sw.arc(-c, c, corner_r, math.pi / 2, math.pi, corner_segments)
    out += edge("W", True)
    out += [(-half, -c)]
    out += sw.arc(-c, -c, corner_r, math.pi, 1.5 * math.pi, corner_segments)
    return sw.dedupe(out)


# --------------------------------------------------------------------------------------
# Cross-check against the fabbed board
# --------------------------------------------------------------------------------------

def _sexpr(text, start):
    depth = 0
    i = start
    while True:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1


def check_against_pcb(pcb_path):
    """Re-derive the index holes, the socket body and the pad reach from the board file.

    Returns (holes, hole_dia, body_half, pad_reach) in the model frame. KiCad's canvas is
    +y DOWN and this model is +y UP, so every y is negated on the way in -- the same
    conversion `gen_sensor_step.py` makes, and the same one that, skipped, once produced a
    mirrored footprint.
    """
    src = open(pcb_path, encoding="utf-8").read()
    for m in re.finditer(r"\n\t\(footprint ", src):
        body = _sexpr(src, m.start() + 1)
        ref = re.search(r'\(property "Reference"\s+"([^"]+)"', body)
        if not ref or ref.group(1) != "U1":
            continue

        holes, dias, pad_reach = [], set(), 0.0
        for pm in re.finditer(r'\(pad "([^"]*)"', body):
            pad = _sexpr(body, pm.start())
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", pad)
            x, y = float(at.group(1)), -float(at.group(2))
            if "np_thru_hole" in pad:
                holes.append((x, y))
                drill = re.search(r"\(drill ([\d.]+)\)", pad)
                dias.add(float(drill.group(1)))
            elif pm.group(1).strip():
                size = re.search(r"\(size ([\d.]+) ([\d.]+)\)", pad)
                w, h = float(size.group(1)), float(size.group(2))
                pad_reach = max(pad_reach, abs(x) + w / 2, abs(y) + h / 2)

        fab = None
        for gm in re.finditer(r"\(fp_rect", body):
            g = _sexpr(body, gm.start())
            if 'layer "F.Fab"' in g:
                s = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)", g)
                e = re.search(r"\(end ([-\d.]+) ([-\d.]+)\)", g)
                fab = max(abs(float(v)) for v in s.groups() + e.groups())

        problems = []
        if len(holes) != 2:
            problems.append("expected 2 non-plated index holes, found %d" % len(holes))
        if len(dias) != 1:
            problems.append("index holes have mixed drill sizes: %s" % sorted(dias))
        elif abs(sorted(dias)[0] - INDEX_HOLE_DIA) > 1e-6:
            problems.append("index hole drill is %.3f, expected %.3f"
                            % (sorted(dias)[0], INDEX_HOLE_DIA))
        for got, want in zip(sorted(holes), sorted(INDEX_HOLES)):
            if abs(got[0] - want[0]) > 1e-6 or abs(got[1] - want[1]) > 1e-6:
                problems.append("index hole at %s, expected %s" % (got, want))
        if fab is None:
            problems.append("no F.Fab body rectangle on U1")
        elif abs(fab * 2 - SOCKET_BODY_XY) > 1e-6:
            problems.append("socket body is %.3f square, expected %.3f"
                            % (fab * 2, SOCKET_BODY_XY))
        if abs(pad_reach - PAD_REACH) > 1e-6:
            problems.append("pads reach %.3f, expected %.3f" % (pad_reach, PAD_REACH))
        if problems:
            raise SystemExit("BOARD MISMATCH vs %s:\n  " % pcb_path + "\n  ".join(problems))
        return sorted(holes), sorted(dias)[0], fab, pad_reach

    raise SystemExit("could not find footprint U1 in %s" % pcb_path)


def neighbours_under(pcb_path, half, exclude="U1"):
    """Every footprint whose origin falls under the tile, so nothing tall is missed."""
    src = open(pcb_path, encoding="utf-8").read()
    u1 = None
    found = []
    for m in re.finditer(r"\n\t\(footprint ", src):
        body = _sexpr(src, m.start() + 1)
        ref = re.search(r'\(property "Reference"\s+"([^"]+)"', body)
        at = re.search(r"\n\t\t\(at ([-\d.]+) ([-\d.]+)", body)
        if not ref or not at:
            continue
        pos = (float(at.group(1)), float(at.group(2)))
        layer = re.search(r'\n\t\t\(layer "([^"]+)"', body)
        if ref.group(1) == exclude:
            u1 = pos
        found.append((ref.group(1), pos, layer.group(1) if layer else "?"))
    out = []
    for name, pos, layer in found:
        if name == exclude or u1 is None:
            continue
        dx, dy = pos[0] - u1[0], -(pos[1] - u1[1])
        if abs(dx) <= half and abs(dy) <= half:
            out.append((name, dx, dy, layer))
    return sorted(out)


# --------------------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------------------

def build(args):
    pcb_ok = False
    holes, hole_dia, body_half, pad_reach = INDEX_HOLES, INDEX_HOLE_DIA, \
        SOCKET_BODY_XY / 2, PAD_REACH
    if args.check:
        holes, hole_dia, body_half, pad_reach = check_against_pcb(args.pcb)
        # The slots are placed from the sensor's pin ring; this asserts that same ring
        # against the board's pads. Passing it is what proves a wire leaving pin N
        # radially outward lands on pad N, which is the whole premise of a radial channel.
        sensor.check_against_pcb(args.pcb, sensor.pin_ring())
        pcb_ok = True

    # The tile is sized by how much of each solder joint it must leave visible, not by a
    # remembered number: the pads end at `pad_reach`, so backing off by `--expose` puts the
    # edge where the joint is still reachable with an iron.
    outer = args.outer if args.outer else 2 * (pad_reach - args.expose)
    outer_half = outer / 2.0
    z0 = args.standoff
    z1 = z0 + args.thickness
    pin_r = args.pin_dia / 2.0

    step = sw.StepFile("camera_socket_tile",
                       "Andon 680-48-SM contact tile, iteration 0",
                       args.timestamp, tool="gen_socket_tile.py")

    ring_outer = sw.rounded_rect(0, 0, outer, outer, args.corner_r)
    win_half = args.window / 2.0
    z_mid = z0 + args.channel_depth
    reach = args.channel_reach if args.channel_reach else outer_half
    through = reach >= outer_half - 1e-9

    # (outline, holes, z0, z1, name)
    solids = []
    if args.no_slots:
        window = sw.rounded_rect(0, 0, args.window, args.window, args.window_r)
        solids.append((ring_outer, [window], z0, z1, "tile"))
    else:
        sides = slot_offsets()
        # Upper: a shallow groove down the window wall. Lower: the same slot carried out
        # across the bottom face. Stacking the two is what makes the groove turn the
        # corner -- a single prism has vertical walls and cannot.
        groove = window_outline(win_half, sides, args.slot_depth, args.slot_width,
                                args.window_r)
        solids.append((ring_outer, [groove], z_mid, z1, "tile_upper"))
        if through:
            # Channels that break out at the rim sever the bottom layer into one piece
            # per gap between slots. That is the geometry, not a modelling compromise --
            # each piece hangs from the upper ring, which is what holds the tile together.
            for i, piece in enumerate(lower_pieces(outer_half, args.corner_r, win_half,
                                                   args.window_r, sides["S"],
                                                   args.slot_width)):
                solids.append((piece, [], z0, z_mid, "tile_lower_%02d" % (i + 1)))
        else:
            channel = window_outline(win_half, sides, reach - win_half,
                                     args.slot_width, args.window_r)
            solids.append((ring_outer, [channel], z0, z_mid, "tile_lower"))

    for outline, hs, a, b, name in solids:
        step.prism(outline, a, b, name, COLORS["tile"], holes=hs)

    # The locating pin is only as tall as it needs to be to do its job; the rest of the
    # drop to the tile is a wider shoulder. The tile's underside cannot come below the
    # socket's 2.90, so a post that were 2 mm overall would stop short of the board
    # entirely -- it is the inserting diameter that is 2 mm, not the whole column.
    pin_length = args.pin_length if args.pin_length else z0 + args.pin_engage
    pin_top = min(-args.pin_engage + pin_length, z0)
    boss_r = args.boss_dia / 2.0
    has_boss = pin_top < z0 - 1e-9
    posts = []
    for i, (hx, hy) in enumerate(holes):
        posts.append(("locating_pin_%d" % (i + 1), hx, hy, pin_r, -args.pin_engage,
                      pin_top, "pin"))
        if has_boss:
            posts.append(("locating_boss_%d" % (i + 1), hx, hy, boss_r, pin_top, z0,
                          "boss"))
    for name, hx, hy, r, za, zb, kind in posts:
        step.prism(sw.circle(hx, hy, r, args.pin_segments), za, zb, name, COLORS[kind])

    out = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "3dmodels", "camera_socket_tile.step")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    text = step.dumps()
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)

    # ----------------------------------------------------------------------------------
    expected = {}
    for outline, hs, a, b, name in solids:
        expected[name] = (abs(sw.signed_area(outline))
                          - sum(abs(sw.signed_area(h)) for h in hs)) * (b - a)
    for name, hx, hy, r, za, zb, kind in posts:
        expected[name] = abs(sw.signed_area(sw.circle(0, 0, r, args.pin_segments))) * (zb - za)

    report = []
    w = report.append
    w("wrote %s  (%d entities, %.1f kB)" % (out, step.entity_count, len(text) / 1024.0))
    if args.stl:
        stl = out[:-5] + ".stl" if args.stl is True else args.stl
        ntri, _ = step.write_stl(stl)
        w("wrote %s  (%d triangles; tile and pins are separate shells, union to print)"
          % (stl, ntri))
    w("")
    if pcb_ok:
        w("  board geometry            re-derived from %s" % os.path.basename(args.pcb))
    w("  tile                      %.3f square x %.2f thick, R%.1f corners"
      % (outer, args.thickness, args.corner_r))
    w("  underside / top           z = %.2f -> %.2f %s above the PCB" % (z0, z1, MM))
    w("  window                    %.2f square, R%.1f" % (args.window, args.window_r))
    if not args.no_slots:
        pitch = sensor.PIN_PITCH
        rib = pitch - args.slot_width
        roof = z1 - z_mid
        sensor_edge = sensor.BODY_XY["typ"] / 2
        w("  wire slots                48, %.2f wide on the sensor's %.3f pitch"
          % (args.slot_width, pitch))
        w("     pads share that pitch, so a wire running straight out from pin N lands")
        w("     on pad N: %.3f (sensor edge) -> %.3f (pad starts), a %.3f %s run."
          % (sensor_edge, pad_reach - PAD_LENGTH, pad_reach - PAD_LENGTH - sensor_edge, MM))
        w("     down the wall         %.2f deep, z = %.2f -> %.2f (full height)"
          % (args.slot_depth, z0, z1))
        w("     across the bottom     %.2f deep, out to r = %.3f from centre%s"
          % (args.channel_depth, reach, " (through the rim)" if through else ""))
        w("     roof over the channel %.2f %s of the %.2f thickness remains"
          % (roof, MM, args.thickness))
        w("     rib between slots     %.3f %s" % (rib, MM))
        if rib < 0.6:
            w("     ** %.3f mm ribs are at or under one FDM extrusion. Print this on a"
              % rib)
            w("        resin machine, or widen the pitch by slotting every 2nd pin. **")
        if through:
            w("     wires exit at the tile edge and land on the exposed %.3f %s of pad."
              % (args.expose, MM))
            w("     the bottom layer is severed into %d pieces -- see below."
              % (len(solids) - 1))
            if z0 > 0.05:
                drop = math.degrees(math.atan2(z0, args.expose))
                w("     ! the wire leaves the channel at z = %.2f and the pad it lands on"
                  % z0)
                w("       ends %.3f %s further out, so it turns down through about %.0f deg"
                  % (args.expose, MM, drop))
                w("       right at the rim. Chamfer the outer bottom edge, raise --expose,")
                w("       or accept a tight bend in fine wire.")
            else:
                w("     the channel floor IS the board, so the wire runs flat from the")
                w("     sensor's castellation onto its pad with no bend at the rim.")
        elif reach <= body_half:
            w("     ** channels stop at r = %.2f, inside the socket body edge at %.3f --"
              % (reach, body_half))
            w("        wires would be trapped between the tile and the socket. **")
        else:
            w("     wires clear the socket body (%.3f) by %.3f; %.2f %s of rim remains."
              % (body_half, reach - body_half, outer_half - reach, MM))
        w("     the slots meet the bottom face as a square step, not a radius -- give")
        w("     the wire its own bend relief when routing.")
    w("  locating legs             2 x dia %.2f, %.2f %s tall, z = %+.2f -> %+.2f"
      % (args.pin_dia, pin_top + args.pin_engage, MM, -args.pin_engage, pin_top))
    w("     %.2f into the %.1f %s board, %.2f clearance in the dia %.2f hole"
      % (args.pin_engage, PCB_THICKNESS, MM, hole_dia - args.pin_dia, hole_dia))
    proud = args.pin_engage - PCB_THICKNESS
    if proud > 0:
        w("     breaks through the underside by %.2f %s" % (proud, MM))
        if proud > 0.5:
            w("     ** %.2f %s is a lot to leave hanging under the board -- the DF40s"
              % (proud, MM))
            w("        mate downward into the Pt. **")
    elif proud > -0.2:
        w("     stops %.2f %s short of the underside -- effectively flush" % (-proud, MM))
    else:
        w("     stops %.2f %s short of the underside; it does not pass through"
          % (-proud, MM))
    if has_boss:
        w("  shoulder                  2 x dia %.2f, z = %+.2f -> %+.2f (%.2f tall)"
          % (args.boss_dia, pin_top, z0, z0 - pin_top))
        w("     carries the drop the pin no longer spans: the tile's underside is at")
        w("     %.2f and cannot come lower, since the socket is %.2f tall."
          % (z0, SOCKET_HEIGHT))
        # The shoulder is the widest thing near the window; check it stays out of it.
        gap = math.hypot(abs(holes[0][0]) - (args.window / 2 - args.window_r),
                         abs(holes[0][1]) - (args.window / 2 - args.window_r)) \
            - args.window_r - boss_r
        w("     %.3f %s clear of the window opening at the corner" % (gap, MM))
        if gap < 0:
            w("     ** the shoulder intrudes into the window -- reduce --boss-dia **")
    for i, (hx, hy) in enumerate(holes):
        w("     post %d                (%+.3f, %+.3f)" % (i + 1, hx, hy))
    w("")

    # -- does it do the job it was asked to do? ----------------------------------------
    w("  coverage")
    w("     solder joints         pads span %.3f .. %.3f from centre; tile edge at %.3f"
      % (pad_reach - PAD_LENGTH, pad_reach, outer_half))
    w("                           -> %.3f %s of every joint left visible and solderable"
      % (pad_reach - outer_half, MM))
    w("     inner edge            +-%.3f, between the sensor at +-%.3f and the pads at"
      " +-%.3f" % (args.window / 2, SENSOR_BODY_MAX / 2, pad_reach - PAD_LENGTH))
    w("                           -> the whole %.3f %s wire run sits under the tile"
      % (pad_reach - PAD_LENGTH - SENSOR_BODY_MAX / 2, MM))
    w("     sensor (max %.2f)     window clearance %.3f %s per side"
      % (SENSOR_BODY_MAX, (args.window - SENSOR_BODY_MAX) / 2, MM))
    if args.window <= SENSOR_BODY_MAX:
        w("     ** the window is not larger than the sensor's worst-case body **")
    w("")

    # -- the thing that does not fit ----------------------------------------------------
    widest = max(pin_r, boss_r if has_boss else 0.0)
    if z0 < SOCKET_HEIGHT - 1e-9:
        w("  ! THIS TILE AND THE ANDON SOCKET CANNOT BOTH BE FITTED")
        w("    The underside is at z = %.2f and the socket stands %.2f tall, and the"
          % (z0, SOCKET_HEIGHT))
        w("    window at +-%.3f is inside the socket body at +-%.3f -- so the tile would"
          % (args.window / 2, body_half))
        w("    have to pass through it. This is the socket-less build: the sensor sits")
        w("    directly on the board and the wires do what the socket would have done.")
        w("    Two things follow, both in your favour:")
        w("      - the index holes are empty, so the post interference that blocked the")
        w("        earlier iterations is moot;")
        w("      - the wire leaves the channel at board level and lands on a pad at board")
        w("        level, so there is no drop at the rim to bend around.")
        w("    And one against: nothing retains the sensor vertically any more. The tile")
        w("    surrounds it without covering it -- that is a separate part.")
    else:
        interference = [i + 1 for i, (hx, hy) in enumerate(holes)
                        if abs(hx) - widest < body_half and abs(hy) - widest < body_half]
        if interference:
            w("  ! LOCATING POSTS FOUL THE SOCKET BODY")
            w("    Both index holes sit exactly ON the socket body outline -- one at")
            w("    x = -8.382 and one at y = +8.382, and the body is +-%.3f. A round post"
              % body_half)
            w("    centred in either hole has half its section inside the socket's")
            w("    footprint, overlapping by up to %.3f %s%s." % (widest, MM,
              " (the shoulder, the wider of the two)" if has_boss else ""))
            w("    The holes exist to take the Andon part's OWN index pins, so they are")
            w("    under its body by construction. Order the -0 socket and relieve the")
            w("    posts to a D section, locate off something else, or add mounting holes")
            w("    on the next board spin.")
    w("")

    if pcb_ok:
        near = neighbours_under(args.pcb, outer_half)
        top = [n for n in near if n[3] == "F.Cu"]
        w("  under the tile            %d footprints, %d of them on the TOP layer"
          % (len(near), len(top)))
        for name, dx, dy, layer in top:
            w("     %-6s (%+.2f, %+.2f)" % (name, dx, dy))
        if top:
            w("     the tile's underside is at z = %.2f -- confirm none of these is"
              % z0)
            w("     taller than that before printing.")
        else:
            w("     nothing on the top layer to clear: the ten U1 decoupling caps that")
            w("     sit in this area were moved to the underside (README section 14).")

    ok = check_step.validate(out, expected, out=open(os.devnull, "w"))
    w("")
    w("  validation                %s" % ("PASS" if ok else "*** FAILED -- run check_step.py"))
    return "\n".join(report), ok


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    board = os.path.dirname(here)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--expose", type=float, default=1.0,
                   help="how much of each solder joint to leave visible past the tile "
                        "edge, mm (default 1.0); sets the outer size unless --outer is given")
    p.add_argument("--outer", type=float, default=None,
                   help="tile outer square, mm; overrides --expose")
    p.add_argument("--window", type=float, default=14.80,
                   help="centre window square, mm (default 14.80, 0.19/side over the "
                        "sensor's worst-case 14.42 body)")
    p.add_argument("--thickness", type=float, default=1.50, help="tile thickness, mm")
    p.add_argument("--standoff", type=float, default=0.0,
                   help="height of the tile's underside above the PCB, mm (default 0, "
                        "flush on the board -- which means no socket, see the README)")
    p.add_argument("--corner-r", type=float, default=0.80, help="outer corner radius, mm")
    p.add_argument("--window-r", type=float, default=0.40, help="window corner radius, mm")
    p.add_argument("--no-slots", action="store_true",
                   help="plain window, without the 48 wire slots")
    p.add_argument("--slot-width", type=float, default=0.51,
                   help="wire slot width, mm (default 0.51, the sensor's own "
                        "castellation width, on a 1.016 pitch)")
    p.add_argument("--slot-depth", type=float, default=0.40,
                   help="how far each slot cuts into the window wall, mm")
    p.add_argument("--channel-depth", type=float, default=0.40,
                   help="how deep the slot runs into the bottom face, mm")
    p.add_argument("--channel-reach", type=float, default=None,
                   help="radius from centre the bottom channels run out to, mm; the "
                        "default runs them through the rim so wires exit at the edge")
    p.add_argument("--pin-dia", type=float, default=1.40,
                   help="locating pin diameter, mm (default 1.40 in a 1.60 hole)")
    p.add_argument("--pin-engage", type=float, default=PCB_THICKNESS + 0.10,
                   help="how far the legs enter the board, mm (default 1.70 -- just "
                        "through a 1.6 mm PCB)")
    p.add_argument("--pin-length", type=float, default=None,
                   help="height of the locating leg itself, mm; defaults to the whole "
                        "drop from the tile, so there is no shoulder. Set it shorter and "
                        "the remainder becomes a --boss-dia shoulder")
    p.add_argument("--boss-dia", type=float, default=2.40,
                   help="shoulder diameter above the pin, mm; must stay clear of the "
                        "window opening at the corners")
    p.add_argument("--pin-segments", type=int, default=32, help="facets per pin")
    p.add_argument("--out", help="output .step path")
    p.add_argument("--stl", nargs="?", const=True, default=None, metavar="PATH",
                   help="also write a binary STL (millimetres), for tools that cannot "
                        "read STEP; defaults to the .step path with a .stl extension")
    p.add_argument("--pcb", default=os.path.join(board, "LauPythonCamera_Pt_Stack.kicad_pcb"))
    p.add_argument("--no-check", dest="check", action="store_false",
                   help="skip re-deriving the board geometry")
    p.add_argument("--timestamp", default="2026-08-07T00:00:00",
                   help="STEP header timestamp; fixed by default so output is reproducible")
    args = p.parse_args()
    if args.check and not os.path.exists(args.pcb):
        print("note: %s not found, using the recorded constants" % args.pcb, file=sys.stderr)
        args.check = False
    report, ok = build(args)
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

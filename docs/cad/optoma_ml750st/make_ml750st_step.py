#!/usr/bin/env python3
"""Simplified Optoma ML750ST envelope as a STEP file, for arrangement diagrams.

    python docs/cad/optoma_ml750st/make_ml750st_step.py

Writes ml750st_simple.step next to this script. Needs cadquery.

NOT a detailed model: a filleted box (the body), a cylinder (the lens barrel, coloured
like the body), a black disc in the barrel's recessed front (the lens glass) and four
feet. It is meant for figures that show where the camera sits relative to the projector
lens, not for fit checks. Where each number comes from is in README.md.

COORDINATES (mm):
    origin   centre of the lens barrel's front face
    +Z       out of the lens, the projection direction (the lens axis)
    +Y       up
    +X       to your right when you face the lens
"""
import os

import cadquery as cq

# ---- measured from Coretronic's "ID SIX VIEW ML750ST" drawing (README.md) ----
BODY_W = 104.3      # X: side to side
BODY_D = 107.9      # Z: front to back, front face to rear face
BODY_H = 32.0       # Y: the vented box, bottom face to top face, without the feet. Drawn
                    # low on purpose so the lens stands out, as on the real projector; the
                    # measured full height, to the top of the hood over the lens, is 45.0.
LENS_D = 45.4       # lens barrel outer diameter
LENS_PROUD = 4.4    # how far the barrel's front stands proud of the front face
LENS_SHIFT = 12.0   # STYLED, not measured: the barrel moved this much further out of the
                    # front face, so it reads clearly in figures (4.4 + 12.0 proud in all)
LENS_X = 74.2 - BODY_W / 2   # lens centre right of the body centre (facing the lens)
LENS_Y = 20.6       # lens centre above the body's bottom face

# ---- assumed, NOT measured: cosmetic only ----
CORNER_R = 6.0      # vertical-edge fillet
GLASS_D = 0.80 * LENS_D
GLASS_RECESS = 2.0
FOOT_D, FOOT_H = 10.0, 5.0
FOOT_INSET = 14.0   # foot centre from the body's side and front/back faces


def build():
    # Body: the front face sits at z = -(LENS_PROUD + LENS_SHIFT); it runs back from there.
    z_front = -(LENS_PROUD + LENS_SHIFT)
    body = (cq.Workplane("XY")
            .box(BODY_W, BODY_H, BODY_D)
            .edges("|Y").fillet(CORNER_R)
            .translate((-LENS_X, BODY_H / 2 - LENS_Y, z_front - BODY_D / 2)))
    # Lens barrel: 58.4 mm long (it reached the body's mid-depth before LENS_SHIFT moved it
    # out), ending at the origin plane.
    barrel_len = LENS_PROUD + BODY_D / 2
    barrel = (cq.Workplane("XY").workplane(offset=-barrel_len)
              .circle(LENS_D / 2).extrude(barrel_len))
    recess = (cq.Workplane("XY").workplane(offset=-GLASS_RECESS)
              .circle(GLASS_D / 2).extrude(GLASS_RECESS))
    barrel = barrel.cut(recess)
    # The glass: a thin black disc on the floor of the recess.
    glass = (cq.Workplane("XY").workplane(offset=-GLASS_RECESS)
             .circle(GLASS_D / 2).extrude(0.5))
    # Feet under the bottom face.
    y_bot = -LENS_Y
    feet = None
    for sx in (-1, 1):
        for zc in (z_front - FOOT_INSET, z_front - BODY_D + FOOT_INSET):
            f = (cq.Workplane("XZ", origin=(0, y_bot, 0))
                 .center(-LENS_X + sx * (BODY_W / 2 - FOOT_INSET), zc)
                 .circle(FOOT_D / 2).extrude(FOOT_H))
            feet = f if feet is None else feet.union(f)
    asm = cq.Assembly(name="ML750ST_simple")
    shell = cq.Color(0.92, 0.92, 0.90)              # the barrel matches the body
    asm.add(body, name="body", color=shell)
    asm.add(barrel, name="lens_barrel", color=shell)
    asm.add(glass, name="lens_glass", color=cq.Color(0.08, 0.08, 0.10))
    asm.add(feet, name="feet", color=cq.Color(0.25, 0.25, 0.25))
    return asm


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml750st_simple.step")
    build().export(out)
    print("wrote", out)

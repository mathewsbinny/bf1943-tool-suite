"""NavKit (big-endian): BF1943's soldier, and the NavKit settings that follow from it.

NavKit's defaults come from BFBC2: some are BFBC2's soldier, some were fitted to DICE's BFBC2 path data. For BF1943
the soldier's values come from the game (Objects/Soldiers/Common/DefaultSoldierPhysics: CharacterPhysicsData and the
Stand pose; BFBC2's soldier has the same values), and configure() sets the NavKit settings that depend on them. The
BF1943 tools (navsample1943, detailed1943) and BF1943 builds call it before they sample, choose sectors or build;
NavKit's own defaults are unchanged for BFBC2.

  import soldier1943
  soldier1943.configure()        # before navbuild.build / detailed.stacked_columns
"""
RADIUS = 0.35                   # PhysicalRadius
STEP_HEIGHT = 0.3               # StepHeight
STEP_TOLERANCE = 0.07           # the soldier climbs a little more than StepHeight: in game he walks into Wake's beach
                                # bunker (mil_bunker_t01v01) over a 0.33 m lip at its door (2026-10-23). The real limit
                                # is not measured; 0.07 keeps a small margin over what was seen.
STEP = STEP_HEIGHT + STEP_TOLERANCE
HEIGHT = 1.8                    # Height (Stand; Crouch 1.15)
SLOPE_MAX = 45.0                # MaxAscendAngle (deg)
WATER_DEPTH = 1.4               # WaterDepthLimit (m): pass as navbuild.build(max_water_depth=...), a default argument


def configure():
    """Set NavKit's soldier-dependent settings to BF1943's soldier (module attributes, for this process)."""
    import detailed
    import navbuild
    import navwalk
    navbuild.SLOPE_MAX = SLOPE_MAX          # BFBC2 NavKit: 40
    detailed.SLOPE_MAX = SLOPE_MAX          # which 0.8 m surfaces count as standable when choosing detailed sectors
    navbuild.MAX_WATER_DEPTH = WATER_DEPTH  # BFBC2 NavKit: 1.5 (build()'s default argument keeps 1.5: pass it)
    # the height step that links straight neighbours in a detailed zone (one 0.2 m cell); NavKit's 0.3 is DICE's
    # one-cell steps (p99 0.31): below the soldier's step, a lip he climbs would not link
    navbuild.DETAILED_LINK_STEP = STEP
    # nodes and outlines stand half the soldier's height above the ground (EntityHeight / 2; BFBC2 NavKit: 1.9 / 2)
    navbuild.Y_OFFSET = HEIGHT / 2
    navwalk.OUTLINE_Y_OFFSET = HEIGHT / 2
    # NavKit's band (0.5, 2.0) is fitted to DICE's BFBC2 data; the soldier stands 1.8 m, and navsample1943's capsule
    # test already blocks less headroom. The 2.0 end removed open ground at Wake's airfield bunker door (1.94 m).
    navwalk.HEADROOM_BAND = (0.5, HEIGHT)
    # a ladder links to each piece of ground beside its ends, not only the nearest surface (Wake's towers: the floor
    # enclosed by the frame is nearer the foot than the ground outside, from which the ladder is climbed)
    navbuild.LADDER_ALL_GROUND = True
    # shared border nodes also on the seam line itself, for ground that reaches the seam but not the border line one
    # sample beyond (Coral Sea's three seam breaks, 2026-10-26)
    navbuild.BORDER_SEAM_LINE = True
    # an area holds no cliff between plan-neighbouring cells (a bridge deck beside the ground under it): the RSNV height
    # grid cannot hold one (2026-10-26: 66 of Wake Island's 75 remaining tight edges lay in such areas)
    navbuild.AREA_CLIFF = 1.0
    # join_groups floods 2 m past a sector's window: ground joined to the rest of its sector only round a corner of
    # the neighbour sector (Coral Sea's last seam break, 2026-10-26)
    navbuild.JOIN_MARGIN = 2.0

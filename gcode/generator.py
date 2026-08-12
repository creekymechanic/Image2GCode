from datetime import datetime
from typing import List, Tuple
import math
import re

_RE_X = re.compile(r'X([-\d.]+)')
_RE_Y = re.compile(r'Y([-\d.]+)')
_RE_F = re.compile(r'F([-\d.]+)')


def pixel_to_mm(
    pt: Tuple[float, float],
    image_size: int,
    draw_width: float,
    draw_height: float,
    offset_x: float,
    offset_y: float,
    flip_y: bool = False,
) -> Tuple[float, float]:
    gx = (pt[0] / image_size) * draw_width + offset_x
    gy = (pt[1] / image_size) * draw_height + offset_y
    if flip_y:
        gy = (draw_height + 2 * offset_y) - gy
    return gx, gy


def gcode_home(config) -> List[str]:
    """
    Establish the machine's coordinate reference. Emit once per session.

    XY homes first — that involves no Z motion, so it is always safe. Then the
    gantry moves clear of the paper before Z homes, because Z homing descends
    until the endstop trips and the pen touches down wherever it happens to be.

    Homing Z is what makes Z an absolute datum measured up from the endstop, so
    Z_DRAW keeps its meaning across power cycles. It relies on a spring-loaded
    (floating) pen mount: the tip rides up as the gantry completes its descent.
    **With a rigid pen holder this would drive the tip into the bed** — such a
    setup must home XY only and set Z by hand.
    """
    return [
        "; >> home",
        "G28 X Y",                                    # no Z motion — always safe
        f"G0 X{config.Z_HOME_X:.3f} Y{config.Z_HOME_Y:.3f} F{config.FEED_TRAVEL}",
        "G28 Z",                                      # pen touches down here
        f"G0 Z{config.Z_TRAVEL:.2f} F{config.Z_SPEED}",
        "; << home",
    ]


def gcode_footer(config) -> List[str]:
    """
    Park moves emitted after all drawing is done: pen up, back to the drawing
    origin, then raise PARK_LIFT for clearance and go to PARK_X/PARK_Y so the
    paper can be lifted out without smudging.

    Kept separate from generate_gcode so callers that append overlays (name
    label, signature) can place them *before* the park moves without having to
    slice a fixed number of lines off the end of the program.
    """
    lift = getattr(config, "PARK_LIFT", 50.0)
    return [
        "",
        f"G0 Z{config.Z_TRAVEL:.2f} F{config.Z_SPEED}",
        f"G0 X{config.BED_OFFSET_X:.3f} Y{config.BED_OFFSET_Y:.3f} F{config.FEED_TRAVEL}",
        f"G0 Z{config.Z_TRAVEL + lift:.2f} F{config.Z_SPEED}",   # clear of the paper
        f"G0 X{getattr(config, 'PARK_X', 15.0):.3f} "
        f"Y{getattr(config, 'PARK_Y', 200.0):.3f} F{config.FEED_TRAVEL}",
    ]


def generate_gcode(
    polylines: List[List[Tuple[float, float]]],
    config,
    style_name: str = "contour",
    include_footer: bool = True,
    include_home: bool | None = None,
) -> List[str]:
    """
    Convert ordered polylines to Marlin G-code (Creality Ender 3).
    Drawing is centered on the bed using BED_OFFSET_X/Y.

    include_footer=False returns the program body only, so the caller can append
    overlay lines followed by gcode_footer(config).

    include_home defaults to config.HOME_ON_START. Pass False for a program that
    will be streamed to an already-homed machine — the server homes once per
    session rather than once per drawing. A program with homing omitted assumes
    Z is already referenced; there is no G92 fallback that invents a datum.
    """
    if include_home is None:
        include_home = getattr(config, "HOME_ON_START", True)
    size   = config.PROCESS_SIZE
    dw     = config.DRAW_WIDTH
    dh     = config.DRAW_HEIGHT
    ox     = config.BED_OFFSET_X
    oy     = config.BED_OFFSET_Y
    z_draw = config.Z_DRAW
    z_trav = config.Z_TRAVEL
    z_spd  = config.Z_SPEED
    f_draw = config.FEED_DRAW
    f_trav = config.FEED_TRAVEL
    flip_y = config.FLIP_Y

    lines = [
        f"; Image2Drawing — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"; Style: {style_name} | Paths: {len(polylines)}",
        f"; Draw area: {dw}x{dh}mm offset ({ox},{oy}) on {config.BED_WIDTH}x{config.BED_HEIGHT}mm bed",
        f"; Pen: draw Z{z_draw} / travel Z{z_trav}",
        "G21",        # mm
        "G90",        # absolute
    ]

    if include_home:
        lines += gcode_home(config)

    lines += [
        f"G0 Z{z_trav:.2f} F{z_spd}",   # pen up before the first traverse
        f"G0 X{ox:.3f} Y{oy:.3f} F{f_trav}",
        "",
    ]

    for poly in polylines:
        if len(poly) < 2:
            continue
        gx, gy = pixel_to_mm(poly[0], size, dw, dh, ox, oy, flip_y)
        lines.append(f"G0 X{gx:.3f} Y{gy:.3f} F{f_trav}")
        lines.append(f"G1 Z{z_draw:.2f} F{z_spd}")
        for pt in poly[1:]:
            gx, gy = pixel_to_mm(pt, size, dw, dh, ox, oy, flip_y)
            lines.append(f"G1 X{gx:.3f} Y{gy:.3f} F{f_draw}")
        lines.append(f"G0 Z{z_trav:.2f} F{z_spd}")

    if include_footer:
        lines += gcode_footer(config)
    return lines


def draw_mm_polylines(
    mm_polylines: List[List[Tuple[float, float]]],
    config,
) -> List[str]:
    """
    Generate G-code for polylines already in absolute mm coordinates.
    No pixel→mm conversion; used for text/signature overlays.
    Respects FLIP_Y: mirrors Y around the bed centre when enabled.
    """
    z_draw = config.Z_DRAW
    z_trav = config.Z_TRAVEL
    z_spd  = config.Z_SPEED
    f_draw = config.FEED_DRAW
    f_trav = config.FEED_TRAVEL
    flip_y = config.FLIP_Y
    dh     = config.DRAW_HEIGHT
    oy     = config.BED_OFFSET_Y

    def _y(y: float) -> float:
        return (dh + 2 * oy) - y if flip_y else y

    lines = []
    for poly in mm_polylines:
        if len(poly) < 2:
            continue
        gx, gy = poly[0][0], _y(poly[0][1])
        lines.append(f"G0 X{gx:.3f} Y{gy:.3f} F{f_trav}")
        lines.append(f"G1 Z{z_draw:.2f} F{z_spd}")
        for x, y in poly[1:]:
            lines.append(f"G1 X{x:.3f} Y{_y(y):.3f} F{f_draw}")
        lines.append(f"G0 Z{z_trav:.2f} F{z_spd}")
    return lines


def estimate_draw_time(lines: List[str], config) -> int:
    """Rough wall-clock estimate in seconds, from XY travel distance / feed rate.

    Ignores Z moves and acceleration, so real prints run somewhat longer.
    """
    total_sec = 0.0
    cx, cy = 0.0, 0.0
    current_feed = config.FEED_TRAVEL

    for line in lines:
        line = line.split(';')[0].strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0].upper()
        if cmd not in ('G0', 'G1'):
            continue

        x_m = _RE_X.search(line)
        y_m = _RE_Y.search(line)
        f_m = _RE_F.search(line)

        if f_m:
            current_feed = float(f_m.group(1))

        nx = float(x_m.group(1)) if x_m else cx
        ny = float(y_m.group(1)) if y_m else cy

        dist = math.sqrt((nx - cx) ** 2 + (ny - cy) ** 2)
        if current_feed > 0:
            total_sec += (dist / current_feed) * 60.0

        cx, cy = nx, ny

    return int(total_sec)

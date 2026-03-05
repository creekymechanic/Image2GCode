from datetime import datetime
from typing import List, Tuple
import math


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


def generate_gcode(
    polylines: List[List[Tuple[float, float]]],
    config,
    style_name: str = "contour",
) -> List[str]:
    """
    Convert ordered polylines to Marlin G-code (Creality Ender 3).
    Drawing is centered on the bed using BED_OFFSET_X/Y.
    """
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
    home   = config.HOME_ON_START

    lines = [
        f"; Image2Drawing — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"; Style: {style_name} | Paths: {len(polylines)}",
        f"; Draw area: {dw}x{dh}mm offset ({ox},{oy}) on {config.BED_WIDTH}x{config.BED_HEIGHT}mm bed",
        "G21",        # mm
        "G90",        # absolute
    ]

    if home:
        lines += [
            "G28 X Y",    # home X and Y (Marlin supports axis args)
            f"G0 Z{z_trav:.2f} F{z_spd}",
        ]
    else:
        lines.append(f"G0 Z{z_trav:.2f} F{z_spd}")

    lines += [
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

    lines += [
        "",
        f"G0 Z{z_trav:.2f} F{z_spd}",
        f"G0 X{ox:.3f} Y{oy:.3f} F{f_trav}",
        "M84",        # disable steppers
    ]
    return lines


def estimate_draw_time(lines: List[str], config) -> int:
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

        import re
        x_m = re.search(r'X([-\d.]+)', line)
        y_m = re.search(r'Y([-\d.]+)', line)
        f_m = re.search(r'F([-\d.]+)', line)

        if f_m:
            current_feed = float(f_m.group(1))

        nx = float(x_m.group(1)) if x_m else cx
        ny = float(y_m.group(1)) if y_m else cy

        dist = math.sqrt((nx - cx) ** 2 + (ny - cy) ** 2)
        if current_feed > 0:
            total_sec += (dist / current_feed) * 60.0

        cx, cy = nx, ny

    return int(total_sec)

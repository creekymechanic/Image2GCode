import numpy as np
from typing import List, Tuple


def extract_hatching(
    gray: np.ndarray,
    config,
    params: dict = None
) -> List[List[Tuple[float, float]]]:
    """
    Fill dark areas with diagonal hatch lines.
    Zone 0 (bright >180): no lines
    Zone 1 (mid 80-180): 45° hatch
    Zone 2 (dark <80): 45° + 135° crosshatch
    """
    params = params or {}
    spacing = params.get("hatch_spacing", config.HATCH_SPACING)

    h, w = gray.shape
    # Wider zones = fewer lines overall; zone2 only catches the darkest regions
    zone1 = (gray >= 100) & (gray <= 200)
    zone2 = gray < 100

    polylines = []

    # 45° lines: diagonals where (x + y) % spacing == 0
    # 135° lines: diagonals where (x - y) % spacing == 0
    for angle in (45, 135):
        if angle == 45:
            mask = zone1 | zone2
        else:
            mask = zone2  # only crosshatch in darkest zone

        ys, xs = np.where(mask)
        if len(xs) == 0:
            continue

        if angle == 45:
            diag_vals = (xs + ys) % spacing == 0
        else:
            diag_vals = (xs - ys) % spacing == 0

        # Keep only pixels on the hatch lines
        on_line = mask.copy()
        line_mask = np.zeros_like(mask, dtype=bool)
        if angle == 45:
            coords = xs + ys
        else:
            coords = xs - ys + w  # shift to positive

        hit = (coords % spacing) == 0
        ys_h = ys[hit]
        xs_h = xs[hit]

        if len(xs_h) == 0:
            continue

        # Group by diagonal index, extract contiguous runs as polylines
        if angle == 45:
            diag_idx = xs_h + ys_h
        else:
            diag_idx = xs_h - ys_h

        order = np.argsort(diag_idx * (w + h) + xs_h)
        ys_h = ys_h[order]
        xs_h = xs_h[order]
        diag_idx = diag_idx[order]

        i = 0
        while i < len(xs_h):
            d = diag_idx[i]
            # Collect all pixels on this diagonal
            j = i
            while j < len(xs_h) and diag_idx[j] == d:
                j += 1
            seg_x = xs_h[i:j]
            seg_y = ys_h[i:j]
            # Sort by x within diagonal
            sort_order = np.argsort(seg_x)
            seg_x = seg_x[sort_order]
            seg_y = seg_y[sort_order]
            # Split into contiguous runs (gap > 1 pixel breaks segment)
            run_start = 0
            for k in range(1, len(seg_x)):
                if seg_x[k] - seg_x[k - 1] > 2:
                    pts = list(zip(seg_x[run_start:k].tolist(),
                                   seg_y[run_start:k].tolist()))
                    if len(pts) >= 2:
                        polylines.append([(float(x), float(y)) for x, y in pts])
                    run_start = k
            pts = list(zip(seg_x[run_start:].tolist(),
                           seg_y[run_start:].tolist()))
            if len(pts) >= 2:
                polylines.append([(float(x), float(y)) for x, y in pts])
            i = j

    return polylines

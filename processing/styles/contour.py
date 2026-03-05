import cv2
import numpy as np
from typing import List, Tuple


def extract_contour(
    gray: np.ndarray,
    config,
    params: dict = None,
) -> List[List[Tuple[float, float]]]:
    """
    Topographic contour lines at evenly-spaced brightness levels.

    Tuning guide
    ------------
    levels     : more levels = more lines = more detail and longer draw time
    blur       : larger = smoother contours, ignores fine texture
    min_arc    : higher = shorter/noisier paths are removed
    epsilon    : lower = more points per curve (more faithful but more G-code)
    level_min  : raise to ignore deep shadows (e.g. background remnants)
    level_max  : lower to ignore bright highlights
    """
    params = params or {}

    n_levels  = int(params.get("contour_levels",  getattr(config, "CONTOUR_LEVELS",    8)))
    blur      = int(params.get("contour_blur",     getattr(config, "CONTOUR_BLUR",      9)))
    min_arc   = float(params.get("contour_min_arc", getattr(config, "CONTOUR_MIN_ARC", 30.0)))
    epsilon   = float(params.get("contour_epsilon", getattr(config, "CONTOUR_EPSILON",  3.0)))
    lev_min   = float(params.get("contour_level_min", getattr(config, "CONTOUR_LEVEL_MIN", 20)))
    lev_max   = float(params.get("contour_level_max", getattr(config, "CONTOUR_LEVEL_MAX", 235)))

    # Blur must be odd
    if blur % 2 == 0:
        blur += 1

    blurred = cv2.GaussianBlur(gray, (blur, blur), 0)
    levels = np.linspace(lev_min, lev_max, n_levels)

    polylines = []
    for t in levels:
        binary = (blurred > t).astype(np.uint8) * 255
        contours, _ = cv2.findContours(
            binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_TC89_KCOS
        )
        for contour in contours:
            if cv2.arcLength(contour, closed=True) < min_arc:
                continue
            approx = cv2.approxPolyDP(contour, epsilon=epsilon, closed=True)
            pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
            if len(pts) >= 2:
                pts.append(pts[0])  # close the loop
                polylines.append(pts)

    return polylines

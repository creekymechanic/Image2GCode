import numpy as np
from typing import List, Tuple


def extract_stipple(
    gray: np.ndarray,
    config,
    params: dict = None
) -> List[List[Tuple[float, float]]]:
    """
    Probability-weighted random dot placement.
    Dark areas get more dots. Each dot is a short angled stroke (~3px)
    so it's visible in the SVG preview and large enough for the printer to draw.
    """
    params = params or {}
    n_dots = params.get("stipple_dots", config.STIPPLE_DOTS)
    stroke_len = params.get("stipple_stroke", 3.0)  # px

    inverted = 255 - gray.astype(np.float64)

    # Apply power curve to push dark/light contrast harder.
    # After histogram equalization the image can be very flat;
    # squaring the inverted values makes dark areas dramatically more likely.
    inverted = inverted ** 2

    total = inverted.sum()
    if total == 0:
        return []

    p = inverted.flatten() / total

    n_nonzero = int(np.count_nonzero(p))
    n_dots = min(n_dots, n_nonzero)

    indices = np.random.choice(len(p), size=n_dots, replace=False, p=p)
    ys, xs = np.unravel_index(indices, gray.shape)

    # Each dot is a short stroke at a random angle so they read as marks, not pixels
    rng = np.random.default_rng()
    angles = rng.uniform(0, np.pi, size=n_dots)
    dx = np.cos(angles) * stroke_len / 2
    dy = np.sin(angles) * stroke_len / 2

    polylines = [
        [(float(x - ddx), float(y - ddy)), (float(x + ddx), float(y + ddy))]
        for x, y, ddx, ddy in zip(xs, ys, dx, dy)
    ]
    return polylines

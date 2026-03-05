import cv2
import numpy as np
from typing import List, Tuple


def _xdog(gray: np.ndarray, sigma: float, k: float, tau: float, phi: float, epsilon: float) -> np.ndarray:
    """
    Extended Difference of Gaussians (XDoG).

    Produces clean, artistic sketch lines from a portrait by:
    1. Subtracting two Gaussian blurs at different scales (DoG)
    2. Applying a soft threshold (the "extended" part)

    The result suppresses skin texture noise while keeping strong feature
    edges (eyes, nose, mouth outline, jaw, hair boundary).

    Parameters
    ----------
    sigma   : base blur radius. Larger = fewer fine details.
    k       : scale ratio between the two blurs (typically 1.6).
    tau     : weight of the wider blur subtraction (0.98–1.0).
              Values close to 1.0 produce tighter, cleaner lines.
    phi     : steepness of the soft threshold. Higher = harder edges.
    epsilon : threshold offset. Lower = more lines included.
    """
    f = gray.astype(np.float64) / 255.0

    # Two Gaussian blurs at sigma and sigma*k
    g1 = cv2.GaussianBlur(f, (0, 0), sigma)
    g2 = cv2.GaussianBlur(f, (0, 0), sigma * k)

    # Difference with tau weighting
    dog = g1 - tau * g2

    # Soft threshold: pixels above epsilon → white (background),
    # pixels below → dark edge via tanh
    result = np.where(dog >= epsilon, 1.0, 1.0 + np.tanh(phi * (dog - epsilon)))

    # Convert to uint8, invert so edges are dark on white
    result = np.clip(result * 255, 0, 255).astype(np.uint8)
    _, binary = cv2.threshold(result, 230, 255, cv2.THRESH_BINARY_INV)
    return binary


def extract_lineart(
    gray: np.ndarray,
    config,
    params: dict = None
) -> List[List[Tuple[float, float]]]:
    """
    XDoG-based line extraction tuned for human faces.

    Pipeline:
      1. Multiple bilateral filter passes — flattens skin texture while
         preserving sharp edges at facial features
      2. XDoG — extracts coherent sketch-like contour lines
      3. Morphological closing — connects small gaps in lines
      4. findContours + approxPolyDP — converts to drawable polylines
    """
    params = params or {}

    # XDoG tuning params (can be overridden via request params)
    sigma   = params.get("xdog_sigma",   1.2)   # base blur; increase for fewer fine lines
    k       = params.get("xdog_k",       1.6)   # scale ratio; keep ~1.6
    tau     = params.get("xdog_tau",     0.99)  # subtraction weight; closer to 1 = cleaner lines
    phi     = params.get("xdog_phi",     15.0)  # edge sharpness; higher = harder threshold
    epsilon = params.get("xdog_epsilon", 0.02)  # threshold level; lower = more lines

    # ── Step 1: flatten skin texture ──────────────────────────────────────
    # Three passes of bilateral filter. Each pass further smooths flat
    # regions (skin, uniform backgrounds) without blurring sharp transitions
    # (eye edges, lip lines, nostril shadows, hair outline).
    smoothed = gray.copy()
    for _ in range(3):
        smoothed = cv2.bilateralFilter(smoothed, d=9, sigmaColor=60, sigmaSpace=60)

    # ── Step 2: XDoG edge extraction ──────────────────────────────────────
    edges = _xdog(smoothed, sigma, k, tau, phi, epsilon)

    # ── Step 3: connect broken line fragments ──────────────────────────────
    # Closing (dilate then erode) bridges small gaps that bilateral smoothing
    # and XDoG can leave at corners and fine features like eyelashes
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    # ── Step 4: contour extraction ────────────────────────────────────────
    contours, _ = cv2.findContours(
        edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_TC89_KCOS
    )

    # Face portraits typically produce 100–400 meaningful contours.
    # Filter short noise contours aggressively; a path shorter than ~12px
    # at 512px resolution maps to < 2.4mm on the bed — too short to draw cleanly.
    min_len = params.get("min_arc_length", 12)
    epsilon_approx = params.get("approx_epsilon", 1.2)

    polylines = []
    for contour in contours:
        if cv2.arcLength(contour, closed=False) < min_len:
            continue
        approx = cv2.approxPolyDP(contour, epsilon=epsilon_approx, closed=False)
        pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(pts) >= 2:
            polylines.append(pts)

    return polylines

"""Render text to plotter polylines via PIL rasterisation + OpenCV contour tracing."""
import os
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PX_PER_MM = 25  # rasterisation density — higher = smoother curves, slower

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# ── Font candidates (first existing file wins) ────────────────────────────────
FONTS_SANS = [
    r"C:\Windows\Fonts\calibrib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

FONTS_SCRIPT = [
    r"C:\Windows\Fonts\SEGOESC.TTF",
    r"C:\Windows\Fonts\KUNSTLER.TTF",
    r"C:\Windows\Fonts\BRUSHSCI.TTF",
    r"C:\Windows\Fonts\segoepr.ttf",
    r"C:\Windows\Fonts\comic.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]

# Cedarville Cursive — single-line handwriting feel, for signature use
FONTS_SIGNATURE = [
    os.path.join(_FONTS_DIR, "CedarvilleCursive-Regular.ttf"),
    r"C:\Windows\Fonts\SEGOESC.TTF",
    r"C:\Windows\Fonts\KUNSTLER.TTF",
]


def _find_font(candidates: list) -> str | None:
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _zhang_suen(img: np.ndarray) -> np.ndarray:
    """
    Vectorized Zhang-Suen thinning. Reduces filled letter forms to ~1px-wide
    centerline strokes so the plotter draws single strokes instead of outlines.
    Input/output: uint8 binary image (0 or 255).
    """
    px = (img > 127).astype(np.uint8)
    changed = True
    while changed:
        changed = False
        for step in range(2):
            # Pad once so slices are safe at borders
            p = np.pad(px, 1, constant_values=0)
            P2 = p[:-2, 1:-1]   # N
            P3 = p[:-2, 2:]     # NE
            P4 = p[1:-1, 2:]    # E
            P5 = p[2:,  2:]     # SE
            P6 = p[2:,  1:-1]   # S
            P7 = p[2:,  :-2]    # SW
            P8 = p[1:-1, :-2]   # W
            P9 = p[:-2, :-2]    # NW

            B = P2 + P3 + P4 + P5 + P6 + P7 + P8 + P9  # neighbour count

            # 0→1 transitions in cyclic order P2…P9,P2
            seq = np.stack([P2, P3, P4, P5, P6, P7, P8, P9, P2])
            A = np.sum((seq[:-1] == 0) & (seq[1:] == 1), axis=0)

            cond = (px == 1) & (B >= 2) & (B <= 6) & (A == 1)
            if step == 0:
                cond &= (P2 * P4 * P6 == 0) & (P4 * P6 * P8 == 0)
            else:
                cond &= (P2 * P4 * P8 == 0) & (P2 * P6 * P8 == 0)

            if cond.any():
                px[cond] = 0
                changed = True

    return px * 255


def text_to_polylines_mm(
    text: str,
    x_mm: float,
    y_mm: float,
    height_mm: float,
    font_candidates: list,
    anchor: str = "topleft",
) -> List[List[Tuple[float, float]]]:
    """
    Rasterise *text* with PIL then trace its contours with OpenCV.
    Returns a list of closed polylines in absolute mm coordinates.

    anchor="topleft"  → (x_mm, y_mm) is the top-left corner of the text block
    anchor="topright" → x_mm is the right edge; text extends leftward
    """
    font_size_px = max(20, int(height_mm * PX_PER_MM))
    font_path = _find_font(font_candidates)

    # ── Render to a temporary PIL image ──────────────────────────────────────
    margin = font_size_px
    canvas_w = font_size_px * max(len(text), 1) * 2 + margin * 2
    canvas_h = font_size_px * 3
    img = Image.new("L", (canvas_w, canvas_h), 0)
    draw = ImageDraw.Draw(img)

    if font_path:
        try:
            font = ImageFont.truetype(font_path, font_size_px)
        except Exception:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()

    draw.text((margin, margin), text, fill=255, font=font)

    img_np = np.array(img)
    if img_np.max() == 0:
        return []

    # ── Tight-crop to actual ink bounds ──────────────────────────────────────
    rows_mask = np.any(img_np > 0, axis=1)
    cols_mask = np.any(img_np > 0, axis=0)
    r0, r1 = np.where(rows_mask)[0][[0, -1]]
    c0, c1 = np.where(cols_mask)[0][[0, -1]]
    cropped = img_np[r0:r1 + 1, c0:c1 + 1]
    text_w_mm = (c1 - c0) / PX_PER_MM

    # ── Trace contours ────────────────────────────────────────────────────────
    _, binary = cv2.threshold(cropped, 80, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    x_offset_mm = x_mm if anchor == "topleft" else x_mm - text_w_mm

    polylines: List[List[Tuple[float, float]]] = []
    for contour in contours:
        approx = cv2.approxPolyDP(contour, 0.7, closed=True)
        pts = approx.squeeze()
        if pts.ndim < 2 or len(pts) < 3:
            continue
        poly = [(x_offset_mm + px / PX_PER_MM, y_mm + py / PX_PER_MM) for px, py in pts]
        poly.append(poly[0])  # close the loop
        polylines.append(poly)

    return polylines


def signature_to_polylines_mm(
    text: str,
    x_mm: float,
    y_mm: float,
    height_mm: float,
    anchor: str = "topright",
) -> List[List[Tuple[float, float]]]:
    """
    Render a handwriting signature using Cedarville Cursive.

    Uses Zhang-Suen thinning to reduce filled letter forms to single centerline
    strokes, so the plotter draws each stroke once rather than tracing the
    outline of every filled glyph.  Polylines are sorted left-to-right to
    reproduce a natural left-to-right writing order.

    anchor="topright" → x_mm is the right edge of the text (right-aligned).
    anchor="topleft"  → x_mm is the left edge.
    """
    font_size_px = max(20, int(height_mm * PX_PER_MM))
    font_path = _find_font(FONTS_SIGNATURE)

    # ── Rasterise ─────────────────────────────────────────────────────────────
    margin = font_size_px
    canvas_w = font_size_px * max(len(text), 1) * 2 + margin * 2
    canvas_h = font_size_px * 3
    img = Image.new("L", (canvas_w, canvas_h), 0)
    draw = ImageDraw.Draw(img)

    if font_path:
        try:
            font = ImageFont.truetype(font_path, font_size_px)
        except Exception:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()

    draw.text((margin, margin), text, fill=255, font=font)

    img_np = np.array(img)
    if img_np.max() == 0:
        return []

    # ── Tight-crop ────────────────────────────────────────────────────────────
    rows_mask = np.any(img_np > 0, axis=1)
    cols_mask = np.any(img_np > 0, axis=0)
    r0, r1 = np.where(rows_mask)[0][[0, -1]]
    c0, c1 = np.where(cols_mask)[0][[0, -1]]
    cropped = img_np[r0:r1 + 1, c0:c1 + 1]
    text_w_mm = (c1 - c0) / PX_PER_MM

    # ── Thin to single-stroke centerlines ─────────────────────────────────────
    _, binary = cv2.threshold(cropped, 80, 255, cv2.THRESH_BINARY)
    thinned = _zhang_suen(binary)

    # One pixel of dilation restores connectivity broken by thinning and gives
    # findContours a stable 2px-wide path to trace cleanly.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    dilated = cv2.dilate(thinned, kernel, iterations=1)

    # ── Trace contours ────────────────────────────────────────────────────────
    contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    x_offset_mm = x_mm if anchor == "topleft" else x_mm - text_w_mm

    polylines: List[List[Tuple[float, float]]] = []
    for contour in contours:
        approx = cv2.approxPolyDP(contour, 0.5, closed=True)
        pts = approx.squeeze()
        if pts.ndim < 2 or len(pts) < 2:
            continue
        poly = [(x_offset_mm + float(px) / PX_PER_MM, y_mm + float(py) / PX_PER_MM)
                for px, py in pts]
        poly.append(poly[0])
        polylines.append(poly)

    # Sort left-to-right: draw leftmost strokes first, like a human writing
    polylines.sort(key=lambda poly: min(p[0] for p in poly))

    return polylines

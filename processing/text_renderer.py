"""Render text to plotter polylines via PIL rasterisation + OpenCV contour tracing."""
import os
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PX_PER_MM = 25  # rasterisation density — higher = smoother curves, slower

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


def _find_font(candidates: list) -> str | None:
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


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

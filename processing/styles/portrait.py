import cv2
import numpy as np
import os
import urllib.request
from typing import List, Tuple
from scipy.interpolate import splprep, splev
from .lineart import extract_lineart

# ── MediaPipe face landmarker model ─────────────────────────────────────────
_MODEL_URL  = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "models", "face_landmarker.task")
_landmarker = None

def _get_landmarker():
    global _landmarker
    if _landmarker is not None:
        return _landmarker
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
    if not os.path.exists(_MODEL_PATH):
        print("Downloading face landmarker model (~2.5MB)...")
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=_MODEL_PATH),
        num_faces=1,
        min_face_detection_confidence=0.4,
        min_face_presence_confidence=0.4,
    )
    _landmarker = mp_vision.FaceLandmarker.create_from_options(options)
    return _landmarker


# ── Ordered landmark index paths for artistic portrait features ───────────────
# These trace each feature as a natural curve rather than a mesh wireframe.
# Indices from: developers.google.com/mediapipe/solutions/vision/face_landmarker

# Eye upper lids (inner→outer), lower lids (outer→inner) — drawn as two arcs
L_EYE_UPPER = [133, 173, 157, 158, 159, 160, 161, 246, 33]
L_EYE_LOWER = [33,   7, 163, 144, 145, 153, 154, 155, 133]
R_EYE_UPPER = [362, 398, 384, 385, 386, 387, 388, 466, 263]
R_EYE_LOWER = [263, 249, 390, 373, 374, 380, 381, 382, 362]

# Eyebrows: single arc from inner to outer end
L_EYEBROW = [107, 66, 105, 63, 70, 156,  35, 124,  46,  53,  52,  65,  55]
R_EYEBROW = [336,296, 334,293,300, 383, 265, 353, 276, 283, 282, 295, 285]

# Nose: bridge line + two nostril arcs
NOSE_BRIDGE   = [168, 6, 197, 195, 5, 4]
NOSE_L_NOSTRIL = [4, 45, 220, 115, 48, 64, 98, 97]
NOSE_R_NOSTRIL = [4, 275, 440, 344, 278, 294, 327, 326]

# Lips: upper and lower as separate curves (more natural than full outline)
LIPS_UPPER_OUTER = [61, 185,  40,  39,  37,   0, 267, 269, 270, 409, 291]
LIPS_LOWER_OUTER = [61, 146,  91, 181,  84,  17, 314, 405, 321, 375, 291]
LIPS_UPPER_INNER = [78, 191,  80,  81,  82,  13, 312, 311, 310, 415, 308]
LIPS_LOWER_INNER = [78,  95,  88, 178,  87,  14, 317, 402, 318, 324, 308]


# ── Smooth spline fitting ────────────────────────────────────────────────────

def _smooth(pts: List[Tuple[float, float]],
            n_out: int = 60,
            closed: bool = False) -> List[Tuple[float, float]]:
    """Fit a cubic spline through pts and resample to n_out evenly-spaced points."""
    if len(pts) < 4:
        return pts
    x = np.array([p[0] for p in pts], dtype=float)
    y = np.array([p[1] for p in pts], dtype=float)
    # Remove duplicate consecutive points (splprep will fail otherwise)
    mask = np.ones(len(x), dtype=bool)
    for i in range(1, len(x)):
        if abs(x[i] - x[i-1]) < 0.5 and abs(y[i] - y[i-1]) < 0.5:
            mask[i] = False
    x, y = x[mask], y[mask]
    if len(x) < 4:
        return pts
    try:
        k = min(3, len(x) - 1)
        tck, _ = splprep([x, y], s=len(x) * 0.5, per=closed, k=k)
        u_new = np.linspace(0, 1, n_out)
        xn, yn = splev(u_new, tck)
        result = list(zip(xn.tolist(), yn.tolist()))
        if closed and result:
            result.append(result[0])
        return result
    except Exception:
        return pts


def _lm_pts(lm, indices, h, w) -> List[Tuple[float, float]]:
    """Convert a list of landmark indices to pixel (x, y) coords."""
    return [
        (float(lm[i].x * w), float(lm[i].y * h))
        for i in indices if i < len(lm)
    ]


# ── Head/hair silhouette from rembg white-background image ───────────────────

def _head_outline(bgr: np.ndarray, epsilon: float = 3.0) -> List[Tuple[float, float]]:
    """
    Extract the head+hair silhouette from a white-background BGR image.
    rembg already placed the subject on white; threshold to find non-white,
    take the largest contour, and smooth it.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # Pixels that are NOT near-white are part of the subject
    _, mask = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)
    # Morphological close to fill small holes (nostrils, gaps in hair)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_TC89_KCOS)
    if not contours:
        return []
    biggest = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(biggest, epsilon=epsilon, closed=True)
    pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
    if len(pts) > 4:
        pts = _smooth(pts, n_out=80, closed=True)
    return pts


# ── Main style function ──────────────────────────────────────────────────────

def extract_portrait(
    gray: np.ndarray,
    bgr: np.ndarray,
    config,
    params: dict = None,
) -> List[List[Tuple[float, float]]]:
    """
    Artistic portrait extraction combining:
      - Head/hair silhouette from rembg white-background image
      - Facial feature curves from MediaPipe Face Landmarker
      - Smooth spline fitting on all paths
    Falls back to XDoG if no face is detected.
    """
    params = params or {}
    h, w = gray.shape

    # ── Head silhouette ──────────────────────────────────────────────────────
    polylines = []
    if getattr(config, 'REMOVE_BG', False):
        outline = _head_outline(bgr)
        if outline:
            polylines.append(outline)

    # ── Face landmarks ───────────────────────────────────────────────────────
    try:
        landmarker = _get_landmarker()
    except Exception as e:
        print(f"MediaPipe unavailable ({e}), falling back to XDoG")
        return extract_lineart(gray, config, params)

    import mediapipe as mp
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_image)

    if not result.face_landmarks:
        # No face — return just the head outline (if any) + XDoG fallback
        if polylines:
            return polylines
        return extract_lineart(gray, config, params)

    lm = result.face_landmarks[0]

    # Eyes: upper lid + lower lid as two separate arcs per eye
    for indices in [L_EYE_UPPER, L_EYE_LOWER, R_EYE_UPPER, R_EYE_LOWER]:
        pts = _smooth(_lm_pts(lm, indices, h, w), n_out=30)
        if len(pts) >= 2:
            polylines.append(pts)

    # Eyebrows: single smooth arc
    for indices in [L_EYEBROW, R_EYEBROW]:
        pts = _smooth(_lm_pts(lm, indices, h, w), n_out=25)
        if len(pts) >= 2:
            polylines.append(pts)

    # Nose bridge
    pts = _smooth(_lm_pts(lm, NOSE_BRIDGE, h, w), n_out=15)
    if len(pts) >= 2:
        polylines.append(pts)

    # Nostril arcs
    for indices in [NOSE_L_NOSTRIL, NOSE_R_NOSTRIL]:
        pts = _smooth(_lm_pts(lm, indices, h, w), n_out=15)
        if len(pts) >= 2:
            polylines.append(pts)

    # Lips: four curves (upper/lower × outer/inner)
    for indices in [LIPS_UPPER_OUTER, LIPS_LOWER_OUTER,
                    LIPS_UPPER_INNER, LIPS_LOWER_INNER]:
        pts = _smooth(_lm_pts(lm, indices, h, w), n_out=30)
        if len(pts) >= 2:
            polylines.append(pts)

    # Irises (if refined landmarks available — index 468+ only present with
    # refine_landmarks, which FaceLandmarkerOptions doesn't expose directly;
    # skip silently if not present)
    if params.get("draw_irises", True) and len(lm) > 476:
        for iris_indices in [
            [468, 469, 470, 471, 468],   # left iris loop
            [473, 474, 475, 476, 473],   # right iris loop
        ]:
            pts = _lm_pts(lm, iris_indices, h, w)
            if len(pts) >= 2:
                polylines.append(pts)

    return polylines

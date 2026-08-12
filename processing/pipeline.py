import numpy as np
from .image_utils import preprocess, extract_silhouette
from .styles.lineart import extract_lineart
from .styles.hatching import extract_hatching
from .styles.stipple import extract_stipple
from .styles.contour import extract_contour
from .styles.portrait import extract_portrait

STYLE_MAP = {
    "lineart":  extract_lineart,
    "hatching": extract_hatching,
    "stipple":  extract_stipple,
    "contour":  extract_contour,
    "portrait": extract_portrait,
}

# Styles that need the processed color image (not just gray)
COLOR_STYLES = {"portrait"}


def apply_style(gray: np.ndarray, bgr_processed: np.ndarray, style_name: str,
                config, params: dict = None):
    """
    Run a single style extractor over an already-preprocessed image.

    Split out from run_pipeline so callers that need several variants of the
    same photo (e.g. the four contour presets) can preprocess once — background
    removal is by far the most expensive step and its result is style-agnostic.
    """
    if style_name not in STYLE_MAP:
        raise ValueError(f"Unknown style '{style_name}'. Valid: {list(STYLE_MAP)}")

    style_fn = STYLE_MAP[style_name]
    if style_name in COLOR_STYLES:
        return style_fn(gray, bgr_processed, config, params or {})
    return style_fn(gray, config, params or {})


def build_outline(bgr_processed: np.ndarray, config):
    """
    Border frame matching the drawing area, plus the subject silhouette when
    background removal is on. Returns [] unless config.OUTLINE is enabled.
    """
    if not getattr(config, 'OUTLINE', False):
        return []

    size = float(config.PROCESS_SIZE)
    border = [(1.0, 1.0), (size - 1.0, 1.0), (size - 1.0, size - 1.0),
              (1.0, size - 1.0), (1.0, 1.0)]
    extras = [border]

    # Subject silhouette — only available after background removal
    if getattr(config, 'REMOVE_BG', False):
        silhouette = extract_silhouette(bgr_processed)
        if silhouette:
            extras.append(silhouette)

    return extras


def run_pipeline(bgr_image: np.ndarray, style_name: str, config, params: dict = None):
    """
    Full pipeline: BGR image → list of polylines in pixel coords.
    """
    gray, bgr_processed = preprocess(
        bgr_image, config.PROCESS_SIZE,
        remove_bg=getattr(config, 'REMOVE_BG', False)
    )
    polylines = apply_style(gray, bgr_processed, style_name, config, params)
    return build_outline(bgr_processed, config) + polylines

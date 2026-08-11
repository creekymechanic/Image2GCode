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


def run_pipeline(bgr_image: np.ndarray, style_name: str, config, params: dict = None):
    """
    Full pipeline: BGR image → list of polylines in pixel coords.
    """
    if style_name not in STYLE_MAP:
        raise ValueError(f"Unknown style '{style_name}'. Valid: {list(STYLE_MAP)}")

    gray, bgr_processed = preprocess(
        bgr_image, config.PROCESS_SIZE,
        remove_bg=getattr(config, 'REMOVE_BG', False)
    )

    style_fn = STYLE_MAP[style_name]

    if style_name in COLOR_STYLES:
        polylines = style_fn(gray, bgr_processed, config, params or {})
    else:
        polylines = style_fn(gray, config, params or {})

    if getattr(config, 'OUTLINE', False):
        size = float(config.PROCESS_SIZE)

        # Square frame matching the drawing area (camera crop bounding box)
        border = [(1.0, 1.0), (size - 1.0, 1.0), (size - 1.0, size - 1.0),
                  (1.0, size - 1.0), (1.0, 1.0)]
        polylines = [border] + polylines

        # Subject silhouette — only available after background removal
        if getattr(config, 'REMOVE_BG', False):
            silhouette = extract_silhouette(bgr_processed)
            if silhouette:
                polylines.insert(1, silhouette)

    return polylines

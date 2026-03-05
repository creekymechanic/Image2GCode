import numpy as np
from .image_utils import preprocess
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

    return polylines

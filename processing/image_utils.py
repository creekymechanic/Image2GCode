import cv2
import numpy as np

# Lazy-load rembg so the app still works if it's not installed
_rembg_remove = None

def _get_rembg():
    global _rembg_remove
    if _rembg_remove is None:
        from rembg import remove
        _rembg_remove = remove
    return _rembg_remove


def remove_background(bgr: np.ndarray) -> np.ndarray:
    """
    Remove image background using rembg (U2Net model).
    Returns a BGR image with the background replaced by white.
    The model downloads automatically on first call (~170MB).
    """
    from PIL import Image
    remove_fn = _get_rembg()

    pil_img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    rgba = remove_fn(pil_img)  # returns RGBA PIL image

    # Composite subject onto white background
    white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    white_bg.paste(rgba, mask=rgba.split()[3])  # use alpha as mask
    result_bgr = cv2.cvtColor(np.array(white_bg.convert("RGB")), cv2.COLOR_RGB2BGR)
    return result_bgr


def preprocess(bgr: np.ndarray, size: int, remove_bg: bool = False):
    """
    Prepare a BGR image for style processing.
    Returns (gray, bgr_processed):
      - gray: equalized uint8 grayscale, shape (size, size)
      - bgr_processed: color uint8 BGR after crop/resize/bg-removal, shape (size, size, 3)
    """
    # Mirror selfie so output matches what user sees on screen
    bgr = cv2.flip(bgr, 1)

    # Center-crop to square
    h, w = bgr.shape[:2]
    min_dim = min(h, w)
    y0 = (h - min_dim) // 2
    x0 = (w - min_dim) // 2
    bgr = bgr[y0:y0 + min_dim, x0:x0 + min_dim]

    # Resize to processing size
    bgr = cv2.resize(bgr, (size, size), interpolation=cv2.INTER_AREA)

    # Remove background — composite subject onto white before edge detection
    if remove_bg:
        bgr = remove_background(bgr)

    bgr_processed = bgr.copy()

    # Convert to grayscale
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Equalize histogram — improves edge detection on dim/uneven selfies
    gray = cv2.equalizeHist(gray)

    return gray, bgr_processed

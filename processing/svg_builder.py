from typing import List, Tuple


def build_svg(
    polylines: List[List[Tuple[float, float]]],
    size: int = 512,
    closed_loops: bool = False
) -> str:
    """
    Convert list of polylines (pixel coords) to an inline SVG string.
    """
    paths = []
    for poly in polylines:
        if len(poly) < 2:
            continue
        pts = " L ".join(f"{x:.1f},{y:.1f}" for x, y in poly)
        # Detect closed loops (first == last point)
        is_closed = (poly[0][0] == poly[-1][0] and poly[0][1] == poly[-1][1])
        d = f"M {pts}"
        if is_closed:
            d += " Z"
        paths.append(f'<path d="{d}"/>')

    body = "\n    ".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" width="300" height="300">\n'
        f'  <rect width="{size}" height="{size}" fill="white"/>\n'
        f'  <g stroke="black" stroke-width="1.5" fill="none" '
        f'stroke-linecap="round" stroke-linejoin="round">\n'
        f'    {body}\n'
        f'  </g>\n'
        f'</svg>'
    )

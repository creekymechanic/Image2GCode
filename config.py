# Serial connection
SERIAL_PORT = "COM6"
BAUD_RATE = 115200

# Printer: Creality Ender 3 (Marlin firmware)
# Bed is 235x235mm. We draw in a smaller centered area.
BED_WIDTH  = 235.0
BED_HEIGHT = 235.0

# Drawing area (mm) — centered on the bed
DRAW_WIDTH  = 100.0
DRAW_HEIGHT = 100.0

# Offset to center DRAW area on BED  (= (BED - DRAW) / 2)
BED_OFFSET_X = 67.5
BED_OFFSET_Y = 67.5

# Z axis pen control
Z_DRAW   = 0.0   # pen touching paper (tune physically; try -0.3 if too shallow)
Z_TRAVEL = 5.0   # pen lifted for travel
Z_SPEED  = 1000  # mm/min for Z moves (slow = more accurate pen placement)

# Feed rates (mm/min)
FEED_DRAW   = 3000
FEED_TRAVEL = 6000

# G-code options
HOME_ON_START = True   # G28 X Y before drawing — safe on Marlin/Ender 3
FLIP_Y = False         # set True if printed output is mirrored vertically

# Image processing
PROCESS_SIZE = 512
REMOVE_BG = True

# Contour style parameters
CONTOUR_LEVELS    = 8     # number of brightness levels (more = more lines)
CONTOUR_BLUR      = 9     # gaussian blur kernel size (odd number; more = smoother)
CONTOUR_MIN_ARC   = 30    # minimum path length in px (higher = less noise)
CONTOUR_EPSILON   = 3.0   # curve simplification (lower = more detail/points)
CONTOUR_LEVEL_MIN = 20    # darkest threshold (0-255; raise to ignore shadows)
CONTOUR_LEVEL_MAX = 235   # lightest threshold (0-255; lower to ignore highlights)

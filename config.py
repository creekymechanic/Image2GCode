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

# ── Z axis pen control ───────────────────────────────────────────────────────
# Z is an ABSOLUTE machine coordinate measured up from the Z endstop, so these
# survive a power cycle once the machine has homed. Find Z_DRAW with the guided
# setup in the dev panel rather than by measuring.
Z_DRAW   = 5.0    # pen just marking the paper
Z_TRAVEL = 10.0   # pen lifted clear for travel  (= Z_DRAW + PEN_LIFT)
Z_SPEED  = 1000   # mm/min for Z moves (slow = more accurate pen placement)
PEN_LIFT = 1.5    # how far above Z_DRAW to lift; enough to clear, small to stay quick
Z_MAX    = 240.0  # gantry travel limit, used to clamp calibration jogs

# Feed rates (mm/min)
FEED_DRAW   = 3000
FEED_TRAVEL = 6000

# ── Homing ───────────────────────────────────────────────────────────────────
# True  = home X, Y and Z automatically on the first print after startup, then
#         trust the coordinate system for the rest of the session.
# False = never home automatically; use the dev panel's Home button instead.
HOME_ON_START = True

# Z homes by descending until the endstop trips, so the pen touches down here
# and leaves a dot. Keep this clear of the paper and any bulldog clips.
Z_HOME_X = 10.0
Z_HOME_Y = 10.0

# Where the pen parks when a drawing finishes, and how far it lifts on the way
# so the paper can be lifted out without smudging.
PARK_X    = 15.0
PARK_Y    = 200.0
PARK_LIFT = 50.0

FLIP_Y = False         # set True if printed output is mirrored vertically

# Image processing
PROCESS_SIZE = 512
REMOVE_BG = True
OUTLINE = False    # draw a border frame + subject silhouette around the image

# Contour style parameters
CONTOUR_LEVELS    = 8     # number of brightness levels (more = more lines)
CONTOUR_BLUR      = 9     # gaussian blur kernel size (odd number; more = smoother)
CONTOUR_MIN_ARC   = 30    # minimum path length in px (higher = less noise)
CONTOUR_EPSILON   = 3.0   # curve simplification (lower = more detail/points)
CONTOUR_LEVEL_MIN = 20    # darkest threshold (0-255; raise to ignore shadows)
CONTOUR_LEVEL_MAX = 235   # lightest threshold (0-255; lower to ignore highlights)

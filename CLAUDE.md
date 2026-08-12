# CLAUDE.md

Guidance for working in this repo.

## What this is

A single-purpose Flask kiosk for an art installation ("Deus Ex Machina — Portrait
Engine", see `Artist_statement.txt`). Webcam selfie → contour-traced line drawing →
Marlin G-code → streamed over serial to a Creality Ender 3 with a pen where the
hotend goes. An offline LLM titles each piece; the machine signs it
"IsoChin Peucker" (a nod to Ramer–Douglas–Peucker).

**This drives real hardware.** A bad Z value or a mis-ordered park move drags a pen
across the bed. Changes to `gcode/generator.py` and the overlay splicing in
`app.py` deserve care.

## Run it

```bat
start.bat
```

Launches `ollama serve`, waits for it, activates `venv`, opens
<https://localhost:5000>, runs `app.py`. Manual equivalent:

```bat
venv\Scripts\activate.bat
python app.py
```

HTTPS is mandatory — `getUserMedia` refuses to hand over the camera on plain HTTP
from a non-localhost origin. `ssl_context="adhoc"` needs `cryptography`, so expect
a browser self-signed-cert warning.

## Architecture

```
templates/index.html + static/js/main.js   two views, one <video> re-parented between them
  └─ POST /process ───────────────────────────────────────────────────────────
       app.py            decode base64 → preprocess once → build variants
       processing/       preprocess (mirror, crop, resize, rembg, equalize)
                         apply_style() → styles/*.py → polylines in pixel coords
                         text_renderer  name label + thinned cursive signature
       gcode/            optimize_path_order → generate_gcode → mm coords
  └─ GET  /print ─────── SSE progress; serial_comm/printer.py blocks on Marlin "ok"
```

| Path | Role |
|---|---|
| `app.py` | Routes, the four contour presets, settings persistence, printer lifecycle |
| `config.py` | Defaults — **see the override note below** |
| `settings.json` | The live rig values; overrides `config.py` |
| `processing/pipeline.py` | `apply_style()` / `build_outline()` / `run_pipeline()` |
| `processing/styles/` | Five extractors; only `contour` is wired to the UI |
| `gcode/generator.py` | Pixel→mm mapping, program body, `gcode_footer()` |
| `gcode/optimizer.py` | Bidirectional nearest-neighbour ordering (KDTree above 500 paths) |
| `serial_comm/printer.py` | Serial transport, plus port scoring / `M115` probing / `autodetect_port` |

## Gotchas

**`settings.json` overrides `config.py` at import.** `_load_settings()` runs at
module level and writes every key onto the `config` module (uppercased). Reading
`config.py` tells you the defaults, *not* what the machine will do. Saving from the
dev panel writes back to `settings.json`. Check `settings.json` first when a value
seems wrong.

**Overlays must land before the park moves.** `generate_gcode(..., include_footer=False)`
returns the body; the caller appends the name label and signature, then
`gcode_footer(config)`. This ordering exists because the LLM-generated name isn't
known until after the drawing G-code is built. Do not reintroduce index slicing
(`gc[-4:]`) to split off the footer — if the footer length ever changes, the pen
draws during the 50mm clearance move.

**Z is the pen, not a hotend, and it is an absolute datum.** `Z_DRAW` (17.0 live)
is pen-on-paper, `Z_TRAVEL` (= `Z_DRAW + PEN_LIFT`) is lifted, both measured up
from the **Z endstop**. That only works because this rig has a *spring-loaded pen
mount*: the tip rides up as the gantry finishes descending to the endstop, so
`G28 Z` is survivable and Z=0 is a real, repeatable zero. Because the datum is
physical, `Z_DRAW` survives a power cycle and only needs finding once.

**With a rigid pen holder none of that holds** — `G28 Z` would drive the tip into
the bed. Such a rig must home XY only and set Z by hand each session. Check the
mount before touching `gcode_home`.

**Homing happens once per session, not per drawing.** `gcode_home()` homes XY
first (no Z motion, always safe), moves to `Z_HOME_X/Y` to get clear of the paper,
*then* homes Z — Z homing descends until the endstop trips, so the pen touches
down and leaves a dot wherever it happens to be. `_homed` guards it; it's cleared
in `_disconnect_printer()` because losing serial usually means the printer was
power-cycled and lost its own homing.

**There is no `G92` anywhere, deliberately.** The old `HOME_ON_START = false` path
emitted `G92 X15 Y200 Z{50+Z_TRAVEL}` and then descended 50mm, which required the
operator to have parked the pen exactly 51.5mm above the paper by eye. It was
removed. Do not reintroduce a `G92` to invent a datum — home, or require that the
machine was homed.

**Three places construct a program, and they differ in homing:**

| Caller | Homing | Why |
|---|---|---|
| `/process` (stored job) | none — `include_home=False` | the server homes once per session |
| `/print` | prepends `gcode_home()` if `not _homed` | streamed to a machine it manages |
| `/gcode/<id>` download | inserts `gcode_home()` after `G90` | standalone file, no server to help it |

The download inserts at the `G90` marker rather than a fixed index, for the same
reason the footer is a function and not `gc[-4:]`.

**The image is mirrored twice.** `preprocess()` flips the frame horizontally so the
subject sees themselves as in a mirror; the frontend then applies
`transform: scaleX(-1)` to the SVG preview. Drop one flip and the drawn output is
reversed relative to the preview.

**Contour params: public sends `{}`, dev overrides everything.** The stage sends no
params so the server's four presets stay distinct (levels 5/7/8/9). Dev mode merges
the tuning fields over *all four* presets, collapsing them — that's intended, since
dev mode is for dialling in a single look.

**Only `contour` is reachable from the UI.** `lineart`, `hatching`, `stipple`, and
`portrait` still work via `run_pipeline()` / a hand-rolled `/process` call, but
nothing in the frontend selects them. `portrait` needs MediaPipe and
`models/face_landmarker.task` (committed deliberately, so the kiosk works offline).

**Names degrade gracefully.** `processing/name_generator.py` calls Ollama
(`llama3.2:1b`) at `localhost:11434`; if it's down you get a word from
`_FALLBACK_NAMES`, not an error. So "the names look repetitive" usually means
Ollama isn't running.

**Serial is one shared blocking connection.** `Printer.send_line` writes a line and
blocks until Marlin answers `ok`, so `/print` streams at the printer's pace. The
connection is a lazily-created singleton, torn down on serial error, on config
change, and on the browser's `beforeunload` beacon to `/disconnect`.

**Opening a serial port reboots the printer.** Toggling DTR resets most Marlin
boards, which is why port detection is split in two and why the expensive half is
never automatic:

| Tier | Where | Cost | Certainty |
|---|---|---|---|
| `score_port` / `port_candidates` | `/status` poll, `_resolve_port()` | free, opens nothing | a guess — cannot tell a printer from any other CH340 device |
| `probe_port` (`M115` handshake) | `/detect-port` only | ~3-6s per port, resets each board it touches | definitive |

`_print_active` (a `threading.Event`) is set for the duration of `/print`, and
`/detect-port` returns 409 while it's set. Do not add probing to any code path
that runs on a timer — `/status` is polled every 10 seconds.

`score_port` is a pure function, deliberately free of pyserial, so detection
ranking can be tested without the venv or any hardware attached.

## Verifying changes

There are no tests, and the app's dependencies live in `venv/` (not committed).
`gcode/generator.py` and `processing/svg_builder.py` are **stdlib-only**, so the
riskiest logic can be exercised without installing anything:

```bash
python -m py_compile app.py config.py gcode/*.py processing/*.py processing/styles/*.py serial_comm/*.py
node --check static/js/main.js
```

```python
import config
from gcode.generator import generate_gcode, gcode_footer
polys = [[(0,0),(100,0),(100,100),(0,100),(0,0)]]
body = generate_gcode(polys, config, "contour", include_footer=False)
prog = body + ["; -- label --"] + gcode_footer(config)
# assert no "G0 X..." rapid happens while Z == config.Z_DRAW
```

Anything touching `cv2`, `flask`, `numpy`, or `rembg` needs the venv. `app.py`
cannot be imported without it.

## Conventions

- Style extractors take `(gray, config, params)` and return polylines in **pixel**
  coords; `pixel_to_mm` converts at G-code time. Only `portrait` also takes `bgr`
  (listed in `COLOR_STYLES`).
- Read tunables as `params.get(k, getattr(config, "K", default))` so a missing
  config constant degrades instead of raising.
- Section comments use the `# ── Title ───` box-drawing style; match it.

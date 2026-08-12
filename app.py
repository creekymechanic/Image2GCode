import base64
import json
import os
import time
import uuid
import threading

import cv2
import numpy as np
from flask import Flask, request, jsonify, render_template, Response, stream_with_context

import config
from processing.pipeline import apply_style, build_outline
from processing.image_utils import preprocess
from processing.svg_builder import build_svg
from processing.text_renderer import text_to_polylines_mm, FONTS_SANS, signature_to_polylines_mm
from processing.name_generator import generate_name, warmup as warmup_models
from gcode.optimizer import optimize_path_order
from gcode.generator import (generate_gcode, gcode_home, gcode_footer,
                             draw_mm_polylines, estimate_draw_time)
from serial_comm.printer import (Printer, list_ports, port_candidates,
                                 autodetect_port)

app = Flask(__name__)

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

# Cap on retained jobs — this process runs for the length of an exhibition, so
# the store is trimmed oldest-first rather than growing without bound.
MAX_JOBS = 32

# ── Static signature G-code (pre-computed, cached per config state) ───────────
_sig_cache: dict = {}


def _draw_signature_continuous(mm_polylines: list, cfg) -> list:
    """
    Generate G-code for signature polylines without lifting the pen between
    strokes.  The pen lowers once at the first point, travels across all
    inter-stroke gaps with G1 (pen on paper), and lifts only at the very end —
    mimicking the continuous motion of a human writing a cursive signature.
    """
    z_draw = cfg.Z_DRAW
    z_trav = cfg.Z_TRAVEL
    z_spd  = cfg.Z_SPEED
    f_draw = cfg.FEED_DRAW
    f_trav = cfg.FEED_TRAVEL
    flip_y = cfg.FLIP_Y
    dh     = cfg.DRAW_HEIGHT
    oy     = cfg.BED_OFFSET_Y

    def _y(y: float) -> float:
        return (dh + 2 * oy) - y if flip_y else y

    if not mm_polylines:
        return []

    lines = []
    pen_down = False

    for poly in mm_polylines:
        if len(poly) < 2:
            continue
        gx, gy = poly[0][0], _y(poly[0][1])

        if not pen_down:
            # Travel to the first point with pen raised, then lower once
            lines.append(f"G0 X{gx:.3f} Y{gy:.3f} F{f_trav}")
            lines.append(f"G1 Z{z_draw:.2f} F{z_spd}")
            pen_down = True
        else:
            # Move to the next stroke with pen still on paper
            lines.append(f"G1 X{gx:.3f} Y{gy:.3f} F{f_trav}")

        for x, y in poly[1:]:
            lines.append(f"G1 X{x:.3f} Y{_y(y):.3f} F{f_draw}")

    # Lift pen once at the very end
    if pen_down:
        lines.append(f"G0 Z{z_trav:.2f} F{z_spd}")

    return lines


def _get_sig_gcode() -> list:
    """
    Return pre-computed G-code lines for the 'IsoChin Peucker' signature.
    Result is cached keyed by the config values that affect position/size.
    Regenerated automatically if config changes.
    """
    key = (config.BED_OFFSET_X, config.BED_OFFSET_Y,
           config.DRAW_WIDTH, config.DRAW_HEIGHT)
    if key not in _sig_cache:
        ox = config.BED_OFFSET_X
        oy = config.BED_OFFSET_Y
        polys = signature_to_polylines_mm(
            "IsoChin Peucker",
            x_mm=ox + config.DRAW_WIDTH,      # right-align to canvas edge
            y_mm=oy + config.DRAW_HEIGHT + 3,  # 3mm gap below canvas
            height_mm=6.0,                     # 6mm height
            anchor="topright",
        )
        _sig_cache[key] = _draw_signature_continuous(polys, config)
    return _sig_cache[key]

def _load_settings():
    """Apply saved settings.json overrides onto config module at startup."""
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
        for key, val in saved.items():
            attr = key.upper()
            if hasattr(config, attr):
                setattr(config, attr, val)
    except Exception:
        pass

def _save_settings(data: dict):
    """Persist a dict of config changes to settings.json."""
    existing = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing.update(data)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(existing, f, indent=2)

# ── Style presets (contour only) ─────────────────────────────────────────────
_STYLE_PRESETS = [
    # Fair — light/pale skin; histogram eq can over-sharpen so smooth slightly more
    {"label": "Fair",   "contour_levels": 5, "contour_blur": 7,  "contour_min_arc": 20, "contour_level_min": 35, "contour_level_max": 240},
    # Medium — balanced mid-tone; the closest to the classic Sharp look
    {"label": "Medium", "contour_levels": 7, "contour_blur": 5,  "contour_min_arc": 18, "contour_level_min": 15, "contour_level_max": 240},
    # Tan/Olive — warm mid-range; pull level_max down slightly to avoid highlight blowout
    {"label": "Tan",    "contour_levels": 8, "contour_blur": 5,  "contour_min_arc": 16, "contour_level_min": 12, "contour_level_max": 220},
    # Deep — darker skin; push into shadow range, more levels to recover fine detail
    {"label": "Deep",   "contour_levels": 9, "contour_blur": 4,  "contour_min_arc": 14, "contour_level_min":  8, "contour_level_max": 195},
]
_DEFAULT_PRESET = 1  # "Medium" selected by default

_load_settings()
warmup_models()  # pre-load Ollama models in background so first request is fast

# In-memory job store: job_id → {"gcode": [...], "presets": [...], "name": str}
job_store: dict[str, dict] = {}
job_store_lock = threading.Lock()


def _store_job(job_id: str, job: dict):
    """Save a job, evicting the oldest once MAX_JOBS is exceeded."""
    with job_store_lock:
        job_store[job_id] = job
        while len(job_store) > MAX_JOBS:
            job_store.pop(next(iter(job_store)))


# ── Persistent printer connection ─────────────────────────────────────────────
_printer: Printer | None = None
_printer_lock = threading.Lock()

# Set while G-code is streaming. Port probing reboots Marlin boards, so it must
# never run mid-print.
_print_active = threading.Event()

# Whether this session has established the machine's coordinate reference.
# Homing takes ~30s, so it happens once and every later drawing reuses the datum.
# Cleared on disconnect: losing serial usually means the printer was power-cycled,
# which drops its homing too.
_homed = False

# Absolute Z last commanded during pen-height calibration
_cal_z: float | None = None


def _resolve_port() -> str:
    """
    Decide which port to connect to.

    The configured port wins whenever it is actually present. If it has vanished
    — Windows renumbers COM ports freely between reboots — fall back to a lone
    unambiguous candidate. This check reads USB identity only and opens nothing,
    so it can never disturb another device; the real handshake lives behind the
    explicit /detect-port call.
    """
    configured = (config.SERIAL_PORT or "").strip()
    available = {p.upper() for p in list_ports()}
    if configured and configured.upper() in available:
        return configured

    strong = [c for c in port_candidates() if c["score"] >= 3]
    if len(strong) == 1:
        found = strong[0]
        print(f"[serial] {configured or 'no port configured'} unavailable — "
              f"falling back to {found['device']} ({found['chip']})")
        return found["device"]

    return configured


def _get_printer() -> Printer:
    """Return the shared Printer, connecting lazily on first use."""
    global _printer
    with _printer_lock:
        if _printer is None or not _printer.is_connected:
            _printer = Printer(_resolve_port(), config.BAUD_RATE)
        return _printer


def _disconnect_printer():
    global _printer, _homed, _cal_z
    with _printer_lock:
        if _printer is not None:
            _printer.close()
            _printer = None
        # A dropped connection usually means the printer was power-cycled, which
        # loses its homing — so the next job must re-establish the datum.
        _homed = False
        _cal_z = None


def _send(lines: list) -> None:
    """Send G-code lines to the printer, skipping comments and blanks."""
    printer = _get_printer()
    for line in lines:
        stripped = line.split(';')[0].strip()
        if stripped:
            printer.send_line(stripped)


def _ensure_homed() -> bool:
    """
    Home the machine unless this session already has. Returns True if it homed.

    Blocks for the duration — Marlin only answers 'ok' once the axes have found
    their endstops.
    """
    global _homed
    if _homed:
        return False
    _send(gcode_home(config))
    _homed = True
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    data = request.get_json()
    if not data or "image" not in data:
        return jsonify({"error": "Missing image data"}), 400

    style_name = data.get("style", "lineart")
    params = data.get("params", {})

    # Decode base64 JPEG from browser
    try:
        image_b64 = data["image"]
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(image_b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("Could not decode image")
    except Exception as e:
        return jsonify({"error": f"Image decode failed: {e}"}), 400

    # ── Preprocess once (bg removal is the expensive step) ───────────────────
    t0 = time.time()
    try:
        gray, bgr_processed = preprocess(
            bgr, config.PROCESS_SIZE,
            remove_bg=getattr(config, "REMOVE_BG", False),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    print(f"[timing] preprocess: {time.time()-t0:.2f}s")

    # ── Name generation (runs while we do contour work below) ─────────────────
    name_result = [None]
    def _gen_name():
        name_result[0] = generate_name(image_b64=image_b64)
    name_thread = threading.Thread(target=_gen_name, daemon=True)
    name_thread.start()

    # Border frame + silhouette are style-agnostic, so compute them once
    outline = build_outline(bgr_processed, config)

    def _build_variant(variant_params):
        """Style → ordered polylines → (polylines, SVG preview, G-code body).

        The body carries neither homing nor footer. Homing is a once-per-session
        concern handled by /print, and the name label and signature are appended
        after the name thread finishes, where they must land before the park
        moves. /gcode adds the homing block back for standalone downloads.
        """
        polys = outline + apply_style(gray, bgr_processed, style_name, config,
                                      variant_params)
        polys = optimize_path_order(polys)
        return (polys,
                build_svg(polys, config.PROCESS_SIZE),
                generate_gcode(polys, config, style_name,
                               include_footer=False, include_home=False))

    # ── Build variants — contour offers four presets, other styles just one ───
    if style_name == "contour":
        # Dev-supplied params override every preset. The stage sends none, so
        # the four presets stay distinct there.
        variants = [({**preset, **params}, preset["label"]) for preset in _STYLE_PRESETS]
        default_idx = _DEFAULT_PRESET
    else:
        variants = [(params, style_name)]
        default_idx = 0

    t0 = time.time()
    built = [_build_variant(p) for p, _ in variants]
    print(f"[timing] {len(built)} {style_name} variant(s): {time.time()-t0:.2f}s")

    # ── Overlays: generated name label, then the machine's signature ──────────
    name_thread.join()
    name = name_result[0] or "Unknown"
    label_polys = text_to_polylines_mm(
        f'"{name}"',
        x_mm=config.BED_OFFSET_X, y_mm=config.BED_OFFSET_Y - 3 - 8,
        height_mm=8.0, font_candidates=FONTS_SANS, anchor="topleft",
    )
    overlay = (["; -- label --", *draw_mm_polylines(label_polys, config)]
               + ["; -- signature --", *_get_sig_gcode()])
    footer = gcode_footer(config)

    presets_out = [
        {"label": label, "svg": svg, "gcode": gc + overlay + footer}
        for (_, label), (_, svg, gc) in zip(variants, built)
    ]

    job_id = str(uuid.uuid4())[:8]
    _store_job(job_id, {
        "gcode": presets_out[default_idx]["gcode"],
        "presets": presets_out,
        "name": name,
    })

    default_polys = built[default_idx][0]
    default_gc = presets_out[default_idx]["gcode"]

    return jsonify({
        "svg": presets_out[default_idx]["svg"],
        "job_id": job_id,
        "name": name,
        # Only contour exposes a preset picker on the stage
        "presets": ([{"label": p["label"], "svg": p["svg"]} for p in presets_out]
                    if style_name == "contour" else []),
        "default_preset": default_idx,
        "stats": {
            "paths":       len(default_polys),
            "gcode_lines": len(default_gc),
            "est_seconds": estimate_draw_time(default_gc, config),
        },
    })


@app.route("/print")
def start_print():
    job_id = request.args.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    with job_store_lock:
        job = job_store.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    gcode_lines = job["gcode"]

    def generate():
        try:
            printer = _get_printer()
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        # Blocks /detect-port for the duration — probing resets Marlin boards
        _print_active.set()
        try:
            # First drawing of the session establishes the coordinate reference;
            # every later one reuses it. Takes ~30s, so tell the operator why.
            if config.HOME_ON_START and not _homed:
                yield f"data: {json.dumps({'status': 'homing'})}\n\n"
                _ensure_homed()

            executable = [l for l in gcode_lines if l.split(';')[0].strip()]
            total = len(executable)
            sent = 0

            for line in gcode_lines:
                stripped = line.split(';')[0].strip()
                if not stripped:
                    continue
                printer.send_line(stripped)
                sent += 1
                pct = int(100 * sent / total) if total else 100
                yield f"data: {json.dumps({'progress': pct})}\n\n"

            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            # Serial error — drop the connection so next job triggers a fresh connect
            _disconnect_printer()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            _print_active.clear()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/select-preset", methods=["POST"])
def select_preset():
    data = request.get_json() or {}
    job_id = data.get("job_id")
    idx = int(data.get("preset_index", 0))
    with job_store_lock:
        job = job_store.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    presets = job.get("presets", [])
    if not presets or idx >= len(presets):
        return jsonify({"error": "Invalid preset index"}), 400
    with job_store_lock:
        job_store[job_id]["gcode"] = presets[idx]["gcode"]
    return jsonify({"ok": True})


@app.route("/gcode/<job_id>")
def download_gcode(job_id):
    with job_store_lock:
        job = job_store.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404

    # Stored jobs omit homing because /print does that once per session. A
    # downloaded file has no such server, so it needs to home itself. Insert
    # after the G90 that generate_gcode always emits, rather than at a fixed
    # index that would drift if the header ever changes.
    lines = job["gcode"]
    insert_at = lines.index("G90") + 1 if "G90" in lines else 0
    content = "\n".join(lines[:insert_at] + gcode_home(config) + lines[insert_at:])
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename=drawing_{job_id}.gcode"},
    )


@app.route("/disconnect", methods=["POST"])
def disconnect():
    _disconnect_printer()
    return jsonify({"ok": True})


@app.route("/status")
def status():
    ports = list_ports()
    configured = config.SERIAL_PORT.upper() if config.SERIAL_PORT else ""
    ports_upper = [p.upper() for p in ports]
    printer_found = bool(configured) and configured in ports_upper

    # Cheap USB-identity guess for when the configured port isn't there. Polled
    # every 10s by the frontend, so it must stay side-effect free.
    suggested = None
    if not printer_found:
        candidates = port_candidates()
        if candidates and candidates[0]["score"] >= 2:
            suggested = candidates[0]["device"]

    return jsonify({
        "configured_port": config.SERIAL_PORT,
        "printer_found":   printer_found,
        "available_ports": ports,
        "suggested_port":  suggested,
    })


@app.route("/detect-port", methods=["POST"])
def detect_port():
    """
    Find the printer's port and save it.

    Handshakes each plausible port with M115 unless {"probe": false} is sent, in
    which case it only ranks USB identities. Probing reboots the Marlin boards it
    touches and takes a few seconds per port, so this is a deliberate action —
    never something the status poll triggers.
    """
    if _print_active.is_set():
        return jsonify({"error": "Cannot scan ports while printing"}), 409

    data  = request.get_json(silent=True) or {}
    probe = bool(data.get("probe", True))
    save  = bool(data.get("save", True))

    # Release our own handle, otherwise the port we most want to test is busy
    _disconnect_printer()

    result = autodetect_port(config.BAUD_RATE, probe=probe)

    if result["port"] and save:
        # Stored verbatim — uppercasing would corrupt POSIX device paths
        config.SERIAL_PORT = result["port"]
        _save_settings({"serial_port": config.SERIAL_PORT})
        print(f"[serial] detected {result['port']} via {result['method']}"
              + (f": {result['firmware']}" if result["firmware"] else ""))

    return jsonify(result)


@app.route("/calibrate", methods=["POST"])
def calibrate():
    """
    Guided pen-height setup. Finds Z_DRAW as an absolute machine coordinate, so
    the value keeps working after a power cycle.

    Actions:
      home  — home all axes; Z becomes a real datum measured up from the endstop
      jog   — move the pen by {dz} mm and remember where it is
      test  — draw a short line at the current height, to see whether it marks
      save  — store the current height as Z_DRAW (and Z_DRAW + PEN_LIFT as Z_TRAVEL)
    """
    global _cal_z, _homed

    if _print_active.is_set():
        return jsonify({"error": "Busy printing"}), 409

    data   = request.get_json(silent=True) or {}
    action = data.get("action", "")

    try:
        if action == "home":
            _send(gcode_home(config))
            _homed = True
            _cal_z = config.Z_TRAVEL

        elif action == "jog":
            if _cal_z is None:
                return jsonify({"error": "Home first — Z has no reference yet"}), 400
            # Clamp to the usable Z range. Downward travel is limited by the
            # endstop anyway, but this keeps a fat-fingered step from running away.
            z_max = getattr(config, "Z_MAX", 240.0)
            target = max(0.0, min(z_max, _cal_z + float(data.get("dz", 0.0))))
            _send([f"G0 Z{target:.2f} F{config.Z_SPEED}"])
            _cal_z = target

        elif action == "test":
            if _cal_z is None:
                return jsonify({"error": "Home first — Z has no reference yet"}), 400
            # A 20mm line just outside the drawing area, at the height being tested
            x0, y0 = config.Z_HOME_X + 5.0, config.Z_HOME_Y + 5.0
            _send([
                f"G0 Z{_cal_z + 5.0:.2f} F{config.Z_SPEED}",
                f"G0 X{x0:.3f} Y{y0:.3f} F{config.FEED_TRAVEL}",
                f"G0 Z{_cal_z:.2f} F{config.Z_SPEED}",
                f"G1 X{x0 + 20.0:.3f} Y{y0:.3f} F{config.FEED_DRAW}",
                f"G0 Z{_cal_z + 5.0:.2f} F{config.Z_SPEED}",
            ])

        elif action == "save":
            if _cal_z is None:
                return jsonify({"error": "Nothing to save — home and jog first"}), 400
            lift = getattr(config, "PEN_LIFT", 1.5)
            config.Z_DRAW   = round(_cal_z, 2)
            config.Z_TRAVEL = round(_cal_z + lift, 2)
            _save_settings({"z_draw": config.Z_DRAW, "z_travel": config.Z_TRAVEL})
            _sig_cache.clear()
            print(f"[calibrate] Z_DRAW={config.Z_DRAW} Z_TRAVEL={config.Z_TRAVEL}")

        else:
            return jsonify({"error": f"Unknown action '{action}'"}), 400

    except Exception as e:
        _disconnect_printer()
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "ok": True,
        "homed":    _homed,
        "current_z": _cal_z,
        "z_draw":   config.Z_DRAW,
        "z_travel": config.Z_TRAVEL,
    })


@app.route("/config", methods=["GET", "POST"])
def get_set_config():
    if request.method == "GET":
        return jsonify({
            "serial_port":    config.SERIAL_PORT,
            "baud_rate":      config.BAUD_RATE,
            "bed_width":      config.BED_WIDTH,
            "bed_height":     config.BED_HEIGHT,
            "draw_width":     config.DRAW_WIDTH,
            "draw_height":    config.DRAW_HEIGHT,
            "bed_offset_x":   config.BED_OFFSET_X,
            "bed_offset_y":   config.BED_OFFSET_Y,
            "z_draw":         config.Z_DRAW,
            "z_travel":       config.Z_TRAVEL,
            "z_speed":        config.Z_SPEED,
            "feed_draw":      config.FEED_DRAW,
            "feed_travel":    config.FEED_TRAVEL,
            "flip_y":         config.FLIP_Y,
            "home_on_start":  config.HOME_ON_START,
            "contour_levels":    config.CONTOUR_LEVELS,
            "contour_blur":      config.CONTOUR_BLUR,
            "contour_min_arc":   config.CONTOUR_MIN_ARC,
            "contour_epsilon":   config.CONTOUR_EPSILON,
            "contour_level_min": config.CONTOUR_LEVEL_MIN,
            "contour_level_max": config.CONTOUR_LEVEL_MAX,
            "outline":           config.OUTLINE,
        })
    else:
        data = request.get_json() or {}
        to_save = {}
        serial_changed = False
        for key, val in data.items():
            attr = key.upper()
            if hasattr(config, attr):
                if attr == "SERIAL_PORT" and isinstance(val, str):
                    val = val.strip().upper()
                if attr in ("SERIAL_PORT", "BAUD_RATE"):
                    serial_changed = True
                setattr(config, attr, val)
                to_save[key] = val
        _save_settings(to_save)
        _sig_cache.clear()
        if serial_changed:
            _disconnect_printer()  # will reconnect lazily on next print
        return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, ssl_context="adhoc")

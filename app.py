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
from processing.pipeline import run_pipeline
from processing.image_utils import preprocess, extract_silhouette
from processing.styles.contour import extract_contour
from processing.svg_builder import build_svg
from processing.text_renderer import text_to_polylines_mm, FONTS_SANS, signature_to_polylines_mm
from processing.name_generator import generate_name, warmup as warmup_models
from gcode.optimizer import optimize_path_order
from gcode.generator import generate_gcode, draw_mm_polylines, estimate_draw_time
from serial_comm.printer import Printer, list_ports

app = Flask(__name__)

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

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

# In-memory job store: job_id → gcode lines
job_store: dict[str, list] = {}
job_store_lock = threading.Lock()

# ── Persistent printer connection ─────────────────────────────────────────────
_printer: Printer | None = None
_printer_lock = threading.Lock()


def _get_printer() -> Printer:
    """Return the shared Printer, connecting lazily on first use."""
    global _printer
    with _printer_lock:
        if _printer is None or not _printer.is_connected:
            _printer = Printer(config.SERIAL_PORT, config.BAUD_RATE)
        return _printer


def _disconnect_printer():
    global _printer
    with _printer_lock:
        if _printer is not None:
            _printer.close()
            _printer = None


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

    # ── Build outline/silhouette polylines (shared across presets) ────────────
    def _outline_polys():
        if not getattr(config, "OUTLINE", False):
            return []
        size = float(config.PROCESS_SIZE)
        border = [(1.0,1.0),(size-1.0,1.0),(size-1.0,size-1.0),(1.0,size-1.0),(1.0,1.0)]
        extras = [border]
        if getattr(config, "REMOVE_BG", False):
            sil = extract_silhouette(bgr_processed)
            if sil:
                extras.append(sil)
        return extras

    outline = _outline_polys()

    # ── Build one variant ─────────────────────────────────────────────────────
    def _build_variant(preset_params):
        if style_name == "contour":
            polys = outline + extract_contour(gray, config, preset_params)
        else:
            try:
                polys = run_pipeline.__wrapped__(bgr, style_name, config, preset_params) \
                    if hasattr(run_pipeline, "__wrapped__") else \
                    run_pipeline(bgr, style_name, config, preset_params)
            except Exception:
                polys = []
        polys = optimize_path_order(polys)
        svg = build_svg(polys, config.PROCESS_SIZE)
        gc = generate_gcode(polys, config, style_name)
        return polys, svg, gc

    # For non-contour styles just run one variant using the user-supplied params
    if style_name != "contour":
        t0 = time.time()
        polys, svg, gcode_lines = _build_variant(params)
        print(f"[timing] pipeline: {time.time()-t0:.2f}s")
        name_thread.join()
        name = name_result[0] or "Unknown"
        ox, oy = config.BED_OFFSET_X, config.BED_OFFSET_Y
        label_polys = text_to_polylines_mm(f'"{name}"', x_mm=ox, y_mm=oy-3-8, height_mm=8.0,
                                            font_candidates=FONTS_SANS, anchor="topleft")
        footer = gcode_lines[-4:]
        gcode_lines = gcode_lines[:-4]
        gcode_lines += ["; -- label --", *draw_mm_polylines(label_polys, config)]
        gcode_lines += ["; -- signature --", *_get_sig_gcode()]
        gcode_lines += footer
        job_id = str(uuid.uuid4())[:8]
        with job_store_lock:
            job_store[job_id] = {"gcode": gcode_lines, "presets": [], "name": name}
        return jsonify({"svg": svg, "job_id": job_id, "name": name, "presets": [],
                        "stats": {"paths": len(polys), "gcode_lines": len(gcode_lines),
                                  "est_seconds": estimate_draw_time(gcode_lines, config)}})

    # ── Contour: build 4 preset variants ─────────────────────────────────────
    t0 = time.time()
    built = [_build_variant(p) for p in _STYLE_PRESETS]
    print(f"[timing] 4 contour presets: {time.time()-t0:.2f}s")

    name_thread.join()
    name = name_result[0] or "Unknown"
    ox, oy = config.BED_OFFSET_X, config.BED_OFFSET_Y
    label_polys = text_to_polylines_mm(f'"{name}"', x_mm=ox, y_mm=oy-3-8, height_mm=8.0,
                                        font_candidates=FONTS_SANS, anchor="topleft")
    sig_gc = _get_sig_gcode()

    def _with_overlays(gc):
        footer = gc[-4:]
        body = gc[:-4]
        body += ["; -- label --", *draw_mm_polylines(label_polys, config)]
        body += ["; -- signature --", *sig_gc]
        body += footer
        return body

    presets_out = []
    for i, (p, preset) in enumerate(zip(built, _STYLE_PRESETS)):
        polys, svg_thumb, gc = p
        gc_full = _with_overlays(gc)
        presets_out.append({"label": preset["label"], "svg": svg_thumb, "gcode": gc_full})

    default_idx = _DEFAULT_PRESET
    job_id = str(uuid.uuid4())[:8]
    with job_store_lock:
        job_store[job_id] = {
            "gcode": presets_out[default_idx]["gcode"],
            "presets": presets_out,
            "name": name,
        }

    default_polys, default_svg, _ = built[default_idx]
    default_gc = presets_out[default_idx]["gcode"]

    return jsonify({
        "svg": default_svg,
        "job_id": job_id,
        "name": name,
        "presets": [{"label": p["label"], "svg": p["svg"]} for p in presets_out],
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

        try:
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
    content = "\n".join(job["gcode"])
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
    printer_found = configured in ports_upper
    return jsonify({
        "configured_port": config.SERIAL_PORT,
        "printer_found": printer_found,
        "available_ports": ports,
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

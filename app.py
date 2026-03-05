import base64
import json
import os
import uuid
import threading

import cv2
import numpy as np
from flask import Flask, request, jsonify, render_template, Response, stream_with_context

import config
from processing.pipeline import run_pipeline
from processing.svg_builder import build_svg
from gcode.optimizer import optimize_path_order
from gcode.generator import generate_gcode, estimate_draw_time
from serial_comm.printer import Printer, list_ports

app = Flask(__name__)

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

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

_load_settings()

# In-memory job store: job_id → gcode lines
job_store: dict[str, list] = {}
# Lock for thread-safe job_store access
job_store_lock = threading.Lock()


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

    # Run processing pipeline
    try:
        polylines = run_pipeline(bgr, style_name, config, params)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Optimize path order (minimize pen-up travel)
    polylines = optimize_path_order(polylines)

    # Build SVG for browser preview
    svg = build_svg(polylines, config.PROCESS_SIZE)

    # Generate G-code
    gcode_lines = generate_gcode(polylines, config, style_name)

    # Store job
    job_id = str(uuid.uuid4())[:8]
    with job_store_lock:
        job_store[job_id] = gcode_lines

    est_seconds = estimate_draw_time(gcode_lines, config)

    return jsonify({
        "svg": svg,
        "job_id": job_id,
        "stats": {
            "paths": len(polylines),
            "gcode_lines": len(gcode_lines),
            "est_seconds": est_seconds,
        },
    })


@app.route("/print")
def start_print():
    job_id = request.args.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    with job_store_lock:
        gcode_lines = job_store.get(job_id)
    if gcode_lines is None:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        try:
            printer = Printer(config.SERIAL_PORT, config.BAUD_RATE)
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        def progress_cb(sent, total):
            pct = int(100 * sent / total) if total else 100
            # Note: can't yield inside a callback; store progress instead

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
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            printer.close()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/gcode/<job_id>")
def download_gcode(job_id):
    with job_store_lock:
        gcode_lines = job_store.get(job_id)
    if gcode_lines is None:
        return jsonify({"error": "Job not found"}), 404
    content = "\n".join(gcode_lines)
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename=drawing_{job_id}.gcode"},
    )


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
        })
    else:
        data = request.get_json() or {}
        to_save = {}
        for key, val in data.items():
            attr = key.upper()
            if hasattr(config, attr):
                if attr == "SERIAL_PORT" and isinstance(val, str):
                    val = val.strip().upper()
                setattr(config, attr, val)
                to_save[key] = val
        _save_settings(to_save)
        return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

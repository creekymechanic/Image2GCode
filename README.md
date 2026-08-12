# Image2GCode

**Deus Ex Machina — Portrait Engine**

A webcam portrait becomes a pen-plotted line drawing. A photo is stripped of its
background, reduced to topographic contour lines, titled by a language model
running entirely offline, and drawn on paper by a converted Creality Ender 3 —
which signs the finished piece itself.

See `Artist_statement.txt` for the work's framing.

## How it works

1. **Capture** — a browser kiosk grabs a 1.6× centre-zoomed webcam frame.
2. **Reduce** — the frame is mirrored, square-cropped, resized to 512px, has its
   background removed (`rembg`/U²-Net), and is histogram-equalised.
3. **Trace** — contours are extracted at evenly spaced brightness levels, so the
   face resolves into nested isometric lines. Four presets are generated at once,
   tuned for different skin tones (Fair / Medium / Tan / Deep).
4. **Name** — a local `llama3.2:1b` picks a one-word title. No internet, no prompt
   from the operator.
5. **Plot** — paths are reordered to minimise pen-up travel, converted to Marlin
   G-code, and streamed over serial. The name is lettered above the portrait and
   the signature — *IsoChin Peucker* — written below it in one continuous stroke.

## Requirements

- Python 3.10+ (the code uses `X | None` type syntax)
- [Ollama](https://ollama.com) with `llama3.2:1b` — optional; titles fall back to a
  curated word list if it isn't running
- A Marlin-firmware printer with a pen mount, on a known COM port

## Setup

```bat
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
ollama pull llama3.2:1b
```

First run downloads the `rembg` background-removal model (~170 MB).

## Running

```bat
start.bat
```

This starts Ollama, waits for it to answer, activates the venv, opens
<https://localhost:5000>, and runs the server.

The certificate is self-signed, so the browser will warn once — accept it, or the
camera will not start. HTTPS is required: browsers only grant camera access on a
secure origin.

## Using it

**Stage (public) view** — the default. Everything runs off the space bar:

| State | Space bar does |
|---|---|
| Ready | Starts a 3-second countdown |
| Counting down | Cancels |
| Preview shown | Sends the drawing to the printer |

After capture the frame blurs, the four presets appear as cards, and the subject
picks one by clicking. Progress shows while the printer draws; the stage resets
itself when the drawing finishes.

**Dev view** — the ⚙ button, bottom corner. Adds a manual capture/retake flow, live
statistics (path count, line count, estimated time), G-code download and copy, and
editable contour and printer settings. Values entered here override all four
presets, so use it to dial in one look rather than to compare them.

## Configuration

`config.py` holds defaults. `settings.json` holds the values actually in use — it
is loaded over `config.py` at startup and rewritten whenever you press **Save
config** in the dev panel.

| Setting | Meaning |
|---|---|
| `serial_port`, `baud_rate` | Printer connection (e.g. `COM5`, `115200`). See auto-detection below. |
| `z_draw`, `z_travel` | Pen height touching paper / lifted, measured up from the Z endstop. Use the guided setup below. |
| `pen_lift` | How far above `z_draw` to lift when travelling (default 1.5mm) |
| `z_home_x`, `z_home_y` | Where Z homes. Must be clear of paper — the pen touches down there. |
| `draw_width`, `draw_height` | Output size in mm (100 or 150 via the dev panel) |
| `bed_offset_x`, `bed_offset_y` | Where the drawing sits on the bed |
| `feed_draw`, `feed_travel` | Drawing and repositioning speeds, mm/min |
| `home_on_start` | `G28` before drawing. If off, the machine **must** already be parked at X15 Y200. |
| `flip_y` | Mirror vertically, if the output comes out upside down |
| `outline` | Draw a border frame and the subject's silhouette |
| `contour_*` | Level count, blur, minimum arc length, simplification, brightness range |

### Setting the pen height

`z_draw` is how high the pen sits above the **Z endstop** when it's touching the
paper. Because that reference is a physical switch, the number is stable — you
find it once and it keeps working across power cycles.

Dev tools → **pen height**:

1. **⌂ home** — establishes the reference. Z touches down at the front corner,
   clear of the paper, so it doesn't mark your sheet.
2. **−1 / −0.1 / +0.1 / +1** — lower the pen, coarse then fine.
3. **✎ test line** — draws a 20mm line at the current height, outside the drawing
   area, so you can see whether it marks.
4. **✓ use this height** — saves it as `z_draw`, with `z_travel` set 1.5mm above.

**Aim for the lightest height that draws a consistent line, not the darkest one.**
Pressing harder splays the nib, drags the paper, and shortens the pen's life. If
the line breaks up in places, come down 0.1mm — not 1mm.

This assumes a **spring-loaded pen mount**, where the tip can ride up. With a
rigidly clamped pen, homing Z would push the tip into the bed — don't use the
homing button, and set `home_on_start` to `false`.

### Homing

Homing runs **once per session**, on the first drawing after startup, and every
later portrait reuses that reference. It takes about 30 seconds and the status
line says so while it happens.

The order is deliberate: X and Y first (no vertical movement, always safe), then
the gantry moves to `z_home_x`/`z_home_y` before homing Z, since Z homing descends
until the endstop trips and the pen will touch whatever is beneath it. Keep that
corner clear of paper and clips.

If the printer is power-cycled mid-session, the serial connection drops and the
next drawing re-homes automatically.

### Finding the printer port

You should not have to look the COM number up by hand. Windows renumbers ports
between reboots, so there are three layers:

- **The status pill guesses.** When the configured port is missing, the header
  reads `try COM11` instead of `no printer`, based on the USB chip IDs of the
  ports present. This only reads identities — it never opens a port.
- **The "detect" button confirms.** Next to the port field in dev tools. It
  handshakes each plausible port with `M115` (Marlin's "report firmware"
  command), takes the first that answers, and saves it. Ports are tried
  best-guess first, and Bluetooth ports are skipped.
- **Prints self-recover.** If the saved port has vanished but exactly one
  unambiguous printer-like port is present, that one is used automatically. No
  probing involved, so nothing else on the machine is disturbed.

Detection is a setup action, not something to run mid-show: **opening a serial
port toggles DTR, which reboots most Marlin boards.** The server refuses to scan
while a print is streaming.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Camera never starts | Page not on HTTPS, or the certificate warning wasn't accepted |
| "no printer" pill | `serial_port` doesn't match an available port — hover for the list, or press **detect** |
| "try COMx" pill | That port looks like a printer; press **detect** to confirm and save it |
| Detect finds nothing | Printer off or asleep, USB cable is charge-only, or another program holds the port |
| Titles look repetitive | Ollama isn't running; the fallback word list is in use |
| Drawing is mirrored | Toggle `flip_y` |
| Pen drags or floats | Re-run the pen height setup; nib wear shifts it over time |
| Line breaks up in places | `z_draw` is 0.1–0.2mm too high, or the bed isn't level under the paper |
| Pen presses too hard | `z_draw` too low — go up 0.1mm at a time |
| Coordinates all wrong | Machine wasn't homed; power-cycle it and let the next drawing re-home |
| Stray dot in a corner | Normal — that's where Z homed. Move `z_home_x`/`z_home_y` if it's on the paper. |
| Homing grinds or the pen bends | Pen mount isn't floating. Set `home_on_start` to `false` and set Z by hand. |

## Layout

```
app.py                  Flask routes, contour presets, printer lifecycle
config.py               Defaults (overridden by settings.json)
processing/             Preprocessing, style extractors, text and signature rendering
gcode/                  Path ordering and Marlin G-code generation
serial_comm/            Serial transport
templates/, static/     Kiosk frontend (stage view + dev view)
models/                 MediaPipe face landmarker, committed so the kiosk runs offline
```

Contributor notes and hardware gotchas: `CLAUDE.md`.

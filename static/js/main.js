// ── State ────────────────────────────────────────────
const State = {
  IDLE: "IDLE",
  CAMERA_READY: "CAMERA_READY",
  CAPTURED: "CAPTURED",
  PROCESSING: "PROCESSING",
  PREVIEW_READY: "PREVIEW_READY",
  PRINTING: "PRINTING",
  DONE: "DONE",
};

let currentState = State.IDLE;
let currentJobId = null;
let capturedImageData = null;
let devMode = false;

// Detail slider presets → contour params
// fast: fewer, coarser lines; detailed: more, finer lines
const DETAIL_PRESETS = {
  1: { contour_levels: 5,  contour_blur: 13, contour_min_arc: 50, contour_epsilon: 5.0 },
  2: { contour_levels: 8,  contour_blur: 9,  contour_min_arc: 30, contour_epsilon: 3.0 },
  3: { contour_levels: 14, contour_blur: 5,  contour_min_arc: 15, contour_epsilon: 1.5 },
};

// ── DOM refs ─────────────────────────────────────────
const videoEl        = document.getElementById("webcam");
const canvasEl       = document.getElementById("capture-canvas");
const capturePreview = document.getElementById("capture-preview");
const captureImg     = document.getElementById("capture-img");
const recBadge       = document.getElementById("rec-badge");
const btnCapture     = document.getElementById("btn-capture");
const btnRetake      = document.getElementById("btn-retake");
const btnProcess     = document.getElementById("btn-process");
const btnPrint       = document.getElementById("btn-print");
const statusText     = document.getElementById("status-text");
const statusCursor   = document.getElementById("status-cursor");
const progressWrap   = document.getElementById("progress-wrap");
const progressFill   = document.getElementById("progress-fill");
const progressLabel  = document.getElementById("progress-label");
const svgContainer   = document.getElementById("svg-container");
const printerPill    = document.getElementById("printer-pill");
const printerLabel   = document.getElementById("printer-label");
const btnDevToggle   = document.getElementById("btn-dev-toggle");
const advancedPanel  = document.getElementById("advanced-panel");
const detailSlider   = document.getElementById("detail-slider");
const statsEl        = document.getElementById("stats");
const statPaths      = document.getElementById("stat-paths");
const statTime       = document.getElementById("stat-time");
const statLines      = document.getElementById("stat-lines");
const gcodeActions   = document.getElementById("gcode-actions");
const btnDownload    = document.getElementById("btn-download");
const btnCopyGcode   = document.getElementById("btn-copy-gcode");

// Contour inputs
const cfgLevels  = document.getElementById("cfg-levels");
const cfgBlur    = document.getElementById("cfg-blur");
const cfgMinarc  = document.getElementById("cfg-minarc");
const cfgEpsilon = document.getElementById("cfg-epsilon");
const cfgLevmin  = document.getElementById("cfg-levmin");
const cfgLevmax  = document.getElementById("cfg-levmax");

// Printer inputs
const cfgPort    = document.getElementById("cfg-port");
const cfgZdraw   = document.getElementById("cfg-zdraw");
const cfgZtravel = document.getElementById("cfg-ztravel");
const cfgFdraw   = document.getElementById("cfg-fdraw");
const cfgFtravel = document.getElementById("cfg-ftravel");
const cfgOffx    = document.getElementById("cfg-offx");
const cfgOffy    = document.getElementById("cfg-offy");
const cfgHome    = document.getElementById("cfg-home");
const cfgFlipy   = document.getElementById("cfg-flipy");
const btnSaveConfig = document.getElementById("btn-save-config");

// ── Camera ───────────────────────────────────────────
async function initCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, facingMode: "user" },
      audio: false,
    });
    videoEl.srcObject = stream;
    setState(State.CAMERA_READY);
  } catch (e) {
    setStatus(`camera error: ${e.message}`);
  }
}

// ── Capture ──────────────────────────────────────────
function captureFrame() {
  canvasEl.width  = videoEl.videoWidth  || 640;
  canvasEl.height = videoEl.videoHeight || 480;
  canvasEl.getContext("2d").drawImage(videoEl, 0, 0);
  return canvasEl.toDataURL("image/jpeg", 0.85);
}

btnCapture.addEventListener("click", () => {
  capturedImageData = captureFrame();
  captureImg.src = capturedImageData;
  videoEl.style.display = "none";
  capturePreview.hidden = false;
  recBadge.classList.add("hidden-badge");
  setState(State.CAPTURED);
});

btnRetake.addEventListener("click", () => {
  capturePreview.hidden = true;
  videoEl.style.display = "";
  recBadge.classList.remove("hidden-badge");
  capturedImageData = null;
  setState(State.CAMERA_READY);
});

// ── Build contour params ──────────────────────────────
function getContourParams() {
  if (devMode) {
    // In dev mode use the explicit input values if filled
    const p = {};
    if (cfgLevels.value)  p.contour_levels    = parseInt(cfgLevels.value);
    if (cfgBlur.value)    p.contour_blur       = parseInt(cfgBlur.value);
    if (cfgMinarc.value)  p.contour_min_arc    = parseFloat(cfgMinarc.value);
    if (cfgEpsilon.value) p.contour_epsilon    = parseFloat(cfgEpsilon.value);
    if (cfgLevmin.value)  p.contour_level_min  = parseFloat(cfgLevmin.value);
    if (cfgLevmax.value)  p.contour_level_max  = parseFloat(cfgLevmax.value);
    return p;
  }
  // Public mode: map slider 1-3 to preset
  return DETAIL_PRESETS[parseInt(detailSlider.value)] || DETAIL_PRESETS[2];
}

// ── Process ──────────────────────────────────────────
btnProcess.addEventListener("click", processImage);

async function processImage() {
  if (!capturedImageData) return;
  setState(State.PROCESSING);

  try {
    const resp = await fetch("/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: capturedImageData,
        style: "contour",
        params: getContourParams(),
      }),
    });

    const data = await resp.json();
    if (data.error) throw new Error(data.error);

    svgContainer.innerHTML = data.svg;

    const s = data.stats;
    const mins = Math.floor(s.est_seconds / 60);
    const secs = s.est_seconds % 60;
    statPaths.textContent = `${s.paths} paths`;
    statTime.textContent  = mins > 0 ? `~${mins}m ${secs}s` : `~${secs}s`;
    statLines.textContent = `${s.gcode_lines} lines`;
    statsEl.hidden = false;
    gcodeActions.hidden = false;

    currentJobId = data.job_id;
    setState(State.PREVIEW_READY);
  } catch (e) {
    setStatus(`error: ${e.message}`);
    setState(State.CAPTURED);
  }
}

// ── G-code actions ────────────────────────────────────
btnDownload.addEventListener("click", () => {
  if (!currentJobId) return;
  const a = document.createElement("a");
  a.href = `/gcode/${currentJobId}`;
  a.download = `drawing_${currentJobId}.gcode`;
  a.click();
});

btnCopyGcode.addEventListener("click", async () => {
  if (!currentJobId) return;
  try {
    const resp = await fetch(`/gcode/${currentJobId}`);
    const text = await resp.text();
    await navigator.clipboard.writeText(text);
    btnCopyGcode.textContent = "✓ Copied!";
    setTimeout(() => { btnCopyGcode.textContent = "⎘ Copy G-code"; }, 2000);
  } catch (e) {
    setStatus(`copy failed: ${e.message}`);
  }
});

// ── Print ─────────────────────────────────────────────
btnPrint.addEventListener("click", startPrint);

function startPrint() {
  if (!currentJobId) return;
  setState(State.PRINTING);

  const evtSource = new EventSource(`/print?job_id=${currentJobId}`);

  evtSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.error) {
      setStatus(`printer error: ${data.error}`);
      evtSource.close();
      setState(State.PREVIEW_READY);
      return;
    }
    if (data.progress !== undefined) {
      const pct = data.progress;
      progressFill.style.width = pct + "%";
      progressLabel.textContent = pct + "%";
      setStatus(`printing ${pct}%`);
    }
    if (data.done) {
      evtSource.close();
      setState(State.DONE);
    }
  };

  evtSource.onerror = () => {
    evtSource.close();
    setStatus("connection lost");
    setState(State.PREVIEW_READY);
  };
}

// ── Developer mode ────────────────────────────────────
btnDevToggle.addEventListener("click", () => {
  devMode = !devMode;
  btnDevToggle.classList.toggle("active", devMode);
  advancedPanel.hidden = !devMode;
  if (devMode) loadConfig();
});

async function loadConfig() {
  try {
    const resp = await fetch("/config");
    const c = await resp.json();
    // Contour
    cfgLevels.value  = c.contour_levels    ?? "";
    cfgBlur.value    = c.contour_blur       ?? "";
    cfgMinarc.value  = c.contour_min_arc    ?? "";
    cfgEpsilon.value = c.contour_epsilon    ?? "";
    cfgLevmin.value  = c.contour_level_min  ?? "";
    cfgLevmax.value  = c.contour_level_max  ?? "";
    // Printer
    cfgPort.value    = c.serial_port   || "";
    cfgZdraw.value   = c.z_draw        ?? "";
    cfgZtravel.value = c.z_travel      ?? "";
    cfgFdraw.value   = c.feed_draw     ?? "";
    cfgFtravel.value = c.feed_travel   ?? "";
    cfgOffx.value    = c.bed_offset_x  ?? "";
    cfgOffy.value    = c.bed_offset_y  ?? "";
    cfgHome.checked  = !!c.home_on_start;
    cfgFlipy.checked = !!c.flip_y;
  } catch { /* ignore */ }
}

btnSaveConfig.addEventListener("click", async () => {
  const payload = {};
  if (cfgLevels.value)  payload.contour_levels    = parseInt(cfgLevels.value);
  if (cfgBlur.value)    payload.contour_blur       = parseInt(cfgBlur.value);
  if (cfgMinarc.value)  payload.contour_min_arc    = parseFloat(cfgMinarc.value);
  if (cfgEpsilon.value) payload.contour_epsilon    = parseFloat(cfgEpsilon.value);
  if (cfgLevmin.value)  payload.contour_level_min  = parseFloat(cfgLevmin.value);
  if (cfgLevmax.value)  payload.contour_level_max  = parseFloat(cfgLevmax.value);
  if (cfgPort.value)    payload.serial_port   = cfgPort.value;
  if (cfgZdraw.value)   payload.z_draw        = parseFloat(cfgZdraw.value);
  if (cfgZtravel.value) payload.z_travel      = parseFloat(cfgZtravel.value);
  if (cfgFdraw.value)   payload.feed_draw     = parseInt(cfgFdraw.value);
  if (cfgFtravel.value) payload.feed_travel   = parseInt(cfgFtravel.value);
  if (cfgOffx.value)    payload.bed_offset_x  = parseFloat(cfgOffx.value);
  if (cfgOffy.value)    payload.bed_offset_y  = parseFloat(cfgOffy.value);
  payload.home_on_start = cfgHome.checked;
  payload.flip_y        = cfgFlipy.checked;

  try {
    await fetch("/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    btnSaveConfig.textContent = "✓ Saved!";
    setTimeout(() => { btnSaveConfig.textContent = "✓ Save config"; }, 2000);
    checkPrinterStatus();
  } catch (e) {
    setStatus(`save failed: ${e.message}`);
  }
});

// ── State machine ─────────────────────────────────────
function setState(state) {
  currentState = state;
  btnCapture.disabled = false;
  btnProcess.disabled = true;
  btnPrint.disabled   = true;
  progressWrap.hidden = true;
  statusCursor.style.display = "";

  switch (state) {
    case State.IDLE:          setStatus("waiting for camera"); break;
    case State.CAMERA_READY:  setStatus("ready — take a photo"); break;
    case State.CAPTURED:
      setStatus("photo captured");
      btnProcess.disabled = false;
      break;
    case State.PROCESSING:
      setStatus("processing...");
      btnCapture.disabled = true;
      btnProcess.disabled = true;
      statusCursor.style.display = "none";
      break;
    case State.PREVIEW_READY:
      setStatus("preview ready");
      btnPrint.disabled = false;
      break;
    case State.PRINTING:
      setStatus("printing 0%");
      btnCapture.disabled = true;
      btnProcess.disabled = true;
      progressWrap.hidden = false;
      progressFill.style.width = "0%";
      progressLabel.textContent = "0%";
      break;
    case State.DONE:
      setStatus("done!");
      progressFill.style.width = "100%";
      progressLabel.textContent = "100%";
      progressWrap.hidden = false;
      btnPrint.disabled = false;
      break;
  }
}

function setStatus(msg) { statusText.textContent = msg; }

// ── Printer status ────────────────────────────────────
async function checkPrinterStatus() {
  try {
    const resp = await fetch("/status");
    const data = await resp.json();
    if (data.printer_found) {
      printerPill.classList.add("online");
      printerLabel.textContent = data.configured_port;
    } else {
      printerPill.classList.remove("online");
      printerLabel.textContent = "no printer";
      const avail = data.available_ports.join(", ") || "none";
      printerPill.title = `${data.configured_port} not found. Available: ${avail}`;
    }
  } catch {
    printerPill.classList.remove("online");
    printerLabel.textContent = "offline";
  }
}

// ── Boot ──────────────────────────────────────────────
initCamera();
checkPrinterStatus();
setInterval(checkPrinterStatus, 10000);

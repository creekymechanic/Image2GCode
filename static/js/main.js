// ── State ────────────────────────────────────────────
const State = {
  IDLE: "IDLE",
  CAMERA_READY: "CAMERA_READY",
  COUNTDOWN: "COUNTDOWN",
  CAPTURED: "CAPTURED",
  PROCESSING: "PROCESSING",
  PREVIEW_READY: "PREVIEW_READY",
  PRINTING: "PRINTING",
  DONE: "DONE",
};

let currentState = State.IDLE;
let currentJobId = null;
let capturedImageData = null;
let isPublicView = true;  // start in public/stage mode
let devMode = false;

// Detail slider presets → contour params
const DETAIL_PRESETS = {
  1: { contour_levels: 5,  contour_blur: 13, contour_min_arc: 50, contour_epsilon: 5.0 },
  2: { contour_levels: 8,  contour_blur: 9,  contour_min_arc: 30, contour_epsilon: 3.0 },
  3: { contour_levels: 14, contour_blur: 5,  contour_min_arc: 15, contour_epsilon: 1.5 },
};

// ── DOM refs — shared ──────────────────────────────────
const videoEl        = document.getElementById("webcam");
const canvasEl       = document.getElementById("capture-canvas");
const btnDevToggle   = document.getElementById("btn-dev-toggle");

// ── DOM refs — stage (public) ──────────────────────────
const stageEl            = document.getElementById("stage");
const stageVideoWrap     = document.getElementById("stage-video-wrap");
const stageCanvas        = document.getElementById("stage-canvas");
const svgStage           = document.getElementById("svg-stage");
const stagePrompt        = document.getElementById("stage-prompt");
const stageName          = document.getElementById("stage-name");
const stageNameValue     = document.getElementById("stage-name-value");
const stageProgressWrap  = document.getElementById("stage-progress-wrap");
const stageProgressFill  = document.getElementById("stage-progress-fill");
const stageProgressLabel = document.getElementById("stage-progress-label");
const stageCountdown     = document.getElementById("stage-countdown");
const stageCountdownNum  = document.getElementById("stage-countdown-number");
const stageArtQuestion   = document.getElementById("stage-art-question");
const stagePresets       = document.getElementById("stage-presets");
const faceGuide          = document.getElementById("face-guide");

// ── DOM refs — dev layout ──────────────────────────────
const devLayout      = document.getElementById("dev-layout");
const devVideoSlot   = document.getElementById("dev-video-slot");
const videoWrap      = document.getElementById("video-wrap");
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
const nameBadge      = document.getElementById("name-badge");
const generatedName  = document.getElementById("generated-name");
const printerPill    = document.getElementById("printer-pill");
const printerLabel   = document.getElementById("printer-label");
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
const cfgFlipy      = document.getElementById("cfg-flipy");
const cfgOutputSize = document.getElementById("cfg-output-size");
const cfgOutline    = document.getElementById("cfg-outline");
const btnSaveConfig = document.getElementById("btn-save-config");

// Auto-fill offset fields when output size preset changes
cfgOutputSize.addEventListener("change", () => {
  const sizeMm = parseInt(cfgOutputSize.value);
  cfgOffx.value = (235 - sizeMm) / 2;
  cfgOffy.value = (235 - sizeMm) / 2;
});

// ── View toggle ────────────────────────────────────────
function toggleView() {
  isPublicView = !isPublicView;

  if (isPublicView) {
    // Switch to public/stage mode
    stageVideoWrap.appendChild(videoEl);
    stageEl.hidden = false;
    devLayout.hidden = true;
    btnDevToggle.classList.remove("active");
    devMode = false;
  } else {
    // Switch to dev mode
    devVideoSlot.style.display = "";   // ensure slot is visible
    devVideoSlot.appendChild(videoEl); // move video from stage to dev slot
    capturePreview.hidden = true;      // reset capture preview
    recBadge.classList.remove("hidden-badge");
    stageEl.hidden = true;
    devLayout.hidden = false;
    btnDevToggle.classList.add("active");
    devMode = true;
    advancedPanel.hidden = false;
    loadConfig();
  }
}

btnDevToggle.addEventListener("click", toggleView);

// ── Camera ────────────────────────────────────────────
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

// ── Capture ───────────────────────────────────────────
const CAM_ZOOM = 1.6;

function _drawZoomed(ctx, canvas) {
  const vw = videoEl.videoWidth  || 640;
  const vh = videoEl.videoHeight || 480;
  const sw = vw / CAM_ZOOM;
  const sh = vh / CAM_ZOOM;
  const sx = (vw - sw) / 2;
  const sy = (vh - sh) / 2;
  ctx.drawImage(videoEl, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
}

function captureFrame() {
  canvasEl.width  = videoEl.videoWidth  || 640;
  canvasEl.height = videoEl.videoHeight || 480;
  _drawZoomed(canvasEl.getContext("2d"), canvasEl);
  return canvasEl.toDataURL("image/jpeg", 0.85);
}

// Dev mode capture
btnCapture.addEventListener("click", () => {
  capturedImageData = captureFrame();
  captureImg.src = capturedImageData;
  devVideoSlot.style.display = "none";
  capturePreview.hidden = false;
  recBadge.classList.add("hidden-badge");
  setState(State.CAPTURED);
});

btnRetake.addEventListener("click", () => {
  capturePreview.hidden = true;
  devVideoSlot.style.display = "";
  recBadge.classList.remove("hidden-badge");
  capturedImageData = null;
  setState(State.CAMERA_READY);
});

// ── Public mode capture (space bar) ───────────────────
let _countdownTimer = null;

function publicCapture() {
  // Freeze zoomed frame onto stage canvas
  stageCanvas.width  = videoEl.videoWidth  || 640;
  stageCanvas.height = videoEl.videoHeight || 480;
  _drawZoomed(stageCanvas.getContext("2d"), stageCanvas);

  // Capture data for background processing (same zoomed crop)
  canvasEl.width  = stageCanvas.width;
  canvasEl.height = stageCanvas.height;
  _drawZoomed(canvasEl.getContext("2d"), canvasEl);
  capturedImageData = canvasEl.toDataURL("image/jpeg", 0.85);

  // Hide live video (frozen frame shows instead)
  videoEl.style.visibility = "hidden";

  // Show countdown — blur and processing happen after
  stagePrompt.hidden = true;
  stageCountdown.hidden = false;
  stageArtQuestion.hidden = true;
  faceGuide.classList.add("hidden");
  setState(State.COUNTDOWN);

  let secs = 3;
  stageCountdownNum.textContent = secs;

  _countdownTimer = setInterval(() => {
    secs--;
    if (secs > 0) {
      stageCountdownNum.textContent = secs;
    } else {
      clearInterval(_countdownTimer);
      _countdownTimer = null;
      _onCountdownComplete();
    }
  }, 1000);
}

function _cancelCountdown() {
  if (_countdownTimer) { clearInterval(_countdownTimer); _countdownTimer = null; }
  stageCountdown.hidden = true;
  videoEl.style.visibility = "";
  const ctx = stageCanvas.getContext("2d");
  ctx.clearRect(0, 0, stageCanvas.width, stageCanvas.height);
  capturedImageData = null;
  stagePrompt.hidden = false;
  stagePrompt.innerHTML = 'press <span class="kbd">space</span> to START';
  faceGuide.classList.remove("hidden");
  setState(State.CAMERA_READY);
}

function _onCountdownComplete() {
  stageCountdown.hidden = true;
  stageCanvas.classList.add("is-blurred");
  stageArtQuestion.hidden = false;
  processImage();
}

function publicReset() {
  setTimeout(() => {
    // Fade out SVG
    svgStage.classList.remove("is-visible");

    // Show live video again
    videoEl.style.visibility = "";

    // Clear frozen canvas (now transparent, behind live video)
    const ctx = stageCanvas.getContext("2d");
    ctx.clearRect(0, 0, stageCanvas.width, stageCanvas.height);
    stageCanvas.classList.remove("is-blurred");

    // Reset overlay
    stagePrompt.hidden = false;
    stagePrompt.innerHTML = 'press <span class="kbd">space</span> to START';
    stageArtQuestion.hidden = true;
    stageCountdown.hidden = true;
    stageName.hidden = true;
    stagePresets.hidden = true;
    stagePresets.innerHTML = "";
    faceGuide.classList.remove("hidden");
    stageProgressWrap.hidden = true;
    stageProgressFill.style.width = "0%";

    // Reset job state
    capturedImageData = null;
    currentJobId = null;
    setTimeout(() => { svgStage.innerHTML = ""; }, 1000);

    setState(State.CAMERA_READY);
  }, 3000);
}

// ── Space bar handler ──────────────────────────────────
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space") return;
  if (!isPublicView) return;
  e.preventDefault();

  if (currentState === State.CAMERA_READY) {
    publicCapture();
  } else if (currentState === State.COUNTDOWN) {
    _cancelCountdown();
  } else if (currentState === State.PREVIEW_READY) {
    startPrint();
  }
});

// ── Build contour params ──────────────────────────────
function getContourParams() {
  if (devMode) {
    const p = {};
    if (cfgLevels.value)  p.contour_levels    = parseInt(cfgLevels.value);
    if (cfgBlur.value)    p.contour_blur       = parseInt(cfgBlur.value);
    if (cfgMinarc.value)  p.contour_min_arc    = parseFloat(cfgMinarc.value);
    if (cfgEpsilon.value) p.contour_epsilon    = parseFloat(cfgEpsilon.value);
    if (cfgLevmin.value)  p.contour_level_min  = parseFloat(cfgLevmin.value);
    if (cfgLevmax.value)  p.contour_level_max  = parseFloat(cfgLevmax.value);
    return p;
  }
  return DETAIL_PRESETS[parseInt(detailSlider.value)] || DETAIL_PRESETS[2];
}

// ── Process ───────────────────────────────────────────
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

    // Update dev preview
    svgContainer.innerHTML = data.svg;

    if (data.name) {
      generatedName.textContent = data.name;
      nameBadge.hidden = false;
    }

    const s = data.stats;
    const mins = Math.floor(s.est_seconds / 60);
    const secs = s.est_seconds % 60;
    statPaths.textContent = `${s.paths} paths`;
    statTime.textContent  = mins > 0 ? `~${mins}m ${secs}s` : `~${secs}s`;
    statLines.textContent = `${s.gcode_lines} lines`;
    statsEl.hidden = false;
    gcodeActions.hidden = false;

    // Update public stage
    if (isPublicView) {
      _showStageSvg(data.svg);

      if (data.name) {
        stageNameValue.textContent = data.name;
        stageName.hidden = false;
      }

      stageArtQuestion.hidden = true;

      // Show preset cards if available, otherwise go straight to print prompt
      if (data.presets && data.presets.length > 0) {
        _renderPresets(data.presets, data.default_preset ?? 1, data.job_id);
      } else {
        stagePresets.hidden = true;
        stagePrompt.hidden = false;
        stagePrompt.innerHTML = 'press <span class="kbd">space</span> to print';
      }
    }

    currentJobId = data.job_id;
    setState(State.PREVIEW_READY);
  } catch (e) {
    setStatus(`error: ${e.message}`);
    if (isPublicView) {
      stageArtQuestion.hidden = true;
      stageCountdown.hidden = true;
      stagePrompt.hidden = false;
      stagePrompt.innerHTML = 'press <span class="kbd">space</span> to START';
      stageCanvas.classList.remove("is-blurred");
      videoEl.style.visibility = "";
      capturedImageData = null;
    }
    setState(State.CAMERA_READY);
  }
}

// ── G-code actions ─────────────────────────────────────
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

// ── Stage SVG helper ───────────────────────────────────
function _showStageSvg(svgStr) {
  svgStage.innerHTML = svgStr;
  const svgEl = svgStage.querySelector("svg");
  if (svgEl) {
    svgEl.style.width = "80vmin";
    svgEl.style.height = "80vmin";
    svgEl.style.display = "block";
    svgEl.style.flexShrink = "0";
    svgEl.style.transform = "scaleX(-1)";
  }
  svgStage.classList.add("is-visible");
}

// ── Preset cards ───────────────────────────────────────
let _selectedPreset = 0;

function _renderPresets(presets, defaultIdx, jobId) {
  _selectedPreset = defaultIdx;
  stagePresets.innerHTML = "";

  presets.forEach((p, i) => {
    const card = document.createElement("div");
    card.className = "preset-card" + (i === defaultIdx ? " active" : "");
    card.dataset.index = i;

    const thumb = document.createElement("div");
    thumb.className = "preset-thumb";
    thumb.innerHTML = p.svg;
    const svgEl = thumb.querySelector("svg");
    if (svgEl) {
      svgEl.removeAttribute("width");
      svgEl.removeAttribute("height");
      svgEl.style.transform = "scaleX(-1)";
    }

    card.appendChild(thumb);

    card.addEventListener("click", async () => {
      if (_selectedPreset === i) return;
      _selectedPreset = i;
      document.querySelectorAll(".preset-card").forEach(c => c.classList.remove("active"));
      card.classList.add("active");
      _showStageSvg(p.svg);
      await fetch("/select-preset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, preset_index: i }),
      });
    });

    stagePresets.appendChild(card);
  });

  stagePresets.hidden = false;
  stagePrompt.hidden = false;
  stagePrompt.innerHTML = 'press <span class="kbd">space</span> to print';
}

// ── Print ──────────────────────────────────────────────
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

      if (isPublicView) {
        stageProgressFill.style.width = pct + "%";
        stageProgressLabel.textContent = pct + "%";
      }
    }
    if (data.done) {
      evtSource.close();
      setState(State.DONE);
      if (isPublicView) publicReset();
    }
  };

  evtSource.onerror = () => {
    evtSource.close();
    setStatus("connection lost");
    setState(State.PREVIEW_READY);
  };
}

// ── Config ─────────────────────────────────────────────
async function loadConfig() {
  try {
    const resp = await fetch("/config");
    const c = await resp.json();
    cfgLevels.value  = c.contour_levels    ?? "";
    cfgBlur.value    = c.contour_blur       ?? "";
    cfgMinarc.value  = c.contour_min_arc    ?? "";
    cfgEpsilon.value = c.contour_epsilon    ?? "";
    cfgLevmin.value  = c.contour_level_min  ?? "";
    cfgLevmax.value  = c.contour_level_max  ?? "";
    cfgPort.value    = c.serial_port   || "";
    cfgZdraw.value   = c.z_draw        ?? "";
    cfgZtravel.value = c.z_travel      ?? "";
    cfgFdraw.value   = c.feed_draw     ?? "";
    cfgFtravel.value = c.feed_travel   ?? "";
    cfgOffx.value    = c.bed_offset_x  ?? "";
    cfgOffy.value    = c.bed_offset_y  ?? "";
    cfgHome.checked  = !!c.home_on_start;
    cfgFlipy.checked = !!c.flip_y;
    cfgOutline.checked = !!c.outline;
    // Set output size select to closest preset
    const w = c.draw_width ?? 100;
    cfgOutputSize.value = w >= 150 ? "150" : "100";
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
  payload.outline       = cfgOutline.checked;
  payload.draw_width    = parseInt(cfgOutputSize.value);
  payload.draw_height   = parseInt(cfgOutputSize.value);

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

// ── State machine ──────────────────────────────────────
function setState(state) {
  currentState = state;
  btnCapture.disabled = false;
  btnProcess.disabled = true;
  btnPrint.disabled   = true;
  progressWrap.hidden = true;
  statusCursor.style.display = "";

  // Stage overlay updates
  if (isPublicView) {
    stageProgressWrap.hidden = true;
  }

  switch (state) {
    case State.IDLE:
      setStatus("waiting for camera");
      break;

    case State.CAMERA_READY:
      setStatus("ready — take a photo");
      if (isPublicView) {
        stagePrompt.hidden = false;
        stagePrompt.innerHTML = 'press <span class="kbd">space</span> to START';
      }
      break;

    case State.COUNTDOWN:
      setStatus("countdown...");
      btnCapture.disabled = true;
      break;

    case State.CAPTURED:
      setStatus("photo captured");
      btnProcess.disabled = false;
      break;

    case State.PROCESSING:
      setStatus("processing...");
      btnCapture.disabled = true;
      btnProcess.disabled = true;
      statusCursor.style.display = "none";
      if (isPublicView) {
        stagePrompt.innerHTML = "thinking&hellip;";
      }
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
      if (isPublicView) {
        stagePrompt.hidden = true;
        stageProgressWrap.hidden = false;
        stageProgressFill.style.width = "0%";
        stageProgressLabel.textContent = "0%";
      }
      break;

    case State.DONE:
      setStatus("done!");
      progressFill.style.width = "100%";
      progressLabel.textContent = "100%";
      progressWrap.hidden = false;
      btnPrint.disabled = false;
      if (isPublicView) {
        stageProgressFill.style.width = "100%";
        stageProgressLabel.textContent = "100%";
      }
      break;
  }
}

function setStatus(msg) { statusText.textContent = msg; }

// ── Printer status ─────────────────────────────────────
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

// ── Boot ───────────────────────────────────────────────
// Place video in stage view initially
stageVideoWrap.appendChild(videoEl);

initCamera();
checkPrinterStatus();
setInterval(checkPrinterStatus, 10000);

// Disconnect serial on browser reload / tab close so next session gets a clean connect
window.addEventListener("beforeunload", () => {
  navigator.sendBeacon("/disconnect");
});

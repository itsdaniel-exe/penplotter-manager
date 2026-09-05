"use strict";

// Values that describe this specific machine or the current job. Everything
// here is persisted to localStorage, so a calibration session survives a
// refresh - the machine settings especially were expensive to establish.
const DEFAULTS = {
  bed: { widthMm: 195, heightMm: 300 },
  page: { widthMm: 195, heightMm: 295, marginTopMm: 15, marginBottomMm: 15, marginLeftMm: 12, marginRightMm: 12 },
  origin: { xMm: 0, yMm: 0 },
  style: { font: "HersheySansMed", fontSizeMm: 5, lineSpacingMm: 8, align: "left", autoFit: true, targetPages: 1 },
  // Handwriting realism. seed keeps a page reproducible, so what Preview
  // shows is exactly what the pen draws - Reshuffle changes it deliberately.
  hand: { enabled: true, amount: 1.0, seed: 7 },
  // invertX/invertY/swapPen were all confirmed against the real machine by
  // test print; see HANDOFF.md before changing the defaults.
  machine: { servoUp: 10, servoDown: 50, travelFeed: 10000, drawFeed: 2500, invertY: false, invertX: true, swapPen: true },
  stepMm: "5",
};

const MACHINE_KEYS = ["bed", "machine"]; // what "Reset to defaults" in Settings covers
const STORAGE_KEY = "penplotter.console.v1";
const LOG_MAX_LINES = 300;

const state = {
  ...structuredClone(DEFAULTS),
  pageSizes: {},
  inputMode: "text",
  docPath: null,
  svgPath: null,
  ws: null,
  connected: false,
  connecting: false,
  port: null,
  position: { x: 0, y: 0 },
  penDown: false,
  jobRunning: false,
  playback: null,    // {pages, pageIndex, playing, simTime, speed, ...}
  lastPreview: null, // kept so a window resize can re-render at the new size
  bedCal: { a: null, b: null }, // corners marked during bed-size calibration
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- init --

async function init() {
  loadSettings();
  wireStaticControls();
  await Promise.all([loadFonts(), loadPorts(), loadPageSizes()]);
  applyStateToForm();
  readFormIntoState();
  drawBedDiagram();
  // Give the (empty) stage the right page shape immediately - otherwise the
  // canvas shows at its intrinsic bitmap size until the first Preview.
  watchStageSize();
  redrawStage();
  log("Console ready. Nothing is connected yet - Preview and Simulate work offline.", "ok");
}

async function loadFonts() {
  const { fonts } = await (await fetch("/api/fonts")).json();
  $("fontSelect").innerHTML = fonts.map((f) => `<option value="${f}">${f}</option>`).join("");
}

async function loadPorts() {
  const { ports } = await (await fetch("/api/ports")).json();
  $("portSelect").innerHTML = ports
    .map((p) => `<option value="${p}">${p === "SIMULATOR" ? "Simulator (no hardware)" : p}</option>`)
    .join("");
}

async function loadPageSizes() {
  const { sizes } = await (await fetch("/api/page-sizes")).json();
  state.pageSizes = sizes;
  const opts = Object.entries(sizes)
    .map(([name, d]) => `<option value="${name}">${name} - ${d.widthMm}×${d.heightMm}mm</option>`)
    .join("");
  $("pagePreset").innerHTML = opts + `<option value="custom">Custom</option>`;
}

// --------------------------------------------------------- persistence --

function loadSettings() {
  let saved;
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch (e) {
    return; // corrupt or unavailable storage - defaults are fine
  }
  if (!saved) return;
  for (const key of ["bed", "page", "origin", "style", "machine", "hand"]) {
    if (saved[key] && typeof saved[key] === "object") Object.assign(state[key], saved[key]);
  }
  if (saved.stepMm) state.stepMm = saved.stepMm;
}

function saveSettings() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      bed: state.bed, page: state.page, origin: state.origin,
      style: state.style, machine: state.machine, hand: state.hand, stepMm: state.stepMm,
    }));
  } catch (e) { /* private mode / storage full - not worth interrupting for */ }
}

function log(msg, kind) {
  const box = $("logBox");
  const div = document.createElement("div");
  div.className = "line" + (kind ? " " + kind : "");
  div.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  box.appendChild(div);
  while (box.childElementCount > LOG_MAX_LINES) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

// --------------------------------------------------------- form <-> state --

function applyStateToForm() {
  $("bedW").value = state.bed.widthMm;
  $("bedH").value = state.bed.heightMm;
  $("pageW").value = state.page.widthMm;
  $("pageH").value = state.page.heightMm;
  $("mTop").value = state.page.marginTopMm;
  $("mBottom").value = state.page.marginBottomMm;
  $("mLeft").value = state.page.marginLeftMm;
  $("mRight").value = state.page.marginRightMm;
  $("originX").value = state.origin.xMm;
  $("originY").value = state.origin.yMm;
  // A saved font may no longer exist on disk; fall back to whatever is first.
  const fontSel = $("fontSelect");
  fontSel.value = state.style.font;
  if (!fontSel.value) fontSel.selectedIndex = 0;
  state.style.font = fontSel.value;
  $("fontSize").value = state.style.fontSizeMm;
  $("lineSpacing").value = state.style.lineSpacingMm;
  $("alignSelect").value = state.style.align;
  $("autoFit").checked = state.style.autoFit;
  $("handEnabled").checked = state.hand.enabled;
  $("handAmount").value = state.hand.amount;
  syncHandwritingControls();
  $("servoUp").value = state.machine.servoUp;
  $("servoDown").value = state.machine.servoDown;
  $("travelFeed").value = state.machine.travelFeed;
  $("drawFeed").value = state.machine.drawFeed;
  $("invertY").checked = state.machine.invertY;
  $("invertX").checked = state.machine.invertX;
  $("swapPen").checked = state.machine.swapPen;
  $("stepSize").value = state.stepMm;
  syncPagePreset();
}

function readFormIntoState() {
  state.bed.widthMm = num("bedW");
  state.bed.heightMm = num("bedH");
  state.page.widthMm = num("pageW");
  state.page.heightMm = num("pageH");
  state.page.marginTopMm = num("mTop");
  state.page.marginBottomMm = num("mBottom");
  state.page.marginLeftMm = num("mLeft");
  state.page.marginRightMm = num("mRight");
  state.origin.xMm = num("originX");
  state.origin.yMm = num("originY");
  state.style.font = $("fontSelect").value;
  state.style.fontSizeMm = num("fontSize");
  state.style.lineSpacingMm = num("lineSpacing");
  state.style.align = $("alignSelect").value;
  state.machine.servoUp = num("servoUp");
  state.machine.servoDown = num("servoDown");
  state.machine.travelFeed = num("travelFeed");
  state.machine.drawFeed = num("drawFeed");
  state.machine.invertY = $("invertY").checked;
  state.machine.invertX = $("invertX").checked;
  state.machine.swapPen = $("swapPen").checked;
  state.style.autoFit = $("autoFit").checked;
  state.hand.enabled = $("handEnabled").checked;
  state.hand.amount = parseFloat($("handAmount").value) || 1;
  state.stepMm = $("stepSize").value;
}

const HAND_AMOUNT_LABELS = [
  [0.6, "barely there"],
  [1.2, "natural"],
  [1.6, "loose"],
  [Infinity, "casual"],
];

/** Size/spacing are computed when auto-fit is on, so show them read-only
 *  rather than letting the operator edit a value that gets overwritten. */
function syncHandwritingControls() {
  const auto = $("autoFit").checked;
  $("fontSize").disabled = auto;
  $("lineSpacing").disabled = auto;
  $("autoFitNote").hidden = !auto;
  if (auto && !$("autoFitNote").textContent) {
    $("autoFitNote").textContent = "Size is chosen on Preview.";
  }

  const on = $("handEnabled").checked;
  $("handControls").hidden = !on;
  const amount = parseFloat($("handAmount").value) || 1;
  $("handAmountLabel").textContent = HAND_AMOUNT_LABELS.find(([max]) => amount < max)[1];
}

function num(id) {
  return parseFloat($(id).value) || 0;
}

/** Show which named page size matches the current width/height, if any. */
function syncPagePreset() {
  const match = Object.entries(state.pageSizes).find(
    ([, d]) => Math.abs(d.widthMm - state.page.widthMm) < 0.51 && Math.abs(d.heightMm - state.page.heightMm) < 0.51
  );
  $("pagePreset").value = match ? match[0] : "custom";
}

function currentInputPayload() {
  if (state.inputMode === "svg") return { svgPath: state.svgPath };
  if (state.inputMode === "file") return { docPath: state.docPath };
  return { text: $("textInput").value };
}

function currentPagePayload() {
  return {
    widthMm: state.page.widthMm, heightMm: state.page.heightMm,
    marginTopMm: state.page.marginTopMm, marginBottomMm: state.page.marginBottomMm,
    marginLeftMm: state.page.marginLeftMm, marginRightMm: state.page.marginRightMm,
  };
}

function currentStylePayload() {
  return { ...state.style };
}

function currentHandPayload() {
  return { ...state.hand };
}

/** Auto-fit computes the size server-side; reflect what it chose. */
function showResolvedSize(data) {
  if (!state.style.autoFit || data.fontSizeMm === undefined) return;
  state.style.fontSizeMm = data.fontSizeMm;
  state.style.lineSpacingMm = data.lineSpacingMm;
  $("fontSize").value = data.fontSizeMm;
  $("lineSpacing").value = data.lineSpacingMm;
  $("autoFitNote").textContent =
    `Fitted to ${data.fontSizeMm}mm text, ${data.lineSpacingMm}mm line spacing.`;
  saveSettings();
}

function currentMachinePayload() {
  return {
    servoUp: state.machine.servoUp, servoDown: state.machine.servoDown,
    travelFeed: state.machine.travelFeed, drawFeed: state.machine.drawFeed,
    invertY: state.machine.invertY, invertX: state.machine.invertX, swapPen: state.machine.swapPen,
    originXMm: state.origin.xMm, originYMm: state.origin.yMm,
  };
}

// ----------------------------------------------------------- bed diagram --

function drawBedDiagram() {
  const svg = $("bedSvg");
  const bedW = state.bed.widthMm, bedH = state.bed.heightMm;
  if (!(bedW > 0 && bedH > 0)) return;
  const pad = Math.max(bedW, bedH) * 0.06;
  svg.setAttribute("viewBox", `${-pad} ${-pad} ${bedW + pad * 2} ${bedH + pad * 2}`);
  const toY = (mmY) => bedH - mmY;

  const ox = state.origin.xMm, oy = state.origin.yMm;
  const pageTop = oy + state.page.heightMm;
  const contentTop = pageTop - state.page.marginTopMm;
  const contentBottom = oy + state.page.marginBottomMm;
  const contentLeft = ox + state.page.marginLeftMm;
  const contentRight = ox + state.page.widthMm - state.page.marginRightMm;

  const sw = bedW * 0.004;
  let s = "";
  s += `<rect x="0" y="0" width="${bedW}" height="${bedH}" fill="var(--paper)" stroke="var(--paper-edge)" stroke-width="${sw}" stroke-dasharray="${bedW * 0.012},${bedW * 0.012}"/>`;
  s += `<rect x="${ox}" y="${toY(pageTop)}" width="${state.page.widthMm}" height="${state.page.heightMm}" fill="none" stroke="var(--accent)" stroke-width="${sw * 1.5}"/>`;
  s += `<rect x="${contentLeft}" y="${toY(contentTop)}" width="${Math.max(0, contentRight - contentLeft)}" height="${Math.max(0, contentTop - contentBottom)}" fill="none" stroke="var(--ink-faint)" stroke-width="${sw}" stroke-dasharray="${bedW * 0.008},${bedW * 0.008}"/>`;
  s += `<circle cx="${ox}" cy="${toY(oy)}" r="${bedW * 0.01}" fill="var(--accent)"/>`;
  s += `<text x="${ox + bedW * 0.018}" y="${toY(oy) - bedW * 0.012}" font-size="${bedW * 0.032}" fill="var(--ink-faint)" font-family="var(--font-mono)">0,0</text>`;

  const px = state.position.x, py = state.position.y;
  s += `<circle cx="${px}" cy="${toY(py)}" r="${bedW * 0.013}" fill="${state.penDown ? "var(--accent)" : "var(--ink-faint)"}" stroke="var(--panel)" stroke-width="${sw}"/>`;

  svg.innerHTML = s;
  $("dimNote").textContent =
    `bed ${bedW}×${bedH}mm · page ${state.page.widthMm}×${state.page.heightMm}mm at (${ox},${oy})`;
  $("posLabel").textContent = `${px.toFixed(1)}, ${py.toFixed(1)}`;
}

/** Called whenever a job/layout field changes. */
function refreshLayout() {
  readFormIntoState();
  syncPagePreset();
  drawBedDiagram();
  saveSettings();
}

// -------------------------------------------------------------- preview --

function drawStaticStrokes(strokes, pageWmm, pageHmm) {
  state.lastPreview = { strokes, pageWmm, pageHmm };
  const canvas = $("stageCanvas");
  const ctx = prepareCanvas(canvas, pageWmm, pageHmm);
  const { sx, sy } = ctx.scaleToPage;
  ctx.lineWidth = 1;
  strokes.forEach((stroke) => {
    if (stroke.length < 2) return;
    ctx.beginPath();
    ctx.moveTo(stroke[0][0] * sx, stroke[0][1] * sy);
    for (let i = 1; i < stroke.length; i++) ctx.lineTo(stroke[i][0] * sx, stroke[i][1] * sy);
    ctx.stroke();
  });
  $("transport").hidden = true;
  $("readout").hidden = true;
  $("warnBox").hidden = true;
}

/** Size the canvas to the page aspect and return a ready-to-draw 2D context.
 *  Fits the page within both the panel width and the viewport height - a tall
 *  page (295mm on a 195mm sheet) otherwise runs off the bottom of the screen. */
function prepareCanvas(canvas, pageWmm, pageHmm) {
  const aspect = pageHmm / pageWmm;
  // innerHeight can read 0 mid-navigation; don't let that collapse the stage.
  const viewportH = window.innerHeight || 720;
  const maxHeight = Math.max(320, viewportH * 0.62);
  let cssWidth = canvas.parentElement.clientWidth || 440;
  let cssHeight = cssWidth * aspect;
  if (cssHeight > maxHeight) {
    cssHeight = maxHeight;
    cssWidth = cssHeight / aspect;
  }
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.style.width = cssWidth + "px";
  canvas.style.height = cssHeight + "px";
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);
  const style = getComputedStyle(document.documentElement);
  ctx.strokeStyle = style.getPropertyValue("--ink").trim();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.scaleToPage = { sx: cssWidth / pageWmm, sy: cssHeight / pageHmm, cssWidth, cssHeight, style };
  return ctx;
}

async function doPreview() {
  readFormIntoState();
  $("stageTitle").textContent = "Preview";
  $("stageHint").textContent = "Renders the formatted page - nothing is sent anywhere.";
  stopPlayback();
  try {
    const r = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: currentInputPayload(), page: currentPagePayload(),
        style: currentStylePayload(), hand: currentHandPayload(),
      }),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    showResolvedSize(data);
    const allStrokes = data.pages.flatMap((p) => p.strokes);
    drawStaticStrokes(allStrokes, data.pageWidthMm, data.pageHeightMm);
    log(`Preview: ${data.pages.length} page(s), ${allStrokes.length} strokes.`, "ok");
  } catch (e) {
    log("Preview failed: " + e.message, "warn");
  }
}

// -------------------------------------------------------------- simulate --

async function doSimulate() {
  readFormIntoState();
  $("stageTitle").textContent = "Simulate";
  $("stageHint").textContent = "Replayed through a simulated GRBL board - no hardware needed.";
  stopPlayback();
  try {
    const r = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: currentInputPayload(), page: currentPagePayload(), style: currentStylePayload(),
        machine: currentMachinePayload(), bed: { widthMm: state.bed.widthMm, heightMm: state.bed.heightMm },
        hand: currentHandPayload(),
      }),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    showResolvedSize(data);
    startPlayback(data);
    const warnCount = data.pages.reduce((n, p) => n + p.boundsWarnings.length, 0);
    $("warnBox").hidden = !warnCount;
    if (warnCount) {
      $("warnBox").textContent = `${warnCount} point(s) fall outside the ${state.bed.widthMm}×${state.bed.heightMm}mm work area. Shrink the page or margins, or move the origin.`;
    }
    log(`Simulate: ${data.pages.length} page(s) streamed through the simulator, ok.`, "ok");
  } catch (e) {
    log("Simulate failed: " + e.message, "warn");
  }
}

function startPlayback(data) {
  state.playback = {
    pages: data.pages, pageIndex: 0, playing: false, simTime: 0,
    pageWmm: data.pageWidthMm, pageHmm: data.pageHeightMm,
  };
  $("transport").hidden = false;
  $("readout").hidden = false;
  setupSpeed(data.pages[0].totalTimeS);
  renderPlaybackFrame(0);
}

function setupSpeed(totalTimeS) {
  const pb = state.playback;
  pb.speed = totalTimeS > 25 ? Math.max(4, Math.round(totalTimeS / 20)) : 1;
  $("scrub").max = String(Math.max(1, Math.round(totalTimeS * 10)));
  $("scrub").value = "0";
}

function currentPageData() {
  return state.playback.pages[state.playback.pageIndex];
}

function fmtTime(s) {
  s = Math.max(0, s);
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function indexForTime(trace, t) {
  let lo = 0, hi = trace.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (trace[mid].t <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

function renderPlaybackFrame(t) {
  const pb = state.playback;
  const page = currentPageData();
  const trace = page.trace;
  const idx = indexForTime(trace, t);

  const ctx = prepareCanvas($("stageCanvas"), pb.pageWmm, pb.pageHmm);
  const { sx, sy, style } = ctx.scaleToPage;
  ctx.lineWidth = 1.1;
  ctx.beginPath();
  let started = false, wasDown = false;
  for (let i = 1; i <= idx && i < trace.length; i++) {
    const prev = trace[i - 1], cur = trace[i];
    if (cur.pen) {
      if (!started || !wasDown) ctx.moveTo(prev.x * sx, prev.y * sy);
      ctx.lineTo(cur.x * sx, cur.y * sy);
      started = true;
    }
    wasDown = cur.pen;
  }
  ctx.stroke();

  const cur = trace[idx];
  if (cur) {
    ctx.fillStyle = cur.pen ? style.getPropertyValue("--accent").trim() : style.getPropertyValue("--ink-faint").trim();
    ctx.beginPath();
    ctx.arc(cur.x * sx, cur.y * sy, cur.pen ? 3 : 2.2, 0, Math.PI * 2);
    ctx.fill();
    setPenBadge(cur.pen);
  }

  $("rTime").textContent = fmtTime(t);
  $("rDrawn").textContent = Math.round(sumSegments(trace, idx, true)) + "mm";
  $("rTravel").textContent = Math.round(sumSegments(trace, idx, false)) + "mm";
  $("rLines").textContent = page.lines;
  $("timeLabel").textContent = `${fmtTime(t)} / ${fmtTime(page.totalTimeS)}`;
  $("scrub").value = String(Math.round(t * 10));
}

/** Total distance up to `idx` of either pen-down (drawing) or pen-up (travel) moves. */
function sumSegments(trace, idx, penDown) {
  let d = 0;
  for (let i = 1; i <= idx && i < trace.length; i++) {
    if (!!trace[i].pen === penDown) d += Math.hypot(trace[i].x - trace[i - 1].x, trace[i].y - trace[i - 1].y);
  }
  return d;
}

function setPenBadge(down) {
  state.penDown = down;
  $("penBadge").classList.toggle("down", down);
  $("penBadgeLabel").textContent = down ? "pen down" : "pen up";
}

let rafId = null;
function playbackStep(ts) {
  const pb = state.playback;
  if (!pb || !pb.playing) return;
  if (!pb._lastFrame) pb._lastFrame = ts;
  const dt = (ts - pb._lastFrame) / 1000;
  pb._lastFrame = ts;
  const page = currentPageData();
  pb.simTime = Math.min(page.totalTimeS, pb.simTime + dt * pb.speed);
  renderPlaybackFrame(pb.simTime);
  if (pb.simTime >= page.totalTimeS) {
    pb.playing = false;
    $("playBtn").textContent = "▶";
    return;
  }
  rafId = requestAnimationFrame(playbackStep);
}

function stopPlayback() {
  if (state.playback) state.playback.playing = false;
  if (rafId) cancelAnimationFrame(rafId);
  $("playBtn").textContent = "▶";
}

// --------------------------------------------------------- ws / session --

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/session`;
}

function setStatus(kind, text) {
  $("statusPill").className = "status-pill" + (kind ? " " + kind : "");
  $("statusText").textContent = text;
}

// The connect handshake sends '$$', so the banner is GRBL's whole settings
// dump. These are the ones that decide whether calibration is even possible:
// travel limits, whether homing/soft-limits will refuse motion, and the
// steps/mm that determine whether a 1mm jog really moves 1mm.
const GRBL_KEY_SETTINGS = {
  20: "soft limits (1 = refuses moves past max travel)",
  21: "hard limits (1 = limit switches wired)",
  22: "homing cycle (1 = boots into Alarm until homed/unlocked)",
  100: "X steps/mm",
  101: "Y steps/mm",
  130: "X max travel (mm)",
  131: "Y max travel (mm)",
};

function logGrblSettings(banner) {
  const settings = {};
  banner.split("\n").forEach((line) => {
    const m = line.trim().match(/^\$(\d+)\s*=\s*([\d.-]+)/);
    if (m) settings[Number(m[1])] = m[2];
  });

  const found = Object.keys(GRBL_KEY_SETTINGS).filter((k) => settings[k] !== undefined);
  if (!found.length) {
    log("GRBL replied, but no $ settings were parsed from it: " + banner.split("\n")[0], "warn");
    return;
  }
  log(`GRBL settings read (${Object.keys(settings).length} values). Key ones:`, "ok");
  found.forEach((k) => log(`  $${k} = ${settings[k]}  - ${GRBL_KEY_SETTINGS[k]}`));

  // $130/$131 are only enforced when soft limits are on. With $20=0 they are
  // inert, and on this machine they still read GRBL's stock 200.000 default -
  // so they say nothing about the real work area. Don't imply otherwise.
  if (settings[130] !== undefined && settings[131] !== undefined && settings[20] === "1") {
    log(`Firmware enforces a ${settings[130]} x ${settings[131]}mm travel limit (soft limits are on).`, "ok");
  } else if (settings[130] !== undefined) {
    log(`Ignore $130/$131 (${settings[130]} x ${settings[131]}) - soft limits are off, so they're unused stock defaults, not the real work area. The measured size in Settings is the one that counts.`, "ok");
  }
  if (settings[22] === "1") {
    $("jogHome").disabled = false;
    $("jogHome").title = "Run GRBL's homing cycle";
    log("Homing is enabled, so GRBL starts in Alarm and will refuse to jog. Click Home to home it, or Unlock to override.", "warn");
  } else if (settings[22] !== undefined) {
    // $H seeks a limit switch. Without one it can only ever return an error,
    // so don't leave a button sitting there that cannot possibly work.
    $("jogHome").disabled = true;
    $("jogHome").title = "No homing switches on this machine ($22=0) - use \"Zero here\" and \"Go to zero\" instead";
    log("No homing switches on this machine ($22=0), so Home is disabled - use \"Zero here\" to set a reference point and \"Go to zero\" to return to it.", "ok");
  }
  if (settings[20] === "1") {
    log("Soft limits are on - moves beyond max travel get rejected instead of crashing the gantry.", "ok");
  }
}

const LIVE_CONTROLS = [
  "jogXp", "jogXm", "jogYp", "jogYm", "jogHome", "jogZero", "goZero",
  "penUpBtn", "penDownBtn", "unlockBtn", "runBtn", "markCornerA", "markCornerB",
];

function setLiveControlsEnabled(enabled) {
  LIVE_CONTROLS.forEach((id) => { $(id).disabled = !enabled; });
  $("runBtn").title = enabled ? "" : "Connect first";
  if (!enabled) setJobControls(false);
}

/** Pause/Resume/Cancel only make sense while a job is actually streaming. */
function setJobControls(running, paused) {
  $("pauseBtn").disabled = !running || !!paused;
  $("resumeBtn").disabled = !running || !paused;
  $("cancelBtn").disabled = !running;
}

function connect() {
  readFormIntoState();
  const port = $("portSelect").value;
  state.connecting = true;
  setStatus("connecting", `connecting to ${port}...`);
  $("connectBtn").disabled = true;

  // Close any socket this page already had. Without this, a second click on
  // Connect leaves the old session alive on the server still holding the
  // serial port, and the new one fails with "Access is denied".
  if (state.ws) {
    try {
      state.ws.onclose = null;
      state.ws.close();
    } catch (e) { /* already dead - nothing to clean up */ }
    state.ws = null;
  }

  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  ws.onopen = () => {
    ws.send(JSON.stringify({
      action: "connect", port, baud: 115200,
      bed: { widthMm: state.bed.widthMm, heightMm: state.bed.heightMm },
    }));
  };
  ws.onmessage = (evt) => handleWsMessage(JSON.parse(evt.data));
  ws.onclose = () => {
    state.connected = false;
    state.connecting = false;
    state.jobRunning = false;
    setStatus("", "disconnected");
    $("connectBtn").textContent = "Connect";
    $("connectBtn").disabled = false;
    setLiveControlsEnabled(false);
  };
  ws.onerror = () => log("WebSocket error.", "warn");
}

function disconnect() {
  wsAction({ action: "disconnect" });
}

function handleWsMessage(msg) {
  switch (msg.type) {
    case "connected":
      state.connected = true;
      state.connecting = false;
      state.port = msg.port;
      setStatus("connected", msg.port === "SIMULATOR" ? "simulator" : `connected: ${msg.port}`);
      $("connectBtn").textContent = "Disconnect";
      $("connectBtn").disabled = false;
      setLiveControlsEnabled(true);
      log(`Connected to ${msg.port}.`, "ok");
      if (msg.tookOver) {
        log("Took the port over from another tab that still had it open - that tab is now disconnected.", "warn");
      }
      if (msg.banner) logGrblSettings(msg.banner);
      break;
    case "unlocked":
      log("Alarm cleared ($X). The machine will accept motion again.", "ok");
      break;
    case "zeroed":
      log(`Zero set here. This spot is now 0,0 (it was ${msg.fromX.toFixed(1)}, ${msg.fromY.toFixed(1)}). Nothing moves - this only sets the reference.`, "ok");
      break;
    case "movedToZero": {
      const dist = Math.hypot(msg.fromX, msg.fromY);
      log(dist < 0.05
        ? "Already at zero, so nothing moved."
        : `Returned to zero from ${msg.fromX.toFixed(1)}, ${msg.fromY.toFixed(1)} (${dist.toFixed(1)}mm of travel).`, "ok");
      break;
    }
    case "disconnected":
      log("Disconnected.", "ok");
      break;
    case "error":
      log(msg.message, "warn");
      state.connecting = false;
      $("connectBtn").disabled = false;
      break;
    case "position":
      state.position = { x: msg.x, y: msg.y };
      setPenBadge(!!msg.pen);
      drawBedDiagram();
      break;
    case "jobStarted":
      state.jobRunning = true;
      setJobControls(true, false);
      log(`Run started: ${msg.totalPages} page(s).`, "ok");
      break;
    case "progress":
      state.position = { x: msg.x, y: msg.y };
      setPenBadge(!!msg.pen);
      drawBedDiagram();
      $("stageTitle").textContent = `Running - page ${msg.page}/${msg.totalPages}`;
      $("rLines").textContent = `${msg.line} / ${msg.totalLines}`;
      break;
    case "pageComplete":
      log(msg.cancelled
        ? `Page ${msg.page}/${msg.totalPages} cancelled partway through.`
        : `Page ${msg.page}/${msg.totalPages} complete.`, msg.cancelled ? "warn" : "ok");
      if (msg.boundsWarnings && msg.boundsWarnings.length) {
        log(`${msg.boundsWarnings.length} point(s) exceeded the work area on that page.`, "warn");
      }
      break;
    case "jobComplete":
      state.jobRunning = false;
      setJobControls(false);
      log(msg.cancelled ? "Job cancelled." : "Job complete.", msg.cancelled ? "warn" : "ok");
      $("stageTitle").textContent = msg.cancelled ? "Cancelled" : "Done";
      break;
    default:
      break;
  }
}

// ------------------------------------------------------------- settings --

function openSettings() {
  $("settingsModal").hidden = false;
}

function closeSettings() {
  $("settingsModal").hidden = true;
  refreshLayout();
}

function resetMachineSettings() {
  MACHINE_KEYS.forEach((key) => Object.assign(state[key], structuredClone(DEFAULTS[key])));
  applyStateToForm();
  refreshLayout();
  log("Machine settings reset to the calibrated defaults (work area, orientation, pen, speeds).", "ok");
}

// -------------------------------------------------------------- controls --

function wireStaticControls() {
  // input mode tabs
  document.querySelectorAll("#inputTabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#inputTabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.inputMode = btn.dataset.mode;
      $("textPane").hidden = state.inputMode !== "text";
      $("filePane").hidden = state.inputMode !== "file";
      $("svgPane").hidden = state.inputMode !== "svg";
    });
  });

  $("fileInput").addEventListener("change", (e) => uploadFile(e.target.files[0], "doc"));
  $("svgInput").addEventListener("change", (e) => uploadFile(e.target.files[0], "svg"));

  // any layout/style field redraws the diagram and persists
  ["bedW", "bedH", "pageW", "pageH", "originX", "originY", "mTop", "mBottom", "mLeft", "mRight",
   "fontSize", "lineSpacing", "servoUp", "servoDown", "travelFeed", "drawFeed"].forEach((id) => {
    $(id).addEventListener("input", refreshLayout);
  });
  ["fontSelect", "alignSelect", "stepSize", "invertX", "invertY", "swapPen"].forEach((id) => {
    $(id).addEventListener("change", refreshLayout);
  });

  ["autoFit", "handEnabled"].forEach((id) => {
    $(id).addEventListener("change", () => { syncHandwritingControls(); refreshLayout(); });
  });
  $("handAmount").addEventListener("input", () => { syncHandwritingControls(); refreshLayout(); });
  $("handReshuffle").addEventListener("click", () => {
    state.hand.seed = Math.floor(Math.random() * 100000);
    saveSettings();
    log("Reshuffled the handwriting - Preview again to see it.", "ok");
    doPreview();
  });

  $("pagePreset").addEventListener("change", () => {
    const size = state.pageSizes[$("pagePreset").value];
    if (!size) return; // "Custom" - leave the numbers alone
    $("pageW").value = size.widthMm;
    $("pageH").value = size.heightMm;
    refreshLayout();
  });

  $("centerBtn").addEventListener("click", () => {
    readFormIntoState();
    $("originX").value = ((state.bed.widthMm - state.page.widthMm) / 2).toFixed(1);
    $("originY").value = ((state.bed.heightMm - state.page.heightMm) / 2).toFixed(1);
    refreshLayout();
  });
  $("cornerBtn").addEventListener("click", () => {
    $("originX").value = 0;
    $("originY").value = 0;
    refreshLayout();
  });

  $("previewBtn").addEventListener("click", doPreview);
  $("simulateBtn").addEventListener("click", doSimulate);
  $("clearLogBtn").addEventListener("click", () => { $("logBox").innerHTML = ""; });

  // settings modal
  $("settingsBtn").addEventListener("click", openSettings);
  $("settingsClose").addEventListener("click", closeSettings);
  $("settingsDone").addEventListener("click", closeSettings);
  $("settingsModal").addEventListener("click", (e) => {
    if (e.target === $("settingsModal")) closeSettings(); // backdrop only
  });
  $("resetSettingsBtn").addEventListener("click", resetMachineSettings);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("settingsModal").hidden) closeSettings();
  });

  // playback transport
  $("playBtn").addEventListener("click", () => {
    const pb = state.playback;
    if (!pb) return;
    if (pb.simTime >= currentPageData().totalTimeS) pb.simTime = 0;
    pb.playing = !pb.playing;
    pb._lastFrame = null;
    $("playBtn").textContent = pb.playing ? "⏸" : "▶";
    if (pb.playing) rafId = requestAnimationFrame(playbackStep);
  });
  $("scrub").addEventListener("input", () => {
    const pb = state.playback;
    if (!pb) return;
    pb.playing = false;
    $("playBtn").textContent = "▶";
    pb.simTime = Number($("scrub").value) / 10;
    renderPlaybackFrame(pb.simTime);
  });

  $("connectBtn").addEventListener("click", () => {
    if (state.connected || state.connecting) disconnect(); else connect();
  });

  // jog
  const step = () => parseFloat($("stepSize").value) || 1;
  $("jogXp").addEventListener("click", () => sendJog(step(), 0));
  $("jogXm").addEventListener("click", () => sendJog(-step(), 0));
  $("jogYp").addEventListener("click", () => sendJog(0, step()));
  $("jogYm").addEventListener("click", () => sendJog(0, -step()));
  $("jogHome").addEventListener("click", () => wsAction({ action: "home" }));
  $("jogZero").addEventListener("click", () => wsAction({ action: "zero" }));
  $("goZero").addEventListener("click", () => wsAction({ action: "goZero" }));

  // Send the whole Machine panel so servo values and the swap-pen setting
  // actually change what these buttons do.
  $("penUpBtn").addEventListener("click", () => {
    readFormIntoState();
    wsAction({ action: "penUp", machine: currentMachinePayload() });
  });
  $("penDownBtn").addEventListener("click", () => {
    readFormIntoState();
    wsAction({ action: "penDown", machine: currentMachinePayload() });
  });
  $("unlockBtn").addEventListener("click", () => wsAction({ action: "unlock" }));

  $("markCornerA").addEventListener("click", () => markCorner("a"));
  $("markCornerB").addEventListener("click", () => markCorner("b"));

  $("pauseBtn").addEventListener("click", () => {
    wsAction({ action: "pause" });
    setJobControls(state.jobRunning, true);
    log("Paused.", "ok");
  });
  $("resumeBtn").addEventListener("click", () => {
    wsAction({ action: "resume" });
    setJobControls(state.jobRunning, false);
    log("Resumed.", "ok");
  });
  $("cancelBtn").addEventListener("click", () => wsAction({ action: "cancel" }));

  $("runBtn").addEventListener("click", () => {
    readFormIntoState();
    wsAction({
      action: "run",
      input: currentInputPayload(), page: currentPagePayload(),
      style: currentStylePayload(), machine: currentMachinePayload(),
      hand: currentHandPayload(),
    });
    $("stageTitle").textContent = "Running";
    stopPlayback();
  });
}

// Work-area calibration: jog to one physical corner, mark it, jog to the
// diagonally opposite corner, mark it - width/height are just the distance
// between the two marks. No manual arithmetic, no reporting numbers by hand.
function markCorner(which) {
  state.bedCal[which] = { x: state.position.x, y: state.position.y };
  const c = state.bedCal[which];
  log(`Corner ${which.toUpperCase()} marked at ${c.x.toFixed(1)}, ${c.y.toFixed(1)}.`, "ok");

  const { a, b } = state.bedCal;
  if (!a || !b) return;
  const widthMm = Math.abs(b.x - a.x);
  const heightMm = Math.abs(b.y - a.y);
  if (widthMm < 5 || heightMm < 5) {
    log("Corners A and B are on the same edge - they need to be diagonally opposite to give both width and height.", "warn");
    return;
  }
  $("bedW").value = widthMm.toFixed(1);
  $("bedH").value = heightMm.toFixed(1);
  refreshLayout();
  log(`Work area set to ${widthMm.toFixed(1)} x ${heightMm.toFixed(1)}mm from the two marked corners.`, "ok");
}

// The arrows should move the pen the way they point, as seen by someone
// standing at the machine. Which machine direction that is depends on how
// this build is wired - the same physical fact the flip X/Y settings encode,
// so derive it from them rather than keeping a second copy. Right = the
// direction page-x grows; up = the direction page-y shrinks.
function sendJog(dx, dy) {
  readFormIntoState();
  const xSign = state.machine.invertX ? -1 : 1;
  const ySign = state.machine.invertY ? 1 : -1;
  wsAction({ action: "jog", dx: dx * xSign, dy: dy * ySign, feed: 3000 });
}

function wsAction(payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(payload));
}

async function uploadFile(file, kind) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    if (kind === "svg") {
      state.svgPath = data.path;
      $("svgName").textContent = file.name;
    } else {
      state.docPath = data.path;
      $("fileName").textContent = file.name;
    }
    log(`Uploaded ${file.name}.`, "ok");
  } catch (e) {
    log(`Could not upload ${file.name}: ${e.message}`, "warn");
  }
}

/** Redraw whatever the stage is currently showing, at the current size. */
function redrawStage() {
  if (state.playback) renderPlaybackFrame(state.playback.simTime);
  else if (state.lastPreview) {
    const { strokes, pageWmm, pageHmm } = state.lastPreview;
    drawStaticStrokes(strokes, pageWmm, pageHmm);
  } else {
    prepareCanvas($("stageCanvas"), state.page.widthMm, state.page.heightMm);
  }
}

// The canvas is sized from its container, which isn't reliably measurable
// during init - so watch the container instead of measuring it once. Only
// width changes trigger a redraw: the canvas sets its own height, and
// reacting to that would loop.
function watchStageSize() {
  const frame = $("stageCanvas").parentElement;
  let lastWidth = 0;
  const onWidth = (w) => {
    if (!w || Math.round(w) === lastWidth) return;
    lastWidth = Math.round(w);
    redrawStage();
  };
  if (window.ResizeObserver) {
    new ResizeObserver((entries) => onWidth(entries[0].contentRect.width)).observe(frame);
  }
  window.addEventListener("resize", () => redrawStage());
}

init();

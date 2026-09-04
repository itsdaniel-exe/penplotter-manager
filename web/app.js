"use strict";

const state = {
  bed: { widthMm: 195, heightMm: 295 },
  page: { widthMm: 195, heightMm: 280, marginTopMm: 15, marginBottomMm: 15, marginLeftMm: 12, marginRightMm: 12 },
  origin: { xMm: 0, yMm: 0 },
  style: { font: "HersheySansMed", fontSizeMm: 5, lineSpacingMm: 8, align: "left" },
  machine: { servoUp: 10, servoDown: 50, travelFeed: 10000, drawFeed: 2500, invertY: true, invertX: false, swapPen: false },
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
  playback: null, // {pages, pageIndex, playing, simTime, speed, statsTotals}
  bedCal: { a: null, b: null }, // corners marked during bed-size calibration
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- init --

async function init() {
  wireStaticControls();
  await Promise.all([loadFonts(), loadPorts()]);
  readFormIntoState();
  drawBedDiagram();
  log("Console ready. Nothing is connected yet - Preview and Simulate work offline.", "ok");
}

async function loadFonts() {
  const r = await fetch("/api/fonts");
  const { fonts } = await r.json();
  const sel = $("fontSelect");
  sel.innerHTML = fonts.map((f) => `<option value="${f}">${f}</option>`).join("");
  sel.value = state.style.font;
}

async function loadPorts() {
  const r = await fetch("/api/ports");
  const { ports } = await r.json();
  const sel = $("portSelect");
  sel.innerHTML = ports
    .map((p) => `<option value="${p}">${p === "SIMULATOR" ? "Simulator (no hardware)" : p}</option>`)
    .join("");
}

function log(msg, kind) {
  const box = $("logBox");
  const div = document.createElement("div");
  div.className = "line" + (kind ? " " + kind : "");
  const t = new Date().toLocaleTimeString();
  div.textContent = `[${t}] ${msg}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// --------------------------------------------------------- form <-> state --

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
}

function num(id) {
  return parseFloat($(id).value) || 0;
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
  s += `<rect x="${contentLeft}" y="${toY(contentTop)}" width="${contentRight - contentLeft}" height="${contentTop - contentBottom}" fill="none" stroke="var(--ink-faint)" stroke-width="${sw}" stroke-dasharray="${bedW * 0.008},${bedW * 0.008}"/>`;
  s += `<circle cx="${ox}" cy="${toY(oy)}" r="${bedW * 0.01}" fill="var(--accent)"/>`;
  s += `<text x="${ox + bedW * 0.018}" y="${toY(oy) - bedW * 0.012}" font-size="${bedW * 0.032}" fill="var(--ink-faint)" font-family="var(--font-mono)">0,0</text>`;

  const px = state.position.x, py = state.position.y;
  s += `<circle cx="${px}" cy="${toY(py)}" r="${bedW * 0.013}" fill="${state.penDown ? "var(--accent)" : "var(--ink-faint)"}" stroke="var(--panel)" stroke-width="${sw}"/>`;

  svg.innerHTML = s;
  $("dimNote").textContent =
    `bed ${bedW}×${bedH}mm · page ${state.page.widthMm}×${state.page.heightMm}mm at (${ox},${oy})`;
  $("posLabel").textContent = `${px.toFixed(1)}, ${py.toFixed(1)}`;
}

// -------------------------------------------------------------- preview --

function drawStaticStrokes(strokesByPage, pageWmm, pageHmm) {
  const canvas = $("stageCanvas");
  sizeCanvasToPage(canvas, pageWmm, pageHmm);
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const cssW = canvas.width / dpr, cssH = canvas.height / dpr;
  ctx.clearRect(0, 0, cssW, cssH);
  const sx = cssW / pageWmm, sy = cssH / pageHmm;

  const style = getComputedStyle(document.documentElement);
  ctx.strokeStyle = style.getPropertyValue("--ink").trim();
  ctx.lineWidth = 1;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  strokesByPage.forEach((stroke) => {
    if (stroke.length < 2) return;
    ctx.beginPath();
    ctx.moveTo(stroke[0][0] * sx, stroke[0][1] * sy);
    for (let i = 1; i < stroke.length; i++) ctx.lineTo(stroke[i][0] * sx, stroke[i][1] * sy);
    ctx.stroke();
  });
  $("transport").style.display = "none";
  $("readout").style.display = "none";
  $("warnBox").style.display = "none";
}

function sizeCanvasToPage(canvas, pageWmm, pageHmm) {
  const cssWidth = canvas.parentElement.clientWidth;
  const cssHeight = cssWidth * (pageHmm / pageWmm);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.style.height = cssHeight + "px";
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);
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
      body: JSON.stringify({ input: currentInputPayload(), page: currentPagePayload(), style: currentStylePayload() }),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
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
      }),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    startPlayback(data);
    const warnCount = data.pages.reduce((n, p) => n + p.boundsWarnings.length, 0);
    if (warnCount) {
      $("warnBox").style.display = "block";
      $("warnBox").textContent = `${warnCount} point(s) fall outside the ${state.bed.widthMm}×${state.bed.heightMm}mm bed you set. Shrink the page/margins or move the origin.`;
    } else {
      $("warnBox").style.display = "none";
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
  $("transport").style.display = "flex";
  $("readout").style.display = "grid";
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

  const canvas = $("stageCanvas");
  sizeCanvasToPage(canvas, pb.pageWmm, pb.pageHmm);
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const cssW = canvas.width / dpr, cssH = canvas.height / dpr;
  ctx.clearRect(0, 0, cssW, cssH);
  const sx = cssW / pb.pageWmm, sy = cssH / pb.pageHmm;

  const style = getComputedStyle(document.documentElement);
  ctx.strokeStyle = style.getPropertyValue("--ink").trim();
  ctx.lineWidth = 1.1;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
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
  $("rDrawn").textContent = Math.round(sumDrawn(trace, idx)) + "mm";
  $("rTravel").textContent = Math.round(sumTravel(trace, idx)) + "mm";
  $("rLines").textContent = page.lines;
  $("timeLabel").textContent = `${fmtTime(t)} / ${fmtTime(page.totalTimeS)}`;
  $("scrub").value = String(Math.round(t * 10));
}

function sumDrawn(trace, idx) {
  let d = 0;
  for (let i = 1; i <= idx && i < trace.length; i++) {
    if (trace[i].pen) d += Math.hypot(trace[i].x - trace[i - 1].x, trace[i].y - trace[i - 1].y);
  }
  return d;
}
function sumTravel(trace, idx) {
  let d = 0;
  for (let i = 1; i <= idx && i < trace.length; i++) {
    if (!trace[i].pen) d += Math.hypot(trace[i].x - trace[i - 1].x, trace[i].y - trace[i - 1].y);
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
}

// --------------------------------------------------------- ws / session --

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/session`;
}

function setStatus(kind, text) {
  const pill = $("statusPill");
  pill.className = "status-pill" + (kind ? " " + kind : "");
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

  if (settings[130] !== undefined && settings[131] !== undefined) {
    log(`Firmware thinks travel is ${settings[130]} x ${settings[131]} mm - a starting guess for bed size, still verify it physically.`, "ok");
  }
  if (settings[22] === "1") {
    log("Homing is enabled, so GRBL starts in Alarm and will refuse to jog. Click Home to home it, or Unlock to override.", "warn");
  }
  if (settings[20] === "1") {
    log("Soft limits are on - moves beyond max travel get rejected instead of crashing the gantry.", "ok");
  }
}

function setLiveControlsEnabled(enabled) {
  ["jogXp", "jogXm", "jogYp", "jogYm", "jogHome", "jogZero", "penUpBtn", "penDownBtn", "unlockBtn", "runBtn", "markCornerA", "markCornerB"].forEach((id) => {
    $(id).disabled = !enabled;
  });
  $("runBtn").title = enabled ? "" : "Connect first";
}

function connect() {
  readFormIntoState();
  const port = $("portSelect").value;
  state.connecting = true;
  setStatus("connecting", `connecting to ${port}...`);
  $("connectBtn").disabled = true;

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
    setStatus("", "disconnected");
    $("connectBtn").textContent = "Connect";
    $("connectBtn").disabled = false;
    setLiveControlsEnabled(false);
  };
  ws.onerror = () => log("WebSocket error.", "warn");
}

function disconnect() {
  if (state.ws) state.ws.send(JSON.stringify({ action: "disconnect" }));
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
      if (msg.banner) logGrblSettings(msg.banner);
      break;
    case "unlocked":
      log("Alarm cleared ($X). The machine will accept motion again.", "ok");
      break;
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
      state.penDown = !!msg.pen;
      setPenBadge(state.penDown);
      drawBedDiagram();
      break;
    case "jobStarted":
      state.jobRunning = true;
      $("pauseBtn").disabled = false;
      $("cancelBtn").disabled = false;
      log(`Run started: ${msg.totalPages} page(s).`, "ok");
      break;
    case "progress":
      state.position = { x: msg.x, y: msg.y };
      state.penDown = !!msg.pen;
      setPenBadge(state.penDown);
      drawBedDiagram();
      $("stageTitle").textContent = `Running - page ${msg.page}/${msg.totalPages}`;
      $("rLines").textContent = `${msg.line} / ${msg.totalLines}`;
      break;
    case "pageComplete":
      log(msg.cancelled ? `Page ${msg.page}/${msg.totalPages} cancelled partway through.` : `Page ${msg.page}/${msg.totalPages} complete.`, msg.cancelled ? "warn" : "ok");
      if (msg.boundsWarnings && msg.boundsWarnings.length) {
        log(`${msg.boundsWarnings.length} point(s) exceeded the bed on that page.`, "warn");
      }
      break;
    case "jobComplete":
      state.jobRunning = false;
      $("pauseBtn").disabled = true;
      $("cancelBtn").disabled = true;
      log(msg.cancelled ? "Job cancelled." : "Job complete.", msg.cancelled ? "warn" : "ok");
      $("stageTitle").textContent = msg.cancelled ? "Cancelled" : "Done";
      break;
    default:
      break;
  }
}

// -------------------------------------------------------------- controls --

function wireStaticControls() {
  document.querySelectorAll("#inputTabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#inputTabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.inputMode = btn.dataset.mode;
      $("textPane").style.display = state.inputMode === "text" ? "block" : "none";
      $("filePane").style.display = state.inputMode === "file" ? "block" : "none";
      $("svgPane").style.display = state.inputMode === "svg" ? "block" : "none";
    });
  });

  $("fileInput").addEventListener("change", (e) => uploadFile(e.target.files[0], "doc"));
  $("svgInput").addEventListener("change", (e) => uploadFile(e.target.files[0], "svg"));

  ["bedW", "bedH", "pageW", "pageH", "originX", "originY", "mTop", "mBottom", "mLeft", "mRight"].forEach((id) => {
    $(id).addEventListener("input", () => {
      readFormIntoState();
      drawBedDiagram();
    });
  });

  $("centerBtn").addEventListener("click", () => {
    readFormIntoState();
    $("originX").value = ((state.bed.widthMm - state.page.widthMm) / 2).toFixed(1);
    $("originY").value = ((state.bed.heightMm - state.page.heightMm) / 2).toFixed(1);
    readFormIntoState();
    drawBedDiagram();
  });
  $("cornerBtn").addEventListener("click", () => {
    $("originX").value = 0;
    $("originY").value = 0;
    readFormIntoState();
    drawBedDiagram();
  });

  $("previewBtn").addEventListener("click", doPreview);
  $("simulateBtn").addEventListener("click", doSimulate);

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

  const step = () => parseFloat($("stepSize").value);
  $("jogXp").addEventListener("click", () => sendJog(step(), 0));
  $("jogXm").addEventListener("click", () => sendJog(-step(), 0));
  $("jogYp").addEventListener("click", () => sendJog(0, step()));
  $("jogYm").addEventListener("click", () => sendJog(0, -step()));
  $("jogHome").addEventListener("click", () => wsAction({ action: "home" }));
  $("jogZero").addEventListener("click", () => wsAction({ action: "zero" }));
  // Send the whole Machine panel so tuning servo values or the swap-pen
  // checkbox actually changes what these buttons do.
  $("penUpBtn").addEventListener("click", () => {
    readFormIntoState();
    wsAction({ action: "penUp", machine: currentMachinePayload() });
  });
  $("penDownBtn").addEventListener("click", () => {
    readFormIntoState();
    wsAction({ action: "penDown", machine: currentMachinePayload() });
  });
  $("unlockBtn").addEventListener("click", () => wsAction({ action: "unlock" }));

  // Bed-size calibration: jog to one physical corner, mark it, jog to the
  // opposite corner, mark it - width/height are just the distance between
  // the two marks. No manual arithmetic, no reporting numbers by hand.
  $("markCornerA").addEventListener("click", () => {
    state.bedCal.a = { x: state.position.x, y: state.position.y };
    log(`Corner A marked at ${state.bedCal.a.x.toFixed(1)}, ${state.bedCal.a.y.toFixed(1)}.`, "ok");
    maybeComputeBedSize();
  });
  $("markCornerB").addEventListener("click", () => {
    state.bedCal.b = { x: state.position.x, y: state.position.y };
    log(`Corner B marked at ${state.bedCal.b.x.toFixed(1)}, ${state.bedCal.b.y.toFixed(1)}.`, "ok");
    maybeComputeBedSize();
  });

  function maybeComputeBedSize() {
    const { a, b } = state.bedCal;
    if (!a || !b) return;
    const widthMm = Math.abs(b.x - a.x);
    const heightMm = Math.abs(b.y - a.y);
    if (widthMm < 5 || heightMm < 5) {
      log("Corners A and B are almost the same spot - jog further apart before marking B.", "warn");
      return;
    }
    $("bedW").value = widthMm.toFixed(1);
    $("bedH").value = heightMm.toFixed(1);
    readFormIntoState();
    drawBedDiagram();
    log(`Bed size set to ${widthMm.toFixed(1)} x ${heightMm.toFixed(1)}mm from the two marked corners.`, "ok");
  }
  $("pauseBtn").addEventListener("click", () => wsAction({ action: "pause" }));
  $("resumeBtn").addEventListener("click", () => wsAction({ action: "resume" }));
  $("cancelBtn").addEventListener("click", () => wsAction({ action: "cancel" }));

  $("runBtn").addEventListener("click", () => {
    readFormIntoState();
    wsAction({
      action: "run",
      input: currentInputPayload(), page: currentPagePayload(),
      style: currentStylePayload(), machine: currentMachinePayload(),
    });
    $("stageTitle").textContent = "Running";
    stopPlayback();
  });
}

function sendJog(dx, dy) {
  wsAction({ action: "jog", dx, dy, feed: 3000 });
}

function wsAction(payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(payload));
}

async function uploadFile(file, kind) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  const data = await r.json();
  if (kind === "svg") {
    state.svgPath = data.path;
    $("svgName").textContent = file.name;
  } else {
    state.docPath = data.path;
    $("fileName").textContent = file.name;
  }
  log(`Uploaded ${file.name}.`, "ok");
}

window.addEventListener("resize", () => {
  if (state.playback) renderPlaybackFrame(state.playback.simTime);
});

init();

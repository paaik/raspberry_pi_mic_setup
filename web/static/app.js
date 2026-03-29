let wavePlotInited = false;
let waveTrace = null;
let melTrace = null;

/** VLC “scope” CRT styling */
const SCOPE_TRACE = "#39ff14";
const SCOPE_GRID = "rgba(57,255,20,0.22)";
const SCOPE_BG = "#0a0d0a";

/** Last payload from server; used to re-apply gain when the slider moves. */
let lastMsg = null;

const dbValue = document.getElementById("dbValue");
const statusEl = document.getElementById("status");
const waveGainSlider = document.getElementById("waveGain");
const waveGainValue = document.getElementById("waveGainValue");
const scopeCanvas = document.getElementById("scopeCanvas");
const spectrumCanvas = document.getElementById("spectrumCanvas");

/** dB baseline for bar height (quieter than this is a short bar). */
const SPECTRUM_DB_FLOOR = -90;
/** Display max height for bars (roughly dB at top of chart in linearized scale). */
const SPECTRUM_DB_CEIL = -15;

function getWaveGain() {
  const v = parseFloat(waveGainSlider.value);
  return Number.isFinite(v) && v > 0 ? v : 1;
}

function scaleWave(wave) {
  const w = coerceWaveArray(wave);
  const g = getWaveGain();
  const out = new Array(w.length);
  for (let i = 0; i < w.length; i++) {
    out[i] = w[i] * g;
  }
  return out;
}

function coerceWaveArray(wave) {
  if (!wave || !Array.isArray(wave) || wave.length === 0) {
    return [];
  }
  const out = new Array(wave.length);
  for (let i = 0; i < wave.length; i++) {
    out[i] = Number(wave[i]);
  }
  return out;
}

function waveYRangeFromData(wy) {
  if (!wy || wy.length === 0) {
    return [-1, 1];
  }
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < wy.length; i++) {
    const v = wy[i];
    if (v < minY) minY = v;
    if (v > maxY) maxY = v;
  }
  if (!Number.isFinite(minY) || !Number.isFinite(maxY)) {
    return [-1, 1];
  }
  const span = Math.max(Math.abs(minY), Math.abs(maxY), 1e-6);
  const pad = Math.max(span * 0.12, 1e-4);
  return [minY - pad, maxY + pad];
}

function scopeYRangeFromData(wy) {
  if (!wy || wy.length === 0) {
    return [-0.05, 0.05];
  }
  const [lo, hi] = waveYRangeFromData(wy);
  const mid = (lo + hi) / 2;
  let half = (hi - lo) / 2;
  const minHalf = 0.02;
  if (half < minHalf) half = minHalf;
  return [mid - half, mid + half];
}

function syncGainLabel() {
  waveGainValue.textContent = `×${getWaveGain().toFixed(0)}`;
}

function toX(n) {
  const arr = new Array(n);
  for (let i = 0; i < n; i++) arr[i] = i;
  return arr;
}

function resizePlot(id) {
  const gd = document.getElementById(id);
  if (gd && window.Plotly && typeof Plotly.Plots.resize === "function") {
    try {
      Plotly.Plots.resize(gd);
    } catch (e) {
      /* ignore */
    }
  }
}

/**
 * Prepare canvas backing store for sharp rendering; ctx is in CSS pixel units.
 */
function prepareCanvas(ctx, canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const cssW = Math.max(1, rect.width);
  const cssH = Math.max(1, rect.height);
  const bw = Math.floor(cssW * dpr);
  const bh = Math.floor(cssH * dpr);
  if (canvas.width !== bw || canvas.height !== bh) {
    canvas.width = bw;
    canvas.height = bh;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { cssW, cssH };
}

function latestMelColumnDb(mel) {
  if (!mel || mel.length === 0 || !mel[0] || mel[0].length === 0) {
    return [];
  }
  const last = mel[0].length - 1;
  const out = new Array(mel.length);
  for (let i = 0; i < mel.length; i++) {
    out[i] = Number(mel[i][last]);
  }
  return out;
}

function dbToBarHeight(db) {
  const t = (db - SPECTRUM_DB_FLOOR) / (SPECTRUM_DB_CEIL - SPECTRUM_DB_FLOOR);
  let h = 100 * Math.max(0, Math.min(1, t));
  if (h > 0 && h < 4) {
    h = 4;
  }
  return h;
}

function vlcBarColors(dbVals) {
  const colors = new Array(dbVals.length);
  for (let i = 0; i < dbVals.length; i++) {
    const t = (dbVals[i] - SPECTRUM_DB_FLOOR) / (SPECTRUM_DB_CEIL - SPECTRUM_DB_FLOOR);
    const u = Math.max(0, Math.min(1, t));
    const r = Math.round(255 * Math.pow(u, 1.1));
    const gCol = Math.round(200 * (1 - Math.pow(u, 1.35)) + 55);
    const b = Math.round(30 * (1 - u));
    colors[i] = `rgb(${r},${gCol},${b})`;
  }
  return colors;
}

function drawScopeCanvas(rawWave) {
  if (!scopeCanvas) return;
  const ctx = scopeCanvas.getContext("2d");
  if (!ctx) return;

  const { cssW, cssH } = prepareCanvas(ctx, scopeCanvas);
  ctx.fillStyle = SCOPE_BG;
  ctx.fillRect(0, 0, cssW, cssH);

  const nx = 14;
  const ny = 10;
  ctx.strokeStyle = SCOPE_GRID;
  ctx.lineWidth = 1;
  for (let i = 0; i <= nx; i++) {
    const x = (i / nx) * cssW;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, cssH);
    ctx.stroke();
  }
  for (let j = 0; j <= ny; j++) {
    const y = (j / ny) * cssH;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(cssW, y);
    ctx.stroke();
  }

  const sy = coerceWaveArray(rawWave);
  if (sy.length < 2) {
    ctx.fillStyle = "#6ee7b7";
    ctx.font = "12px system-ui,sans-serif";
    ctx.fillText("No waveform samples yet", 8, 18);
    return;
  }

  const [ymin, ymax] = scopeYRangeFromData(sy);
  const mid = (ymin + ymax) / 2;
  const half = Math.max((ymax - ymin) / 2, 1e-9);

  ctx.strokeStyle = SCOPE_TRACE;
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  ctx.beginPath();
  for (let i = 0; i < sy.length; i++) {
    const x = (i / (sy.length - 1)) * cssW;
    const norm = (sy[i] - mid) / half;
    const y = cssH / 2 - norm * (cssH * 0.42);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function drawSpectrumCanvas(mel) {
  if (!spectrumCanvas) return;
  const ctx = spectrumCanvas.getContext("2d");
  if (!ctx) return;

  const { cssW, cssH } = prepareCanvas(ctx, spectrumCanvas);
  ctx.fillStyle = "#06080c";
  ctx.fillRect(0, 0, cssW, cssH);

  const dbVals = latestMelColumnDb(mel);
  const n = dbVals.length;
  if (n === 0) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "12px system-ui,sans-serif";
    ctx.fillText("No spectrum data yet", 8, 18);
    return;
  }

  const padBottom = 6;
  const padTop = 8;
  const plotH = Math.max(10, cssH - padBottom - padTop);
  const gapFrac = 0.12;
  const totalUnits = n + (n - 1) * gapFrac;
  const unitW = cssW / totalUnits;

  const colors = vlcBarColors(dbVals);
  for (let i = 0; i < n; i++) {
    const frac = dbToBarHeight(dbVals[i]) / 100;
    const barH = frac * plotH;
    const x = i * unitW * (1 + gapFrac);
    const y = cssH - padBottom - barH;
    const wBar = unitW;
    ctx.fillStyle = colors[i];
    ctx.fillRect(x, y, wBar * 0.98, barH);
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y, wBar * 0.98, barH);
  }
}

function initPlots(first) {
  const wave = scaleWave(first.wave);
  const mel = first.mel;
  const yRange = waveYRangeFromData(wave);

  const waveX = toX(wave.length);
  waveTrace = {
    x: waveX,
    y: wave,
    mode: "lines",
    line: { width: 1.5, color: "#38bdf8" },
    type: "scatter",
  };

  Plotly.newPlot(
    "wavePlot",
    [waveTrace],
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 40, r: 10, t: 10, b: 30 },
      autosize: true,
      xaxis: { title: "Samples (decimated)", tickfont: { size: 10 } },
      yaxis: {
        title: "Amplitude × gain",
        range: yRange,
        tickfont: { size: 10 },
      },
    },
    { responsive: true }
  );

  const melRows = mel.length;
  const melCols = mel[0].length;
  const melY = toX(melRows);
  const melX = toX(melCols);
  melTrace = {
    z: mel,
    x: melX,
    y: melY,
    type: "heatmap",
    colorscale: "Viridis",
  };

  Plotly.newPlot(
    "melPlot",
    [melTrace],
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 50, r: 10, t: 10, b: 35 },
      autosize: true,
      xaxis: { title: "Time (frames)", tickfont: { size: 10 } },
      yaxis: { title: "Mel bin", tickfont: { size: 10 } },
    },
    { responsive: true }
  );

  drawScopeCanvas(first.wave);
  drawSpectrumCanvas(mel);

  wavePlotInited = true;

  requestAnimationFrame(() => {
    resizePlot("wavePlot");
    resizePlot("melPlot");
    if (lastMsg) {
      drawScopeCanvas(lastMsg.wave);
      drawSpectrumCanvas(lastMsg.mel);
    }
  });
}

function updatePlots(msg) {
  lastMsg = msg;
  dbValue.textContent = `${Number(msg.db).toFixed(1)} dBFS`;

  if (!wavePlotInited) {
    initPlots(msg);
    statusEl.textContent = "Live";
    return;
  }

  const wy = scaleWave(msg.wave);
  const yRange = waveYRangeFromData(wy);
  Plotly.restyle("wavePlot", { x: [toX(wy.length)], y: [wy] });
  Plotly.relayout("wavePlot", { "yaxis.range": yRange, "yaxis.title.text": "Amplitude × gain" });

  Plotly.restyle("melPlot", { z: [msg.mel] });

  drawScopeCanvas(msg.wave);
  drawSpectrumCanvas(msg.mel);
}

function wsUrl() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

function onWaveGainInput() {
  syncGainLabel();
  if (!lastMsg || !wavePlotInited) return;
  const wy = scaleWave(lastMsg.wave);
  const yRange = waveYRangeFromData(wy);
  Plotly.restyle("wavePlot", { x: [toX(wy.length)], y: [wy] });
  Plotly.relayout("wavePlot", { "yaxis.range": yRange });
}

waveGainSlider.addEventListener("input", onWaveGainInput);
syncGainLabel();

function onResize() {
  if (!lastMsg) return;
  drawScopeCanvas(lastMsg.wave);
  drawSpectrumCanvas(lastMsg.mel);
}

let resizeT = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(onResize, 120);
});

function start() {
  const ws = new WebSocket(wsUrl());

  ws.onopen = () => {
    statusEl.textContent = "Connecting...";
  };

  ws.onmessage = (event) => {
    if (!event.data) return;
    try {
      const msg = JSON.parse(event.data);
      updatePlots(msg);
    } catch (e) {
      // Ignore non-JSON messages.
    }
  };

  ws.onclose = () => {
    statusEl.textContent = "Disconnected";
  };

  ws.onerror = () => {
    statusEl.textContent = "Error";
  };
}

start();

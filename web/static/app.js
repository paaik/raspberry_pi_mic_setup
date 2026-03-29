let wavePlotInited = false;
let scopePlotInited = false;
let melPlotInited = false;
let spectrumPlotInited = false;
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

/** Ensure we always have a plain Float64 array (JSON numbers). */
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

/** Keep a minimum vertical span so a quiet trace still resembles an oscilloscope window. */
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
 * Newest mel column is the rightmost column (time scrolls left → right in heatmap).
 */
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

/** Map mel bin index to rough frequency label (matches server DspConfig defaults). */
function melBinLabels(nMels) {
  const sr = 48000;
  const fmax = sr / 2;
  const labels = new Array(nMels);
  for (let m = 0; m < nMels; m++) {
    const fMel = (m / Math.max(1, nMels - 1)) * (2595 * Math.log10(1 + fmax / 700));
    const hz = 700 * (Math.pow(10, fMel / 2595) - 1);
    if (hz >= 1000) {
      labels[m] = `${(hz / 1000).toFixed(1)}k`;
    } else {
      labels[m] = `${Math.round(hz)}`;
    }
  }
  return labels;
}

function dbToBarHeight(db) {
  const t = (db - SPECTRUM_DB_FLOOR) / (SPECTRUM_DB_CEIL - SPECTRUM_DB_FLOOR);
  let h = 100 * Math.max(0, Math.min(1, t));
  /* Tiny bars disappear on dark backgrounds; keep a visible floor. */
  if (h > 0 && h < 4) {
    h = 4;
  }
  return h;
}

/** VLC-style: green (quiet) → yellow → red (loud). */
function vlcBarColors(dbVals) {
  const colors = new Array(dbVals.length);
  for (let i = 0; i < dbVals.length; i++) {
    const t = (dbVals[i] - SPECTRUM_DB_FLOOR) / (SPECTRUM_DB_CEIL - SPECTRUM_DB_FLOOR);
    const u = Math.max(0, Math.min(1, t));
    const r = Math.round(255 * Math.pow(u, 1.1));
    const g = Math.round(200 * (1 - Math.pow(u, 1.35)) + 55);
    const b = Math.round(30 * (1 - u));
    colors[i] = `rgb(${r},${g},${b})`;
  }
  return colors;
}

function buildSpectrumTrace(mel) {
  const dbVals = latestMelColumnDb(mel);
  const n = dbVals.length;
  const x = toX(n);
  const y = new Array(n);
  for (let i = 0; i < n; i++) {
    y[i] = dbToBarHeight(dbVals[i]);
  }
  const colors = vlcBarColors(dbVals);
  return {
    x,
    y,
    customdata: dbVals,
    hovertemplate: "Bin %{x}<br>%{customdata:.1f} dB<extra></extra>",
    type: "bar",
    marker: {
      color: colors,
      line: { color: "rgba(255,255,255,0.06)", width: 0.5 },
    },
  };
}

function scopeLayout(yRange) {
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: SCOPE_BG,
    margin: { l: 44, r: 10, t: 8, b: 32 },
    autosize: true,
    xaxis: {
      title: "Time (decimated samples)",
      tickfont: { size: 9, color: "#86efac" },
      showgrid: true,
      gridcolor: SCOPE_GRID,
      zeroline: true,
      zerolinecolor: SCOPE_GRID,
      zerolinewidth: 1,
      color: "#4ade80",
    },
    yaxis: {
      title: "Amplitude",
      range: yRange,
      tickfont: { size: 9, color: "#86efac" },
      showgrid: true,
      gridcolor: SCOPE_GRID,
      zeroline: true,
      zerolinecolor: SCOPE_GRID,
      color: "#4ade80",
    },
    showlegend: false,
  };
}

function spectrumLayout(nMels) {
  const xTickvals = [];
  const xTicktext = [];
  const step = Math.max(1, Math.floor(nMels / 8));
  const labels = melBinLabels(nMels);
  for (let i = 0; i < nMels; i += step) {
    xTickvals.push(i);
    xTicktext.push(labels[i]);
  }
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#06080c",
    margin: { l: 42, r: 10, t: 8, b: 36 },
    autosize: true,
    xaxis: {
      title: "Frequency (approx.)",
      tickfont: { size: 9, color: "#94a3b8" },
      tickvals: xTickvals,
      ticktext: xTicktext,
      showgrid: false,
      zeroline: false,
      color: "#64748b",
    },
    yaxis: {
      title: "Level (arb.)",
      range: [0, 108],
      tickfont: { size: 10, color: "#94a3b8" },
      showgrid: true,
      gridcolor: "rgba(148,163,184,0.18)",
      zeroline: false,
      color: "#64748b",
    },
    showlegend: false,
    bargap: 0.08,
  };
}

function buildScopeTrace(rawWave) {
  const sy = coerceWaveArray(rawWave);
  const scopeX = toX(sy.length);
  return {
    x: scopeX,
    y: sy,
    type: "scatter",
    mode: "lines",
    line: { color: SCOPE_TRACE, width: 2.5, shape: "linear" },
    hoverinfo: "skip",
  };
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
  wavePlotInited = true;

  const sy = coerceWaveArray(first.wave);
  const scopeR0 = scopeYRangeFromData(sy);
  Plotly.newPlot(
    "scopePlot",
    [buildScopeTrace(sy)],
    scopeLayout(scopeR0),
    { responsive: true }
  );
  scopePlotInited = true;

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
  melPlotInited = true;

  const nMels = mel.length;
  Plotly.newPlot(
    "spectrumPlot",
    [buildSpectrumTrace(mel)],
    spectrumLayout(nMels),
    { responsive: true }
  );
  spectrumPlotInited = true;

  requestAnimationFrame(() => {
    resizePlot("wavePlot");
    resizePlot("scopePlot");
    resizePlot("spectrumPlot");
    resizePlot("melPlot");
  });
}

function updatePlots(msg) {
  lastMsg = msg;
  dbValue.textContent = `${Number(msg.db).toFixed(1)} dBFS`;
  if (!wavePlotInited || !scopePlotInited || !melPlotInited || !spectrumPlotInited) {
    initPlots(msg);
    statusEl.textContent = "Live";
    return;
  }

  const wy = scaleWave(msg.wave);
  const yRange = waveYRangeFromData(wy);
  Plotly.restyle("wavePlot", { x: [toX(wy.length)], y: [wy] });
  Plotly.relayout("wavePlot", { "yaxis.range": yRange, "yaxis.title.text": "Amplitude × gain" });

  const raw = coerceWaveArray(msg.wave);
  const scopeRng = scopeYRangeFromData(raw);
  /* Plotly.restyle often fails silently for some trace types; react is reliable. */
  Plotly.react("scopePlot", [buildScopeTrace(raw)], scopeLayout(scopeRng));

  Plotly.restyle("melPlot", { z: [msg.mel] });

  const nMels = msg.mel.length;
  Plotly.react("spectrumPlot", [buildSpectrumTrace(msg.mel)], spectrumLayout(nMels));
}

function wsUrl() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

/** When gain changes, replot waveform from last message without waiting for WS. */
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

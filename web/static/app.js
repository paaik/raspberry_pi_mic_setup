let wavePlotInited = false;
let waveTrace = null;
let melTrace = null;

/** VLC “scope” CRT styling */
const SCOPE_TRACE_L = "#39ff14";
const SCOPE_TRACE_R = "#f472b6";
const SCOPE_GRID = "rgba(57,255,20,0.22)";
const SCOPE_BG = "#0a0d0a";

/** Last payload from server; used to re-apply gain when the slider moves. */
let lastMsg = null;
/** Tracks capture channel count (1 / 2 / 8) so we can re-init Plotly when the mode changes. */
let lastChannels = null;

/** FPGA / ALSA order: stereo pairs for four physical mics. */
const FPGA8_NAMES = ["1 L", "1 R", "2 L", "2 R", "3 L", "3 R", "4 L", "4 R"];

const CH_COLORS = [
  "#38bdf8",
  "#e879f9",
  "#fbbf24",
  "#34d399",
  "#f472b6",
  "#a78bfa",
  "#fb923c",
  "#2dd4bf",
];

function channelNames(n) {
  if (n === 8) return [...FPGA8_NAMES];
  return Array.from({ length: n }, (_, i) => `Ch ${i + 1}`);
}

function isMultichannel(msg) {
  const c = msg && msg.channels != null ? Number(msg.channels) : 0;
  return c > 2;
}

const dbValue = document.getElementById("dbValue");
const waveChLabel = document.getElementById("waveChLabel");
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

function scopeYRangeStereo(L, R) {
  const combo = L.length && R.length ? L.concat(R) : L.length ? L : R;
  return scopeYRangeFromData(combo);
}

function formatDbLine(msg) {
  if (!msg) return "-";
  const n = msg.channels != null ? Number(msg.channels) : msg.stereo ? 2 : 1;
  if (n > 2 && Array.isArray(msg.db_ch) && msg.db_ch.length === n) {
    const names = channelNames(n);
    const lines = [];
    for (let i = 0; i < n; i++) {
      lines.push(`${names[i]}: ${Number(msg.db_ch[i]).toFixed(1)} dBFS`);
    }
    lines.push(`Mix (mean): ${Number(msg.db).toFixed(1)} dBFS`);
    return lines.join("\n");
  }
  if (msg.stereo && msg.db_l != null && msg.db_r != null) {
    const l = Number(msg.db_l).toFixed(1);
    const r = Number(msg.db_r).toFixed(1);
    const m = Number(msg.db).toFixed(1);
    return `L: ${l} dBFS  |  R: ${r} dBFS\nMix (L+R)/2: ${m} dBFS`;
  }
  return `${Number(msg.db).toFixed(1)} dBFS`;
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

function drawScopeTrace(ctx, sy, cssW, cssH, ymin, ymax, color) {
  if (sy.length < 2) return;
  const mid = (ymin + ymax) / 2;
  const half = Math.max((ymax - ymin) / 2, 1e-9);
  ctx.strokeStyle = color;
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

function drawScopeCanvas(msg) {
  if (!scopeCanvas) return;
  const ctx = scopeCanvas.getContext("2d");
  if (!ctx) return;

  const { cssW, cssH } = prepareCanvas(ctx, scopeCanvas);
  ctx.fillStyle = SCOPE_BG;
  ctx.fillRect(0, 0, cssW, cssH);

  if (isMultichannel(msg) && Array.isArray(msg.wave_ch) && msg.wave_ch.length > 2) {
    const waves = msg.wave_ch.map((w) => coerceWaveArray(w));
    const n = waves.length;
    const nx = 14;
    ctx.strokeStyle = SCOPE_GRID;
    ctx.lineWidth = 1;
    for (let i = 0; i <= nx; i++) {
      const x = (i / nx) * cssW;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, cssH);
      ctx.stroke();
    }
    const bandH = cssH / n;
    const names = channelNames(n);
    let any = false;
    for (let c = 0; c < n; c++) {
      const wy = waves[c];
      if (wy.length < 2) continue;
      any = true;
      const y0 = c * bandH;
      const y1 = (c + 1) * bandH;
      ctx.strokeStyle = "rgba(57,255,20,0.12)";
      ctx.beginPath();
      ctx.moveTo(0, y0);
      ctx.lineTo(cssW, y0);
      ctx.stroke();
      const mid = (y0 + y1) / 2;
      const rng = scopeYRangeFromData(wy);
      const ymin = rng[0];
      const ymax = rng[1];
      const half = Math.max((ymax - ymin) / 2, 1e-9);
      const color = CH_COLORS[c % CH_COLORS.length];
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.lineJoin = "round";
      ctx.beginPath();
      for (let i = 0; i < wy.length; i++) {
        const x = (i / (wy.length - 1)) * cssW;
        const norm = (wy[i] - (ymin + ymax) / 2) / half;
        const y = mid - norm * (bandH * 0.38);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.fillStyle = "rgba(230,237,243,0.55)";
      ctx.font = "11px system-ui,sans-serif";
      ctx.fillText(names[c], 6, y0 + 14);
    }
    if (!any) {
      ctx.fillStyle = "#6ee7b7";
      ctx.font = "12px system-ui,sans-serif";
      ctx.fillText("No multichannel waveform yet", 8, 18);
    }
    return;
  }

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

  const stereo = msg && msg.stereo && Array.isArray(msg.wave_r);
  const L = coerceWaveArray(msg && msg.wave);
  const R = stereo ? coerceWaveArray(msg.wave_r) : [];

  if (L.length < 2 && R.length < 2) {
    ctx.fillStyle = "#6ee7b7";
    ctx.font = "12px system-ui,sans-serif";
    ctx.fillText("No waveform samples yet", 8, 18);
    return;
  }

  let ymin;
  let ymax;
  if (stereo && R.length >= 2) {
    const rng = scopeYRangeStereo(L, R);
    ymin = rng[0];
    ymax = rng[1];
    drawScopeTrace(ctx, L, cssW, cssH, ymin, ymax, SCOPE_TRACE_L);
    drawScopeTrace(ctx, R, cssW, cssH, ymin, ymax, SCOPE_TRACE_R);
  } else {
    const rng = scopeYRangeFromData(L);
    ymin = rng[0];
    ymax = rng[1];
    drawScopeTrace(ctx, L, cssW, cssH, ymin, ymax, SCOPE_TRACE_L);
  }
}

function drawSpectrumCanvas(mel, msg) {
  if (!spectrumCanvas) return;
  const ctx = spectrumCanvas.getContext("2d");
  if (!ctx) return;

  const { cssW, cssH } = prepareCanvas(ctx, spectrumCanvas);
  ctx.fillStyle = "#06080c";
  ctx.fillRect(0, 0, cssW, cssH);

  if (msg && isMultichannel(msg) && Array.isArray(msg.db_ch) && msg.db_ch.length > 0) {
    const dbVals = msg.db_ch.map((v) => Number(v));
    const n = dbVals.length;
    const names = channelNames(n);
    const padBottom = 22;
    const padTop = 8;
    const plotH = Math.max(10, cssH - padBottom - padTop);
    const gapFrac = 0.15;
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
      ctx.fillStyle = "rgba(226,232,240,0.88)";
      ctx.font = "9px system-ui,sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(names[i].replace(/\s+/g, ""), x + unitW * 0.49, cssH - 3);
    }
    return;
  }

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
  const stereo = !!first.stereo;
  const chCount =
    first.channels != null ? Number(first.channels) : stereo ? 2 : 1;
  lastChannels = chCount;

  const fpgaNote = document.getElementById("fpgaModeNote");
  const scopeHelp = document.getElementById("scopeHelp");
  const vuHelp = document.getElementById("vuHelp");
  const melSub = document.getElementById("melSubtitle");
  const multi = isMultichannel(first);

  if (fpgaNote) {
    if (multi) {
      fpgaNote.style.display = "inline";
      fpgaNote.textContent = "— 8× S32_LE interleaved on I₂S (FPGA bit clock / word clock / data)";
    } else {
      fpgaNote.style.display = "none";
      fpgaNote.textContent = "";
    }
  }
  if (scopeHelp) {
    scopeHelp.textContent = multi
      ? "Stacked scopes: one row per channel (raw amplitude, no gain slider)."
      : "Canvas oscilloscope (raw amplitude, no gain slider). Stereo: L = green, R = magenta.";
  }
  if (vuHelp) {
    vuHelp.textContent = multi
      ? "Per-channel dBFS from each 32-bit sample (green → yellow → red)."
      : "Canvas bars from the latest mel column (green → yellow → red).";
  }
  if (melSub) {
    melSub.textContent = multi ? "— mix (mean of all mics)" : "";
  }

  if (waveChLabel) {
    if (multi && Array.isArray(first.wave_ch)) {
      waveChLabel.textContent = `— ${first.wave_ch.length} ch (Plotly)`;
    } else {
      waveChLabel.textContent = stereo ? "— L cyan / R magenta" : "— mono";
    }
  }

  let traces;
  let yRange;
  let showLegend = stereo;

  if (multi && Array.isArray(first.wave_ch) && first.wave_ch.length > 0) {
    const n = first.wave_ch.length;
    const names = channelNames(n);
    let all = [];
    traces = [];
    for (let i = 0; i < n; i++) {
      const wy = scaleWave(first.wave_ch[i]);
      all = all.concat(wy);
      traces.push({
        x: toX(wy.length),
        y: wy,
        mode: "lines",
        line: { width: 1.15, color: CH_COLORS[i % CH_COLORS.length] },
        type: "scatter",
        name: names[i],
      });
    }
    yRange = waveYRangeFromData(all);
    showLegend = n <= 8;
  } else {
    const wL = scaleWave(first.wave);
    const wR = stereo && Array.isArray(first.wave_r) ? scaleWave(first.wave_r) : wL;
    yRange = stereo ? waveYRangeFromData(wL.concat(wR)) : waveYRangeFromData(wL);
    const wx = toX(wL.length);
    traces = stereo
      ? [
          {
            x: wx,
            y: wL,
            mode: "lines",
            line: { width: 1.5, color: "#38bdf8" },
            type: "scatter",
            name: "L",
          },
          {
            x: wx,
            y: wR,
            mode: "lines",
            line: { width: 1.5, color: "#e879f9" },
            type: "scatter",
            name: "R",
          },
        ]
      : [
          {
            x: wx,
            y: wL,
            mode: "lines",
            line: { width: 1.5, color: "#38bdf8" },
            type: "scatter",
          },
        ];
  }

  waveTrace = traces[0];

  Plotly.newPlot(
    "wavePlot",
    traces,
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 40, r: 10, t: 10, b: 30 },
      autosize: true,
      showlegend: showLegend,
      legend: { font: { size: 9 }, orientation: "h", y: 1.12 },
      xaxis: { title: "Samples (decimated)", tickfont: { size: 10 } },
      yaxis: {
        title: "Amplitude × gain",
        range: yRange,
        tickfont: { size: 10 },
      },
    },
    { responsive: true }
  );

  const mel = first.mel;
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

  drawScopeCanvas(first);
  drawSpectrumCanvas(first.mel, first);

  wavePlotInited = true;

  requestAnimationFrame(() => {
    resizePlot("wavePlot");
    resizePlot("melPlot");
    if (lastMsg) {
      drawScopeCanvas(lastMsg);
      drawSpectrumCanvas(lastMsg.mel, lastMsg);
    }
  });
}

function updatePlots(msg) {
  lastMsg = msg;
  if (dbValue) {
    dbValue.textContent = formatDbLine(msg);
  }

  const stereo = !!msg.stereo;
  const chCount = msg.channels != null ? Number(msg.channels) : stereo ? 2 : 1;
  if (wavePlotInited && lastChannels !== null && chCount !== lastChannels) {
    try {
      Plotly.purge("wavePlot");
      Plotly.purge("melPlot");
    } catch (e) {
      /* ignore */
    }
    wavePlotInited = false;
  }

  if (!wavePlotInited) {
    initPlots(msg);
    statusEl.textContent = "Live";
    return;
  }

  lastChannels = chCount;
  if (waveChLabel) {
    if (isMultichannel(msg) && Array.isArray(msg.wave_ch)) {
      waveChLabel.textContent = `— ${msg.wave_ch.length} ch (Plotly)`;
    } else {
      waveChLabel.textContent = stereo ? "— L cyan / R magenta" : "— mono";
    }
  }

  if (isMultichannel(msg) && Array.isArray(msg.wave_ch) && msg.wave_ch.length > 0) {
    const n = msg.wave_ch.length;
    const xs = [];
    const ys = [];
    let all = [];
    for (let i = 0; i < n; i++) {
      const wy = scaleWave(msg.wave_ch[i]);
      xs.push(toX(wy.length));
      ys.push(wy);
      all = all.concat(wy);
    }
    const yRange = waveYRangeFromData(all);
    Plotly.restyle("wavePlot", { x: xs, y: ys });
    Plotly.relayout("wavePlot", { "yaxis.range": yRange, "yaxis.title.text": "Amplitude × gain" });
  } else {
    const wL = scaleWave(msg.wave);
    const wR = stereo && Array.isArray(msg.wave_r) ? scaleWave(msg.wave_r) : wL;
    const yRange = stereo ? waveYRangeFromData(wL.concat(wR)) : waveYRangeFromData(wL);
    if (stereo) {
      Plotly.restyle("wavePlot", {
        x: [toX(wL.length), toX(wL.length)],
        y: [wL, wR],
      });
    } else {
      Plotly.restyle("wavePlot", { x: [toX(wL.length)], y: [wL] });
    }
    Plotly.relayout("wavePlot", { "yaxis.range": yRange, "yaxis.title.text": "Amplitude × gain" });
  }

  Plotly.restyle("melPlot", { z: [msg.mel] });

  drawScopeCanvas(msg);
  drawSpectrumCanvas(msg.mel, msg);
}

function wsUrl() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

function onWaveGainInput() {
  syncGainLabel();
  if (!lastMsg || !wavePlotInited) return;
  const stereo = !!lastMsg.stereo;
  if (isMultichannel(lastMsg) && Array.isArray(lastMsg.wave_ch) && lastMsg.wave_ch.length > 0) {
    const n = lastMsg.wave_ch.length;
    const xs = [];
    const ys = [];
    let all = [];
    for (let i = 0; i < n; i++) {
      const wy = scaleWave(lastMsg.wave_ch[i]);
      xs.push(toX(wy.length));
      ys.push(wy);
      all = all.concat(wy);
    }
    Plotly.restyle("wavePlot", { x: xs, y: ys });
    Plotly.relayout("wavePlot", { "yaxis.range": waveYRangeFromData(all) });
    return;
  }
  const wL = scaleWave(lastMsg.wave);
  const wR = stereo && Array.isArray(lastMsg.wave_r) ? scaleWave(lastMsg.wave_r) : wL;
  const yRange = stereo ? waveYRangeFromData(wL.concat(wR)) : waveYRangeFromData(wL);
  if (stereo) {
    Plotly.restyle("wavePlot", {
      x: [toX(wL.length), toX(wL.length)],
      y: [wL, wR],
    });
  } else {
    Plotly.restyle("wavePlot", { x: [toX(wL.length)], y: [wL] });
  }
  Plotly.relayout("wavePlot", { "yaxis.range": yRange });
}

waveGainSlider.addEventListener("input", onWaveGainInput);
syncGainLabel();

function onResize() {
  if (!lastMsg) return;
  drawScopeCanvas(lastMsg);
  drawSpectrumCanvas(lastMsg.mel, lastMsg);
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

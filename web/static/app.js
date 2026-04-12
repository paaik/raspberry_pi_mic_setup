let wavePlotInited = false;

const SCOPE_GRID = "rgba(57,255,20,0.22)";
const SCOPE_BG = "#0a0d0a";

let lastMsg = null;

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

const dbValue = document.getElementById("dbValue");
const waveChLabel = document.getElementById("waveChLabel");
const statusEl = document.getElementById("status");
const waveGainSlider = document.getElementById("waveGain");
const waveGainValue = document.getElementById("waveGainValue");
const scopeCanvas = document.getElementById("scopeCanvas");
const spectrumCanvas = document.getElementById("spectrumCanvas");
const fpgaModeNote = document.getElementById("fpgaModeNote");

const SPECTRUM_DB_FLOOR = -90;
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

function formatDbLine(msg) {
  if (!msg || !Array.isArray(msg.db_ch) || msg.db_ch.length === 0) return "-";
  const lines = [];
  for (let i = 0; i < FPGA8_NAMES.length && i < msg.db_ch.length; i++) {
    lines.push(`${FPGA8_NAMES[i]}: ${Number(msg.db_ch[i]).toFixed(1)} dBFS`);
  }
  lines.push(`Mix (mean): ${Number(msg.db).toFixed(1)} dBFS`);
  return lines.join("\n");
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

function drawScopeCanvas(msg) {
  if (!scopeCanvas) return;
  const ctx = scopeCanvas.getContext("2d");
  if (!ctx) return;

  const { cssW, cssH } = prepareCanvas(ctx, scopeCanvas);
  ctx.fillStyle = SCOPE_BG;
  ctx.fillRect(0, 0, cssW, cssH);

  if (!msg || !Array.isArray(msg.wave_ch) || msg.wave_ch.length < 2) {
    ctx.fillStyle = "#6ee7b7";
    ctx.font = "12px system-ui,sans-serif";
    ctx.fillText("No waveform samples yet", 8, 18);
    return;
  }

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
  const names = FPGA8_NAMES;
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
    const rng = waveYRangeFromData(wy);
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
    ctx.fillText(names[c] || `Ch ${c + 1}`, 6, y0 + 14);
  }
  if (!any) {
    ctx.fillStyle = "#6ee7b7";
    ctx.font = "12px system-ui,sans-serif";
    ctx.fillText("No waveform samples yet", 8, 18);
  }
}

function drawVuCanvas(msg) {
  if (!spectrumCanvas) return;
  const ctx = spectrumCanvas.getContext("2d");
  if (!ctx) return;

  const { cssW, cssH } = prepareCanvas(ctx, spectrumCanvas);
  ctx.fillStyle = "#06080c";
  ctx.fillRect(0, 0, cssW, cssH);

  if (!msg || !Array.isArray(msg.db_ch) || msg.db_ch.length === 0) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "12px system-ui,sans-serif";
    ctx.fillText("No level data yet", 8, 18);
    return;
  }

  const dbVals = msg.db_ch.map((v) => Number(v));
  const n = dbVals.length;
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
    ctx.fillText((FPGA8_NAMES[i] || `C${i + 1}`).replace(/\s+/g, ""), x + unitW * 0.49, cssH - 3);
  }
}

function initPlots(first) {
  if (waveChLabel) {
    waveChLabel.textContent = "— 8 channels (Plotly)";
  }
  if (fpgaModeNote) {
    fpgaModeNote.style.display = "inline";
    fpgaModeNote.textContent =
      "— 8× S32_LE interleaved on I₂S (FPGA: BCLK / LRCLK / DOUT); order 1L…4R";
  }

  const scopeHelp = document.getElementById("scopeHelp");
  const vuHelp = document.getElementById("vuHelp");
  const melSub = document.getElementById("melSubtitle");
  if (scopeHelp) {
    scopeHelp.textContent =
      "Stacked scopes: one row per mic (raw amplitude, no gain slider).";
  }
  if (vuHelp) {
    vuHelp.textContent =
      "Per-channel dBFS from each 32-bit sample (green → yellow → red).";
  }
  if (melSub) {
    melSub.textContent = "— mix (mean of 8 ch)";
  }

  const n = 8;
  let all = [];
  const traces = [];
  for (let i = 0; i < n; i++) {
    const wy = scaleWave(first.wave_ch && first.wave_ch[i] ? first.wave_ch[i] : []);
    all = all.concat(wy);
    traces.push({
      x: toX(wy.length),
      y: wy,
      mode: "lines",
      line: { width: 1.15, color: CH_COLORS[i % CH_COLORS.length] },
      type: "scatter",
      name: FPGA8_NAMES[i],
    });
  }
  const yRange = waveYRangeFromData(all);

  Plotly.newPlot(
    "wavePlot",
    traces,
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 40, r: 10, t: 10, b: 30 },
      autosize: true,
      showlegend: true,
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

  Plotly.newPlot(
    "melPlot",
    [
      {
        z: mel,
        x: melX,
        y: melY,
        type: "heatmap",
        colorscale: "Viridis",
      },
    ],
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
  drawVuCanvas(first);

  wavePlotInited = true;

  requestAnimationFrame(() => {
    resizePlot("wavePlot");
    resizePlot("melPlot");
    if (lastMsg) {
      drawScopeCanvas(lastMsg);
      drawVuCanvas(lastMsg);
    }
  });
}

function updatePlots(msg) {
  lastMsg = msg;
  if (dbValue) {
    dbValue.textContent = formatDbLine(msg);
  }

  if (!wavePlotInited) {
    initPlots(msg);
    statusEl.textContent = "Live";
    return;
  }

  const n = 8;
  const xs = [];
  const ys = [];
  let all = [];
  for (let i = 0; i < n; i++) {
    const wy = scaleWave(msg.wave_ch && msg.wave_ch[i] ? msg.wave_ch[i] : []);
    xs.push(toX(wy.length));
    ys.push(wy);
    all = all.concat(wy);
  }
  const yRange = waveYRangeFromData(all);
  Plotly.restyle("wavePlot", { x: xs, y: ys });
  Plotly.relayout("wavePlot", { "yaxis.range": yRange, "yaxis.title.text": "Amplitude × gain" });

  Plotly.restyle("melPlot", { z: [msg.mel] });

  drawScopeCanvas(msg);
  drawVuCanvas(msg);
}

function wsUrl() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

waveGainSlider.addEventListener("input", () => {
  syncGainLabel();
  if (!lastMsg || !wavePlotInited) return;
  const n = 8;
  const xs = [];
  const ys = [];
  let all = [];
  for (let i = 0; i < n; i++) {
    const wy = scaleWave(lastMsg.wave_ch && lastMsg.wave_ch[i] ? lastMsg.wave_ch[i] : []);
    xs.push(toX(wy.length));
    ys.push(wy);
    all = all.concat(wy);
  }
  Plotly.restyle("wavePlot", { x: xs, y: ys });
  Plotly.relayout("wavePlot", { "yaxis.range": waveYRangeFromData(all) });
});
syncGainLabel();

function onResize() {
  if (!lastMsg) return;
  drawScopeCanvas(lastMsg);
  drawVuCanvas(lastMsg);
}

let resizeT = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(onResize, 120);
});

const recordMic1Btn = document.getElementById("recordMic1Btn");
const recordMic1Status = document.getElementById("recordMic1Status");
const alnAlignBtn = document.getElementById("alnAlignBtn");
const alnAlignStatus = document.getElementById("alnAlignStatus");

async function refreshAlnButton() {
  if (!alnAlignBtn) return;
  try {
    const r = await fetch("/health");
    if (!r.ok) return;
    const h = await r.json();
    const cap = !!h.aln_capable;
    alnAlignBtn.disabled = !cap;
    if (alnAlignStatus) {
      if (!cap) {
        alnAlignStatus.textContent = "ALN off (--aln-gpio 0) or unavailable.";
      } else {
        alnAlignStatus.textContent = `BCM GPIO ${h.aln_gpio} — tap to align TDM`;
      }
    }
  } catch (e) {
    /* ignore */
  }
}

if (alnAlignBtn) {
  alnAlignBtn.addEventListener("click", async () => {
    alnAlignBtn.disabled = true;
    if (alnAlignStatus) alnAlignStatus.textContent = "Aligning (can take ~30 s)…";
    try {
      const r = await fetch("/api/fpga/aln-align", { method: "POST" });
      const t = await r.text();
      if (!r.ok) throw new Error(t || r.statusText);
      if (alnAlignStatus) alnAlignStatus.textContent = "Alignment done.";
    } catch (e) {
      if (alnAlignStatus) {
        alnAlignStatus.textContent = `Error: ${e && e.message ? e.message : e}`;
      }
    } finally {
      await refreshAlnButton();
    }
  });
  refreshAlnButton();
}

if (recordMic1Btn) {
  recordMic1Btn.addEventListener("click", async () => {
    recordMic1Btn.disabled = true;
    if (recordMic1Status) recordMic1Status.textContent = "Recording 15 s…";
    try {
      const r = await fetch("/api/record/stereo_mic1_15s.wav");
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t || r.statusText);
      }
      const blob = await r.blob();
      const u = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = u;
      a.download = "stereo_mic1_15s.wav";
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(u);
      if (recordMic1Status) recordMic1Status.textContent = "Download started.";
    } catch (e) {
      if (recordMic1Status) {
        recordMic1Status.textContent = `Error: ${e && e.message ? e.message : e}`;
      }
    } finally {
      recordMic1Btn.disabled = false;
    }
  });
}

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
      /* ignore */
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

let wavePlotInited = false;
let melPlotInited = false;
let waveTrace = null;
let melTrace = null;

/** Last payload from server; used to re-apply gain when the slider moves. */
let lastMsg = null;

const dbValue = document.getElementById("dbValue");
const statusEl = document.getElementById("status");
const waveGainSlider = document.getElementById("waveGain");
const waveGainValue = document.getElementById("waveGainValue");

function getWaveGain() {
  const v = parseFloat(waveGainSlider.value);
  return Number.isFinite(v) && v > 0 ? v : 1;
}

function scaleWave(wave) {
  const g = getWaveGain();
  const out = new Array(wave.length);
  for (let i = 0; i < wave.length; i++) {
    out[i] = wave[i] * g;
  }
  return out;
}

function waveYRangeFromData(wy) {
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

function syncGainLabel() {
  waveGainValue.textContent = `×${getWaveGain().toFixed(0)}`;
}

function toX(n) {
  const arr = new Array(n);
  for (let i = 0; i < n; i++) arr[i] = i;
  return arr;
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
    line: { width: 1.5 },
    type: "scatter",
  };

  Plotly.newPlot(
    "wavePlot",
    [waveTrace],
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 40, r: 10, t: 10, b: 30 },
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
      xaxis: { title: "Time (frames)", tickfont: { size: 10 } },
      yaxis: { title: "Mel bin", tickfont: { size: 10 } },
    },
    { responsive: true }
  );
  melPlotInited = true;
}

function updatePlots(msg) {
  lastMsg = msg;
  dbValue.textContent = `${Number(msg.db).toFixed(1)} dBFS`;
  if (!wavePlotInited || !melPlotInited) {
    initPlots(msg);
    statusEl.textContent = "Live";
    return;
  }

  const wy = scaleWave(msg.wave);
  const yRange = waveYRangeFromData(wy);
  Plotly.restyle("wavePlot", { y: [wy] });
  Plotly.relayout("wavePlot", { "yaxis.range": yRange, "yaxis.title.text": "Amplitude × gain" });

  Plotly.restyle("melPlot", { z: [msg.mel] });
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
  Plotly.restyle("wavePlot", { y: [wy] });
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

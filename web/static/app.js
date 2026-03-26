let wavePlotInited = false;
let melPlotInited = false;
let waveTrace = null;
let melTrace = null;

const dbValue = document.getElementById("dbValue");
const statusEl = document.getElementById("status");

function toX(n) {
  const arr = new Array(n);
  for (let i = 0; i < n; i++) arr[i] = i;
  return arr;
}

function initPlots(first) {
  const wave = first.wave;
  const mel = first.mel;

  // Waveform
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
      yaxis: { title: "Amplitude", range: [-1, 1], tickfont: { size: 10 } },
    },
    { responsive: true }
  );
  wavePlotInited = true;

  // Mel spectrogram
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
  dbValue.textContent = `${Number(msg.db).toFixed(1)} dBFS`;
  if (!wavePlotInited || !melPlotInited) {
    initPlots(msg);
    statusEl.textContent = "Live";
    return;
  }

  // Waveform update
  Plotly.restyle("wavePlot", { y: [msg.wave] });

  // Mel update
  Plotly.restyle("melPlot", { z: [msg.mel] });
}

function wsUrl() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
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


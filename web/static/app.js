const CH_NAMES = ["1 L", "1 R", "2 L", "2 R", "3 L", "3 R", "4 L", "4 R"];

const statusEl = document.getElementById("status");
const captureHint = document.getElementById("captureHint");
const meterBody = document.getElementById("meterBody");
const mixRow = document.getElementById("mixRow");
const recordMic1Btn = document.getElementById("recordMic1Btn");
const recordMic1Status = document.getElementById("recordMic1Status");
const alnAlignBtn = document.getElementById("alnAlignBtn");
const alnAlignStatus = document.getElementById("alnAlignStatus");
const alnDetail = document.getElementById("alnDetail");

const POLL_MS = 280;
const FETCH_TIMEOUT_MS = 8000;

let pollInFlight = false;

function formatDb(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(1);
}

function renderMeters(data) {
  if (!meterBody) return;
  const ch = Array.isArray(data.db_ch) ? data.db_ch : [];
  let html = "";
  for (let i = 0; i < CH_NAMES.length; i++) {
    const label = CH_NAMES[i];
    const db = i < ch.length ? formatDb(ch[i]) : "—";
    html += `<tr><td>${label}</td><td>${db}</td></tr>`;
  }
  meterBody.innerHTML = html;
  if (mixRow) {
    mixRow.textContent = `Mix (mean): ${formatDb(data.db)} dBFS`;
  }
}

function describeCapture(c) {
  if (!c || typeof c !== "object") return { line: "", hintClass: "", showHint: false };

  const st = c.state;
  const err = c.error;
  const dev = c.alsa_device || "?";
  const backend = c.capture_backend || "alsa";
  const sr = c.sample_rate;
  const blocks = c.pcm_blocks;
  const peak = c.last_block_peak_int32;
  const alive = c.capture_thread_alive;

  if (st === "failed" && err) {
    return {
      line: `Capture failed — ${err}`,
      hintClass: "err",
      showHint: true,
    };
  }
  if (st === "starting") {
    return {
      line: "ALSA capture thread is starting…",
      hintClass: "",
      showHint: true,
    };
  }
  if (!alive && st !== "running") {
    return {
      line: "Capture thread is not running — check server logs and ALSA.",
      hintClass: "err",
      showHint: true,
    };
  }
  if (st === "running") {
    const src =
      backend === "pi_sd"
        ? `raw pi_sd stream ${dev} (256-bit / 32-byte frames → 8× int32)`
        : `ALSA ${dev}`;
    let line = `Receiving PCM (${backend}) — ${src} @ ${sr} Hz nominal · blocks ${blocks} · last block |max sample| = ${peak} (int32)`;
    if (blocks > 40 && peak === 0) {
      line +=
        " · If this stays 0, the stream may be all zeros (wrong device, I²S not wired, or FPGA silent).";
      return { line, hintClass: "warn", showHint: true };
    }
    return { line, hintClass: "", showHint: true };
  }
  return { line: JSON.stringify(c), hintClass: "", showHint: true };
}

function updateCaptureHint(data) {
  if (!captureHint) return;
  const c = data.capture;
  const { line, hintClass, showHint } = describeCapture(c);
  if (!showHint || !line) {
    captureHint.style.display = "none";
    captureHint.textContent = "";
    captureHint.className = "small";
    return;
  }
  captureHint.style.display = "block";
  captureHint.textContent = line;
  captureHint.className = "small" + (hintClass ? " " + hintClass : "");
}

function updateStatusLine(data) {
  if (!statusEl) return;
  const c = data.capture;
  if (!c) {
    statusEl.textContent = data.ok ? "Server OK (no capture info)" : "Unknown";
    return;
  }
  if (c.state === "running") {
    statusEl.textContent = "Live — levels update from PCM";
  } else if (c.state === "failed") {
    statusEl.textContent = "Server up — audio capture failed (see box below)";
  } else if (c.state === "starting") {
    statusEl.textContent = "Starting capture…";
  } else {
    statusEl.textContent = `Capture: ${c.state}`;
  }
}

async function fetchHealth() {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), FETCH_TIMEOUT_MS);
  try {
    const r = await fetch("/health", { cache: "no-store", signal: ac.signal });
    if (!r.ok) throw new Error(r.statusText || String(r.status));
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

async function pollOnce() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const data = await fetchHealth();
    updateStatusLine(data);
    updateCaptureHint(data);
    renderMeters(data);

    if (alnDetail && data.aln_backend_status) {
      alnDetail.textContent = data.aln_backend_status;
    } else if (alnDetail && !data.aln_align_active) {
      alnDetail.textContent = "When you run PI_ALN, step-by-step backend messages appear here.";
    }

    if (alnAlignBtn) {
      alnAlignBtn.classList.toggle("aln-busy", !!data.aln_align_active);
    }

    return data;
  } catch (e) {
    if (statusEl) {
      const msg =
        e && e.name === "AbortError"
          ? "No response from /health (timeout) — is the server reachable on this host/port?"
          : "Cannot reach /health — open the dashboard from the same URL as the server (e.g. http://pi:8000), check HTTPS mixed-content, firewall, and that static/app.js loaded.";
      statusEl.textContent = msg;
    }
    if (captureHint) {
      captureHint.style.display = "block";
      captureHint.className = "small err";
      captureHint.textContent =
        "Tip: use http://<pi-ip>:8000 in the browser bar. If the page is https:// but the API is http://, the browser blocks fetch (mixed content).";
    }
    return null;
  } finally {
    pollInFlight = false;
  }
}

async function refreshAlnButton() {
  if (!alnAlignBtn) return;
  try {
    const h = await fetchHealth();
    const cap = !!h.aln_capable;
    const busy = !!h.aln_align_active;
    alnAlignBtn.disabled = !cap || busy;
    if (alnAlignStatus) {
      if (!cap) {
        alnAlignStatus.textContent = "ALN off (--aln-gpio 0) or unavailable.";
      } else if (busy) {
        alnAlignStatus.textContent = "Alignment running on Pi…";
      } else {
        alnAlignStatus.textContent = `BCM GPIO ${h.aln_gpio} — tap to align TDM`;
      }
    }
  } catch {
    /* ignore */
  }
}

if (alnAlignBtn) {
  alnAlignBtn.addEventListener("click", async () => {
    alnAlignBtn.disabled = true;
    if (alnAlignStatus) alnAlignStatus.textContent = "Requested — watch backend steps below…";
    if (alnDetail) alnDetail.textContent = "Waiting for capture thread to start PI_ALN…";
    try {
      const ac = new AbortController();
      const t = setTimeout(() => ac.abort(), 120000);
      const r = await fetch("/api/fpga/aln-align", { method: "POST", signal: ac.signal });
      const txt = await r.text();
      clearTimeout(t);
      if (!r.ok) throw new Error(txt || r.statusText);
      if (alnAlignStatus) alnAlignStatus.textContent = "Alignment finished (check message above).";
    } catch (e) {
      if (alnAlignStatus) {
        alnAlignStatus.textContent = `Error: ${e && e.message ? e.message : e}`;
      }
    } finally {
      await refreshAlnButton();
    }
  });
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

pollOnce();
setInterval(pollOnce, POLL_MS);
refreshAlnButton();
setInterval(refreshAlnButton, 2000);

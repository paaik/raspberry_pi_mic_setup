const CH_NAMES = ["1 L", "1 R", "2 L", "2 R", "3 L", "3 R", "4 L", "4 R"];

const statusEl = document.getElementById("status");
const meterBody = document.getElementById("meterBody");
const mixRow = document.getElementById("mixRow");
const recordMic1Btn = document.getElementById("recordMic1Btn");
const recordMic1Status = document.getElementById("recordMic1Status");
const alnAlignBtn = document.getElementById("alnAlignBtn");
const alnAlignStatus = document.getElementById("alnAlignStatus");

const POLL_MS = 400;

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

async function pollOnce() {
  try {
    const r = await fetch("/health", { cache: "no-store" });
    if (!r.ok) throw new Error(r.statusText || String(r.status));
    const data = await r.json();
    if (statusEl) {
      statusEl.textContent = data.ok ? "Live" : "Unknown";
    }
    renderMeters(data);
    return data;
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = "Disconnected — check server / network";
    }
    return null;
  }
}

async function refreshAlnButton() {
  if (!alnAlignBtn) return;
  try {
    const r = await fetch("/health", { cache: "no-store" });
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
  } catch {
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

function startPolling() {
  pollOnce();
  setInterval(pollOnce, POLL_MS);
}

startPolling();

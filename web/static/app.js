function dbfsToPercent(db) {
  // Map roughly -96..0 dBFS to 0..100 for the bar
  const clamped = Math.max(-96, Math.min(0, db));
  return ((clamped + 96) / 96) * 100;
}

function renderChannels(channels) {
  const grid = document.getElementById("meter-grid");
  grid.innerHTML = "";
  for (const ch of channels) {
    const card = document.createElement("article");
    card.className = "card";
    const pct = dbfsToPercent(ch.dbfs);
    card.innerHTML = `
      <h3>Channel ${ch.id + 1}</h3>
      <p class="meta">${escapeHtml(ch.label)}</p>
      <div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct.toFixed(
        0
      )}">
        <div class="fill" style="width:${pct.toFixed(1)}%"></div>
      </div>
      <div class="db">${ch.dbfs.toFixed(1)} dBFS</div>
    `;
    grid.appendChild(card);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function tick() {
  const statusEl = document.getElementById("status");
  try {
    const res = await fetch("/api/meters", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderChannels(data.channels);
    const parts = [`frames decoded: ${data.frames}`];
    if (data.last_error) parts.push(`error: ${data.last_error}`);
    statusEl.textContent = parts.join(" · ");
  } catch (e) {
    statusEl.textContent = `Cannot reach server (${e})`;
  }
}

setInterval(tick, 200);
tick();

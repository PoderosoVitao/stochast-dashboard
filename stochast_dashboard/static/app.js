const form = document.getElementById("start-form");
const previewBtn = document.getElementById("preview-btn");
const previewEl = document.getElementById("preview");
const errorEl = document.getElementById("error");
const scenariosEl = document.getElementById("scenarios");

const scenarioCards = new Map();

function showError(message) {
  errorEl.textContent = message || "";
}

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

previewBtn.addEventListener("click", async () => {
  showError("");
  const path = form.path.value;
  if (!path) {
    showError("enter a scenario path first");
    return;
  }
  try {
    const scenarios = await fetchJSON(`/api/scenarios?path=${encodeURIComponent(path)}`);
    previewEl.innerHTML =
      scenarios
        .map((s) => `<div>${s.name} — ${s.runs} runs${s.tags.length ? " [" + s.tags.join(", ") + "]" : ""}</div>`)
        .join("") || "<div>No scenarios matched.</div>";
  } catch (err) {
    showError(err.message);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  scenariosEl.innerHTML = "";
  scenarioCards.clear();

  const body = {
    path: form.path.value,
    adapter: form.adapter.value,
    keyword: form.keyword.value || "",
    runs: form.runs.value ? Number(form.runs.value) : null,
    concurrency: Number(form.concurrency.value) || 5,
  };

  try {
    await fetchJSON("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    showError(err.message);
  }
});

function ensureCard(name, totalRuns) {
  if (scenarioCards.has(name)) {
    const card = scenarioCards.get(name);
    if (totalRuns) card.total = totalRuns;
    return card;
  }
  const el = document.createElement("div");
  el.className = "scenario-card";
  el.innerHTML = `
    <h3>${name}</h3>
    <div class="progress"><div class="progress-bar" style="width:0%"></div></div>
    <div class="dots"></div>
    <div class="pass-rate"></div>
    <div class="stats"></div>
  `;
  scenariosEl.appendChild(el);
  const card = { el, total: totalRuns, done: 0, passed: 0 };
  scenarioCards.set(name, card);
  return card;
}

function renderStats(container, stats) {
  const [lo, hi] = stats.pass_rate_interval;
  let html =
    `<p><strong>Pass rate:</strong> ${stats.passed_runs}/${stats.total_runs} ` +
    `(${(stats.pass_rate * 100).toFixed(0)}%, 95% CI [${(lo * 100).toFixed(0)}%, ${(hi * 100).toFixed(0)}%])</p>`;

  if (stats.assertions.length) {
    html += "<table><tr><th>Assertion</th><th>Failures</th><th>Rate</th></tr>";
    for (const a of stats.assertions) {
      html += `<tr><td>${a.label}</td><td>${a.failures}/${a.total}</td><td>${(a.failure_rate * 100).toFixed(0)}%</td></tr>`;
    }
    html += "</table>";
  }

  if (stats.tool_paths.length) {
    html +=
      "<pre>" +
      stats.tool_paths
        .map((p) => `${p.count}/${stats.total_runs}  ${p.path.length ? p.path.join(" -> ") : "(no tool calls)"}`)
        .join("\n") +
      "</pre>";
  }

  const lat = stats.latency_ms;
  const cost = stats.cost_usd;
  html += `<p><strong>Latency:</strong> p50 ${lat.p50.toFixed(0)}ms, p95 ${lat.p95.toFixed(0)}ms, p99 ${lat.p99.toFixed(0)}ms</p>`;
  html += `<p><strong>Cost:</strong> p50 $${cost.p50.toFixed(4)}, p95 $${cost.p95.toFixed(4)}, p99 $${cost.p99.toFixed(4)}</p>`;

  container.innerHTML = html;
}

function handleEvent(event) {
  if (event.type === "job_started") {
    for (const s of event.scenarios) ensureCard(s.name, s.runs);
  } else if (event.type === "run_completed") {
    const card = ensureCard(event.scenario, 0);
    card.done += 1;
    if (event.passed) card.passed += 1;
    const pct = card.total ? Math.round((card.done / card.total) * 100) : 0;
    card.el.querySelector(".progress-bar").style.width = pct + "%";
    const dot = document.createElement("span");
    dot.className = "dot " + (event.passed ? "pass" : "fail");
    card.el.querySelector(".dots").appendChild(dot);
    card.el.querySelector(".pass-rate").textContent = `${card.passed}/${card.done} passed so far`;
  } else if (event.type === "scenario_finished") {
    const card = ensureCard(event.scenario, event.stats.total_runs);
    renderStats(card.el.querySelector(".stats"), event.stats);
  } else if (event.type === "job_error") {
    showError(event.message);
  }
}

const source = new EventSource("/api/runs/current/events");
source.onmessage = (event) => handleEvent(JSON.parse(event.data));

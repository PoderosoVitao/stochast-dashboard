"use strict";

const COLLAPSE_AFTER = 100;
const REFRESH_MS = 2000;

const form = document.getElementById("start-form");
const inputs = {
  path: document.getElementById("path"),
  adapter: document.getElementById("adapter"),
  keyword: document.getElementById("keyword"),
  runs: document.getElementById("runs"),
  concurrency: document.getElementById("concurrency"),
};
const startBtn = document.getElementById("start-btn");
const previewBtn = document.getElementById("preview-btn");
const formError = document.getElementById("form-error");
const previewPanel = document.getElementById("preview");
const previewList = document.getElementById("preview-list");
const jobStatus = document.getElementById("job-status");
const jobError = document.getElementById("job-error");
const emptyState = document.getElementById("empty-state");
const scenariosEl = document.getElementById("scenarios");
const scenarioTemplate = document.getElementById("scenario-template");
const dialog = document.getElementById("run-dialog");
const dialogTitle = document.getElementById("run-dialog-title");
const dialogSubtitle = document.getElementById("run-dialog-subtitle");
const dialogBody = document.getElementById("run-dialog-body");
const themeToggle = document.getElementById("theme-toggle");
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

const cards = new Map();
const pendingRender = new Set();
let scenarioOrder = [];
let jobRunning = false;
let dialogToken = 0;

const integer = new Intl.NumberFormat();
const pct = (x) => `${Math.round(x * 100)}%`;
const ms = (x) => `${integer.format(Math.round(x))} ms`;
const usd = (x) => `$${x.toFixed(4)}`;

// Builds an element whose text is always set through textContent, never parsed
// as HTML: run data includes model output and model-chosen tool names, and
// none of it may ever inject markup into this page.
function el(tag, { className, text, attrs } = {}, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (attrs) {
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  }
  for (const child of children) {
    if (child) node.append(child);
  }
  return node;
}

// Builds a status pill, which always pairs its color with an icon and a label.
function pill(kind, label) {
  return el("span", { className: `status-pill ${kind}` }, [
    el("span", { className: "status-icon", attrs: { "aria-hidden": "true" } }),
    el("span", { className: "status-text", text: label }),
  ]);
}

function setPill(node, kind, label) {
  node.className = `status-pill ${kind}`;
  node.querySelector(".status-text").textContent = label;
}

function scenarioUrl(name, suffix) {
  return `/api/runs/current/scenarios/${encodeURIComponent(name)}/${suffix}`;
}

function pretty(value) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

// Fetches JSON, turning a non-2xx response into an Error that carries the
// server's own explanation.
async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}

function postJSON(url, data) {
  return fetchJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

function showFormError(message) {
  formError.textContent = message || "";
  formError.hidden = !message;
}

function setJobRunning(running) {
  jobRunning = running;
  startBtn.disabled = running;
  startBtn.textContent = running ? "Run in progress…" : "Start run";
}

// Reads and checks the form, returning the values the API expects, or
// explaining what's missing and returning null.
function readForm({ needAdapter }) {
  const path = inputs.path.value.trim();
  const adapter = inputs.adapter.value.trim();
  if (!path) {
    showFormError("Enter a scenario path.");
    return null;
  }
  if (needAdapter && !adapter) {
    showFormError("Enter an adapter, like /path/to/adapter.py:build_adapter.");
    return null;
  }
  if (needAdapter && !adapter.includes(":")) {
    showFormError("The adapter needs its factory function after a colon, like adapter.py:build_adapter.");
    return null;
  }
  return {
    path,
    adapter,
    keyword: inputs.keyword.value.trim(),
    runs: inputs.runs.value ? Number(inputs.runs.value) : null,
    concurrency: Number(inputs.concurrency.value) || 5,
  };
}

function renderPreview(scenarios) {
  previewList.replaceChildren();
  if (!scenarios.length) {
    previewList.append(el("li", { text: "No scenarios found at that path." }));
  }
  for (const scenario of scenarios) {
    const tags = scenario.tags.map((tag) => el("span", { className: "chip", text: tag }));
    previewList.append(
      el("li", {}, [
        el("span", {}, [el("code", { text: scenario.name }), ...tags]),
        el("span", { className: "muted", text: `${integer.format(scenario.runs)} runs` }),
      ]),
    );
  }
  previewPanel.hidden = false;
}

previewBtn.addEventListener("click", async () => {
  showFormError("");
  const values = readForm({ needAdapter: false });
  if (!values) return;
  previewBtn.disabled = true;
  try {
    renderPreview(await postJSON("/api/scenarios", { path: values.path }));
  } catch (err) {
    showFormError(err.message);
  } finally {
    previewBtn.disabled = false;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showFormError("");
  const values = readForm({ needAdapter: true });
  if (!values) return;
  startBtn.disabled = true;
  try {
    await postJSON("/api/runs", values);
  } catch (err) {
    showFormError(err.message);
    startBtn.disabled = jobRunning;
  }
});

function setKpi(node, key, text) {
  node.querySelector(`[data-kpi="${key}"]`).textContent = text;
}

// Creates the results panel for one scenario of the current job. Past
// COLLAPSE_AFTER runs, its run grid starts collapsed.
function createCard(name, total) {
  const node = scenarioTemplate.content.firstElementChild.cloneNode(true);
  const card = {
    name,
    total,
    node,
    done: 0,
    passed: 0,
    costTotal: 0,
    runs: [],
    dirty: false,
    finished: false,
    refreshing: false,
    chartVersion: 0,
    gridRendered: false,
  };
  node.querySelector(".scenario-name").textContent = name;

  const runsPanel = node.querySelector("details.runs");
  runsPanel.open = total <= COLLAPSE_AFTER;
  runsPanel.addEventListener("toggle", () => syncRunGrid(card));
  node.querySelector(".failed-only").addEventListener("change", () => {
    card.gridRendered = false;
    syncRunGrid(card);
  });
  node.querySelector(".run-grid").addEventListener("click", (event) => {
    const dot = event.target.closest(".run-dot");
    if (dot) openRun(card.name, Number(dot.dataset.runIndex));
  });

  cards.set(name, card);
  scenariosEl.append(node);
  renderProgress(card);
  syncRunGrid(card);
  return card;
}

function makeDot(run) {
  const verdict = run.passed ? "passed" : "failed";
  return el("button", {
    className: `run-dot ${run.passed ? "pass" : "fail"}`,
    attrs: {
      type: "button",
      "data-run-index": String(run.run_index),
      "aria-label": `Run ${run.run_index}, ${verdict}`,
      title: `Run ${run.run_index} · ${verdict} · ${ms(run.latency_ms)}`,
    },
  });
}

// Keeps a scenario's dot grid in step with its runs. A collapsed panel holds no
// dots at all, so a 5,000-run job costs nothing until someone opens it.
function syncRunGrid(card) {
  const runsPanel = card.node.querySelector("details.runs");
  const grid = card.node.querySelector(".run-grid");
  if (!runsPanel.open) {
    grid.replaceChildren();
    card.gridRendered = false;
    return;
  }
  if (card.gridRendered) return;
  const failedOnly = card.node.querySelector(".failed-only").checked;
  const fragment = document.createDocumentFragment();
  for (const run of card.runs) {
    if (!failedOnly || !run.passed) fragment.append(makeDot(run));
  }
  grid.replaceChildren(fragment);
  card.gridRendered = true;
}

function appendDot(card, run) {
  if (!card.gridRendered) return;
  if (run.passed && card.node.querySelector(".failed-only").checked) return;
  card.node.querySelector(".run-grid").append(makeDot(run));
}

function scheduleRender(card) {
  if (pendingRender.size === 0) requestAnimationFrame(flushRender);
  pendingRender.add(card);
}

function flushRender() {
  for (const card of pendingRender) renderProgress(card);
  pendingRender.clear();
}

// Updates the parts of a scenario panel that change with every completed run:
// progress, pass/fail counts, and the live pass rate.
function renderProgress(card) {
  const { node, done, total, passed } = card;
  const failed = done - passed;
  const bar = node.querySelector(".progress");
  bar.setAttribute("aria-valuemax", String(total));
  bar.setAttribute("aria-valuenow", String(done));
  node.querySelector(".progress-bar").style.width = `${total ? (done / total) * 100 : 0}%`;
  node.querySelector(".scenario-progress").textContent =
    `${integer.format(done)} of ${integer.format(total)} runs complete`;

  setKpi(node, "pass-rate", done ? pct(passed / done) : "–");
  setKpi(node, "counts", done ? `${integer.format(passed)} / ${integer.format(failed)}` : "–");
  setKpi(node, "counts-sub", done ? `${pct(failed / done)} of runs failed` : "");

  const sharePass = node.querySelector(".share-pass");
  const shareFail = node.querySelector(".share-fail");
  sharePass.style.flex = `${passed} 1 0`;
  shareFail.style.flex = `${failed} 1 0`;
  sharePass.hidden = passed === 0;
  shareFail.hidden = failed === 0;
  node.querySelector(".summary-counts").textContent = done
    ? `${integer.format(passed)} passed · ${integer.format(failed)} failed`
    : "No runs yet";
}

// Fills in what comes from the server's analysis: the confidence interval,
// latency and cost percentiles, and the data tables behind the charts.
function applyStats(card, stats) {
  const { node } = card;
  const [low, high] = stats.pass_rate_interval;
  setKpi(node, "pass-ci", `95% interval ${pct(low)}–${pct(high)}`);
  setKpi(node, "latency", ms(stats.latency_ms.p50));
  setKpi(node, "latency-sub", `p95 ${ms(stats.latency_ms.p95)} · p99 ${ms(stats.latency_ms.p99)}`);

  const costTracked = stats.cost_usd.max > 0;
  setKpi(node, "cost", costTracked ? usd(stats.cost_usd.p50) : "Not tracked");
  setKpi(node, "cost-sub", costTracked ? `Total ${usd(card.costTotal)}` : "Pass token prices to the adapter");
  node.querySelector('[data-chart="cost"]').hidden = !costTracked;

  renderTables(card, stats);
}

function renderTables(card, stats) {
  const assertionRows = stats.assertions.map((a) =>
    el("tr", {}, [
      el("td", {}, [el("code", { text: a.label })]),
      el("td", { className: "num", text: `${integer.format(a.failures)} / ${integer.format(a.total)}` }),
      el("td", { className: "num", text: pct(a.failure_rate) }),
      el("td", {
        className: "num",
        text: `${pct(a.failure_rate_interval[0])}–${pct(a.failure_rate_interval[1])}`,
      }),
    ]),
  );
  if (!assertionRows.length) {
    assertionRows.push(el("tr", {}, [el("td", { text: "No assertions recorded.", attrs: { colspan: "4" } })]));
  }
  card.node.querySelector(".assertion-table tbody").replaceChildren(...assertionRows);

  const pathRows = stats.tool_paths.map((p) =>
    el("tr", {}, [
      el("td", {}, [el("code", { text: p.path.length ? p.path.join(" → ") : "(no tool calls)" })]),
      el("td", { className: "num", text: integer.format(p.count) }),
      el("td", { className: "num", text: pct(p.frequency) }),
    ]),
  );
  card.node.querySelector(".path-table tbody").replaceChildren(...pathRows);
}

// Reloads a scenario's charts, keeping each old image on screen until its
// replacement has loaded so the panel never flashes empty.
function refreshCharts(card) {
  card.chartVersion += 1;
  for (const figure of card.node.querySelectorAll("figure[data-chart]")) {
    if (figure.hidden) continue;
    const img = figure.querySelector("img");
    const next = new Image();
    next.onload = () => {
      img.src = next.src;
      figure.classList.remove("empty");
    };
    const query = `v=${card.chartVersion}&theme=${currentTheme()}`;
    next.src = `${scenarioUrl(card.name, `charts/${figure.dataset.chart}.svg`)}?${query}`;
  }
}

// Pulls fresh statistics and charts for a scenario that has new runs.
async function refreshCard(card) {
  card.dirty = false;
  card.refreshing = true;
  try {
    const stats = await fetchJSON(scenarioUrl(card.name, "stats"));
    if (!card.finished && cards.get(card.name) === card) applyStats(card, stats);
  } catch {
    return;
  } finally {
    card.refreshing = false;
  }
  refreshCharts(card);
}

setInterval(() => {
  for (const card of cards.values()) {
    if (card.dirty && !card.refreshing) refreshCard(card);
  }
}, REFRESH_MS);

function markRunning(name) {
  const card = cards.get(name);
  if (card) setPill(card.node.querySelector(".status-pill"), "running", "Running");
}

function startJob(scenarios) {
  cards.clear();
  scenariosEl.replaceChildren();
  scenarioOrder = scenarios.map((s) => s.name);
  emptyState.hidden = true;
  jobError.hidden = true;
  for (const scenario of scenarios) createCard(scenario.name, scenario.runs);
  markRunning(scenarioOrder[0]);
  setPill(jobStatus, "running", "Running");
  setJobRunning(true);
}

function completeRun(event) {
  const card = cards.get(event.scenario);
  if (!card) return;
  const run = { run_index: event.run_index, passed: event.passed, latency_ms: event.latency_ms };
  card.runs.push(run);
  card.done += 1;
  if (event.passed) card.passed += 1;
  card.costTotal += event.cost_usd;
  card.dirty = true;
  appendDot(card, run);
  scheduleRender(card);
}

function finishScenario(name, stats) {
  const card = cards.get(name);
  if (!card) return;
  card.finished = true;
  card.dirty = false;
  setPill(card.node.querySelector(".status-pill"), "done", "Done");
  applyStats(card, stats);
  refreshCharts(card);
  const next = scenarioOrder[scenarioOrder.indexOf(name) + 1];
  if (next) markRunning(next);
}

function endJob(message) {
  setJobRunning(false);
  if (!message) {
    setPill(jobStatus, "finished", "Finished");
    return;
  }
  setPill(jobStatus, "error", "Error");
  jobError.textContent = `The run stopped: ${message}`;
  jobError.hidden = false;
  for (const card of cards.values()) {
    if (!card.finished) setPill(card.node.querySelector(".status-pill"), "error", "Stopped");
  }
}

// Applies one event from the server's live stream. A reconnect replays the
// whole log from its job_started, which rebuilds the page from scratch.
function handleEvent(event) {
  if (event.type === "job_started") startJob(event.scenarios);
  else if (event.type === "run_completed") completeRun(event);
  else if (event.type === "scenario_finished") finishScenario(event.scenario, event.stats);
  else if (event.type === "job_finished") endJob(null);
  else if (event.type === "job_error") endJob(event.message);
}

function section(title, children) {
  return el("section", { className: "dialog-section" }, [el("h3", { text: title }), ...children]);
}

function renderMessage(message) {
  const parts = [el("span", { className: "role", text: message.role || "message" })];
  if (message.content !== null && message.content !== undefined && message.content !== "") {
    parts.push(el("pre", { text: pretty(message.content) }));
  }
  if (message.tool_calls) parts.push(el("pre", { text: pretty(message.tool_calls) }));
  return el("div", { className: "message" }, parts);
}

function renderToolCall(call, index) {
  const timing = call.error ? `failed · ${ms(call.duration_ms)}` : ms(call.duration_ms);
  return el("div", { className: "tool-call" }, [
    el("div", { className: "tool-call-header" }, [
      el("code", { text: `${index + 1}. ${call.name}` }),
      el("span", { className: "muted", text: timing }),
    ]),
    el("p", { className: "tool-call-label", text: "Arguments" }),
    el("pre", { text: pretty(call.arguments) }),
    el("p", { className: "tool-call-label", text: call.error ? "Error" : "Result" }),
    el("pre", { text: call.error ?? pretty(call.result) }),
  ]);
}

// Lays out one run's full record: its verdict, the numbers, every assertion,
// every tool call, the final output, and the raw conversation.
function renderRecord(record) {
  const passed = record.error === null && record.assertions.every((a) => a.passed);
  const meta = [
    ["Latency", ms(record.latency_ms)],
    ["Prompt tokens", integer.format(record.prompt_tokens)],
    ["Completion tokens", integer.format(record.completion_tokens)],
    ["Cost", usd(record.cost_usd)],
    ["Tool calls", integer.format(record.tool_calls.length)],
  ];

  const sections = [
    el("div", { className: "dialog-section" }, [passed ? pill("done", "Passed") : pill("error", "Failed")]),
    el("dl", { className: "meta-grid dialog-section" }, meta.map(([label, value]) =>
      el("div", {}, [el("dt", { text: label }), el("dd", { text: value })]),
    )),
  ];

  if (record.error) {
    sections.push(section("Error", [el("div", { className: "alert", text: record.error })]));
  }

  sections.push(
    section("Assertions", [
      record.assertions.length
        ? el("ul", { className: "check-list" }, record.assertions.map((a) =>
            el("li", {}, [
              el("span", {
                className: `check-icon ${a.passed ? "pass" : "fail"}`,
                text: a.passed ? "✓" : "✕",
                attrs: { "aria-label": a.passed ? "Passed" : "Failed" },
              }),
              el("span", {}, [
                el("code", { text: a.label }),
                a.detail ? el("span", { className: "check-detail", text: a.detail }) : null,
              ]),
            ]),
          ))
        : el("p", { className: "muted", text: "This scenario made no assertions." }),
    ]),
  );

  sections.push(
    section(
      "Tool calls",
      record.tool_calls.length
        ? record.tool_calls.map(renderToolCall)
        : [el("p", { className: "muted", text: "No tools were called." })],
    ),
  );

  sections.push(section("Final output", [el("pre", { text: record.final_output || "(empty)" })]));

  sections.push(
    el("details", {}, [
      el("summary", {}, [
        el("span", { className: "summary-title", text: `Full conversation (${record.raw_messages.length} messages)` }),
      ]),
      el("div", { className: "conversation" }, record.raw_messages.map(renderMessage)),
    ]),
  );
  return sections;
}

// Opens the full trace of one run. A token guards against a slow response for
// an earlier click overwriting the run that was clicked last.
async function openRun(scenario, runIndex) {
  const token = ++dialogToken;
  dialogTitle.textContent = `Run ${runIndex}`;
  dialogSubtitle.textContent = scenario;
  dialogBody.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
  if (!dialog.open) dialog.showModal();
  try {
    const record = await fetchJSON(scenarioUrl(scenario, `runs/${runIndex}`));
    if (token === dialogToken) dialogBody.replaceChildren(...renderRecord(record));
  } catch (err) {
    if (token === dialogToken) dialogBody.replaceChildren(el("div", { className: "alert", text: err.message }));
  }
}

// Returns the theme on screen: the one the user picked, otherwise the OS's.
function currentTheme() {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return systemDark.matches ? "dark" : "light";
}

function renderThemeToggle() {
  const dark = currentTheme() === "dark";
  themeToggle.querySelector(".theme-icon").textContent = dark ? "☀" : "☾";
  themeToggle.querySelector(".theme-label").textContent = dark ? "Light" : "Dark";
  themeToggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
}

// Re-requests every chart in the current theme: they're server-rendered
// images, so unlike the rest of the page they can't restyle themselves.
function rethemeCharts() {
  renderThemeToggle();
  for (const card of cards.values()) {
    if (card.done > 0) refreshCharts(card);
  }
}

themeToggle.addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("stochast-theme", next);
  } catch {}
  rethemeCharts();
});
systemDark.addEventListener("change", rethemeCharts);
renderThemeToggle();

document.getElementById("run-dialog-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

const source = new EventSource("/api/runs/current/events");
source.onmessage = (event) => handleEvent(JSON.parse(event.data));

/* StockRelate frontend. All discrete-maths results come from the Python API;
   this file only holds UI state, renders results and draws Plotly figures. */
"use strict";

// ------------------------------------------------------------------ constants
const FONT = '"Plus Jakarta Sans", Arial, sans-serif';
const COMPONENT_COLORS = [
  "#2a5e3f", "#038ff7", "#e0892b", "#8b4fc7", "#d94f70",
  "#1a9e8f", "#86521e", "#5b6475", "#8a9a2b", "#5fb3e8",
];
const TABS = ["sets", "relation", "properties", "reachability", "explorer"];

// ------------------------------------------------------------------ state
const state = {
  config: null,
  mode: null,
  source: null, // payload from /api/sources or /api/upload
  sourceError: null,
  draft: { symbols: new Set(), start: null, end: null },
  applied: { symbols: [], start: null, end: null },
  tick: 65,
  analysis: null,
  applyError: null,
  sets: null,
  x: [],
  y: [],
  tab: "sets",
  pair: { a: null, b: null },
  path: { from: null, to: null },
  pathResult: null,
  seq: 0,
  dirtyCharts: new Set(),
};

// ------------------------------------------------------------------ utilities
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

function g6(value) {
  // Python's "%.6g"
  if (value === 0) return "0";
  const exponent = Math.floor(Math.log10(Math.abs(value)));
  if (exponent < -4 || exponent >= 6) {
    return value.toExponential(5).replace(/\.?0+e/, "e").replace(/e([+-])(\d)$/, "e$10$2");
  }
  return String(parseFloat(value.toPrecision(6)));
}
const compareText = (value, tau) => (value >= tau ? `${g6(value)} ≥ ${g6(tau)}` : `${g6(value)} < ${g6(tau)}`);
const tauText = (tick) => (tick / 100).toFixed(2);
const labeled = (names, s) => (names[s] && names[s] !== s ? `${s} · ${names[s]}` : s);
const yesNo = (ok, yes = "True", no = "False") =>
  `<span class="pill ${ok ? "pill--yes" : "pill--no"}">${ok ? "✓ " + yes : "✗ " + no}</span>`;

class ApiError extends Error {}

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new ApiError("The StockRelate server could not be reached. Is it still running?");
  }
  if (!response.ok) {
    let detail = null;
    try {
      detail = (await response.json()).detail;
    } catch (_) {
      /* not JSON */
    }
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${response.status}).`);
  }
  return response;
}
const getJSON = (path) => api(path).then((r) => r.json());
const postJSON = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(
    (r) => r.json(),
  );

let toastTimer = null;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3200);
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function selectionBody(extra = {}) {
  return {
    source: state.mode,
    dataset_key: state.source ? state.source.dataset_key : null,
    symbols: state.applied.symbols,
    start: state.applied.start,
    end: state.applied.end,
    tick: state.tick,
    ...extra,
  };
}

// ------------------------------------------------------------------ data flow
async function init() {
  $("#hero-art").innerHTML = heroArt();
  bindStaticEvents();
  try {
    state.config = await getJSON("/api/config");
  } catch (error) {
    showGlobalError(error.message);
    return;
  }
  state.tick = state.config.default_tick;
  $("#footer-version").textContent = `v${state.config.version}`;
  renderPresets();
  renderSourceList();
  setTab(location.hash.replace("#", "") in TAB_LOOKUP ? location.hash.replace("#", "") : "sets", false);
  await selectSource(state.config.default_source);
}
const TAB_LOOKUP = Object.fromEntries(TABS.map((t) => [t, true]));

async function selectSource(mode) {
  state.mode = mode;
  state.source = null;
  state.sourceError = null;
  state.analysis = null;
  state.applyError = null;
  state.applied = { symbols: [], start: null, end: null };
  state.draft = { symbols: new Set(), start: null, end: null };
  state.x = [];
  state.y = [];
  state.sets = null;
  state.pathResult = null;
  renderSourceList();
  if (mode === "upload") {
    renderAll();
    renderDrawer();
    return;
  }
  try {
    setSource(await getJSON(`/api/sources/${encodeURIComponent(mode)}`));
  } catch (error) {
    state.sourceError = error.message;
    renderAll();
    renderDrawer();
  }
}

function setSource(source) {
  state.source = source;
  state.draft.symbols = new Set(source.default_selection);
  state.draft.start = source.date_bounds ? source.date_bounds[0] : null;
  state.draft.end = source.date_bounds ? source.date_bounds[1] : null;
  renderDrawer();
  if (source.auto_apply) {
    apply();
  } else {
    renderAll();
    refreshSets();
  }
}

async function apply() {
  if (!state.source) return false;
  const symbols = Array.from(state.draft.symbols).sort();
  const dated = Boolean(state.source.date_bounds);
  state.applied = {
    symbols,
    start: dated ? state.draft.start : null,
    end: dated ? state.draft.end : null,
  };
  const applied = new Set(symbols);
  state.x = state.x.filter((s) => applied.has(s));
  state.y = state.y.filter((s) => applied.has(s));
  state.analysis = null;
  state.applyError = null;
  refreshSets();
  if (!symbols.length) {
    renderAll();
    return true;
  }
  return analyze();
}

async function analyze() {
  if (!state.source || !state.applied.symbols.length) return false;
  const seq = ++state.seq;
  document.body.classList.add("is-busy");
  try {
    const result = await postJSON("/api/analyze", selectionBody());
    if (seq !== state.seq) return false;
    state.analysis = result;
    state.applyError = null;
    resetEndpoints(result.symbols);
    renderAll();
    refreshPath();
    return true;
  } catch (error) {
    if (seq !== state.seq) return false;
    state.analysis = null;
    state.applyError = error.message;
    renderAll();
    return false;
  } finally {
    if (seq === state.seq) document.body.classList.remove("is-busy");
  }
}

function resetEndpoints(symbols) {
  const fix = (value, fallback) => (symbols.includes(value) ? value : fallback);
  state.pair.a = fix(state.pair.a, symbols[0]);
  state.pair.b = fix(state.pair.b, symbols[symbols.length - 1]);
  state.path.from = fix(state.path.from, symbols[0]);
  state.path.to = fix(state.path.to, symbols[symbols.length - 1]);
}

async function refreshSets() {
  if (!state.source) {
    state.sets = null;
    renderSets();
    return;
  }
  try {
    state.sets = await postJSON("/api/sets", {
      source: state.mode,
      dataset_key: state.source.dataset_key,
      applied: state.applied.symbols,
      x: state.x,
      y: state.y,
    });
  } catch (error) {
    state.sets = null;
    toast(error.message);
  }
  renderSets();
}

async function refreshPath() {
  if (!state.analysis) return;
  const seq = state.seq;
  try {
    const result = await postJSON(
      "/api/path",
      selectionBody({ path_from: state.path.from, path_to: state.path.to }),
    );
    if (seq !== state.seq) return;
    state.pathResult = result;
  } catch (error) {
    state.pathResult = { found: false, message: error.message };
  }
  renderPathResult();
}

function setTick(tick) {
  state.tick = tick;
  renderTau();
  analyzeSoon();
}
const analyzeSoon = debounce(() => analyze(), 90);

function useAsSelection(members) {
  state.draft.symbols = new Set(members);
  apply().then(() => toast(`S replaced with ${members.length ? "{" + members.join(", ") + "}" : "∅"}.`));
}

async function downloadExport(name) {
  try {
    const response = await api(`/api/export/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(selectionBody()),
    });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    toast(error.message);
  }
}

// ------------------------------------------------------------------ events
function bindStaticEvents() {
  const slider = $("#tau-slider");
  slider.addEventListener("input", () => setTick(Number(slider.value)));

  $$("[data-open-drawer]").forEach((el) => el.addEventListener("click", () => openDrawer(el)));
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#drawer").hidden) closeDrawer();
  });

  $$("[data-goto]").forEach((el) =>
    el.addEventListener("click", (event) => {
      event.preventDefault();
      setTab(el.dataset.goto, true);
    }),
  );
  $$(".tab").forEach((el) => {
    el.addEventListener("click", () => setTab(el.dataset.tab, false));
    el.addEventListener("keydown", (event) => {
      const index = TABS.indexOf(el.dataset.tab);
      let next = null;
      if (event.key === "ArrowRight") next = TABS[(index + 1) % TABS.length];
      if (event.key === "ArrowLeft") next = TABS[(index - 1 + TABS.length) % TABS.length];
      if (next) {
        event.preventDefault();
        setTab(next, false);
        $(`#tab-${next}`).focus();
      }
    });
  });

  window.addEventListener(
    "scroll",
    () => $(".nav").classList.toggle("is-scrolled", window.scrollY > 8),
    { passive: true },
  );

  // drawer controls
  $("#stock-search").addEventListener("input", renderStockList);
  $("#select-all").addEventListener("click", () => {
    if (!state.source) return;
    const all = state.source.stocks.map((s) => s.symbol).slice(0, state.config.max_selected);
    state.draft.symbols = new Set(all);
    renderStockList();
  });
  $("#select-none").addEventListener("click", () => {
    state.draft.symbols = new Set();
    renderStockList();
  });
  $("#stock-list").addEventListener("change", (event) => {
    const input = event.target.closest("input[type=checkbox]");
    if (!input) return;
    if (input.checked) state.draft.symbols.add(input.value);
    else state.draft.symbols.delete(input.value);
    renderStockList();
  });
  $("#date-start").addEventListener("change", (e) => (state.draft.start = e.target.value || null));
  $("#date-end").addEventListener("change", (e) => (state.draft.end = e.target.value || null));
  $("#source-list").addEventListener("change", (event) => {
    if (event.target.name === "source") selectSource(event.target.value);
  });
  $("#upload-btn").addEventListener("click", uploadFiles);
  $("#apply-btn").addEventListener("click", async () => {
    const button = $("#apply-btn");
    button.disabled = true;
    const ok = await apply();
    button.disabled = false;
    if (ok) {
      closeDrawer();
      toast(`Applied ${state.applied.symbols.length} stock(s).`);
    } else {
      renderDrawerStatus();
    }
  });

  // module bodies use delegation
  document.addEventListener("click", onBodyClick);
  document.addEventListener("change", onBodyChange);
}

function onBodyClick(event) {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  if (action === "toggle-x" || action === "toggle-y") {
    const key = action === "toggle-x" ? "x" : "y";
    const symbol = target.dataset.symbol;
    state[key] = state[key].includes(symbol) ? state[key].filter((s) => s !== symbol) : [...state[key], symbol];
    refreshSets();
  } else if (action === "use-set") {
    useAsSelection(JSON.parse(target.dataset.members));
  } else if (action === "preset") {
    const tick = Number(target.dataset.tick);
    $("#tau-slider").value = tick;
    setTick(tick);
  } else if (action === "download") {
    downloadExport(target.dataset.name);
  } else if (action === "open-drawer") {
    openDrawer(target);
  } else if (action === "clear-subset") {
    state[target.dataset.key] = [];
    refreshSets();
  }
}

function onBodyChange(event) {
  const target = event.target;
  if (target.matches("[data-fill]")) {
    const key = target.dataset.fill;
    const sector = target.value;
    if (!sector || !state.sets) return;
    const subset = state.sets.sector_subsets.find((s) => s.sector === sector);
    state[key] = subset ? [...subset.symbols] : [];
    target.value = "";
    refreshSets();
  } else if (target.matches("[data-pair]")) {
    state.pair[target.dataset.pair] = target.value;
    renderPairResult();
  } else if (target.matches("[data-path]")) {
    state.path[target.dataset.path] = target.value;
    refreshPath();
  }
}

async function uploadFiles() {
  const metadata = $("#upload-metadata").files[0];
  const prices = $("#upload-prices").files[0];
  if (!metadata || !prices) {
    setDrawerStatus("Choose both CSV files first.");
    return;
  }
  const form = new FormData();
  form.append("metadata", metadata);
  form.append("prices", prices);
  const button = $("#upload-btn");
  button.disabled = true;
  setDrawerStatus("Validating files…");
  try {
    const source = await api("/api/upload", { method: "POST", body: form }).then((r) => r.json());
    setSource(source);
    setDrawerStatus(`Validated ${source.stocks.length} stocks. Choose stocks and press Apply.`);
  } catch (error) {
    setDrawerStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
}

// ------------------------------------------------------------------ tabs
function setTab(tab, scroll) {
  if (!TAB_LOOKUP[tab]) tab = "sets";
  state.tab = tab;
  $$(".tab").forEach((el) => {
    const active = el.dataset.tab === tab;
    el.setAttribute("aria-selected", String(active));
    el.tabIndex = active ? 0 : -1;
  });
  $$(".panel").forEach((el) => (el.hidden = el.dataset.panel !== tab));
  $$(".nav__links a").forEach((el) => el.classList.toggle("is-active", el.dataset.goto === tab));
  drawCharts(tab);
  if (scroll) $("#modules").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------------ drawer
let drawerOpener = null;
function openDrawer(opener) {
  drawerOpener = opener || null;
  $("#scrim").hidden = false;
  $("#drawer").hidden = false;
  document.body.style.overflow = "hidden";
  renderDrawer();
  $("#drawer-close").focus();
}
function closeDrawer() {
  $("#scrim").hidden = true;
  $("#drawer").hidden = true;
  document.body.style.overflow = "";
  if (drawerOpener) drawerOpener.focus();
}

function renderSourceList() {
  if (!state.config) return;
  $("#source-list").innerHTML = state.config.sources
    .map(
      (s) => `
      <label class="source">
        <input type="radio" name="source" value="${esc(s.id)}" ${s.id === state.mode ? "checked" : ""}>
        <span class="source__title">${esc(s.label)}</span>
        <span class="source__detail">${esc(s.detail)}</span>
      </label>`,
    )
    .join("");
}

function renderDrawer() {
  const source = state.source;
  $("#upload-fields").hidden = state.mode !== "upload";
  $("#stock-field").hidden = !source;
  $("#date-field").hidden = !(source && source.date_bounds);
  if (source && source.date_bounds) {
    const [first, last] = source.date_bounds;
    for (const id of ["#date-start", "#date-end"]) {
      $(id).min = first;
      $(id).max = last;
    }
    $("#date-start").value = state.draft.start || first;
    $("#date-end").value = state.draft.end || last;
    $("#date-help").textContent = `Prices available ${first} to ${last}. At least ${state.config.min_return_observations} aligned returns are required.`;
  }
  const warnings = $("#dataset-warnings");
  const list = source ? source.warnings : [];
  warnings.hidden = !list.length;
  if (list.length) {
    $("summary", warnings).textContent = `Dataset warnings (${list.length})`;
    $("ul", warnings).innerHTML = list.map((w) => `<li>${esc(w)}</li>`).join("");
  }
  $("#apply-btn").disabled = !source;
  renderStockList();
  renderDrawerStatus();
}

function renderStockList() {
  const source = state.source;
  const container = $("#stock-list");
  if (!source) {
    container.innerHTML = "";
    return;
  }
  const max = state.config.max_selected;
  const query = $("#stock-search").value.trim().toLowerCase();
  const full = state.draft.symbols.size >= max;
  const rows = source.stocks
    .filter((s) => !query || `${s.symbol} ${s.name} ${s.sector}`.toLowerCase().includes(query))
    .map((s) => {
      const checked = state.draft.symbols.has(s.symbol);
      return `
        <label class="stock ${s.available ? "" : "is-unavailable"}">
          <input type="checkbox" value="${esc(s.symbol)}" ${checked ? "checked" : ""} ${!checked && full ? "disabled" : ""}>
          <span class="stock__sym">${esc(s.symbol)}</span>
          <span class="stock__name">${esc(s.name)} <small>· ${esc(s.sector)}${s.available ? "" : " · unavailable"}</small></span>
        </label>`;
    });
  container.innerHTML = rows.length ? rows.join("") : `<div class="stock-empty">No stocks match “${esc(query)}”.</div>`;
  $("#stock-count").textContent = `${state.draft.symbols.size} / ${Math.min(max, source.stocks.length)} selected`;
}

function setDrawerStatus(message, isError = false) {
  const el = $("#drawer-status");
  el.textContent = message || "";
  el.style.color = isError ? "var(--error-ink)" : "";
}
function renderDrawerStatus() {
  if (state.mode === "upload" && !state.source) setDrawerStatus("Upload both CSV files to continue.");
  else if (state.sourceError) setDrawerStatus(state.sourceError, true);
  else if (state.applyError) setDrawerStatus("Selection could not be analyzed: " + state.applyError, true);
  else setDrawerStatus("");
}

// ------------------------------------------------------------------ render: shell
function renderAll() {
  renderTau();
  renderHeroMeta();
  renderStatus();
  renderAnalysisPanels();
  renderDownloads();
  renderSets();
}

function renderPresets() {
  $("#tau-presets").innerHTML = state.config.preset_ticks
    .map((t) => `<button class="preset" type="button" data-action="preset" data-tick="${t}">τ ${tauText(t)}</button>`)
    .join("");
}

function renderTau() {
  const slider = $("#tau-slider");
  slider.value = state.tick;
  slider.style.setProperty("--fill", `${state.tick}%`);
  slider.setAttribute("aria-valuetext", `τ = ${tauText(state.tick)}`);
  $("#tau-value").textContent = tauText(state.tick);
  $$(".preset").forEach((el) => el.classList.toggle("is-active", Number(el.dataset.tick) === state.tick));
  const metrics = state.analysis ? state.analysis.metrics : null;
  $$("#hero-stats dd").forEach((dd) => {
    const key = dd.dataset.metric;
    dd.classList.remove("is-yes", "is-no");
    if (!metrics) {
      dd.textContent = "–";
    } else if (key === "transitive") {
      const holds = state.analysis.direct.cards[2].holds;
      dd.textContent = holds ? "Yes" : "No";
      dd.classList.add(holds ? "is-yes" : "is-no");
    } else {
      dd.textContent = metrics[key];
    }
  });
}

function renderHeroMeta() {
  const label = state.config.sources.find((s) => s.id === state.mode)?.label || "";
  $("#nav-source").textContent = label;
  let window;
  if (state.mode === "teaching_matrix") window = "no price window (teaching matrix)";
  else if (state.applied.start) window = `${state.applied.start} → ${state.applied.end}`;
  else window = "not applied yet";
  $("#hero-meta").innerHTML = `<strong>${esc(label)}</strong> · ${state.applied.symbols.length} stock(s) applied · ${esc(window)}`;
  $("#footer-provenance").textContent = state.source ? state.source.provenance : "";
}

function showGlobalError(message) {
  const el = $("#global-error");
  el.textContent = message;
  el.hidden = !message;
}

function renderStatus() {
  if (state.sourceError) showGlobalError(state.sourceError);
  else if (state.applyError) showGlobalError("Selection could not be analyzed: " + state.applyError);
  else showGlobalError("");
}

function renderDownloads() {
  const list = $("#downloads-list");
  const a = state.analysis;
  if (!a) {
    list.innerHTML = `<p class="muted">Apply a selection to enable downloads.</p>`;
    $("#validation-json").textContent = "–";
    return;
  }
  const icon = `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v10M6 9l4 4 4-4M4 16h12"/></svg>`;
  list.innerHTML = a.exports
    .map(
      (name) =>
        `<button class="btn btn--outline btn--sm download-btn" type="button" data-action="download" data-name="${esc(name)}">${icon}${esc(name)}</button>`,
    )
    .join("");
  $("#validation-json").textContent = JSON.stringify(a.validation, null, 2);
}

// ------------------------------------------------------------------ render: helpers
function metric(label, value) {
  return `<div class="metric"><div class="metric__label">${label}</div><div class="metric__value">${esc(value)}</div></div>`;
}

function table(headers, rows, { tall = false, numeric = [] } = {}) {
  const head = headers.map((h, i) => `<th class="${numeric.includes(i) ? "num" : ""}">${h}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        `<tr class="${row.className || ""}">${(row.cells || row)
          .map((cell, i) => `<td class="${numeric.includes(i) ? "num" : ""}">${cell}</td>`)
          .join("")}</tr>`,
    )
    .join("");
  return `<div class="table-wrap ${tall ? "table-wrap--tall" : ""}"><table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function chartCard(id, title, { tall = false, aside = "", footer = "" } = {}) {
  return `
    <div class="card chart-card">
      <div class="card__title"><h3>${title}</h3>${aside}</div>
      <div class="chart ${tall ? "chart--tall" : ""}" id="${id}"></div>
      ${footer}
    </div>`;
}

function disclosure(title, body) {
  return `<details class="disclosure"><summary>${title}</summary><div class="disclosure__body">${body}</div></details>`;
}

function select(attr, value, symbols, names) {
  return `<select class="select" ${attr}>${symbols
    .map((s) => `<option value="${esc(s)}" ${s === value ? "selected" : ""}>${esc(labeled(names, s))}</option>`)
    .join("")}</select>`;
}

function propertyCards(cards) {
  return `<div class="grid grid--3">${cards
    .map(
      (c) => `
      <article class="card prop ${c.holds ? "" : "prop--fail"}">
        <div class="prop__head">
          <h3 class="prop__name">${esc(c.name)}</h3>
          ${c.holds ? '<span class="pill pill--yes">✓ Pass</span>' : '<span class="pill pill--no">✗ Fail</span>'}
        </div>
        <div class="prop__statement"><span class="math">${esc(c.statement)}</span></div>
        <p class="prop__text">${esc(c.text)}</p>
        ${c.detail.length ? `<ul class="prop__detail">${c.detail.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}
      </article>`,
    )
    .join("")}</div>`;
}

function classColor(index) {
  return COMPONENT_COLORS[index % COMPONENT_COLORS.length];
}

function emptyAnalysis() {
  const message =
    state.mode === "upload" && !state.source
      ? "Upload a metadata CSV and a price CSV to begin."
      : "Modules 2–5 need a valid applied selection of at least one stock.";
  return `
    <div class="empty">
      <span class="eyebrow">Nothing to analyze yet</span>
      <p>${esc(message)}</p>
      <button class="btn btn--primary" type="button" data-action="open-drawer">Choose data</button>
    </div>`;
}

// ------------------------------------------------------------------ render: module 1
function renderSets() {
  const body = $("#sets-body");
  const s = state.sets;
  if (!s) {
    body.innerHTML = state.source ? `<div class="card is-loading" style="height:220px"></div>` : emptyAnalysis();
    return;
  }
  const names = Object.fromEntries((state.source?.stocks || []).map((x) => [x.symbol, x.name]));
  const applied = state.applied.symbols;
  const emptyS = !applied.length;

  const stockTable = table(
    ["Symbol", "Name", "Sector", "In S", "Price data"],
    s.table.map((r) => ({
      className: r.in_s ? "row-active" : "",
      cells: [
        `<strong>${esc(r.symbol)}</strong>`,
        esc(r.name),
        esc(r.sector),
        r.in_s ? '<span class="pill pill--yes">yes</span>' : '<span class="pill pill--neutral">no</span>',
        r.price_data === "unavailable" ? `<span class="pill pill--no">unavailable</span>` : esc(r.price_data),
      ],
    })),
    { tall: true },
  );

  const sectorTable = s.sector_subsets.length
    ? table(
        ["Sector k", "Sₖ", "|Sₖ|", "Stocks"],
        s.sector_subsets.map((r) => [esc(r.sector), `<span class="math">${esc(r.members)}</span>`, r.size, esc(r.stocks)]),
        { numeric: [2] },
      ) +
      `<p class="caption table-note">Each stock has exactly one sector, so the nonempty sector subsets are pairwise disjoint and their union is S: they partition S.</p>`
    : `<p class="muted">S = ∅, so there are no sector subsets.</p>`;

  const sectorOptions = `<option value="">Fill from sector…</option>${s.sector_subsets
    .map((r) => `<option value="${esc(r.sector)}">S ∩ U_${esc(r.sector)}</option>`)
    .join("")}`;
  const picker = (key, title) => `
    <div class="card picker">
      <div class="picker__head">
        <h3 class="h4">${title} <span class="muted small">= <span class="math">${esc(formatSet(state[key]))}</span></span></h3>
        <div class="op-row">
          <select class="select" data-fill="${key}" aria-label="Fill ${title} from sector" ${emptyS ? "disabled" : ""}>${sectorOptions}</select>
          <button class="btn btn--ghost btn--xs" type="button" data-action="clear-subset" data-key="${key}">Clear</button>
        </div>
      </div>
      <div class="chips">${
        applied.length
          ? applied
              .map(
                (sym) =>
                  `<button class="chip" type="button" data-action="toggle-${key}" data-symbol="${esc(sym)}" aria-pressed="${state[key].includes(sym)}" title="${esc(labeled(names, sym))}">${esc(sym)}</button>`,
              )
              .join("")
          : '<span class="muted small">Custom subsets are drawn from S; they are all ∅ while S is empty.</span>'
      }</div>
    </div>`;

  const operationsTable = table(
    ["Expression", "Result", "|·|", "Stocks", ""],
    s.operations.map((op) => [
      `<strong>${esc(op.expression)}</strong>`,
      `<span class="math">${esc(op.result)}</span>`,
      op.size,
      esc(op.stocks),
      `<button class="btn btn--ghost btn--xs" type="button" data-action="use-set" data-members='${esc(JSON.stringify(op.symbols))}'>Use as S</button>`,
    ]),
    { numeric: [2] },
  );

  const checks = s.checks
    .map(
      (c) => `
      <div class="set-line"><span class="set-line__value"><span class="math">${esc(c.statement)}</span></span>
      <span style="margin-left:auto">${typeof c.value === "boolean" ? yesNo(c.value) : `<strong>${esc(c.value)}</strong>`}</span></div>`,
    )
    .join("");

  body.innerHTML = `
    <div class="block">
      <div class="grid grid--3">
        ${metric("|U| — universe", s.universe_size)}
        ${metric("|S| — applied selection", s.applied_size)}
        <div class="metric"><div class="metric__label">S ⊆ U</div><div class="metric__value">${s.applied_subset ? "True" : "False"}</div></div>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="set-line"><span class="set-line__name">U</span><span class="set-line__value"><span class="math">${esc(s.universe_text)}</span></span></div>
        <div class="set-line"><span class="set-line__name">S</span><span class="set-line__value"><span class="math">${esc(s.applied_text)}</span></span></div>
      </div>
      ${
        emptyS
          ? `<div class="alert alert--info" style="margin-top:16px">S = ∅ (cardinality 0). Choose stocks in the Data drawer and press Apply selection to enable Modules 2–5.</div>`
          : ""
      }
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">The universe U</h3><p>Every stock in the loaded data source, and whether it is in S.</p></div>
      ${stockTable}
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Sector subsets Sₖ = S ∩ Uₖ</h3><p>Grouping the applied selection by sector.</p></div>
      ${sectorTable}
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Custom subsets X, Y ⊆ S</h3><p>Tap tickers to build X and Y. Inspecting never changes S — use “Use as S” to replace it.</p></div>
      <div class="grid grid--2">${picker("x", "X")}${picker("y", "Y")}</div>
      <div class="grid grid--wide" style="margin-top:16px">
        ${operationsTable}
        <div class="card">${checks}</div>
      </div>
    </div>`;
}

function formatSet(members) {
  const sorted = Array.from(new Set(members)).sort();
  return sorted.length ? `{${sorted.join(", ")}}` : "∅";
}

// ------------------------------------------------------------------ render: modules 2–5
function renderAnalysisPanels() {
  const a = state.analysis;
  for (const el of $$("[data-needs-analysis]")) {
    if (!a) el.innerHTML = emptyAnalysis();
  }
  if (!a) {
    state.dirtyCharts.clear();
    return;
  }
  renderRelation(a);
  renderProperties(a);
  renderReachability(a);
  renderExplorer(a);
  state.dirtyCharts = new Set(TABS);
  drawCharts(state.tab);
}

function windowCaption(a) {
  if (a.source_mode === "teaching_matrix") {
    return "Synthetic teaching correlations; no price window and no empirical observation count.";
  }
  return `Requested window ${a.requested_start} to ${a.requested_end}; ${a.sample_count} aligned return observations retained (from ${a.candidate_return_rows} candidates, ${a.removed_return_count} discarded).`;
}

function graphLegend(a, withWitness = false) {
  const classes = a.components
    .slice(0, 10)
    .map((c, i) => `<span><span class="dot" style="background:${classColor(i)}"></span>[${esc(c.id)}]</span>`)
    .join("");
  const witness = withWitness && a.direct.witness.length
    ? `<span><i style="border-color:#f5a623"></i>premise edges</span><span><i class="dash" style="border-color:#ee0000"></i>missing conclusion</span>`
    : "";
  return `<div class="legend">${classes}${a.components.length > 10 ? "<span>…</span>" : ""}${witness}</div>`;
}

function renderRelation(a) {
  const tau = a.tau;
  const synthetic = a.synthetic ? '<span class="pill pill--amber">synthetic</span>' : "";
  const pairNote =
    a.relation.ordered_pair_total > a.relation.ordered_pairs.length
      ? ` — first ${a.relation.ordered_pairs.length} shown; download ordered_pairs.csv for all`
      : "";
  $("#relation-body").innerHTML = `
    <div class="block">
      <div class="card-feature callout">
        <div>
          <div class="strong">${esc(a.source_label)}</div>
          <p class="small muted" style="margin-top:4px">${esc(windowCaption(a))}</p>
        </div>
      </div>
      ${disclosure(
        "How returns and correlations are defined",
        `<p>Simple fractional returns between consecutive observed dates: <span class="math">r(t,s) = P(t,s) / P(t−1,s) − 1</span> (0.02 means 2%).</p>
         <p>Return rows touching any missing price are discarded for <strong>all</strong> stocks (listwise alignment), so every correlation uses the same dates.</p>
         <p>Pearson correlation <span class="math">C(i,j) = Σ(rᵢ−r̄ᵢ)(rⱼ−r̄ⱼ) / (√Σ(rᵢ−r̄ᵢ)² · √Σ(rⱼ−r̄ⱼ)²)</span>.</p>`,
      )}
    </div>

    <div class="block">
      ${chartCard("chart-correlation", "Correlation matrix C", { tall: true, aside: synthetic })}
    </div>

    <div class="block">
      <div class="block__head">
        <h3 class="h4">Rτ = {(a, b) ∈ S × S : C(a, b) ≥ τ} with τ = <span class="math">${esc(String(tau))}</span></h3>
        <p>Signed, inclusive, unrounded; negative correlations never qualify.</p>
      </div>
      <div class="grid grid--3">
        ${metric("Nodes n = |S|", a.metrics.node_count)}
        ${metric("Undirected edges m", a.metrics.edge_count)}
        ${metric("Ordered pairs |Rτ| = n + 2m", a.metrics.ordered_pair_count)}
      </div>
    </div>

    <div class="block grid grid--2">
      ${chartCard("chart-relation-matrix", "Relation matrix A <span class='muted small'>(diagonal kept)</span>")}
      ${chartCard("chart-relation-graph", "Graph Gτ <span class='muted small'>(self-loops hidden)</span>", {
        footer: graphLegend(a) + `<p class="caption" style="padding:0 8px 8px">The graph omits loops for readability; every (a, a) is still in Rτ. Each line is one undirected edge = two ordered pairs.</p>`,
      })}
    </div>

    <div class="block">
      ${disclosure(
        "Relation matrix as a table (0/1)",
        table(
          ["", ...a.symbols.map(esc)],
          a.relation.matrix.map((row, i) => [`<strong>${esc(a.symbols[i])}</strong>`, ...row.map((v) => (v ? "1" : '<span class="muted">0</span>'))]),
        ),
      )}
    </div>

    <div class="block grid grid--2">
      <div>
        <div class="block__head"><h3 class="h4">Ordered pairs</h3><p>${a.relation.ordered_pair_total} total${pairNote}</p></div>
        ${table(["Source", "Target"], a.relation.ordered_pairs.map(([x, y]) => [esc(x), esc(y)]), { tall: true })}
      </div>
      <div>
        <div class="block__head"><h3 class="h4">Undirected edges</h3><p>${a.relation.edges.length} edge(s)</p></div>
        ${
          a.relation.edges.length
            ? table(
                ["Source", "Target", "Correlation"],
                a.relation.edges.map((e) => [esc(e.source_label), esc(e.target_label), e.correlation.toFixed(4)]),
                { tall: true, numeric: [2] },
              )
            : `<div class="card muted small">No edges at this threshold — every stock is isolated.</div>`
        }
      </div>
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Pair inspector</h3><p>Pick any ordered pair and see exactly why it is or is not in Rτ.</p></div>
      <div class="card">
        <div class="inline-fields">
          <label><span class="label">Source a</span>${select('data-pair="a"', state.pair.a, a.symbols, a.names)}</label>
          <label><span class="label">Target b</span>${select('data-pair="b"', state.pair.b, a.symbols, a.names)}</label>
        </div>
        <div id="pair-result" style="margin-top:16px"></div>
      </div>
    </div>`;
  renderPairResult();
}

function renderPairResult() {
  const a = state.analysis;
  const el = $("#pair-result");
  if (!a || !el) return;
  const i = a.symbols.indexOf(state.pair.a);
  const j = a.symbols.indexOf(state.pair.b);
  if (i < 0 || j < 0) return;
  const value = a.correlation[i][j];
  const member = a.relation.matrix[i][j];
  const self = state.pair.a === state.pair.b;
  el.innerHTML = table(
    ["Item", "Value"],
    [
      ["C(a, b)", `<span class="math">${esc(String(value))}</span>`],
      ["Comparison", `<span class="math">${esc(compareText(value, a.tau))}</span>`],
      ["(a, b) ∈ Rτ", yesNo(member)],
      ["Self-relation (a = b)", self ? "yes" : "no"],
      ["Drawn as graph edge", member && !self ? "yes" : "no"],
    ],
  );
}

function renderProperties(a) {
  const witness = a.direct.witness;
  $("#properties-body").innerHTML = `
    <div class="block">
      <p class="small" style="margin-bottom:16px">Analyzing: <strong>Direct relation R</strong> = Rτ at τ = <span class="math">${esc(String(a.tau))}</span></p>
      ${propertyCards(a.direct.cards)}
      <div class="alert ${a.direct.is_equivalence ? "alert--success" : "alert--warning"}" style="margin-top:16px">
        ${
          a.direct.is_equivalence
            ? "R itself is an equivalence relation at this threshold, so its classes coincide with the reachability classes of Module 4."
            : "R is <strong>not</strong> an equivalence relation here. Module 4 builds the reachability relation Q, which always is."
        }
      </div>
    </div>
    <div class="block grid grid--2">
      ${chartCard("chart-witness-matrix", "Direct relation R <span class='muted small'>(witness cells outlined)</span>")}
      ${chartCard("chart-witness-graph", "Gτ with witness highlighted", { footer: graphLegend(a, true) })}
    </div>
    ${
      witness.length
        ? `<p class="caption">Amber: the two premise edges and outlined cells. Red dashed: the missing conclusion pair.</p>`
        : ""
    }`;
}

function renderReachability(a) {
  const added = a.reachability.closure_added_pairs;
  const classTable = table(
    ["Class", "Members", "Size", "Stocks"],
    a.components.map((c, i) => [
      `<span style="display:inline-flex;gap:8px;align-items:center"><span class="dot" style="background:${classColor(i)}"></span><strong>[${esc(c.id)}]</strong></span>`,
      `<span class="math">${esc(c.members)}</span>`,
      c.size,
      esc(c.stocks),
    ]),
    { numeric: [2] },
  );
  const stockToClass = table(
    ["Symbol", "Name", "Sector", "Class"],
    a.stock_rows.map((r) => [`<strong>${esc(r.symbol)}</strong>`, esc(r.name), esc(r.sector), `[${esc(r.class_id)}]`]),
  );
  const checks = a.partition_checks
    .map((c) => `<div class="set-line"><span class="set-line__value">${esc(c.check)}</span><span style="margin-left:auto">${yesNo(c.ok)}</span></div>`)
    .join("");

  $("#reachability-body").innerHTML = `
    <div class="block">
      <div class="grid grid--3">
        ${metric("Classes", a.metrics.component_count)}
        ${metric("Largest class", a.metrics.largest_component_size)}
        ${metric("Isolated stocks", a.metrics.isolated_count)}
      </div>
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Reachability classes</h3><p>The connected components of Gτ. Class IDs are each class's smallest member.</p></div>
      <div class="grid grid--wide">
        ${classTable}
        <div class="card"><h3 class="h4" style="margin-bottom:12px">Partition checks</h3>${checks}</div>
      </div>
      ${disclosure("Stock → class table", stockToClass)}
    </div>

    <div class="block">
      ${chartCard("chart-class-graph", "Gτ coloured by class <span class='muted small'>(original edges only)</span>", {
        tall: true,
        footer: graphLegend(a) + `<p class="caption" style="padding:0 8px 8px">Node colours mark classes; class IDs in the tables are authoritative.</p>`,
      })}
    </div>

    <div class="block grid grid--2">
      ${chartCard("chart-reach-r", "Direct relation Rτ")}
      ${chartCard("chart-reach-q", "Reachability Q <span class='muted small'>(Q \\ R outlined)</span>")}
    </div>
    <div class="card-feature" style="margin-top:16px">
      ${
        added.length
          ? `<p><strong>Q \\ R</strong> (added by closure): <span class="math">${esc(added.map(([x, y]) => `(${x}, ${y})`).join(", "))}</span></p>
             <p class="small muted" style="margin-top:8px">These are reachability links through intermediate stocks, not newly measured correlations. A class need not be a clique: members can be below the threshold with each other.</p>`
          : `<p><strong>Q \\ R = ∅</strong> — the closure added no pairs, so R was already transitive.</p>`
      }
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Equivalence check of Q</h3><p>Analyzing: <strong>Reachability relation Q</strong></p></div>
      ${propertyCards(a.reachability.cards)}
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Shortest path</h3><p>Fewest edges, found by BFS — it minimizes edge count, not correlation distance.</p></div>
      <div class="card">
        <div class="inline-fields">
          <label><span class="label">From</span>${select('data-path="from"', state.path.from, a.symbols, a.names)}</label>
          <label><span class="label">To</span>${select('data-path="to"', state.path.to, a.symbols, a.names)}</label>
        </div>
        <div id="path-result" style="margin-top:16px"><div class="is-loading" style="height:44px;border-radius:12px"></div></div>
      </div>
      ${disclosure(
        "How Warshall's algorithm works",
        `<p>Start from R with every diagonal cell set to 1 (paths of length 0). For each intermediate stock k (outer loop), for each i and j: <span class="math">Q[i][j] ← Q[i][j] OR (Q[i][k] AND Q[k][j])</span>.</p>
         <p>After processing k, every pair connected by a path whose internal stocks are among those processed is marked. The result is the smallest reflexive-transitive relation containing R.</p>`,
      )}
    </div>`;
  renderPathResult();
}

function renderPathResult() {
  const el = $("#path-result");
  const result = state.pathResult;
  if (!el || !result) return;
  if (!result.found) {
    el.innerHTML = `<div class="alert alert--warning">${esc(result.message)}</div>`;
    return;
  }
  if (!result.steps || !result.steps.length) {
    el.innerHTML = `<div class="alert alert--success">${esc(result.message)}</div>`;
    return;
  }
  const chain = result.path
    .map((s, i) => {
      const node = `<span class="path__node" title="${esc(result.labels[i])}">${esc(s)}</span>`;
      const step = result.steps[i];
      return step ? `${node}<span class="path__edge">C = ${step.correlation.toFixed(4)}</span>` : node;
    })
    .join("");
  el.innerHTML = `
    <p class="small strong" style="margin-bottom:10px">${esc(result.message)}</p>
    <div class="path">${chain}</div>
    <p class="caption" style="margin-top:10px">${esc(result.labels.join(" → "))}</p>`;
}

function renderExplorer(a) {
  const story =
    a.source_mode === "teaching_matrix"
      ? "Expected story on the teaching matrix: at 0.20 the triangle A–B–C is complete; at 0.65 the edge A–C disappears and transitivity fails while A, B, C stay one class; at 0.75 C splits off and transitivity returns; at 0.90 every stock is isolated."
      : "Try the presets and watch the table: edges only disappear as τ rises, classes only split, while direct transitivity can switch between failing and holding.";
  const presets = state.config.preset_ticks
    .map(
      (t) =>
        `<button class="btn ${t === state.tick ? "btn--dark" : "btn--outline"} btn--sm" type="button" data-action="preset" data-tick="${t}">τ = ${tauText(t)}</button>`,
    )
    .join("");
  const comparison = table(
    ["τ", "Edges", "R transitive?", "Classes", "Largest class"],
    a.comparison.map((r) => ({
      className: Math.round(r.threshold * 100) === state.tick ? "row-active" : "",
      cells: [
        `<strong>${r.threshold.toFixed(2)}</strong>`,
        r.edge_count,
        yesNo(r.direct_transitive, "yes", "no"),
        r.component_count,
        r.largest_component_size,
      ],
    })),
    { numeric: [1, 3, 4] },
  );
  const degreeTable = table(
    ["Symbol", "Name", "Sector", "Degree", "Class"],
    a.stock_rows.map((r) => [`<strong>${esc(r.symbol)}</strong>`, esc(r.name), esc(r.sector), r.degree, `[${esc(r.class_id)}]`]),
    { numeric: [3], tall: true },
  );
  $("#explorer-body").innerHTML = `
    <div class="block">
      <div class="block__head"><h3 class="h4">Teaching presets</h3><p>Every view reads the single shared threshold slider in the hero. Presets move it.</p></div>
      <div class="op-row">${presets}</div>
      <p class="caption" style="margin-top:12px;max-width:820px">${esc(story)}</p>
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Preset comparison</h3><p>Same prepared correlation matrix at each preset.</p></div>
      ${comparison}
    </div>

    <div class="block">
      <div class="block__head"><h3 class="h4">Current threshold</h3></div>
      <div class="grid grid--4">
        ${metric("τ", a.tau.toFixed(2))}
        ${metric("Edges", a.metrics.edge_count)}
        ${metric("Classes", a.metrics.component_count)}
        ${metric("Density", a.metrics.density.toFixed(3))}
      </div>
      ${a.symbols.length < 2 ? '<p class="caption" style="margin-top:8px">Density is shown as 0 by convention when n &lt; 2 (no possible edges).</p>' : ""}
    </div>

    <div class="block grid grid--2">
      ${chartCard("chart-explorer-graph", "Gτ <span class='muted small'>(fixed positions across thresholds)</span>", { footer: graphLegend(a) })}
      <div>${degreeTable}</div>
    </div>

    <div class="block">
      ${disclosure(
        "Why classes refine as τ rises",
        `<p>For fixed data, τ₁ ≤ τ₂ implies R(τ₂) ⊆ R(τ₁), E(τ₂) ⊆ E(τ₁) and Q(τ₂) ⊆ Q(τ₁). Raising τ can only remove edges, so classes can split but never merge, and the class count never decreases: the partition becomes finer.</p>
         <p><strong>Transitivity is not monotone:</strong> a triangle is transitive, removing one edge breaks it, and removing another can make it transitive again.</p>`,
      )}
    </div>`;
}

// ------------------------------------------------------------------ charts
const CHARTS = {
  relation: [
    ["chart-correlation", "correlation", "heatmap"],
    ["chart-relation-matrix", "relation_matrix", "bool"],
    ["chart-relation-graph", "graph", "graph"],
  ],
  properties: [
    ["chart-witness-matrix", "witness_matrix", "bool"],
    ["chart-witness-graph", "witness_graph", "graph"],
  ],
  reachability: [
    ["chart-class-graph", "graph", "graph"],
    ["chart-reach-r", "relation_matrix", "bool"],
    ["chart-reach-q", "reachability_matrix", "bool"],
  ],
  explorer: [["chart-explorer-graph", "graph", "graph"]],
  sets: [],
};

function themed(figure, kind, el) {
  const layout = JSON.parse(JSON.stringify(figure.layout || {}));
  const data = JSON.parse(JSON.stringify(figure.data || []));
  delete layout.title;
  delete layout.template;
  const height = el.classList.contains("chart--tall") ? 520 : kind === "graph" ? 400 : 380;
  const axisFont = { family: FONT, size: 11, color: "#1b1a18" };
  Object.assign(layout, {
    height,
    autosize: true,
    font: { family: FONT, size: 12, color: "#1b1a18" },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    hoverlabel: { bgcolor: "#1b1a18", bordercolor: "#1b1a18", font: { family: FONT, size: 12, color: "#fefdfb" } },
    margin: kind === "graph" ? { l: 8, r: 8, t: 8, b: 8 } : { l: 56, r: 16, t: 8, b: 48 },
  });
  if (kind !== "graph") {
    for (const axis of ["xaxis", "yaxis"]) {
      layout[axis] = {
        ...layout[axis],
        tickfont: axisFont,
        showgrid: false,
        zeroline: false,
        linecolor: "#e8e6e1",
        ticks: "",
        constrain: "domain",
        constraintoward: "center",
      };
    }
  }
  if (kind === "graph") {
    layout.showlegend = false;
    for (const trace of data) {
      if (trace.mode && trace.mode.includes("text")) {
        trace.textfont = { family: FONT, size: 12, color: "#1b1a18" };
      }
    }
  }
  for (const trace of data) {
    if (trace.type === "heatmap") {
      trace.textfont = { family: FONT, size: data.length && trace.x && trace.x.length > 12 ? 9 : 11 };
      if (trace.showscale !== false) {
        trace.colorbar = { thickness: 10, outlinewidth: 0, tickfont: axisFont, len: 0.9 };
      }
      trace.xgap = trace.xgap ?? 1;
      trace.ygap = trace.ygap ?? 1;
    }
  }
  return { data, layout };
}

const PLOT_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines", "hoverClosestCartesian", "hoverCompareCartesian"],
  toImageButtonOptions: { format: "png", scale: 2 },
};

function drawCharts(tab) {
  const a = state.analysis;
  if (!a || !state.dirtyCharts.has(tab)) {
    // panel became visible: make sure existing plots fit the width
    if (window.Plotly) {
      for (const [id] of CHARTS[tab]) {
        const el = document.getElementById(id);
        if (el && el.data) Plotly.Plots.resize(el);
      }
    }
    return;
  }
  if (!window.Plotly) {
    CHARTS[tab].forEach(([id]) => $(`#${id}`)?.classList.add("is-loading"));
    whenPlotly(() => drawCharts(state.tab));
    return;
  }
  for (const [id, key, kind] of CHARTS[tab]) {
    const el = $(`#${id}`);
    if (!el) continue;
    el.classList.remove("is-loading");
    const { data, layout } = themed(a.figures[key], kind, el);
    Plotly.react(el, data, layout, PLOT_CONFIG);
  }
  state.dirtyCharts.delete(tab);
}

let plotlyWaiters = [];
function whenPlotly(fn) {
  if (window.Plotly) return fn();
  plotlyWaiters.push(fn);
  if (plotlyWaiters.length === 1) {
    const timer = setInterval(() => {
      if (!window.Plotly) return;
      clearInterval(timer);
      const waiters = plotlyWaiters;
      plotlyWaiters = [];
      waiters.forEach((w) => w());
    }, 100);
  }
}

// ------------------------------------------------------------------ hero art
function heroArt() {
  // Soft "clay" nodes clustering into components above rolling hills.
  const palettes = {
    cream: ["#ffffff", "#f5e0c3", "#d7b68c"],
    blue: ["#b9e0ff", "#4aa8f0", "#1667b8"],
    amber: ["#ffd9a3", "#e9963a", "#a8601a"],
    plum: ["#e3cbff", "#a77bdc", "#6b3fa3"],
    rose: ["#ffd0db", "#e66f8c", "#a83c58"],
  };
  const nodes = [
    // cluster 1 (cream)
    { x: 900, y: 150, r: 46, c: "cream", f: "" },
    { x: 1040, y: 96, r: 32, c: "cream", f: "--b" },
    { x: 1000, y: 262, r: 38, c: "cream", f: "--c" },
    { x: 1132, y: 206, r: 28, c: "cream", f: "" },
    // cluster 2 (blue)
    { x: 1262, y: 104, r: 36, c: "blue", f: "--b" },
    { x: 1372, y: 186, r: 28, c: "blue", f: "" },
    { x: 1286, y: 300, r: 32, c: "blue", f: "--c" },
    // cluster 3 (amber)
    { x: 1180, y: 392, r: 26, c: "amber", f: "--b" },
    { x: 1330, y: 440, r: 34, c: "amber", f: "" },
    // isolated
    { x: 812, y: 330, r: 22, c: "plum", f: "--c" },
    { x: 1418, y: 360, r: 18, c: "rose", f: "--b" },
  ];
  const edges = [
    [0, 1], [0, 2], [2, 3], [1, 3], [0, 3],
    [4, 5], [5, 6],
    [7, 8],
  ];
  const gradients = Object.entries(palettes)
    .map(
      ([name, [light, base, dark]]) => `
      <radialGradient id="clay-${name}" cx="35%" cy="30%" r="75%">
        <stop offset="0" stop-color="${light}"/>
        <stop offset="0.55" stop-color="${base}"/>
        <stop offset="1" stop-color="${dark}"/>
      </radialGradient>`,
    )
    .join("");
  const lines = edges
    .map(([i, j]) => {
      const a = nodes[i];
      const b = nodes[j];
      return `<line x1="${a.x}" y1="${a.y + 8}" x2="${b.x}" y2="${b.y + 8}" stroke="rgba(10,30,18,0.35)" stroke-width="12" stroke-linecap="round"/>
              <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="rgba(254,253,251,0.78)" stroke-width="9" stroke-linecap="round"/>`;
    })
    .join("");
  const circles = nodes
    .map(
      (n) => `
      <g class="clay-float${n.f ? " clay-float" + n.f : ""}">
        <circle cx="${n.x}" cy="${n.y}" r="${n.r}" fill="url(#clay-${n.c})" filter="url(#clay-shadow)"/>
        <ellipse cx="${n.x - n.r * 0.32}" cy="${n.y - n.r * 0.4}" rx="${n.r * 0.32}" ry="${n.r * 0.18}" fill="rgba(255,255,255,0.55)" transform="rotate(-24 ${n.x - n.r * 0.32} ${n.y - n.r * 0.4})"/>
      </g>`,
    )
    .join("");
  return `
    <svg viewBox="0 0 1440 640" preserveAspectRatio="xMaxYMid slice" xmlns="http://www.w3.org/2000/svg" focusable="false">
      <defs>
        ${gradients}
        <filter id="clay-shadow" x="-40%" y="-40%" width="180%" height="190%">
          <feDropShadow dx="0" dy="14" stdDeviation="12" flood-color="#0b2416" flood-opacity="0.35"/>
        </filter>
        <radialGradient id="clay-glow" cx="78%" cy="30%" r="55%">
          <stop offset="0" stop-color="#3b7a54" stop-opacity="0.9"/>
          <stop offset="1" stop-color="#2a5e3f" stop-opacity="0"/>
        </radialGradient>
      </defs>
      <rect width="1440" height="640" fill="url(#clay-glow)"/>
      <path d="M0 520 C 240 450, 520 470, 760 520 S 1200 455, 1440 492 L1440 640 L0 640 Z" fill="#326c49"/>
      <path d="M0 585 C 300 530, 640 565, 920 592 S 1300 545, 1440 570 L1440 640 L0 640 Z" fill="#245239"/>
      <g opacity="0.95">${lines}${circles}</g>
    </svg>`;
}

document.addEventListener("DOMContentLoaded", init);

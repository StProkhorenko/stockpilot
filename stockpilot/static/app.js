"use strict";

const byId = (id) => document.getElementById(id);
const form = byId("scenario");
const submit = form.querySelector("[type=submit]");
const status = byId("status");
const tabs = document.querySelectorAll("[data-chart]");
const number = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 1});
const percent = new Intl.NumberFormat("ru-RU", {style: "percent", maximumFractionDigits: 1});
const baselineNames = {last_week: "Последняя неделя", four_week: "Среднее 4 недель"};
const colors = {model: "#65e3d3", base: "#ffbb7d", previous: "#c6a7ff", actual: "#cdd9f7"};
let chartMode = "forecast";
let current;
let catalog;
let modelLabel = "Демо-прогноз";
let requestVersion = 0;

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}

function renderChart() {
  if (!current) return;
  const {forecast, simulation} = current;
  const title = byId("chart-title");
  const caption = byId("chart-caption");
  const baseline = baselineNames[catalog.baseline];
  const model = modelLabel;
  let dates;
  let lines;
  if (chartMode === "forecast") {
    title.textContent = "Прогноз на 14 дней";
    caption.textContent = "Последние 14 дней истории и первый прогноз выбранного окна · шт.";
    dates = forecast.history_dates.slice(-14).concat(forecast.dates.slice(0, 14));
    const pad = Array(14).fill(null);
    lines = [
      {name: "Продажи", color: colors.actual, values: forecast.history.slice(-14).concat(forecast.actual.slice(0, 14))},
      {name: baseline, color: colors.base, values: pad.concat(forecast.baseline[0])},
      {name: model, color: colors.model, values: pad.concat(forecast.model[0])},
    ];
    if (forecast.previous) lines.push({name: "LightGBM v1", color: colors.previous, values: pad.concat(forecast.previous[0]), dash: "6 5"});
  } else {
    title.textContent = "Остаток на конец дня";
    caption.textContent = "Всё выбранное окно · шт.";
    dates = forecast.dates.slice(0, simulation.model.daily.length);
    lines = [
      {name: baseline, color: colors.base, values: simulation.baseline.daily.map((row) => row.stock)},
      {name: model, color: colors.model, values: simulation.model.daily.map((row) => row.stock)},
    ];
    if (simulation.previous) lines.push({name: "LightGBM v1", color: colors.previous, values: simulation.previous.daily.map((row) => row.stock), dash: "6 5"});
  }
  const width = 680;
  const left = 43;
  const top = 12;
  const bottom = 207;
  const right = width - 18;
  const maxValue = Math.max(4, ...lines.flatMap((line) => line.values));
  const step = Math.max(1, Math.ceil(maxValue / 4));
  const max = step * 4;
  const x = (index) => left + index / (dates.length - 1) * (right - left);
  const y = (value) => bottom - value / max * (bottom - top);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 680 240");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", title.textContent + ". " + caption.textContent);
  function draw(tag, attrs, content) {
    const node = document.createElementNS(svg.namespaceURI, tag);
    Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
    if (content !== undefined) node.textContent = content;
    svg.append(node);
  }
  for (let i = 0; i <= 4; i += 1) {
    const level = i * step;
    draw("line", {x1: left, x2: right, y1: y(level), y2: y(level), stroke: "#333c58"});
    draw("text", {x: 35, y: y(level) + 4, "text-anchor": "end", fill: "#adb7d4", "font-size": 12}, level);
  }
  for (const i of [0, 7, 14, 21, dates.length - 1].filter((index) => index < dates.length)) {
    draw("text", {x: x(i), y: 229, "text-anchor": i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle", fill: "#adb7d4", "font-size": 12}, dates[i].slice(5));
  }
  if (chartMode === "forecast") {
    const boundary = (x(13) + x(14)) / 2;
    draw("line", {x1: boundary, x2: boundary, y1: top, y2: bottom, stroke: "#839a90", "stroke-dasharray": "4 5"});
  }
  for (const line of lines) {
    const points = line.values.flatMap((value, index) => value === null ? [] : [x(index) + "," + y(value)]);
    draw("polyline", {points: points.join(" "), fill: "none", stroke: line.color, "stroke-width": 2.6, "stroke-linejoin": "round", "stroke-dasharray": line.dash || "none"});
  }
  byId("chart").replaceChildren(svg);
  byId("legend").replaceChildren(...lines.map((line) => {
    const label = document.createElement("span");
    label.style.setProperty("--swatch", line.color);
    label.textContent = line.name;
    return label;
  }));
}

function metricCard(label, value, reference) {
  const card = document.createElement("div");
  card.className = "metric";
  for (const [tag, text] of [["span", label], ["strong", value], ["small", reference]]) {
    const line = document.createElement(tag);
    line.textContent = text;
    card.append(line);
  }
  return card;
}

function renderOutcomes() {
  const {baseline, model, previous, initial} = current.simulation;
  const metrics = [
    ["Недополучено, шт.", "lost", number],
    ["Удовлетворено", "fill_rate", percent],
    ["Средний остаток, шт.", "mean_stock", number],
    ["Условные издержки", "cost", number],
  ];
  byId("result-caption").textContent = model.daily.length + " дней симуляции · стартовый запас " + number.format(initial) +
    " шт. · крупным шрифтом показана выбранная модель.";
  byId("outcomes").replaceChildren(...metrics.map(([label, key, format]) => {
    const show = (value) => value === null ? "—" : format.format(value);
    return metricCard(label, show(model[key]), "Сезонный " + show(baseline[key]) + (previous ? " · v1 " + show(previous[key]) : ""));
  }));
}

function renderReport(report) {
  const element = byId("report");
  if (report.source !== "m5") {
    element.textContent = "Синтетический набор проверяет интерфейс. Сгенерируйте снимок M5, чтобы увидеть измерения.";
    return;
  }
  const message = document.createElement("p");
  message.textContent = report.series + " пары товар–магазин · итоговое окно выделено отдельно.";
  const final = report.final?.metrics;
  if (final) {
    const m = final.lightgbm;
    const old = final.v1;
    byId("highlights").replaceChildren(
      metricCard("Итог · дневная WAPE", percent.format(m.wape), "LightGBM v1 · " + percent.format(old.wape)),
      metricCard("Итог · недельная WAPE", percent.format(m.weekly_wape), "LightGBM v1 · " + percent.format(old.weekly_wape)),
      metricCard("Итог · смещение", percent.format(m.bias), "LightGBM v1 · " + percent.format(old.bias)),
      metricCard("Тестовый набор", report.series + " ряда", report.final.start + " — " + report.final.end),
    );
  }
  const table = document.createElement("table");
  const header = table.createTHead().insertRow();
  for (const label of ["Окно", "Дневная · сезонный", "Дневная · v1", "Дневная · модель", "Недельная · модель"]) {
    const heading = document.createElement("th");
    heading.scope = "col";
    heading.textContent = label;
    header.append(heading);
  }
  const tbody = table.createTBody();
  const add = (label, scores, emphasis = false) => {
    const row = tbody.insertRow();
    row.classList.toggle("emphasis", emphasis);
    [label, scores[report.baseline].wape, scores.v1?.wape, scores.lightgbm.wape, scores.lightgbm.weekly_wape].forEach((v) => {
      row.insertCell().textContent = typeof v === "number" ? percent.format(v) : v ?? "—";
    });
  };
  for (const fold of report.test.filter((window) => window.start !== report.final?.start)) add(fold.start + " — " + fold.end, fold.metrics);
  add("Четыре предыдущих окна", report.totals);
  if (report.final) add("Итог · " + report.final.start, report.final.metrics, true);
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  wrap.append(table);
  const summary = document.createElement("p");
  if (report.final_inventory) {
    const {model, previous, baseline} = report.final_inventory;
    summary.className = "fine";
    summary.textContent = "Итоговая симуляция, " + report.final.inventory_days + " день, поставка " +
      report.scenario.lead_days + " дня, буфер " + report.scenario.buffer_days + " день. Дефицит, шт.: " +
      model.lost + " у выбранной модели, " + previous.lost + " у v1, " + baseline.lost + " у сезонного прогноза.";
  }
  element.replaceChildren(message, wrap, summary);
}

async function run() {
  const version = ++requestVersion;
  submit.disabled = true;
  status.textContent = "Пересчитываем…";
  try {
    const result = await getJSON("/api/scenario?" + new URLSearchParams(new FormData(form)));
    if (version !== requestVersion) return;
    current = result;
    renderChart();
    renderOutcomes();
    status.textContent = "Сценарий рассчитан.";
  } catch (error) {
    if (version === requestVersion) status.textContent = "Не удалось рассчитать сценарий. " + error.message;
  } finally {
    if (version === requestVersion) submit.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  run();
});
tabs.forEach((tab) => tab.addEventListener("click", () => {
  chartMode = tab.dataset.chart;
  tabs.forEach((button) => button.setAttribute("aria-pressed", String(button === tab)));
  renderChart();
}));

async function init() {
  try {
    const [result, report] = await Promise.all([getJSON("/api/catalog"), getJSON("/api/report")]);
    catalog = result;
    if (catalog.source === "m5") modelLabel = report.selected === "tweedie" ? "LightGBM · Tweedie" : "Выбранная модель";
    byId("source").textContent = catalog.source === "m5" ? "M5 · реальные продажи" : "Синтетический пример";
    for (const item of catalog.series) {
      form.elements.series.add(new Option(item.item_id + " · " + item.store_id, item.id));
    }
    catalog.folds.forEach((fold, index) => {
      const final = fold.start === report.final?.start;
      form.elements.window.add(new Option((final ? "Итог · " : "") + fold.start + " — " + fold.end, index));
      if (final) form.elements.window.value = index;
    });
    renderReport(report);
    await run();
  } catch (error) {
    status.textContent = "Не удалось загрузить данные. " + error.message;
  }
}
init();

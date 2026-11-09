"use strict";

const byId = (id) => document.getElementById(id);
const form = byId("scenario");
const submit = form.querySelector("[type=submit]");
const status = byId("status");
const tabs = document.querySelectorAll("[data-chart]");
const number = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 1});
const percent = new Intl.NumberFormat("ru-RU", {style: "percent", maximumFractionDigits: 1});
const baselineNames = {last_week: "Последняя неделя", four_week: "Среднее 4 недель"};
const colors = {model: "#008465", base: "#9c551f", actual: "#465d79"};
let chartMode = "forecast";
let current;
let catalog;
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
  const model = catalog.source === "m5" ? "LightGBM" : "Демо-прогноз";
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
  } else {
    title.textContent = "Остаток на конец дня";
    caption.textContent = "Всё выбранное окно · шт.";
    dates = forecast.dates;
    lines = [
      {name: baseline, color: colors.base, values: simulation.baseline.daily.map((row) => row.stock)},
      {name: model, color: colors.model, values: simulation.model.daily.map((row) => row.stock)},
    ];
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
    draw("line", {x1: left, x2: right, y1: y(level), y2: y(level), stroke: "#dce4db"});
    draw("text", {x: 35, y: y(level) + 4, "text-anchor": "end", fill: "#53645f", "font-size": 12}, level);
  }
  for (const i of [0, 7, 14, 21, dates.length - 1]) {
    draw("text", {x: x(i), y: 229, "text-anchor": i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle", fill: "#53645f", "font-size": 12}, dates[i].slice(5));
  }
  if (chartMode === "forecast") {
    const boundary = (x(13) + x(14)) / 2;
    draw("line", {x1: boundary, x2: boundary, y1: top, y2: bottom, stroke: "#839a90", "stroke-dasharray": "4 5"});
  }
  for (const line of lines) {
    const points = line.values.flatMap((value, index) => value === null ? [] : [x(index) + "," + y(value)]);
    draw("polyline", {points: points.join(" "), fill: "none", stroke: line.color, "stroke-width": 2.6, "stroke-linejoin": "round"});
  }
  byId("chart").replaceChildren(svg);
  byId("legend").replaceChildren(...lines.map((line) => {
    const label = document.createElement("span");
    label.style.setProperty("--swatch", line.color);
    label.textContent = line.name;
    return label;
  }));
}

function renderOutcomes() {
  const {baseline, model, initial} = current.simulation;
  const metrics = [
    ["Недополучено, шт.", "lost", number],
    ["Удовлетворено", "fill_rate", percent],
    ["Средний остаток, шт.", "mean_stock", number],
    ["Условные издержки", "cost", number],
  ];
  byId("result-caption").textContent = "Выбранное окно · стартовый запас " + number.format(initial) + " шт. · " +
    (catalog.source === "m5" ? "LightGBM" : "Демо-прогноз") + " и сезонный бейзлайн.";
  byId("outcomes").replaceChildren(...metrics.map(([label, key, format]) => {
    const card = document.createElement("div");
    card.className = "metric";
    const caption = document.createElement("span");
    caption.textContent = label;
    const value = document.createElement("strong");
    value.textContent = model[key] === null ? "—" : format.format(model[key]);
    const reference = document.createElement("small");
    reference.textContent = "Бейзлайн " + (baseline[key] === null ? "—" : format.format(baseline[key]));
    card.append(caption, value, reference);
    return card;
  }));
}

function renderReport(report) {
  const element = byId("report");
  if (report.source !== "m5") {
    element.textContent = "Синтетический набор проверяет интерфейс. Сгенерируйте снимок M5, чтобы увидеть измерения.";
    return;
  }
  const message = document.createElement("p");
  message.textContent = report.series + " пар товар–магазин · " + report.test.length + " окна · WAPE ниже — лучше.";
  const table = document.createElement("table");
  const header = table.createTHead().insertRow();
  for (const label of ["Окно", "WAPE · бейзлайн", "WAPE · LightGBM", "Смещение · LightGBM"]) {
    const heading = document.createElement("th");
    heading.scope = "col";
    heading.textContent = label;
    header.append(heading);
  }
  const tbody = table.createTBody();
  const add = (label, scores) => {
    const row = tbody.insertRow();
    [label, scores[report.baseline].wape, scores.lightgbm.wape, scores.lightgbm.bias].forEach((v) => {
      row.insertCell().textContent = typeof v === "number" ? percent.format(v) : v;
    });
  };
  for (const fold of report.test) add(fold.start + " — " + fold.end, fold.metrics);
  add("Все окна", report.totals);
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  wrap.append(table);
  element.replaceChildren(message, wrap);
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
    byId("source").textContent = catalog.source === "m5" ? "M5 · реальные продажи" : "Синтетический пример";
    for (const item of catalog.series) {
      form.elements.series.add(new Option(item.item_id + " · " + item.store_id, item.id));
    }
    catalog.folds.forEach((fold, index) => {
      form.elements.window.add(new Option(fold.start + " — " + fold.end, index));
    });
    renderReport(report);
    await run();
  } catch (error) {
    status.textContent = "Не удалось загрузить данные. " + error.message;
  }
}
init();

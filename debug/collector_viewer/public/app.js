const state = {
  lastPayload: null,
};

const palette = [
  "#0f766e",
  "#b45309",
  "#2563eb",
  "#be123c",
  "#6d28d9",
  "#15803d",
  "#c2410c",
  "#0369a1",
];

const timeline = document.querySelector("#timeline");
const emptyState = document.querySelector("#empty-state");
const template = document.querySelector("#event-template");
const legend = document.querySelector("#legend");
const refreshButton = document.querySelector("#refresh");
const eventCount = document.querySelector("#event-count");
const collectorCount = document.querySelector("#collector-count");
const parseErrors = document.querySelector("#parse-errors");
const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function pad(value) {
  return String(value).padStart(2, "0");
}

function formatDate(ms) {
  if (!ms) {
    return "Unknown time";
  }
  const date = new Date(ms);
  return `${date.getDate()} ${monthNames[date.getMonth()]} ${date.getFullYear()}, ${formatShortTime(ms)}`;
}

function formatMarker(ms) {
  if (!ms) {
    return "Unknown time";
  }
  const date = new Date(ms);
  return `${date.getDate()} ${monthNames[date.getMonth()]} ${date.getFullYear()} · ${pad(date.getHours())}:00`;
}

function formatShortTime(ms) {
  if (!ms) {
    return "";
  }
  const date = new Date(ms);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function truncate(value, length = 120) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= length) {
    return text;
  }
  return `${text.slice(0, length).trim()}...`;
}

function queryString() {
  const params = new URLSearchParams();
  params.set("limit", "1000");
  return params.toString();
}

async function loadEvents() {
  refreshButton.disabled = true;
  refreshButton.textContent = "Loading";

  try {
    const response = await fetch(`/api/events?${queryString()}`);
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    const payload = await response.json();
    state.lastPayload = payload;
    render(payload);
  } catch (error) {
    timeline.replaceChildren();
    emptyState.hidden = false;
    emptyState.querySelector("h2").textContent = "Could not load collected data";
    emptyState.querySelector("p").textContent = error.message;
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "Refresh";
  }
}

function render(payload) {
  eventCount.textContent = new Intl.NumberFormat().format(payload.totalEvents || 0);
  collectorCount.textContent = new Intl.NumberFormat().format(payload.collectors?.length || 0);
  parseErrors.textContent = payload.errors?.length
    ? `${payload.errors.length} JSONL parse issue${payload.errors.length === 1 ? "" : "s"}`
    : "";

  const colorMap = createColorMap(payload.collectors || []);
  renderLegend(payload.collectors || [], colorMap);

  const items = renderTimeline(payload.events || [], colorMap);
  timeline.replaceChildren(...items);
  emptyState.hidden = items.length > 0;
  if (items.length === 0) {
    emptyState.querySelector("h2").textContent = "No collected data matched";
    emptyState.querySelector("p").textContent =
      payload.totalEvents > 0
        ? "Refresh after more collected data arrives."
        : "Start a collector or point the server at a folder that contains JSONL events.";
  }
}

function createColorMap(collectors) {
  const colorMap = new Map();
  collectors.forEach((collector, index) => {
    colorMap.set(collector, palette[index % palette.length]);
  });
  return colorMap;
}

function renderLegend(collectors, colorMap) {
  const items = collectors.map((collector) => {
    const item = document.createElement("span");
    item.className = "legend-item";
    item.style.setProperty("--event-color", colorMap.get(collector));

    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";

    const label = document.createElement("span");
    label.textContent = collector;

    item.append(swatch, label);
    return item;
  });
  legend.replaceChildren(...items);
}

function markerKey(ms) {
  if (!ms) {
    return "unknown";
  }
  const date = new Date(ms);
  date.setMinutes(0, 0, 0);
  return String(date.getTime());
}

function renderTimeline(events, colorMap) {
  const nodes = [];
  let currentMarker = "";

  for (const event of events) {
    const nextMarker = markerKey(event.observedAt);
    if (nextMarker !== currentMarker) {
      currentMarker = nextMarker;
      nodes.push(renderTimeMarker(event.observedAt));
    }
    nodes.push(renderEvent(event, colorMap));
  }

  return nodes;
}

function renderTimeMarker(ms) {
  const item = document.createElement("li");
  item.className = "time-marker";

  const line = document.createElement("span");
  const label = document.createElement("time");
  label.textContent = formatMarker(ms);
  if (ms) {
    label.dateTime = new Date(ms).toISOString();
  }

  item.append(line, label);
  return item;
}

function renderEvent(event, colorMap) {
  const node = template.content.firstElementChild.cloneNode(true);
  const title = node.querySelector("h2");
  const time = node.querySelector("time");
  const collector = node.querySelector(".collector-pill");
  const text = node.querySelector(".event-text");
  const kind = node.querySelector(".event-kind");
  const color = colorMap.get(event.collectorId) || palette[0];

  title.textContent = event.title || "Untitled event";
  time.textContent = formatShortTime(event.observedAt) || formatDate(event.observedAt);
  time.dateTime = event.observedAt ? new Date(event.observedAt).toISOString() : "";
  collector.textContent = event.collectorId;
  text.textContent = truncate(event.text);
  kind.textContent = [event.sourceApp, event.subjectKind].filter(Boolean).join(" / ");
  node.style.setProperty("--event-color", color);
  return node;
}

refreshButton.addEventListener("click", loadEvents);

loadEvents();

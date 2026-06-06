const state = {
  debounceTimer: 0,
  lastPayload: null,
};

const timeline = document.querySelector("#timeline");
const emptyState = document.querySelector("#empty-state");
const template = document.querySelector("#event-template");
const searchInput = document.querySelector("#search");
const collectorFilter = document.querySelector("#collector-filter");
const sourceFilter = document.querySelector("#source-filter");
const limitInput = document.querySelector("#limit");
const refreshButton = document.querySelector("#refresh");
const eventCount = document.querySelector("#event-count");
const collectorCount = document.querySelector("#collector-count");
const dataRoot = document.querySelector("#data-root");
const parseErrors = document.querySelector("#parse-errors");

function formatDate(ms) {
  if (!ms) {
    return "Unknown time";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(ms));
}

function formatRelative(ms) {
  if (!ms) {
    return "";
  }
  const deltaSeconds = Math.round((Date.now() - ms) / 1000);
  const abs = Math.abs(deltaSeconds);
  const units = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
    ["second", 1],
  ];
  const [unit, seconds] = units.find(([, size]) => abs >= size) || units.at(-1);
  const value = Math.round(deltaSeconds / seconds) * -1;
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(value, unit);
}

function truncate(value, length = 180) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= length) {
    return text;
  }
  return `${text.slice(0, length).trim()}...`;
}

function setOptions(select, values, placeholder) {
  const selected = select.value;
  select.replaceChildren(new Option(placeholder, ""));
  for (const value of values) {
    select.append(new Option(value, value));
  }
  select.value = values.includes(selected) ? selected : "";
}

function queryString() {
  const params = new URLSearchParams();
  if (searchInput.value.trim()) {
    params.set("q", searchInput.value.trim());
  }
  if (collectorFilter.value) {
    params.set("collector", collectorFilter.value);
  }
  if (sourceFilter.value) {
    params.set("source", sourceFilter.value);
  }
  params.set("limit", String(limitInput.value || 500));
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
  dataRoot.textContent = payload.dataRoot ? `Reading ${payload.dataRoot}` : "";
  parseErrors.textContent = payload.errors?.length
    ? `${payload.errors.length} JSONL parse issue${payload.errors.length === 1 ? "" : "s"}`
    : "";

  setOptions(collectorFilter, payload.collectors || [], "All collectors");
  setOptions(sourceFilter, payload.sources || [], "All sources");

  const cards = (payload.events || []).map(renderEvent);
  timeline.replaceChildren(...cards);
  emptyState.hidden = cards.length > 0;
  if (cards.length === 0) {
    emptyState.querySelector("h2").textContent = "No collected data matched";
    emptyState.querySelector("p").textContent =
      payload.totalEvents > 0
        ? "Try a broader search, collector, source, or limit."
        : "Start a collector or point the server at a folder that contains JSONL events.";
  }
}

function renderEvent(event) {
  const node = template.content.firstElementChild.cloneNode(true);
  const title = node.querySelector("h2");
  const time = node.querySelector("time");
  const collector = node.querySelector(".collector-pill");
  const text = node.querySelector(".event-text");
  const meta = node.querySelector(".meta-row");
  const detailGrid = node.querySelector(".detail-grid");
  const raw = node.querySelector("pre");

  const relative = formatRelative(event.observedAt);
  title.textContent = event.title || "Untitled event";
  time.textContent = relative ? `${formatDate(event.observedAt)} · ${relative}` : formatDate(event.observedAt);
  time.dateTime = event.observedAt ? new Date(event.observedAt).toISOString() : "";
  collector.textContent = event.collectorId;
  text.textContent = truncate(event.text, 520);

  const chips = [
    event.sourceApp && `app: ${event.sourceApp}`,
    event.sourceType && `source: ${event.sourceType}`,
    event.subjectKind && `kind: ${event.subjectKind}`,
    event.anchorType && `anchor: ${event.anchorType}`,
    event.artifactCount ? `artifacts: ${event.artifactCount}` : "",
    event.filePath,
  ].filter(Boolean);
  meta.replaceChildren(...chips.map((chip) => renderChip(chip)));

  detailGrid.replaceChildren(
    renderDetail("Event ID", event.id),
    renderDetail("Collector", event.collectorId),
    renderDetail("Source URI", event.sourceUri || "None"),
    renderDetail("File", `${event.filePath}:${event.lineNumber}`),
    renderDetail("Window", event.contextWindow || "None"),
    renderDetail("Provenance", event.provenanceType || "None"),
  );
  raw.textContent = JSON.stringify(event.raw, null, 2);
  return node;
}

function renderChip(text) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = text;
  return chip;
}

function renderDetail(label, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "detail";

  const key = document.createElement("b");
  key.textContent = label;

  const content = document.createElement("span");
  content.textContent = value || "None";

  wrapper.append(key, content);
  return wrapper;
}

function scheduleLoad() {
  clearTimeout(state.debounceTimer);
  state.debounceTimer = window.setTimeout(loadEvents, 180);
}

searchInput.addEventListener("input", scheduleLoad);
collectorFilter.addEventListener("change", loadEvents);
sourceFilter.addEventListener("change", loadEvents);
limitInput.addEventListener("change", loadEvents);
refreshButton.addEventListener("click", loadEvents);

loadEvents();

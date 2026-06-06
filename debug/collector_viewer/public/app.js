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
const timelineScroll = document.querySelector(".timeline-scroll");
const emptyState = document.querySelector("#empty-state");
const legend = document.querySelector("#legend");
const refreshButton = document.querySelector("#refresh");
const scrollLeftButton = document.querySelector("#scroll-left");
const scrollRightButton = document.querySelector("#scroll-right");
const eventCount = document.querySelector("#event-count");
const collectorCount = document.querySelector("#collector-count");
const parseErrors = document.querySelector("#parse-errors");
const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const eventUnitPx = 22;
const groupMinWidthPx = 132;

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
  return `${date.getDate()} ${monthNames[date.getMonth()]} · ${pad(date.getHours())}:00`;
}

function formatShortTime(ms) {
  if (!ms) {
    return "";
  }
  const date = new Date(ms);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
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

  const { nodes: items, firstRun } = renderTimeline(payload.events || [], colorMap);
  timeline.replaceChildren(...items);
  emptyState.hidden = Boolean(firstRun);
  requestAnimationFrame(updateScrollArrows);
  if (!firstRun) {
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
  const chronologicalEvents = [...events].sort((a, b) => a.observedAt - b.observedAt);
  const runs = createTimelineRuns(chronologicalEvents);

  for (const run of runs) {
    const nextMarker = markerKey(run.startAt);
    if (nextMarker !== currentMarker) {
      currentMarker = nextMarker;
      nodes.push(renderTimeMarker(run.startAt));
    }
    nodes.push(renderRun(run, colorMap));
  }

  return { nodes, firstRun: runs[0] || null };
}

function createTimelineRuns(events) {
  const runs = [];

  for (const event of events) {
    const lastRun = runs.at(-1);
    if (lastRun && canMerge(lastRun, event)) {
      lastRun.events.push(event);
      lastRun.endAt = event.observedAt || lastRun.endAt;
      continue;
    }

    runs.push({
      collectorId: event.collectorId,
      startAt: event.observedAt,
      endAt: event.observedAt,
      events: [event],
    });
  }

  return runs;
}

function canMerge(run, event) {
  return run.collectorId === event.collectorId;
}

function renderTimeMarker(ms) {
  const item = document.createElement("li");
  item.className = "time-marker";

  const label = document.createElement("time");
  label.textContent = formatMarker(ms);
  if (ms) {
    label.dateTime = new Date(ms).toISOString();
  }

  item.append(label);
  return item;
}

function renderRun(run, colorMap) {
  const node = document.createElement("li");
  const segment = document.createElement("div");
  const color = colorMap.get(run.collectorId) || palette[0];
  const count = run.events.length;
  const segmentWidth = eventUnitPx * count;

  node.className = "timeline-group";
  node.style.inlineSize = `${Math.max(segmentWidth, groupMinWidthPx)}px`;
  node.setAttribute("aria-label", runLabel(run));

  segment.className = "timeline-segment";
  segment.style.setProperty("--event-color", color);
  segment.style.inlineSize = `${segmentWidth}px`;
  segment.title = runLabel(run);

  if (count > 1) {
    const badge = document.createElement("span");
    badge.textContent = new Intl.NumberFormat().format(count);
    segment.append(badge);
  }

  const meta = document.createElement("div");
  meta.className = "segment-meta";

  const source = document.createElement("span");
  source.textContent = formatValueList(uniqueValues(run.events, "sourceType"));

  const app = document.createElement("span");
  app.textContent = formatValueList(uniqueValues(run.events, "sourceApp"));

  const kind = document.createElement("small");
  kind.textContent = [formatValueList(uniqueValues(run.events, "subjectKind")), formatRunTime(run)]
    .filter((value) => value && value !== "-")
    .join(" / ");

  meta.append(source, app, kind);
  node.append(segment, meta);
  return node;
}

function uniqueValues(events, key) {
  return [...new Set(events.map((event) => event[key]).filter(Boolean))];
}

function formatValueList(values) {
  if (values.length === 0) {
    return "-";
  }
  if (values.length <= 2) {
    return values.join(", ");
  }
  return `${values.slice(0, 2).join(", ")} +${values.length - 2}`;
}

function formatRunTime(run) {
  if (run.startAt === run.endAt) {
    return formatShortTime(run.startAt) || "Unknown";
  }
  return `${formatShortTime(run.startAt)}-${formatShortTime(run.endAt)}`;
}

function runLabel(run) {
  const count = run.events.length;
  const range =
    run.startAt === run.endAt
      ? formatDate(run.startAt)
      : `${formatDate(run.startAt)} to ${formatDate(run.endAt)}`;
  return `${count} ${count === 1 ? "event" : "events"} from ${run.collectorId}, ${range}`;
}

function scrollTimeline(direction) {
  if (!timelineScroll) {
    return;
  }

  timelineScroll.scrollBy({
    left: direction * Math.max(timelineScroll.clientWidth * 0.8, 240),
    behavior: "smooth",
  });
}

function updateScrollArrows() {
  if (!timelineScroll || !scrollLeftButton || !scrollRightButton) {
    return;
  }

  const maxScrollLeft = timelineScroll.scrollWidth - timelineScroll.clientWidth;
  const canScroll = maxScrollLeft > 1;
  const canScrollLeft = canScroll && timelineScroll.scrollLeft > 1;
  const canScrollRight = canScroll && timelineScroll.scrollLeft < maxScrollLeft - 1;

  scrollLeftButton.hidden = !canScrollLeft;
  scrollRightButton.hidden = !canScrollRight;
}

refreshButton.addEventListener("click", loadEvents);
scrollLeftButton?.addEventListener("click", () => scrollTimeline(-1));
scrollRightButton?.addEventListener("click", () => scrollTimeline(1));
timelineScroll?.addEventListener("scroll", updateScrollArrows, { passive: true });
window.addEventListener("resize", updateScrollArrows);

loadEvents();

const DEFAULT_COLLECTOR_URL = "http://127.0.0.1:47321/event";
const statusElement = document.getElementById("status");
const collectorUrlInput = document.getElementById("collector-url");
const saveButton = document.getElementById("save");

function renderStatus(status) {
  if (!status) {
    statusElement.textContent = "No events sent yet.";
    return;
  }
  if (status.ok === true) {
    statusElement.textContent = `Connected. Last event: ${status.lastEventName || "unknown"}.`;
    return;
  }
  if (status.ok === false) {
    statusElement.textContent = `Collector unavailable: ${status.lastError || "unknown error"}`;
    return;
  }
  statusElement.textContent = "Waiting for page activity.";
}

async function loadState() {
  const state = await chrome.storage.local.get({
    collectorUrl: DEFAULT_COLLECTOR_URL,
    radar_status: null
  });
  collectorUrlInput.value = state.collectorUrl;
  renderStatus(state.radar_status);
}

saveButton.addEventListener("click", async () => {
  await chrome.storage.local.set({
    collectorUrl: collectorUrlInput.value || DEFAULT_COLLECTOR_URL
  });
  await loadState();
});

loadState();

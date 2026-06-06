const DEFAULT_COLLECTOR_URL = "http://127.0.0.1:47321/event";
const STATUS_KEY = "radar_status";

async function getCollectorUrl() {
  const config = await chrome.storage.local.get({ collectorUrl: DEFAULT_COLLECTOR_URL });
  return config.collectorUrl;
}

async function setStatus(status) {
  await chrome.storage.local.set({
    [STATUS_KEY]: {
      ...status,
      updatedAt: Date.now()
    }
  });
}

async function forwardEvent(event) {
  const collectorUrl = await getCollectorUrl();
  const response = await fetch(collectorUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(event)
  });

  if (!response.ok) {
    throw new Error(`collector returned ${response.status}`);
  }

  await setStatus({
    ok: true,
    collectorUrl,
    lastEventName: event.event_name || event.eventName || "unknown",
    lastError: null
  });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "radar:event") {
    return false;
  }

  const payload = {
    ...message.payload,
    tab_id: sender.tab && typeof sender.tab.id === "number" ? sender.tab.id : undefined,
    frame_id: sender.frameId
  };

  forwardEvent(payload)
    .then(() => sendResponse({ ok: true }))
    .catch(async (error) => {
      const collectorUrl = await getCollectorUrl();
      await setStatus({
        ok: false,
        collectorUrl,
        lastEventName: payload.event_name || "unknown",
        lastError: String(error && error.message ? error.message : error)
      });
      sendResponse({
        ok: false,
        error: String(error && error.message ? error.message : error)
      });
    });

  return true;
});

chrome.runtime.onInstalled.addListener(async () => {
  const config = await chrome.storage.local.get({ collectorUrl: DEFAULT_COLLECTOR_URL });
  await chrome.storage.local.set({ collectorUrl: config.collectorUrl });
  await setStatus({
    ok: null,
    collectorUrl: config.collectorUrl,
    lastEventName: null,
    lastError: null
  });
});

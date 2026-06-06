const DEFAULT_COLLECTOR_URL = "http://127.0.0.1:47321/event";
const DEFAULT_BRIDGE_URL = "ws://127.0.0.1:9223/radar-chrome-bridge-ws";
const STATUS_KEY = "radar_status";
const BRIDGE_RECONNECT_MS = 3000;

let bridgeSocket = null;
let bridgeReconnectTimer = null;

async function getCollectorUrl() {
  const config = await chrome.storage.local.get({ collectorUrl: DEFAULT_COLLECTOR_URL });
  return config.collectorUrl;
}

async function getBridgeUrl() {
  const config = await chrome.storage.local.get({ bridgeUrl: DEFAULT_BRIDGE_URL });
  return config.bridgeUrl;
}

async function setStatus(status) {
  await chrome.storage.local.set({
    [STATUS_KEY]: {
      ...status,
      updatedAt: Date.now()
    }
  });
}

function setBridgeStatus(status) {
  chrome.storage.local.set({
    radar_bridge_status: {
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
  const config = await chrome.storage.local.get({
    collectorUrl: DEFAULT_COLLECTOR_URL,
    bridgeUrl: DEFAULT_BRIDGE_URL
  });
  await chrome.storage.local.set({
    collectorUrl: config.collectorUrl,
    bridgeUrl: config.bridgeUrl
  });
  await setStatus({
    ok: null,
    collectorUrl: config.collectorUrl,
    lastEventName: null,
    lastError: null
  });
});

chrome.runtime.onStartup.addListener(() => {
  connectBridge();
});

connectBridge();

async function connectBridge() {
  if (bridgeSocket && (bridgeSocket.readyState === WebSocket.OPEN || bridgeSocket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const bridgeUrl = await getBridgeUrl();
  try {
    bridgeSocket = new WebSocket(bridgeUrl);
  } catch (error) {
    setBridgeStatus({ connected: false, bridgeUrl, lastError: String(error && error.message ? error.message : error) });
    scheduleBridgeReconnect();
    return;
  }

  bridgeSocket.onopen = () => {
    setBridgeStatus({ connected: true, bridgeUrl, lastError: null });
  };
  bridgeSocket.onmessage = (event) => {
    handleBridgeMessage(event.data);
  };
  bridgeSocket.onerror = () => {
    setBridgeStatus({ connected: false, bridgeUrl, lastError: "websocket error" });
  };
  bridgeSocket.onclose = () => {
    setBridgeStatus({ connected: false, bridgeUrl, lastError: "websocket closed" });
    bridgeSocket = null;
    scheduleBridgeReconnect();
  };
}

function scheduleBridgeReconnect() {
  if (bridgeReconnectTimer) {
    return;
  }
  bridgeReconnectTimer = setTimeout(() => {
    bridgeReconnectTimer = null;
    connectBridge();
  }, BRIDGE_RECONNECT_MS);
}

async function handleBridgeMessage(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch (_error) {
    return;
  }

  if (!message || typeof message.id === "undefined" || !message.method) {
    return;
  }

  try {
    let result;
    if (message.method === "ping") {
      result = { ok: true };
    } else if (message.method === "page_content") {
      result = await getPageContent(firstParam(message.params));
    } else if (message.method === "page_action") {
      result = await performPageActions(firstParam(message.params));
    } else {
      throw new Error(`Unknown Radar bridge method: ${message.method}`);
    }
    sendBridgeMessage({
      jsonrpc: "2.0",
      id: message.id,
      result
    });
  } catch (error) {
    sendBridgeMessage({
      jsonrpc: "2.0",
      id: message.id,
      error: {
        code: -32000,
        message: String(error && error.message ? error.message : error)
      }
    });
  }
}

function firstParam(params) {
  if (Array.isArray(params)) {
    return params[0] || {};
  }
  return params || {};
}

function sendBridgeMessage(message) {
  if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
    bridgeSocket.send(JSON.stringify(message));
  }
}

async function getActiveTab(tabId) {
  if (tabId && tabId > 0) {
    return chrome.tabs.get(tabId);
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs[0] || !tabs[0].id) {
    throw new Error("No active tab found");
  }
  return tabs[0];
}

function canAccessTab(tab) {
  return Boolean(tab && tab.url && !/^(chrome|chrome-extension|edge|devtools|about):/.test(tab.url));
}

async function getPageContent(request) {
  const tab = await getActiveTab(request.tab_id);
  if (!canAccessTab(tab)) {
    return { error: "Cannot access this tab" };
  }

  const injection = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: collectPageContent,
    args: [request || {}]
  });
  const result = injection && injection[0] ? injection[0].result || {} : {};
  return {
    url: tab.url,
    tab_id: tab.id,
    ...result
  };
}

function collectPageContent(request) {
  const result = {};

  if (request.include_html) {
    result.html = document.documentElement.outerHTML;
  }
  if (request.include_text) {
    result.text = document.body ? document.body.innerText : "";
  }
  if (request.include_selection) {
    const selection = window.getSelection();
    result.selection = selection ? selection.toString() : "";
  }
  if (request.include_metadata) {
    const getMeta = (name) => {
      const element = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
      return element ? element.getAttribute("content") || undefined : undefined;
    };
    result.metadata = {
      title: document.title,
      description: getMeta("description"),
      canonical: document.querySelector('link[rel="canonical"]')?.href,
      og_title: getMeta("og:title"),
      og_description: getMeta("og:description"),
      og_image: getMeta("og:image"),
      og_type: getMeta("og:type"),
      og_url: getMeta("og:url")
    };
  }
  if (Array.isArray(request.selectors) && request.selectors.length > 0) {
    result.selectors = {};
    for (const query of request.selectors) {
      try {
        const read = (element) => {
          if (!element) {
            return null;
          }
          if (query.attribute) {
            return element.getAttribute(query.attribute);
          }
          if (query.property) {
            return element[query.property];
          }
          if (query.inner_html) {
            return element.innerHTML;
          }
          if (query.outer_html) {
            return element.outerHTML;
          }
          return element.textContent;
        };
        if (query.all) {
          result.selectors[query.name] = Array.from(document.querySelectorAll(query.selector)).map(read);
        } else {
          result.selectors[query.name] = read(document.querySelector(query.selector));
        }
      } catch (error) {
        result.selectors[query.name] = { error: String(error && error.message ? error.message : error) };
      }
    }
  }
  return result;
}

async function performPageActions(request) {
  const tab = await getActiveTab(request.tab_id);
  if (!canAccessTab(tab)) {
    return { success: false, results: [], error: "Cannot access this tab" };
  }

  if (!urlPatternsMatch(request, tab.url)) {
    return {
      success: false,
      results: [],
      error: `URL does not match pattern: ${urlPatternLabel(request)}`
    };
  }

  const actions = Array.isArray(request.actions) ? request.actions : [];
  const results = [];
  for (const action of actions) {
    try {
      const injection = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: executePageAction,
        args: [action]
      });
      const result = injection && injection[0] ? injection[0].result : null;
      results.push(result || { type: action.type, success: false, error: "No result", element_found: false });
      if (request.stop_on_error && (!result || !result.success)) {
        break;
      }
      if (action.wait_ms && action.wait_ms > 0) {
        await new Promise((resolve) => setTimeout(resolve, action.wait_ms));
      }
    } catch (error) {
      results.push({
        name: action.name,
        type: action.type,
        success: false,
        error: String(error && error.message ? error.message : error),
        element_found: false
      });
      if (request.stop_on_error) {
        break;
      }
    }
  }

  return {
    success: results.every((item) => item.success),
    url: tab.url,
    tab_id: tab.id,
    results
  };
}

async function executePageAction(action) {
  async function findActionElement() {
    const timeoutMs = Number(action.wait_for_selector_ms || action.waitForSelectorMs || 0);
    const deadline = Date.now() + Math.max(0, timeoutMs);

    while (true) {
      const element = queryActionElement();
      if (element) {
        return element;
      }
      if (Date.now() >= deadline) {
        return null;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  function queryActionElement() {
    const elements = Array.from(document.querySelectorAll(action.selector));
    if (!action.visible) {
      return elements[0] || null;
    }
    return elements.find((element) => isVisibleElement(element)) || null;
  }

  function isVisibleElement(element) {
    if (!element || !(element instanceof Element)) {
      return false;
    }
    const style = window.getComputedStyle(element);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function setNativeInputValue(element, value) {
    const prototype =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor && typeof descriptor.set === "function") {
      descriptor.set.call(element, value);
      return;
    }
    element.value = value;
  }

  function dispatchInputEvents(element, value) {
    element.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        composed: true,
        data: value,
        inputType: "insertText"
      })
    );
    element.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  const result = {
    name: action.name,
    type: action.type,
    success: false,
    element_found: false
  };

  try {
    if (action.type === "wait") {
      result.success = true;
      return result;
    }
    if (action.type === "script") {
      result.error = "script page actions are not supported by the Radar extension bridge";
      return result;
    }
    if (!action.selector) {
      result.error = "Selector is required";
      return result;
    }

    const element = await findActionElement();
    if (!element) {
      result.error = `Element not found: ${action.selector}`;
      return result;
    }
    result.element_found = true;
    result.element_tag = element.tagName;

    if (action.type === "focus") {
      element.focus();
      result.success = true;
      result.value = "value" in element ? element.value : undefined;
      return result;
    }
    if (action.type === "fill") {
      if ("value" in element) {
        if (action.clear !== false) {
          setNativeInputValue(element, "");
        }
        setNativeInputValue(element, action.value || "");
      } else if (element.isContentEditable) {
        element.textContent = action.value || "";
      } else {
        result.error = "Element is not fillable";
        return result;
      }
      if (action.trigger_input !== false) {
        dispatchInputEvents(element, action.value || "");
      }
      result.success = true;
      result.value = "value" in element ? element.value : undefined;
      return result;
    }
    if (action.type === "click") {
      element.click();
      result.success = true;
      return result;
    }

    result.error = `Unsupported action type: ${action.type}`;
    return result;
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
    return result;
  }
}

function urlPatternsMatch(request, url) {
  const patterns = Array.isArray(request.url_patterns) ? [...request.url_patterns] : [];
  if (request.url_pattern) {
    patterns.push(request.url_pattern);
  }
  if (patterns.length === 0) {
    return true;
  }
  return patterns.some((pattern) => globMatches(pattern, url));
}

function urlPatternLabel(request) {
  const patterns = Array.isArray(request.url_patterns) ? [...request.url_patterns] : [];
  if (request.url_pattern) {
    patterns.push(request.url_pattern);
  }
  return patterns.join(", ");
}

function globMatches(pattern, value) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${escaped}$`).test(value || "");
}

const INPUT_DEBOUNCE_MS = 800;
const MAX_TEXT_LENGTH = 2000;

const lastInputSentAt = new WeakMap();
const pendingInputs = new WeakMap();

function nowMs() {
  return Date.now();
}

function textOf(value) {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  return trimmed.slice(0, MAX_TEXT_LENGTH);
}

function visibleText(element) {
  if (!element) {
    return undefined;
  }
  const aria = textOf(element.getAttribute("aria-label"));
  if (aria) {
    return aria;
  }
  const title = textOf(element.getAttribute("title"));
  if (title) {
    return title;
  }
  const placeholder = textOf(element.getAttribute("placeholder"));
  if (placeholder) {
    return placeholder;
  }
  return textOf(element.innerText || element.textContent || element.value);
}

function cssPath(element) {
  if (!element || element.nodeType !== Node.ELEMENT_NODE) {
    return undefined;
  }

  const parts = [];
  let current = element;
  while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
    let selector = current.localName;
    if (current.id) {
      selector += `#${CSS.escape(current.id)}`;
      parts.unshift(selector);
      break;
    }

    const testId = current.getAttribute("data-testid") || current.getAttribute("data-test-id");
    if (testId) {
      selector += `[data-testid="${CSS.escape(testId)}"]`;
      parts.unshift(selector);
      current = current.parentElement;
      continue;
    }

    if (current.classList && current.classList.length) {
      selector += `.${Array.from(current.classList).slice(0, 2).map((name) => CSS.escape(name)).join(".")}`;
    }

    parts.unshift(selector);
    current = current.parentElement;
  }

  return parts.join(" > ");
}

function elementPayload(element) {
  if (!element || element.nodeType !== Node.ELEMENT_NODE) {
    return undefined;
  }

  return dropUndefined({
    tag: element.localName,
    role: element.getAttribute("role") || undefined,
    type: element.getAttribute("type") || undefined,
    name: element.getAttribute("name") || undefined,
    id: element.id || undefined,
    aria_label: element.getAttribute("aria-label") || undefined,
    title: element.getAttribute("title") || undefined,
    placeholder: element.getAttribute("placeholder") || undefined,
    text: visibleText(element),
    css_path: cssPath(element),
    autocomplete: element.getAttribute("autocomplete") || undefined
  });
}

function focusedElementPayload() {
  const active = document.activeElement;
  if (!active || active === document.body) {
    return undefined;
  }
  return elementPayload(active);
}

function valueForElement(element) {
  if (!element) {
    return undefined;
  }
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    return element.value ? element.value.slice(0, MAX_TEXT_LENGTH) : undefined;
  }
  if (element.isContentEditable) {
    return textOf(element.innerText || element.textContent);
  }
  return undefined;
}

function basePayload(eventName, target) {
  return dropUndefined({
    event_name: eventName,
    observed_at: nowMs(),
    occurred_at: new Date().toISOString(),
    document_title: document.title || undefined,
    document_url: location.href,
    window_title: document.title || undefined,
    element: elementPayload(target),
    focused_element: focusedElementPayload(),
    selected_text: textOf(String(window.getSelection ? window.getSelection() : "")),
    page_visibility: document.visibilityState
  });
}

function dropUndefined(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}

function sendEvent(eventName, target, extra = {}) {
  const payload = {
    ...basePayload(eventName, target),
    ...extra
  };

  chrome.runtime.sendMessage({
    type: "radar:event",
    payload
  }, () => {
    // The service worker records delivery status. Content scripts ignore errors
    // so page interaction is never blocked by collection.
    void chrome.runtime.lastError;
  });
}

document.addEventListener("click", (event) => {
  sendEvent("click", event.target);
}, true);

document.addEventListener("submit", (event) => {
  sendEvent("submit", event.target);
}, true);

document.addEventListener("copy", (event) => {
  sendEvent("copy", event.target);
}, true);

document.addEventListener("paste", (event) => {
  sendEvent("paste", event.target);
}, true);

document.addEventListener("selectionchange", () => {
  const selected = textOf(String(window.getSelection ? window.getSelection() : ""));
  if (selected) {
    sendEvent("selection", document.activeElement, { selected_text: selected });
  }
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (!target || target.nodeType !== Node.ELEMENT_NODE) {
    return;
  }

  const previous = pendingInputs.get(target);
  if (previous) {
    clearTimeout(previous);
  }

  const timeout = setTimeout(() => {
    const lastSent = lastInputSentAt.get(target) || 0;
    const now = nowMs();
    if (now - lastSent < INPUT_DEBOUNCE_MS) {
      return;
    }
    lastInputSentAt.set(target, now);
    sendEvent("input", target, {
      text: valueForElement(target)
    });
  }, INPUT_DEBOUNCE_MS);

  pendingInputs.set(target, timeout);
}, true);

sendEvent("navigation", document.documentElement);

"""Build Chrome observations in the collector spec format."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

CHROME_COLLECTOR_ID = "chrome.extension"
CHROME_APP_NAME = "Google Chrome"
CHROME_BUNDLE_ID = "com.google.Chrome"
REDACTED = "[REDACTED]"

SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "passcode",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "creditcard",
    "credit_card",
    "cardnumber",
    "cvv",
    "otp",
    "one-time-code",
)

USER_ACTION_BY_EVENT = {
    "click": "clicked",
    "mouse_click": "clicked",
    "input": "typed",
    "type": "typed",
    "keydown": "typed",
    "navigation": "opened",
    "url_changed": "opened",
    "focus": "focused",
    "copy": "copied",
    "paste": "pasted",
    "selection": "selected",
    "select": "selected",
    "submit": "submitted",
    "message_sent": "sent",
    "message_received": "received",
}

ANCHOR_NAME_BY_EVENT = {
    "click": "mouse_click",
    "mouse_click": "mouse_click",
    "input": "text_input",
    "type": "text_input",
    "keydown": "keyboard_input",
    "navigation": "url_changed",
    "url_changed": "url_changed",
    "focus": "element_focus",
    "copy": "clipboard_copy",
    "paste": "clipboard_paste",
    "selection": "text_selection",
    "select": "text_selection",
    "submit": "form_submit",
    "message_sent": "message_sent",
    "message_received": "message_received",
}


def epoch_ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_from_epoch_ms(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def coerce_epoch_ms(value: Any | None) -> int:
    if value is None:
        return epoch_ms_now()
    if isinstance(value, bool):
        return epoch_ms_now()
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric < 10_000_000_000:
            numeric *= 1000
        return int(numeric)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return coerce_epoch_ms(int(stripped))
        try:
            normalized = stripped.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return epoch_ms_now()
    return epoch_ms_now()


def coerce_occurred_at(value: Any | None, fallback_epoch_ms: int) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return iso_from_epoch_ms(coerce_epoch_ms(value))
    return iso_from_epoch_ms(fallback_epoch_ms)


def build_observation(
    event: dict[str, Any],
    *,
    collector_id: str = CHROME_COLLECTOR_ID,
) -> dict[str, Any]:
    """Convert a Chrome browser event into the repository observation format."""

    event_name = normalize_event_name(event.get("eventName") or event.get("type"))
    observed_at = coerce_epoch_ms(event.get("observedAt"))
    occurred_at = coerce_occurred_at(event.get("occurredAt"), observed_at)
    document_title = first_text(
        event.get("documentTitle"),
        event.get("title"),
        event.get("windowTitle"),
    )
    document_url = first_text(event.get("documentUrl"), event.get("url"))
    window_title = first_text(event.get("windowTitle"), document_title)
    element = ensure_dict(event.get("element"))
    focused_element = ensure_dict(event.get("focusedElement"))
    sensitive = is_sensitive_event(event, element, focused_element)
    content_text = extract_content_text(event, focused_element, sensitive)
    anchor_target = build_anchor_target(event, element, document_url, window_title)
    artifacts = event.get("artifacts") if isinstance(event.get("artifacts"), list) else []

    observation = {
        "id": str(uuid4()),
        "collectorId": collector_id,
        "source": {
            "type": "browser",
            "app": CHROME_APP_NAME,
            "bundleId": CHROME_BUNDLE_ID,
        },
        "time": {
            "observedAt": observed_at,
        },
        "anchor": {
            "id": str(uuid4()),
            "type": "user_action" if event_name in USER_ACTION_BY_EVENT else "state_change",
            "name": ANCHOR_NAME_BY_EVENT.get(event_name, event_name),
            "occurredAt": occurred_at,
            "target": anchor_target,
        },
        "subject": {
            "kind": "tab",
            "title": document_title,
            "url": document_url,
        },
        "content": build_content(content_text),
        "context": {
            "activeApp": CHROME_APP_NAME,
            "activeWindowTitle": window_title,
            "userAction": USER_ACTION_BY_EVENT.get(event_name, "viewed"),
        },
        "artifacts": artifacts,
        "extraData": {
            "browser": sanitize_browser_extra(event, sensitive),
        },
        "privacy": build_privacy(content_text, sensitive),
        "quality": {
            "confidence": 0.95 if event_name != "snapshot" else 0.75,
            "completeness": "partial",
            "extractionMethod": "browser_extension",
        },
    }
    remove_none(observation)
    return observation


def normalize_event_name(value: Any | None) -> str:
    if not isinstance(value, str) or not value.strip():
        return "snapshot"
    return value.strip().lower().replace("-", "_")


def first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_anchor_target(
    event: dict[str, Any],
    element: dict[str, Any],
    document_url: str | None,
    window_title: str | None,
) -> dict[str, Any]:
    target = {
        "app": CHROME_APP_NAME,
        "bundleId": CHROME_BUNDLE_ID,
        "windowTitle": window_title,
        "url": document_url,
        "elementRole": first_text(element.get("role"), element.get("tag")),
        "elementTitle": first_text(
            element.get("ariaLabel"),
            element.get("text"),
            element.get("title"),
            element.get("placeholder"),
            event.get("elementTitle"),
        ),
    }
    remove_none(target)
    return target


def build_content(text: str | None) -> dict[str, Any]:
    if text is None:
        return {}
    return {"text": text}


def extract_content_text(
    event: dict[str, Any],
    focused_element: dict[str, Any],
    sensitive: bool,
) -> str | None:
    text = first_text(
        event.get("text"),
        event.get("selectedText"),
        focused_element.get("value"),
        focused_element.get("text"),
    )
    if text is None:
        return None
    return REDACTED if sensitive else text


def is_sensitive_event(
    event: dict[str, Any],
    element: dict[str, Any],
    focused_element: dict[str, Any],
) -> bool:
    values: list[str] = []
    for source in (event, element, focused_element):
        for key in (
            "type",
            "inputType",
            "name",
            "id",
            "autocomplete",
            "ariaLabel",
            "placeholder",
            "title",
        ):
            value = source.get(key)
            if isinstance(value, str):
                values.append(value.lower())
    return any(marker in " ".join(values) for marker in SENSITIVE_MARKERS)


def sanitize_browser_extra(event: dict[str, Any], sensitive: bool) -> dict[str, Any]:
    extra = deepcopy(event)
    if sensitive:
        redact_value_fields(extra)
    return json_safe(extra)


def redact_value_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            lowered = key.lower()
            if lowered in {"text", "value", "selectedtext"}:
                value[key] = REDACTED
            else:
                redact_value_fields(child)
    elif isinstance(value, list):
        for child in value:
            redact_value_fields(child)


def build_privacy(text: str | None, sensitive: bool) -> dict[str, Any]:
    has_text = bool(text)
    if sensitive:
        return {
            "sensitivity": "high",
            "containsPII": True,
            "redacted": True,
            "rawContentStored": False,
            "redactionStrategy": "mask",
            "permissionScope": ["chrome.activeTab", "chrome.tabs"],
        }
    return {
        "sensitivity": "medium" if has_text else "low",
        "containsPII": has_text,
        "redacted": False,
        "rawContentStored": has_text,
        "redactionStrategy": "none",
        "permissionScope": ["chrome.activeTab", "chrome.tabs"],
    }


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, child in value.items():
            if isinstance(key, str):
                safe[key] = json_safe(child)
        return safe
    return str(value)


def remove_none(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value.keys()):
            child = value[key]
            if child is None:
                del value[key]
            else:
                remove_none(child)
    elif isinstance(value, list):
        for child in value:
            remove_none(child)

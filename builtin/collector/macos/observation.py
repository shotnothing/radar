"""Observation builder for macOS activity trigger events."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

MACOS_COLLECTOR_ID = "macos.activity"
REDACTED = "[REDACTED]"
MAX_TEXT_LENGTH = 2000

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
    "mouse_click": "clicked",
    "enter_key": "submitted",
}

ANCHOR_NAME_BY_EVENT = {
    "click": "mouse_click",
    "mouse_click": "mouse_click",
    "enter": "enter_key",
    "enter_key": "enter_key",
    "return_key": "enter_key",
}


def epoch_ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_from_epoch_ms(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def coerce_epoch_ms(value: Any | None) -> int:
    if value is None or isinstance(value, bool):
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
    collector_id: str = MACOS_COLLECTOR_ID,
) -> dict[str, Any]:
    """Convert a native macOS trigger event into the collected-data JSON shape."""

    event_name = normalize_event_name(get_first(event, "event_name", "eventName", "name"))
    event_name = ANCHOR_NAME_BY_EVENT.get(event_name, event_name)
    observed_at = coerce_epoch_ms(get_first(event, "observed_at", "observedAt", "timestamp"))
    occurred_at = coerce_occurred_at(
        get_first(event, "occurred_at", "occurredAt"),
        observed_at,
    )
    context = ensure_dict(event.get("context"))
    focused_element = ensure_dict(get_first(context, "focused_element", "focusedElement"))
    sensitive = is_sensitive_context(context, focused_element)
    content_text = extract_content_text(context, focused_element, sensitive)
    document_path = first_text(get_first(context, "document_path", "documentPath"))
    url = first_text(context.get("url"), document_path if is_url(document_path) else None)
    window_title = first_text(get_first(context, "window_title", "windowTitle"))
    app_name = first_text(get_first(context, "app_name", "appName")) or "macOS"
    bundle_id = first_text(get_first(context, "bundle_id", "bundleId"))

    observation = {
        "id": str(uuid4()),
        "collector_id": collector_id,
        "source": {
            "type": "macos",
            "app": app_name,
            "bundle_id": bundle_id,
        },
        "time": {
            "observed_at": observed_at,
        },
        "anchor": {
            "id": str(uuid4()),
            "type": "user_action" if event_name in USER_ACTION_BY_EVENT else "state_change",
            "name": event_name,
            "occurred_at": occurred_at,
            "target": build_anchor_target(context, focused_element, app_name, bundle_id),
        },
        "subject": {
            "kind": "document" if document_path else "window",
            "title": window_title,
            "url": url,
            "document_path": document_path if not is_url(document_path) else None,
        },
        "content": {"text": content_text} if content_text is not None else {},
        "context": {
            "active_app": app_name,
            "active_window_title": window_title,
            "user_action": USER_ACTION_BY_EVENT.get(event_name, "observed"),
        },
        "provenance": build_provenance(context, event_name),
        "extra_data": {
            "macos": sanitize_macos_extra(event, sensitive),
        },
        "privacy": build_privacy(content_text, sensitive),
        "quality": {
            "confidence": 0.85,
            "extraction_method": "macos_event_tap_accessibility",
        },
    }
    remove_none(observation)
    return observation


def normalize_event_name(value: Any | None) -> str:
    if not isinstance(value, str) or not value.strip():
        return "snapshot"
    return to_snake_case(value.strip())


def get_first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_anchor_target(
    context: dict[str, Any],
    focused_element: dict[str, Any],
    app_name: str,
    bundle_id: str | None,
) -> dict[str, Any]:
    target = {
        "app": app_name,
        "bundle_id": bundle_id,
        "window_title": first_text(get_first(context, "window_title", "windowTitle")),
        "url": first_text(context.get("url")),
        "element_role": first_text(get_first(focused_element, "role", "focused_role")),
        "element_title": first_text(
            get_first(focused_element, "title", "focused_title"),
            get_first(focused_element, "description", "focused_description"),
        ),
    }
    remove_none(target)
    return target


def extract_content_text(
    context: dict[str, Any],
    focused_element: dict[str, Any],
    sensitive: bool,
) -> str | None:
    text = first_text(
        get_first(focused_element, "selected_text", "selectedText"),
        get_first(context, "selected_text", "selectedText"),
        get_first(focused_element, "value", "focused_value"),
    )
    if text is None:
        return None
    return REDACTED if sensitive else truncate_text(text)


def is_sensitive_context(
    context: dict[str, Any],
    focused_element: dict[str, Any],
) -> bool:
    values: list[str] = []
    for source in (context, focused_element):
        for key in (
            "role",
            "title",
            "description",
            "label",
            "placeholder",
            "identifier",
            "url",
            "document_path",
            "documentPath",
        ):
            value = source.get(key)
            if isinstance(value, str):
                values.append(value.lower())
    return any(marker in " ".join(values) for marker in SENSITIVE_MARKERS)


def build_provenance(context: dict[str, Any], event_name: str) -> dict[str, Any]:
    source_uri = first_text(context.get("url"), get_first(context, "document_path", "documentPath"))
    provenance = {
        "source_uri": source_uri,
        "source_type": "macos_activity",
        "trigger": event_name,
    }
    remove_none(provenance)
    return provenance


def sanitize_macos_extra(event: dict[str, Any], sensitive: bool) -> dict[str, Any]:
    extra = to_snake_case_keys(deepcopy(event))
    if sensitive:
        redact_value_fields(extra)
    else:
        truncate_value_fields(extra)
    return json_safe(extra)


def redact_value_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            if key.lower() in {"text", "value", "selected_text", "focused_value"}:
                value[key] = REDACTED
            else:
                redact_value_fields(child)
    elif isinstance(value, list):
        for child in value:
            redact_value_fields(child)


def truncate_value_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            if isinstance(child, str) and key.lower() in {"text", "value", "selected_text", "focused_value"}:
                value[key] = truncate_text(child)
            else:
                truncate_value_fields(child)
    elif isinstance(value, list):
        for child in value:
            truncate_value_fields(child)


def truncate_text(value: str) -> str:
    if len(value) <= MAX_TEXT_LENGTH:
        return value
    return value[:MAX_TEXT_LENGTH]


def build_privacy(text: str | None, sensitive: bool) -> dict[str, Any]:
    has_text = bool(text)
    if sensitive:
        return {
            "sensitivity": "high",
            "contains_raw_content": False,
            "redaction_applied": True,
        }
    return {
        "sensitivity": "medium" if has_text else "low",
        "contains_raw_content": has_text,
        "redaction_applied": False,
    }


def is_url(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith(("http://", "https://", "file://"))


def to_snake_case(value: str) -> str:
    value = value.replace("-", "_").replace(" ", "_")
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"_+", "_", value).strip("_")


def to_snake_case_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            to_snake_case(str(key)): to_snake_case_keys(child)
            for key, child in value.items()
            if isinstance(key, str)
        }
    if isinstance(value, list):
        return [to_snake_case_keys(item) for item in value]
    return value


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(child) for key, child in value.items() if isinstance(key, str)}
    return str(value)


def remove_none(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value.keys()):
            child = value[key]
            if child is None:
                del value[key]
            else:
                remove_none(child)
            if key in value and value[key] == {}:
                del value[key]
    elif isinstance(value, list):
        for child in value:
            remove_none(child)

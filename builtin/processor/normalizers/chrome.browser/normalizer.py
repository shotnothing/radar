import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


COLLECTOR_ID = "chrome.browser"
SEPARATOR = "::"
DEFAULT_ACTION_BLACKLIST = {"element_focus", "text_selection"}


def get_path(value, *path):
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def compact_component(value):
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.replace(SEPARATOR, ":")


def coerce_epoch_ms(value):
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric < 10_000_000_000:
            numeric *= 1000
        return int(numeric)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        if stripped.isdigit():
            return coerce_epoch_ms(int(stripped))
        try:
            normalized = stripped.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def observed_timestamp(event):
    return coerce_epoch_ms(
        get_path(event, "time", "observed_at")
        or get_path(event, "extra_data", "browser", "observed_at")
        or get_path(event, "anchor", "occurred_at")
    )


def document_url(event):
    return first_text(
        get_path(event, "extra_data", "browser", "document_url"),
        get_path(event, "subject", "url"),
        get_path(event, "anchor", "target", "url"),
        get_path(event, "extra_data", "browser", "url"),
    )


def css_path(event):
    return first_text(
        get_path(event, "extra_data", "browser", "element", "css_path"),
        get_path(event, "extra_data", "browser", "focused_element", "css_path"),
        get_path(event, "anchor", "target", "css_path"),
    )


def anchor_name(event):
    return first_text(
        get_path(event, "anchor", "name"),
        get_path(event, "extra_data", "browser", "event_name"),
        get_path(event, "context", "user_action"),
    )


def parse_action_blacklist(value):
    if value is None:
        return set(DEFAULT_ACTION_BLACKLIST)
    if isinstance(value, str):
        return {
            item.strip()
            for item in value.split(",")
            if item.strip()
        }
    return {str(item).strip() for item in value if str(item).strip()}


def action_blacklist():
    return parse_action_blacklist(os.environ.get("RADAR_CHROME_BROWSER_ACTION_BLACKLIST"))


def normalize_event(event, blacklist=None):
    action = anchor_name(event)
    ignored_actions = action_blacklist() if blacklist is None else parse_action_blacklist(blacklist)
    if action in ignored_actions:
        return None

    collector_id = first_text(event.get("collector_id"), COLLECTOR_ID) or COLLECTOR_ID
    payload = SEPARATOR.join(
        compact_component(part)
        for part in (
            collector_id,
            action,
            css_path(event),
            document_url(event),
        )
    )
    return {
        "collector_id": collector_id,
        "timestamp": observed_timestamp(event),
        "payload": payload,
    }


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {error}") from error


def iter_jsonl_files(path):
    root = Path(path)
    if root.is_file():
        yield root
        return
    for candidate in sorted(root.rglob("*.jsonl")):
        if candidate.is_file():
            yield candidate


def normalize_path(path, blacklist=None):
    ignored_actions = action_blacklist() if blacklist is None else parse_action_blacklist(blacklist)
    for jsonl_file in iter_jsonl_files(path):
        for event in read_jsonl(jsonl_file):
            if event.get("collector_id") not in (None, COLLECTOR_ID):
                continue
            record = normalize_event(event, blacklist=ignored_actions)
            if record:
                yield record


def default_input_path():
    explicit = os.environ.get("RADAR_CHROME_BROWSER_WORK_DIR")
    if explicit:
        return explicit

    radar_home = Path(os.path.expandvars(os.environ.get("RADAR_HOME", ""))).expanduser()
    if radar_home and str(radar_home) != ".":
        candidate = radar_home / "collectors" / "chrome_browser"
        if candidate.exists():
            return str(candidate)

    return "debug/work/collectors/chrome_browser"


def write_jsonl(records, output_path=None):
    output = sys.stdout
    should_close = False
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        output = path.open("w", encoding="utf-8")
        should_close = True
    try:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            output.write("\n")
    finally:
        if should_close:
            output.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Normalize chrome.browser collector JSONL into compact predict input."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=default_input_path(),
        help="Chrome collector work directory or JSONL file.",
    )
    parser.add_argument(
        "--output",
        help="Optional JSONL output path. Defaults to stdout.",
    )
    parser.add_argument(
        "--action-blacklist",
        default=os.environ.get("RADAR_CHROME_BROWSER_ACTION_BLACKLIST"),
        help="Comma-separated action names to skip. Defaults to element_focus.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    blacklist = parse_action_blacklist(args.action_blacklist)
    write_jsonl(normalize_path(args.input, blacklist=blacklist), args.output)


if __name__ == "__main__":
    main()

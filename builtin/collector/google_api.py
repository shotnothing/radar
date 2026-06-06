import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests


class LocalGoogleApiError(RuntimeError):
    def __init__(self, message, *, status_code=0, auth_required=False):
        super().__init__(message)
        self.status_code = status_code
        self.auth_required = auth_required


def observed_time_ms():
    return int(time.time() * 1000)


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_epoch_ms(value):
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def parse_time_ms(value, default=0):
    if value is None or value == "":
        return default
    try:
        numeric = int(float(value))
        if numeric > 10_000_000_000:
            return numeric
        if numeric > 946_684_800:
            return numeric * 1000
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_checkpoint(work_dir, key):
    path = Path(work_dir) / "state" / "checkpoint.json"
    if not path.exists():
        return {"version": 1, key: {}}
    try:
        with path.open(encoding="utf-8") as file:
            checkpoint = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, key: {}}
    checkpoint.setdefault("version", 1)
    checkpoint.setdefault(key, {})
    return checkpoint


def save_checkpoint(work_dir, checkpoint):
    path = Path(work_dir) / "state" / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(checkpoint, file, ensure_ascii=True, indent=2, sort_keys=True)
        file.write("\n")


def output_path_for(work_dir, observed_at):
    day_dir = Path(work_dir) / datetime.fromtimestamp(observed_at / 1000).strftime(
        "%Y%m%d"
    )
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{observed_at}.jsonl"


def write_events(work_dir, events):
    if not events:
        return None
    observed_at = observed_time_ms()
    path = output_path_for(work_dir, observed_at)
    with path.open("a", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event, ensure_ascii=True, sort_keys=True))
            file.write("\n")
    return path


def safe_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def compact_text(value, limit=240):
    value = " ".join(str(value or "").split())
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].strip() + "..."


def item_list(payload, *keys):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def request_headers(token):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class LocalGoogleApiClient:
    def __init__(self, *, base_url, token="", timeout=10):
        self.base_url = str(base_url or "").rstrip("/") + "/"
        self.token = token
        self.timeout = timeout

    def get(self, endpoint, params=None):
        if not self.base_url or self.base_url == "/":
            raise LocalGoogleApiError("local Google API base URL is not configured")
        url = urljoin(self.base_url, str(endpoint or "").lstrip("/"))
        try:
            response = requests.get(
                url,
                params=params or {},
                headers=request_headers(self.token),
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise LocalGoogleApiError(str(error)) from error

        if response.status_code in {401, 403}:
            raise LocalGoogleApiError(
                f"local Google API authorization failed with HTTP {response.status_code}",
                status_code=response.status_code,
                auth_required=True,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise LocalGoogleApiError(
                f"local Google API request failed with HTTP {response.status_code}: {response.text[:240]}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as error:
            raise LocalGoogleApiError("local Google API returned non-JSON response") from error

from __future__ import annotations

import json
import sys
from urllib.parse import urlparse


FOREGROUND_CHROME_NAMES = {"chrome", "google chrome"}
FOREGROUND_CHROME_BUNDLE_IDS = {"com.google.chrome"}
CALENDAR_WEEK_PATH = "/calendar/u/0/r/week"


def is_foreground_chrome(active_context: dict) -> bool:
    app_name = str(active_context.get("app_name") or "").strip().lower()
    bundle_id = str(active_context.get("bundle_id") or "").strip().lower()
    return app_name in FOREGROUND_CHROME_NAMES or bundle_id in FOREGROUND_CHROME_BUNDLE_IDS


def is_calendar_week_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return parsed.netloc.lower() == "calendar.google.com" and path == CALENDAR_WEEK_PATH


def main() -> None:
    payload = json.load(sys.stdin)
    active_context = payload.get("active_context") or {}
    if not is_foreground_chrome(active_context):
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "Google Calendar is not in the foreground browser window.",
                }
            )
        )
        return

    browser = payload.get("browser") or {}
    active_tab = browser.get("active_tab") or {}
    url = active_tab.get("url") or ""

    if not is_calendar_week_url(url):
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "Active browser tab is not Google Calendar week view.",
                }
            )
        )
        return

    print(
        json.dumps(
            {
                "available": True,
                "reason": "Active browser tab is Google Calendar week view.",
                "presentation": {
                    "title": "Find next open timeslot",
                    "message": "Find a possible opening in the current Google Calendar week view.",
                    "button_label": "Find my next open timeslot",
                },
                "action_context": {
                    "url": url,
                },
                "debounce_seconds": 30,
            }
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import sys
from urllib.parse import urlparse


FOREGROUND_CHROME_NAMES = {"chrome", "google chrome"}
FOREGROUND_CHROME_BUNDLE_IDS = {"com.google.chrome"}


def is_foreground_chrome(active_context: dict) -> bool:
    app_name = str(active_context.get("app_name") or "").strip().lower()
    bundle_id = str(active_context.get("bundle_id") or "").strip().lower()
    return app_name in FOREGROUND_CHROME_NAMES or bundle_id in FOREGROUND_CHROME_BUNDLE_IDS


def is_gmail_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() == "mail.google.com"


def main() -> None:
    payload = json.load(sys.stdin)
    active_context = payload.get("active_context") or {}
    if not is_foreground_chrome(active_context):
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "Gmail is not in the foreground browser window.",
                }
            )
        )
        return

    browser = payload.get("browser") or {}
    active_tab = browser.get("active_tab") or {}
    url = active_tab.get("url") or ""

    if not is_gmail_url(url):
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "Active browser tab is not Gmail.",
                }
            )
        )
        return

    draft_body = (
        "Hi,\n\n"
        "Just following up on this. Please let me know if there is anything "
        "you need from me.\n\n"
        "Best,"
    )
    print(
        json.dumps(
            {
                "available": True,
                "reason": "Active browser tab is Gmail.",
                "presentation": {
                    "title": "Draft Gmail follow-up",
                    "message": "Open Compose and insert a short follow-up draft.",
                    "button_label": "Draft follow-up",
                },
                "action_context": {
                    "draft_body": draft_body,
                    "url": url,
                },
                "debounce_seconds": 30,
            }
        )
    )


if __name__ == "__main__":
    main()

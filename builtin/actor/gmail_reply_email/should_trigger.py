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


def is_gmail_thread_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "mail.google.com":
        return False
    if not parsed.path.startswith("/mail/"):
        return False

    fragment_parts = [part for part in parsed.fragment.split("/") if part]
    return len(fragment_parts) >= 2


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

    if not is_gmail_thread_url(url):
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "Active browser tab is not an open Gmail email thread.",
                }
            )
        )
        return

    print(
        json.dumps(
            {
                "available": True,
                "reason": "Active browser tab is an open Gmail email thread.",
                "presentation": {
                    "title": "Reply this email",
                    "message": "Reply placeholder for the current Gmail thread.",
                    "button_label": "reply this email",
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

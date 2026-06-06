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


def is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return host in {"youtube.com", "www.youtube.com"} or host.endswith(".youtube.com")


def main() -> None:
    payload = json.load(sys.stdin)
    active_context = payload.get("active_context") or {}
    if not is_foreground_chrome(active_context):
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "YouTube is not in the foreground browser window.",
                }
            )
        )
        return

    browser = payload.get("browser") or {}
    active_tab = browser.get("active_tab") or {}
    url = active_tab.get("url") or ""

    available = is_youtube_url(url)
    if not available:
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "Active browser tab is not YouTube.",
                }
            )
        )
        return

    print(
        json.dumps(
            {
                "available": True,
                "reason": "Active browser tab is YouTube.",
                "presentation": {
                    "title": "You might want to search kpop",
                    "message": "Search kpop on YouTube automatically.",
                    "button_label": "Search kpop",
                },
                "action_context": {
                    "query": "kpop",
                    "url": url,
                },
                "debounce_seconds": 30,
            }
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import sys
from urllib.parse import urlparse


def main() -> None:
    payload = json.load(sys.stdin)
    browser = payload.get("browser") or {}
    active_tab = browser.get("active_tab") or {}
    url = active_tab.get("url") or ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    available = host in {"youtube.com", "www.youtube.com"} or host.endswith(".youtube.com")
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
                    "title": "Search YouTube for NAB",
                    "message": "Fill the YouTube search box with NAB.",
                    "button_label": "Type NAB",
                },
                "action_context": {
                    "query": "NAB",
                    "url": url,
                },
                "debounce_seconds": 30,
            }
        )
    )


if __name__ == "__main__":
    main()

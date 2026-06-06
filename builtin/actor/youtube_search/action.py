from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


SEARCH_SELECTOR = ".yt-searchbox-input"


def main() -> None:
    payload = json.load(sys.stdin)
    action_context = payload.get("action_context") or {}
    browser = payload.get("browser") or {}
    active_tab = browser.get("active_tab") or {}
    query = action_context.get("query") or "kpop"
    tab_id = action_context.get("tab_id")
    if not isinstance(tab_id, int):
        tab_id = active_tab.get("tab_id")
    api_url = os.environ["RADAR_API_URL"].rstrip("/")
    token = os.environ.get("RADAR_API_TOKEN", "")

    page_action = {
        "url_patterns": ["*://youtube.com/*", "*://*.youtube.com/*"],
        "stop_on_error": True,
        "tab_id": tab_id if isinstance(tab_id, int) else None,
        "actions": [
            {
                "name": "focus_search",
                "type": "focus",
                "selector": SEARCH_SELECTOR,
                "visible": True,
                "wait_for_selector_ms": 5000,
            },
            {
                "name": "fill_search",
                "type": "fill",
                "selector": SEARCH_SELECTOR,
                "value": query,
                "clear": True,
                "trigger_input": True,
                "visible": True,
                "wait_for_selector_ms": 5000,
            },
        ],
    }

    result = post_json(f"{api_url}/api/browser/page_action", page_action, token)
    success = bool(result.get("success")) and not result.get("error")
    print(
        json.dumps(
            {
                "success": success,
                "message": f"Filled YouTube search with {query}." if success else "Failed to fill YouTube search.",
                "browser_result": result,
            }
        )
    )


def post_json(url: str, payload: dict[str, object], token: str) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {"success": False, "error": body or str(error)}
    except Exception as error:
        return {"success": False, "error": str(error)}


if __name__ == "__main__":
    main()

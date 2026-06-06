from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


COMPOSE_SELECTOR = "div[role='button'][gh='cm'], .T-I.T-I-KE, div[aria-label='Compose']"
BODY_SELECTOR = (
    "div[role='textbox'][aria-label='Message Body'], "
    "div[aria-label='Message Body'][contenteditable='true'], "
    "div[contenteditable='true'][role='textbox']"
)


def main() -> None:
    payload = json.load(sys.stdin)
    action_context = payload.get("action_context") or {}
    draft_body = action_context.get("draft_body") or (
        "Hi,\n\n"
        "Just following up on this. Please let me know if there is anything you need from me.\n\n"
        "Best,"
    )
    api_url = os.environ["RADAR_API_URL"].rstrip("/")
    token = os.environ.get("RADAR_API_TOKEN", "")

    page_action = {
        "url_pattern": "https://mail.google.com/*",
        "stop_on_error": True,
        "actions": [
            {
                "name": "open_compose",
                "type": "click",
                "selector": COMPOSE_SELECTOR,
                "wait_ms": 1200,
            },
            {
                "name": "focus_body",
                "type": "focus",
                "selector": BODY_SELECTOR,
            },
            {
                "name": "fill_body",
                "type": "fill",
                "selector": BODY_SELECTOR,
                "value": draft_body,
                "clear": True,
                "trigger_input": True,
            },
        ],
    }

    result = post_json(f"{api_url}/api/browser/page_action", page_action, token)
    success = bool(result.get("success")) and not result.get("error")
    print(
        json.dumps(
            {
                "success": success,
                "message": "Prepared a Gmail follow-up draft." if success else "Failed to prepare Gmail draft.",
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

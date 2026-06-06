from __future__ import annotations

import json
import sys

from lib import run_helper


def event_name(trigger_event: dict) -> str:
    anchor = trigger_event.get("anchor") or {}
    return str(trigger_event.get("event_name") or trigger_event.get("name") or anchor.get("name") or "")


def main() -> None:
    payload = json.load(sys.stdin)
    trigger_event = payload.get("trigger_event") or {}
    if event_name(trigger_event) != "mouse_click":
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": "SeaTalk ping thread actor only runs after mouse clicks.",
                }
            )
        )
        return

    result = run_helper("detect-conversation", timeout=20)
    available = bool(result.get("available"))
    if not available:
        print(
            json.dumps(
                {
                    "available": False,
                    "reason": result.get("reason") or result.get("error") or "No SeaTalk @You ping found.",
                    "helper_result": result,
                }
            )
        )
        return

    print(
        json.dumps(
            {
                "available": True,
                "reason": result.get("reason") or "Radar has a SeaTalk @You ping.",
                "presentation": {
                    "title": "Open SeaTalk ping thread",
                    "message": "Open the Radar thread that mentioned you.",
                    "button_label": "Open thread",
                },
                "action_context": {
                    "chat_name": result.get("chat_name", "Radar"),
                    "marker": result.get("marker", "@You"),
                },
                "debounce_seconds": 20,
            }
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import sys

from lib import run_helper


def main() -> None:
    read_input()
    result = run_helper("open", timeout=30)
    success = bool(result.get("success"))
    print(
        json.dumps(
            {
                "success": success,
                "message": (
                    result.get("message")
                    or ("Opened the SeaTalk ping thread." if success else "Failed to open the SeaTalk ping thread.")
                ),
                "helper_result": result,
            }
        )
    )


def read_input() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


if __name__ == "__main__":
    main()

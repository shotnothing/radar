from __future__ import annotations

import json
import sys


def main() -> None:
    json.load(sys.stdin)
    print(
        json.dumps(
            {
                "success": True,
                "message": "Find next open timeslot action is not implemented yet.",
            }
        )
    )


if __name__ == "__main__":
    main()

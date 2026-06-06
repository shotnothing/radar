from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from debug.active_context import MacOSActiveContextReader, render_accessibility_tree


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a macOS Accessibility AX tree for actor development.",
    )
    parser.add_argument(
        "--app",
        "--app-name",
        dest="app_name",
        default="",
        help="Target application process name, for example Codex or Google Chrome.",
    )
    parser.add_argument(
        "--bundle-id",
        default="",
        help="Target bundle identifier, for example com.google.Chrome.",
    )
    parser.add_argument(
        "--depth",
        "--max-depth",
        dest="max_depth",
        type=int,
        default=5,
        help="Maximum AX tree depth to dump.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of the compact text tree.",
    )
    parser.add_argument(
        "--server-url",
        default="",
        help="Optional debug coordinator URL, for example http://127.0.0.1:5000.",
    )
    args = parser.parse_args()

    payload = read_tree(args.server_url, args.bundle_id, args.app_name, args.max_depth)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_accessibility_tree(payload))
    return 0 if payload.get("success") else 1


def read_tree(server_url: str, bundle_id: str, app_name: str, max_depth: int) -> dict[str, Any]:
    if server_url:
        return read_tree_from_server(server_url, bundle_id, app_name, max_depth)
    return MacOSActiveContextReader().read_accessibility(
        bundle_id=bundle_id,
        app_name=app_name,
        max_depth=max_depth,
    )


def read_tree_from_server(
    server_url: str,
    bundle_id: str,
    app_name: str,
    max_depth: int,
) -> dict[str, Any]:
    url = f"{server_url.rstrip('/')}/debug/accessibility/query"
    body = json.dumps(
        {
            "bundle_id": bundle_id,
            "app_name": app_name,
            "max_depth": max_depth,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {
            "success": False,
            "bundle_id": bundle_id,
            "app_name": app_name,
            "error": f"could not query debug coordinator: {exc}",
        }


if __name__ == "__main__":
    raise SystemExit(main())

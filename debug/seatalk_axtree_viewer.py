from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from builtin.actor.seatalk_thread_ping.lib import run_helper


def main() -> int:
    parser = argparse.ArgumentParser(description="Print SeaTalk AX text nodes for actor debugging.")
    parser.add_argument(
        "--scope",
        choices=["rows", "sidebar", "all"],
        default="rows",
        help="Print likely conversation rows, sidebar text nodes, or all visible text nodes.",
    )
    parser.add_argument(
        "--sidebar-max-x",
        type=int,
        default=700,
        help="Maximum node x-coordinate for sidebar output.",
    )
    parser.add_argument("--limit", type=int, default=240)
    args = parser.parse_args()

    scan = run_helper("scan", timeout=45)
    print_json_status("scan", scan)

    result = run_helper("text", timeout=60)
    if not result.get("success"):
        print_json_status("text", result)
        return 1

    nodes = [node for node in result.get("nodes", []) if isinstance(node, dict)]
    if args.scope == "rows":
        nodes = [node for node in nodes if is_conversation_row(node, args.sidebar_max_x)]
    elif args.scope == "sidebar":
        nodes = [node for node in nodes if is_sidebar_node(node, args.sidebar_max_x)]

    print()
    print(f"SeaTalk AX text nodes ({args.scope}, {len(nodes)} shown before limit):")
    for node in nodes[: args.limit]:
        print(format_node(node))
    if len(nodes) > args.limit:
        print(f"... {len(nodes) - args.limit} more nodes hidden; rerun with --limit {len(nodes)}")
    return 0


def print_json_status(label: str, payload: dict[str, Any]) -> None:
    if label == "scan" and payload.get("success"):
        matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        print(f"SeaTalk AX scan found {len(matches)} Radar/@You text match(es):")
        for item in matches:
            if isinstance(item, dict):
                print(format_node(item))
        return
    print(f"{label}: {json.dumps(payload, sort_keys=True)}")


def is_sidebar_node(node: dict[str, Any], max_x: int) -> bool:
    frame = node.get("frame")
    if not isinstance(frame, dict):
        return True
    x = frame.get("x")
    width = frame.get("width")
    if not isinstance(x, int):
        return True
    if isinstance(width, int) and width > 900:
        return False
    return x <= max_x


def is_conversation_row(node: dict[str, Any], max_x: int) -> bool:
    frame = node.get("frame")
    if not isinstance(frame, dict):
        return False
    x = frame.get("x")
    width = frame.get("width")
    height = frame.get("height")
    if not all(isinstance(value, int) for value in (x, width, height)):
        return False
    if x > max_x or width < 120:
        return False
    return 36 <= height <= 96


def format_node(node: dict[str, Any]) -> str:
    path = str(node.get("path", "?"))
    depth = node.get("depth", "?")
    frame = node.get("frame") if isinstance(node.get("frame"), dict) else {}
    frame_text = ""
    if frame:
        frame_text = (
            f" x={frame.get('x')} y={frame.get('y')}"
            f" w={frame.get('width')} h={frame.get('height')}"
        )
    texts = node.get("texts") if isinstance(node.get("texts"), list) else []
    text = " | ".join(sanitize_text(str(item)) for item in texts if str(item))
    return f"{path} depth={depth}{frame_text} :: {text}"


def sanitize_text(value: str) -> str:
    compact = " ".join(value.split())
    compact = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-[REDACTED]", compact)
    compact = re.sub(r"(code=)[^&\\s]+", r"\1[REDACTED]", compact)
    compact = re.sub(r"(token=)[^&\\s]+", r"\1[REDACTED]", compact)
    compact = re.sub(r"(state=)[^&\\s]+", r"\1[REDACTED]", compact)
    if len(compact) > 220:
        compact = compact[:217] + "..."
    return compact


if __name__ == "__main__":
    raise SystemExit(main())

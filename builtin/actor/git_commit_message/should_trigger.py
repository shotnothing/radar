from __future__ import annotations

import json

from lib import (
    build_action_context,
    command_tail_is_supported,
    detect_git_commit_command,
    find_repo_for_active_terminal,
    read_stdin_json,
    supported_terminal,
)


def main() -> None:
    payload = read_stdin_json()
    active_context = payload.get("active_context") or {}

    if not supported_terminal(active_context):
        print(json.dumps({"available": False, "reason": "The active app is not a supported terminal."}))
        return

    command = detect_git_commit_command(active_context)
    if not command:
        print(json.dumps({"available": False, "reason": "No git commit command is being typed."}))
        return

    if not command_tail_is_supported(command["tail"]):
        print(json.dumps({"available": False, "reason": "The git commit command already has unsupported arguments."}))
        return

    repo_path = find_repo_for_active_terminal(active_context)
    if not repo_path:
        print(json.dumps({"available": False, "reason": "Could not find the active terminal repository."}))
        return

    print(
        json.dumps(
            {
                "available": True,
                "reason": f"Ready to generate a Git commit message for {repo_path}.",
                "action_context": build_action_context(command, repo_path),
                "debounce_seconds": 20,
            }
        )
    )


if __name__ == "__main__":
    main()

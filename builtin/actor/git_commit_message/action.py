from __future__ import annotations

import json

from lib import (
    copy_text,
    generate_commit_message,
    git_diff_for_commit,
    read_stdin_json,
)


def main() -> None:
    payload = read_stdin_json()
    action_context = payload.get("action_context") or {}
    repo_path = action_context.get("repo_path") or ""

    if not repo_path:
        print(json.dumps({"success": False, "message": "No repository path was provided."}))
        return

    diff_result = git_diff_for_commit(repo_path)
    if not diff_result.get("diff"):
        print(json.dumps({"success": False, "message": "No Git changes found for commit message generation."}))
        return

    commit_message = generate_commit_message(diff_result)
    copy_result = copy_text(commit_message)
    success = bool(copy_result.get("success"))

    print(
        json.dumps(
            {
                "success": success,
                "message": "Copied Git commit message." if success else "Failed to copy Git commit message.",
                "action": {
                    "repo_path": repo_path,
                    "branch": diff_result.get("branch", ""),
                    "has_staged": diff_result.get("has_staged", False),
                    "commit_message": commit_message,
                },
                "copy_result": copy_result,
            }
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import json

from lib import find_available_skill, query_accessibility_tree, read_stdin_json


def main() -> None:
    payload = read_stdin_json()
    accessibility = query_accessibility_tree()
    result = find_available_skill(payload, accessibility)

    if not result.get("available"):
        print(json.dumps({"available": False, "reason": result.get("reason", "Radar skill is not available.")}))
        return

    repo_path = result["repo_path"]
    repo_skill_path = result["repo_skill_path"]
    print(
        json.dumps(
            {
                "available": True,
                "reason": f"Codex is open on {repo_path} and Radar has skill guidance for it.",
                "presentation": {
                    "title": "Use Radar skill in Codex",
                    "message": f"Add a skill instruction for {repo_skill_path} to the current Codex prompt.",
                    "button_label": "Use skill",
                },
                "action_context": result,
                "debounce_seconds": 300,
            }
        )
    )


if __name__ == "__main__":
    main()

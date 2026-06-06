from __future__ import annotations

import json

from lib import build_codex_skill_prompt, paste_text_into_codex, read_stdin_json


def main() -> None:
    payload = read_stdin_json()
    action_context = payload.get("action_context") or {}
    repo_skill_path = action_context.get("repo_skill_path") or ""
    if not repo_skill_path:
        print(json.dumps({"success": False, "message": "No Radar repo skill path was provided."}))
        return

    paste_text = build_codex_skill_prompt(repo_skill_path)
    result = paste_text_into_codex(paste_text)
    success = bool(result.get("success"))
    print(
        json.dumps(
            {
                "success": success,
                "message": f"Added Radar skill instruction to Codex: {repo_skill_path}" if success else "Failed to add Radar skill instruction to Codex.",
                "action": {
                    "skill_path": action_context.get("skill_path", ""),
                    "repo_path": action_context.get("repo_path", ""),
                    "repo_skill_path": repo_skill_path,
                    "prompt": paste_text,
                },
                "paste_result": result,
            }
        )
    )


if __name__ == "__main__":
    main()

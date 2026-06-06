from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


DEFAULT_CODEX_TIMEOUT_SECONDS = 120


def build_codex_prompt(url: str) -> str:
    return (
        "You are running as a Radar desktop actor for the user's active Gmail tab.\n"
        "Use macOS automation via command-line tools such as osascript/System Events. "
        "Do not use Gmail APIs, do not modify files, and do not send the email.\n\n"
        "Task:\n"
        "1. Bring Google Chrome to the foreground.\n"
        "2. Ensure the active Gmail tab is this open email thread: "
        f"{url or 'the currently open Gmail email thread'}\n"
        "3. Open Gmail's reply editor for the current email thread.\n"
        "4. Stop after the reply editor is visible/focused. Do not type a reply body and do not click Send.\n\n"
        "Return a concise final status describing whether the reply editor was opened."
    )


def run_codex_reply_action(url: str) -> dict[str, Any]:
    codex_bin = os.environ.get("RADAR_CODEX_BIN", "codex")
    timeout = int(os.environ.get("RADAR_CODEX_TIMEOUT_SECONDS", DEFAULT_CODEX_TIMEOUT_SECONDS))
    prompt = build_codex_prompt(url)
    command = [
        codex_bin,
        "exec",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "danger-full-access",
        "--skip-git-repo-check",
        "--cd",
        os.getcwd(),
        prompt,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"Codex CLI was not found: {codex_bin}",
        }
    except subprocess.TimeoutExpired as error:
        return {
            "success": False,
            "returncode": None,
            "stdout": (error.stdout or "").strip() if isinstance(error.stdout, str) else "",
            "stderr": f"Codex CLI timed out after {timeout} seconds.",
        }
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def main() -> None:
    payload = json.load(sys.stdin)
    action_context = payload.get("action_context") or {}
    url = str(action_context.get("url") or "")
    result = run_codex_reply_action(url)
    success = bool(result.get("success"))
    print(
        json.dumps(
            {
                "success": success,
                "message": "Opened Gmail reply editor." if success else "Failed to open Gmail reply editor.",
                "codex_result": result,
            }
        )
    )


if __name__ == "__main__":
    main()

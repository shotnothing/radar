from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CODEX_TIMEOUT_SECONDS = 120
DEFAULT_LOG_FILE_NAME = "action.log"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def action_log_path() -> Path:
    configured = os.environ.get("RADAR_GMAIL_REPLY_ACTION_LOG", "").strip()
    if configured:
        return Path(configured).expanduser()
    actor_dir = os.environ.get("RADAR_ACTOR_DIR", "").strip()
    root = Path(actor_dir).expanduser() if actor_dir else Path(__file__).resolve().parent
    return root / DEFAULT_LOG_FILE_NAME


def append_action_log(event: str, fields: dict[str, Any] | None = None) -> None:
    entry = {
        "timestamp": utc_now(),
        "event": event,
        **(fields or {}),
    }
    try:
        path = action_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
    except Exception:
        pass


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
        "--ask-for-approval",
        "never",
        "--sandbox",
        "danger-full-access",
        "exec",
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
    log_context = {
        "trigger_id": payload.get("trigger_id", ""),
        "url": url,
    }
    append_action_log("triggered", log_context)
    try:
        result = run_codex_reply_action(url)
        success = bool(result.get("success"))
        output = {
            "success": success,
            "message": "Opened Gmail reply editor." if success else "Failed to open Gmail reply editor.",
            "codex_result": result,
        }
        append_action_log(
            "completed",
            {
                **log_context,
                "success": success,
                "message": output["message"],
                "returncode": result.get("returncode"),
                "error": result.get("stderr", ""),
            },
        )
    except Exception as error:
        output = {
            "success": False,
            "message": "Gmail reply action failed.",
            "error": str(error),
        }
        append_action_log(
            "error",
            {
                **log_context,
                "success": False,
                "error": str(error),
                "traceback": traceback.format_exc(),
            },
        )

    print(json.dumps(output))


if __name__ == "__main__":
    main()

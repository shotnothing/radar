from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 300


def build_prompt(payload: dict[str, Any]) -> str:
    action_context = payload.get("action_context") or {}
    browser = payload.get("browser") or {}
    active_tab = browser.get("active_tab") or {}
    url = action_context.get("url") or active_tab.get("url") or ""
    title = active_tab.get("title") or "Google Calendar"

    return f"""You are Codex CLI running from a Radar desktop actor.

Task: find my next open timeslot in the currently open Google Calendar week view.

Current browser tab:
- Title: {title}
- URL: {url}

Use macOS command-line automation to inspect the active Google Calendar UI. You may use tools such as osascript/System Events, screencapture, accessibility inspection, and read-only browser/page inspection when available. Focus or inspect Google Chrome if needed.

Constraints:
- Do not create, edit, delete, accept, decline, or move calendar events.
- Do not send emails, invitations, notifications, or messages.
- Prefer the earliest available 30-minute slot from now in the visible/current week.
- If you cannot determine a slot reliably, say what blocked you and what context was visible.

Return only a concise answer with:
1. The next open timeslot, including date, start time, end time, and timezone.
2. One short evidence sentence explaining how you determined it.
"""


def resolve_codex_bin() -> str | None:
    configured = os.environ.get("RADAR_CODEX_BIN", "").strip()
    if configured:
        return configured
    return shutil.which("codex")


def codex_timeout_seconds() -> int:
    value = os.environ.get("RADAR_CALENDAR_CODEX_TIMEOUT_SECONDS", "")
    if not value:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1, int(value))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def run_codex(prompt: str) -> dict[str, Any]:
    codex_bin = resolve_codex_bin()
    if not codex_bin:
        return {
            "success": False,
            "message": "Codex CLI was not found on PATH.",
            "error": "codex executable not found",
        }

    timeout_seconds = codex_timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="radar-calendar-codex-") as tmp:
        output_path = Path(tmp) / "answer.md"
        command = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "danger-full-access",
            "--ask-for-approval",
            "never",
            "--cd",
            tmp,
            "--output-last-message",
            str(output_path),
            prompt,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return {
                "success": False,
                "message": "Codex CLI timed out while looking for an open timeslot.",
                "answer": "",
                "command": [codex_bin, "exec"],
                "stdout": (error.stdout or "").strip()
                if isinstance(error.stdout, str)
                else "",
                "stderr": (error.stderr or "").strip()
                if isinstance(error.stderr, str)
                else "",
                "exit_code": None,
                "error": f"timed out after {timeout_seconds} seconds",
            }
        answer = ""
        try:
            answer = output_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if not answer:
            answer = result.stdout.strip()

    success = result.returncode == 0 and bool(answer)
    return {
        "success": success,
        "message": answer if success else "Codex CLI could not find an open timeslot.",
        "answer": answer,
        "command": [codex_bin, "exec"],
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "exit_code": result.returncode,
    }


def post_view(markdown: str) -> dict[str, Any] | None:
    api_url = os.environ.get("RADAR_API_URL", "").rstrip("/")
    if not api_url:
        return None
    token = os.environ.get("RADAR_API_TOKEN", "")
    payload = {
        "title": "Next open timeslot",
        "kind": "markdown",
        "markdown": markdown,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url}/api/view/open",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {"success": False, "error": body or str(error)}
    except Exception as error:
        return {"success": False, "error": str(error)}


def main() -> None:
    payload = json.load(sys.stdin)
    prompt = build_prompt(payload)
    result = run_codex(prompt)
    if result.get("answer"):
        result["view_result"] = post_view(str(result["answer"]))

    print(
        json.dumps(
            {
                "success": bool(result.get("success")),
                "message": result.get("message", ""),
                "answer": result.get("answer", ""),
                "codex_result": result,
            }
        )
    )


if __name__ == "__main__":
    main()

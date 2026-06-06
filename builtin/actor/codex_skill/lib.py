from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import time
from pathlib import Path
from typing import Any


ABS_PATH_RE = re.compile(
    r"(?:(?:/Users|/Volumes|/private|/tmp|/var|/opt|/workspace|/home)/[^\s\"'<>|]+)"
)
TRAILING_PATH_CHARS = ".,;:)]}"
NON_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
RECENT_CODEX_SESSION_MAX_AGE_SECONDS = 6 * 60 * 60


def safe_slug(value: object) -> str:
    text = NON_SLUG_RE.sub("-", str(value or "").strip().replace("/", "-").replace(":", "-"))
    text = text.strip("-._")
    if not text:
        return "unknown"
    if len(text) > 120:
        return text[:120] + "-" + hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:12]
    return text


def default_skill_dir() -> Path:
    return Path(os.environ.get("RADAR_SKILL_DIR", "~/.radar/skill")).expanduser().resolve()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def read_stdin_json() -> dict[str, Any]:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


def query_accessibility_tree(max_depth: int = 7) -> dict[str, Any]:
    api_url = os.environ.get("RADAR_API_URL", "").rstrip("/")
    if not api_url:
        return {"success": False, "error": "RADAR_API_URL is not set"}

    token = os.environ.get("RADAR_API_TOKEN", "")
    payload = {"mode": "tree", "max_depth": max_depth}
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url}/api/accessibility/query",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=9) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {"success": False, "error": body or str(error)}
    except Exception as error:
        return {"success": False, "error": str(error)}


def flattened_strings(value: Any) -> list[str]:
    strings: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and item.strip():
            strings.append(item.strip())
    return strings


def context_text(payload: dict[str, Any], accessibility: dict[str, Any] | None = None) -> str:
    chunks = flattened_strings(payload.get("active_context") or {})
    if accessibility:
        chunks.extend(flattened_strings(accessibility.get("data") or {}))
    return "\n".join(chunks)


def looks_like_codex(payload: dict[str, Any], accessibility: dict[str, Any] | None = None) -> bool:
    active_context = payload.get("active_context") or {}
    direct_values = [
        active_context.get("app_name", ""),
        active_context.get("bundle_id", ""),
        active_context.get("window_title", ""),
    ]
    if accessibility:
        data = accessibility.get("data") or {}
        direct_values.extend([data.get("app_name", ""), data.get("bundle_id", "")])

    if any("codex" in str(value).lower() for value in direct_values):
        return True

    text = context_text(payload, accessibility).lower()
    return "codex" in text and ("chatgpt" in text or "workspace" in text or "repo" in text)


def extract_candidate_paths(payload: dict[str, Any], accessibility: dict[str, Any] | None = None) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for text in flattened_strings(payload.get("active_context") or {}):
        add_paths_from_text(text, paths, seen)
    if accessibility:
        for text in flattened_strings(accessibility.get("data") or {}):
            add_paths_from_text(text, paths, seen)
    return paths


def add_paths_from_text(text: str, paths: list[Path], seen: set[str]) -> None:
    for match in ABS_PATH_RE.findall(text):
        candidate = match.rstrip(TRAILING_PATH_CHARS)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        paths.append(Path(candidate).expanduser())


def find_git_root(start: Path) -> Path | None:
    try:
        current = start.resolve()
    except OSError:
        current = start.expanduser()

    if current.is_file():
        current = current.parent

    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def resolve_repo_path(paths: list[Path]) -> Path | None:
    for path in paths:
        root = find_git_root(path)
        if root is not None:
            return root
    for path in paths:
        if path.exists() and path.is_dir():
            return path.resolve()
    return None


def recent_codex_session_paths(limit: int = 8) -> list[Path]:
    sessions_root = codex_home() / "sessions"
    try:
        paths = [path for path in sessions_root.glob("**/*.jsonl") if path.is_file()]
    except OSError:
        return []

    now = time.time()
    fresh_paths: list[Path] = []
    for path in paths:
        try:
            if now - path.stat().st_mtime <= RECENT_CODEX_SESSION_MAX_AGE_SECONDS:
                fresh_paths.append(path)
        except OSError:
            continue

    return sorted(fresh_paths, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def extract_path_from_codex_event(event: dict[str, Any]) -> Path | None:
    payload = event.get("payload") or {}
    cwd = payload.get("cwd")
    if cwd:
        return Path(str(cwd)).expanduser()

    arguments = payload.get("arguments")
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            parsed_arguments = {}
        workdir = parsed_arguments.get("workdir") if isinstance(parsed_arguments, dict) else ""
        if workdir:
            return Path(str(workdir)).expanduser()

    return None


def extract_paths_from_recent_codex_sessions() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for session_path in recent_codex_session_paths():
        try:
            lines = session_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            candidate = extract_path_from_codex_event(event)
            if candidate is None:
                continue
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            paths.append(candidate)
    return paths


def project_skill_path(skill_dir: Path, repo_path: Path) -> Path:
    try:
        resolved_repo = repo_path.resolve()
    except OSError:
        resolved_repo = repo_path
    return skill_dir / "projects" / safe_slug(str(resolved_repo))


def skill_has_content(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return any(child.suffix == ".md" and child.name != "SKILL.md" for child in path.iterdir() if child.is_file())


def find_available_skill(
    payload: dict[str, Any],
    accessibility: dict[str, Any] | None = None,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    skill_dir = (skill_dir or default_skill_dir()).resolve()
    if not (skill_dir / "SKILL.md").exists():
        return {"available": False, "reason": f"Radar skill is not initialized at {skill_dir}."}

    if not looks_like_codex(payload, accessibility):
        return {"available": False, "reason": "Codex is not the active app."}

    repo_path = resolve_repo_path(extract_candidate_paths(payload, accessibility))
    repo_source = "accessibility"
    if repo_path is None:
        repo_path = resolve_repo_path(extract_paths_from_recent_codex_sessions())
        repo_source = "recent_codex_session"
    if repo_path is None:
        return {"available": False, "reason": "Could not find the active Codex repository from the AX tree."}

    repo_skill_path = project_skill_path(skill_dir, repo_path)
    if not skill_has_content(repo_skill_path):
        return {
            "available": False,
            "reason": f"No Radar skill entries found for {repo_path}.",
            "repo_path": str(repo_path),
            "repo_skill_path": str(repo_skill_path),
        }

    return {
        "available": True,
        "repo_path": str(repo_path),
        "skill_path": str(skill_dir),
        "repo_skill_path": str(repo_skill_path),
        "repo_source": repo_source,
    }


def build_codex_skill_prompt(repo_skill_path: str) -> str:
    return f" Please use the skill at {repo_skill_path}."


def paste_text_into_codex(text: str) -> dict[str, Any]:
    script = r'''
    on run argv
        set pasteText to item 1 of argv
        set the clipboard to pasteText
        tell application "System Events"
            set targetProc to missing value
            repeat with proc in application processes
                try
                    if ((name of proc as text) contains "Codex") then
                        set targetProc to proc
                        exit repeat
                    end if
                end try
            end repeat
            if targetProc is missing value then error "Codex process is not running"
            set frontmost of targetProc to true
            delay 0.2
            keystroke "v" using command down
        end tell
    end run
    '''
    result = subprocess.run(
        ["osascript", "-e", script, text],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout.strip(),
        "error": result.stderr.strip(),
    }

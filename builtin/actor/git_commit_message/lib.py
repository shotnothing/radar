from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SUPPORTED_TERMINAL_BUNDLE_IDS = {
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "dev.warp.Warp-Stable",
    "io.alacritty",
    "com.github.wez.wezterm",
    "net.kovidgoyal.kitty",
    "co.zeit.hyper",
    "com.microsoft.VSCode",
    "com.jetbrains.intellij.ce",
    "com.jetbrains.intellij",
    "com.jetbrains.goland",
    "com.jetbrains.pycharm",
    "com.jetbrains.pycharm.ce",
    "com.jetbrains.WebStorm",
    "com.todesktop.230313mzl4w4u92",
}

SUPPORTED_TERMINAL_APP_NAMES = {
    "terminal",
    "iterm",
    "iterm2",
    "warp",
    "alacritty",
    "wezterm",
    "kitty",
    "hyper",
    "visual studio code",
    "code",
    "cursor",
    "intellij idea",
    "goland",
    "pycharm",
    "webstorm",
}

DEFAULT_FILE_EXCLUDES = [
    ".*",
    "**/.*",
    "*.pb.go",
    "*_pb2.py",
    "*.proto",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "target/*",
    "node_modules/*",
    "dist/*",
    "build/*",
]

DEFAULT_COMMIT_PROMPT = """Generate a git commit message for the provided diff.

STRICT RULES:
1. Output ONLY the commit message - no explanations, no metadata, no markdown.
2. First line format: <type>(<scope>): <short description>.
3. type must be one of feat, fix, docs, style, refactor, test, chore, perf, ci, build.
4. Use imperative mood, lowercase description, no final period, max 50 chars.
5. If the branch contains a Jira key like ABC-123, prepend it in brackets.
6. If a body is useful, add one blank line then wrap body lines at 72 chars.
"""

ABS_PATH_RE = re.compile(r"(?:(?:/Users|/Volumes|/private|/tmp|/var|/opt|/workspace|/home)/[^\s\"'<>|]+)")
COMMIT_COMMAND_RE = re.compile(r"git\s+commit(?P<tail>[^\n\r]*)$")
JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")
RECENT_SECONDS = 90
MAX_DIFF_PROMPT_CHARS = 20000


def read_stdin_json() -> dict[str, Any]:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


def load_local_env() -> None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = os.path.expandvars(value.strip().strip("\"'"))


def radar_home() -> Path:
    return Path(os.environ.get("RADAR_HOME", "~/.radar")).expanduser().resolve()


def supported_terminal(active_context: dict[str, Any]) -> bool:
    bundle_id = str(active_context.get("bundle_id") or "").strip()
    app_name = str(active_context.get("app_name") or "").strip().lower()
    if bundle_id in SUPPORTED_TERMINAL_BUNDLE_IDS:
        return True
    return app_name in SUPPORTED_TERMINAL_APP_NAMES


def context_text_values(active_context: dict[str, Any]) -> list[str]:
    signals = active_context.get("signals") or {}
    values = [
        signals.get("focused_value"),
        signals.get("focused_title"),
        active_context.get("label"),
        active_context.get("window_title"),
        active_context.get("document_path"),
    ]
    return [str(value) for value in values if isinstance(value, str) and value.strip()]


def detect_git_commit_command(active_context: dict[str, Any]) -> dict[str, str] | None:
    for text in context_text_values(active_context):
        candidate = last_command_line(text)
        match = COMMIT_COMMAND_RE.search(candidate)
        if not match:
            continue
        return {
            "command_text": candidate,
            "command_tail": match.group("tail"),
            "tail": match.group("tail"),
        }
    candidate = recent_terminal_key_buffer()
    match = COMMIT_COMMAND_RE.search(candidate)
    if match:
        return {
            "command_text": candidate,
            "command_tail": match.group("tail"),
            "tail": match.group("tail"),
        }
    return None


def last_command_line(text: str) -> str:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1].lstrip()[-500:]


def command_tail_is_supported(tail: str) -> bool:
    stripped = str(tail or "")
    if any(char in stripped for char in "\"'`"):
        return False
    return stripped in {"", " ", "-", "m", " -", "-m", " -m", "-m ", " -m "}


def build_action_context(command: dict[str, str], repo_path: str) -> dict[str, Any]:
    return {
        "repo_path": repo_path,
        "command_text": command.get("command_text", ""),
        "command_tail": command.get("command_tail", ""),
        "triggered_at": int(time.time() * 1000),
    }


def recent_terminal_key_buffer() -> str:
    events = recent_macos_key_events()
    line = ""
    last_observed_at = 0
    for event in events:
        observed_at = int((event.get("time") or {}).get("observed_at") or 0)
        if last_observed_at and observed_at - last_observed_at > RECENT_SECONDS * 1000:
            line = ""
        last_observed_at = observed_at

        macos = ((event.get("extra_data") or {}).get("macos") or {})
        key_code = int(macos.get("key_code") or 0)
        if key_code in {36, 76}:
            line = ""
            continue
        if key_code == 51:
            line = line[:-1]
            continue
        text = str(macos.get("text") or "")
        if not text or any(ord(char) < 32 for char in text):
            continue
        line += text
        if len(line) > 500:
            line = line[-500:]
    return line


def recent_macos_key_events() -> list[dict[str, Any]]:
    collectors_root = radar_home() / "collectors" / "macos_activity"
    if not collectors_root.exists():
        return []
    cutoff_ms = int((time.time() - RECENT_SECONDS) * 1000)
    paths = sorted(
        (
            path
            for path in collectors_root.glob("**/*.jsonl")
            if path.is_file() and path.stat().st_mtime >= time.time() - RECENT_SECONDS - 10
        ),
        key=lambda path: path.stat().st_mtime,
    )
    events: list[dict[str, Any]] = []
    for path in paths[-6:]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (event.get("anchor") or {}).get("name") != "key_input":
                continue
            observed_at = int((event.get("time") or {}).get("observed_at") or 0)
            if observed_at >= cutoff_ms:
                events.append(event)
    return sorted(events, key=lambda event: int((event.get("time") or {}).get("observed_at") or 0))


def find_repo_for_active_terminal(active_context: dict[str, Any]) -> str:
    override = os.environ.get("RADAR_GIT_COMMIT_REPO_PATH", "").strip()
    if override:
        root = git_root(override)
        if root:
            return root

    for candidate in candidate_paths_from_context(active_context):
        root = git_root(candidate)
        if root:
            return root

    bundle_id = str(active_context.get("bundle_id") or "")
    app_name = str(active_context.get("app_name") or "")
    terminal_paths = terminal_cwd_candidates(bundle_id, app_name, active_context)
    for candidate in terminal_paths:
        root = git_root(candidate)
        if root:
            return root

    return ""


def candidate_paths_from_context(active_context: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for text in context_text_values(active_context):
        for match in ABS_PATH_RE.findall(text):
            candidates.append(match.rstrip(".,;:)]}"))
    return candidates


def git_root(path: str | Path) -> str:
    try:
        current = Path(path).expanduser().resolve()
    except OSError:
        current = Path(path).expanduser()
    if current.is_file():
        current = current.parent
    while True:
        if (current / ".git").exists():
            return str(current)
        if current.parent == current:
            return ""
        current = current.parent


def terminal_cwd_candidates(bundle_id: str, app_name: str, active_context: dict[str, Any]) -> list[str]:
    if bundle_id in {"com.apple.Terminal", "com.googlecode.iterm2"} or app_name.lower() in {"terminal", "iterm", "iterm2"}:
        cwd = focused_terminal_cwd(bundle_id, app_name)
        if cwd:
            return [cwd]

    title_path = workspace_from_window_title(str(active_context.get("window_title") or ""))
    if title_path:
        return [title_path]

    app_pid = active_app_pid(bundle_id, app_name)
    if not app_pid:
        return []
    return cwd_candidates_from_process_tree(app_pid)


def focused_terminal_cwd(bundle_id: str, app_name: str) -> str:
    script = ""
    if bundle_id == "com.apple.Terminal" or app_name.lower() == "terminal":
        script = 'tell application "Terminal" to get tty of selected tab of front window'
    elif bundle_id == "com.googlecode.iterm2" or app_name.lower() in {"iterm", "iterm2"}:
        script = 'tell application "iTerm" to get tty of current session of current window'
    if not script:
        return ""
    result = run_command(["osascript", "-e", script], timeout=3)
    tty = result.get("stdout", "").strip()
    if not tty:
        return ""
    return cwd_from_tty(tty)


def cwd_from_tty(tty: str) -> str:
    tty_name = tty.removeprefix("/dev/")
    result = run_command(["ps", "-t", tty_name, "-o", "pid=", "-o", "stat="], timeout=3)
    target_pid = ""
    last_pid = ""
    for line in result.get("stdout", "").splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        last_pid = fields[0]
        if "+" in fields[1]:
            target_pid = fields[0]
    return cwd_from_pid(target_pid or last_pid)


def active_app_pid(bundle_id: str, app_name: str) -> str:
    if bundle_id:
        script = f'tell application "System Events" to get unix id of first process whose bundle identifier is "{escape_applescript(bundle_id)}"'
    elif app_name:
        script = f'tell application "System Events" to get unix id of first application process whose name is "{escape_applescript(app_name)}"'
    else:
        return ""
    result = run_command(["osascript", "-e", script], timeout=3)
    return result.get("stdout", "").strip()


def cwd_candidates_from_process_tree(root_pid: str) -> list[str]:
    pids = child_pids(root_pid)
    candidates: list[str] = []
    seen: set[str] = set()
    for pid in pids:
        cwd = cwd_from_pid(pid)
        if not cwd or cwd == "/" or cwd in seen:
            continue
        seen.add(cwd)
        candidates.append(cwd)
    return candidates


def child_pids(root_pid: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def collect(pid: str) -> None:
        if pid in seen:
            return
        seen.add(pid)
        result = run_command(["pgrep", "-P", pid], timeout=3)
        for child in result.get("stdout", "").split():
            found.append(child)
            collect(child)

    collect(root_pid)
    return found


def cwd_from_pid(pid: str) -> str:
    if not pid:
        return ""
    result = run_command(["lsof", "-p", pid, "-Fn"], timeout=5)
    lines = result.get("stdout", "").splitlines()
    for index, line in enumerate(lines):
        if line == "fcwd" and index + 1 < len(lines):
            path = lines[index + 1].removeprefix("n")
            if Path(path).is_dir():
                return path
    return ""


def workspace_from_window_title(title: str) -> str:
    for part in re.split(r"\s+[—-]\s+", title):
        part = part.strip().removeprefix("● ").strip()
        if part.startswith("~"):
            part = str(Path(part).expanduser())
        if part.startswith("/") and Path(part).is_dir():
            return part
    return ""


def git_diff_for_commit(repo_path: str) -> dict[str, Any]:
    staged_diff = git_diff(repo_path, staged=True)
    has_staged = bool(staged_diff)
    diff = staged_diff or git_diff(repo_path, staged=False)
    stats = git_numstat(repo_path, staged=has_staged)
    return {
        "repo_path": repo_path,
        "branch": git_branch(repo_path),
        "diff": diff,
        "has_staged": has_staged,
        "file_count": stats["file_count"],
        "insertions": stats["insertions"],
        "deletions": stats["deletions"],
        "files": changed_files_from_diff(diff),
    }


def git_diff(repo_path: str, *, staged: bool) -> str:
    args = ["git", "diff"]
    if staged:
        args.append("--staged")
    args.extend(["--no-color", "--no-ext-diff", "-U0", "--", "."])
    for pattern in DEFAULT_FILE_EXCLUDES:
        args.append(f":!{pattern}")
    return run_command(args, cwd=repo_path, timeout=10).get("stdout", "")


def git_numstat(repo_path: str, *, staged: bool) -> dict[str, int]:
    args = ["git", "diff", "--numstat"]
    if staged:
        args.append("--staged")
    args.extend(["--", "."])
    for pattern in DEFAULT_FILE_EXCLUDES:
        args.append(f":!{pattern}")
    output = run_command(args, cwd=repo_path, timeout=10).get("stdout", "")
    file_count = 0
    insertions = 0
    deletions = 0
    for line in output.splitlines():
        match = NUMSTAT_RE.match(line)
        if not match:
            continue
        file_count += 1
        if match.group(1).isdigit():
            insertions += int(match.group(1))
        if match.group(2).isdigit():
            deletions += int(match.group(2))
    return {"file_count": file_count, "insertions": insertions, "deletions": deletions}


def git_branch(repo_path: str) -> str:
    output = run_command(["git", "branch", "--show-current"], cwd=repo_path, timeout=5).get("stdout", "")
    return output.strip() or "unknown"


def changed_files_from_diff(diff: str) -> list[str]:
    seen: set[str] = set()
    files: list[str] = []
    for match in DIFF_FILE_RE.finditer(diff or ""):
        file_path = match.group(2)
        if file_path not in seen:
            seen.add(file_path)
            files.append(file_path)
    return files


def format_diff_for_prompt(diff_result: dict[str, Any]) -> str:
    diff = diff_result.get("diff", "")
    if len(diff) > MAX_DIFF_PROMPT_CHARS:
        file_list = "\n".join(f"- {path}" for path in diff_result.get("files", []))
        diff = diff[:MAX_DIFF_PROMPT_CHARS] + "\n\n... [diff truncated] ..."
        diff = f"Files in this diff:\n{file_list}\n\n{diff}"
    return "\n".join(
        [
            f"Branch: {diff_result.get('branch', '')}",
            f"Files changed: {diff_result.get('file_count', 0)} (+{diff_result.get('insertions', 0)}/-{diff_result.get('deletions', 0)})",
            "",
            "```diff",
            diff,
            "```",
        ]
    )


def generate_commit_message(diff_result: dict[str, Any]) -> str:
    load_local_env()
    api_key = os.environ.get("RADAR_LLM_API_KEY", "").strip()
    if api_key:
        generated = generate_commit_message_with_llm(diff_result, api_key)
        if generated:
            return cleanup_commit_message(generated)
    return heuristic_commit_message(diff_result)


def generate_commit_message_with_llm(diff_result: dict[str, Any], api_key: str) -> str:
    base_url = os.environ.get("RADAR_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("RADAR_LLM_MODEL", "gpt-4.1-mini")
    timeout = float(os.environ.get("RADAR_LLM_TIMEOUT_SECONDS", "60") or "60")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": DEFAULT_COMMIT_PROMPT},
            {"role": "user", "content": format_diff_for_prompt(diff_result)},
        ],
        "temperature": 0.2,
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError):
        return ""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def heuristic_commit_message(diff_result: dict[str, Any]) -> str:
    files = diff_result.get("files") or []
    branch = str(diff_result.get("branch") or "")
    prefix = ""
    jira = JIRA_KEY_RE.search(branch)
    if jira:
        prefix = f"[{jira.group(0)}] "

    commit_type = "chore"
    if files and all(is_doc_file(path) for path in files):
        commit_type = "docs"
    elif files and all(is_test_file(path) for path in files):
        commit_type = "test"
    elif any(is_build_file(path) for path in files):
        commit_type = "build"
    elif any(is_new_file_header(diff_result.get("diff", ""), path) for path in files):
        commit_type = "feat"
    elif diff_result.get("deletions", 0) > diff_result.get("insertions", 0) * 2:
        commit_type = "refactor"
    else:
        commit_type = "fix"

    scope = common_scope(files)
    subject = "update project changes"
    if files:
        subject = f"update {Path(files[0]).stem.replace('_', ' ').replace('-', ' ')}"
    head = f"{commit_type}{f'({scope})' if scope else ''}: {subject}"
    return cleanup_commit_message(prefix + head)


def is_doc_file(path: str) -> bool:
    return Path(path).suffix.lower() in {".md", ".mdx", ".txt", ".rst"}


def is_test_file(path: str) -> bool:
    lowered = path.lower()
    return "test" in lowered or "spec" in lowered


def is_build_file(path: str) -> bool:
    name = Path(path).name.lower()
    return name in {"package.json", "cargo.toml", "go.mod", "pom.xml", "build.gradle"}


def is_new_file_header(diff: str, path: str) -> bool:
    return f"diff --git a/{path} b/{path}" in diff and "new file mode" in diff


def common_scope(files: list[str]) -> str:
    if not files:
        return ""
    first_parts = [Path(path).parts[0] for path in files if Path(path).parts]
    if first_parts and all(part == first_parts[0] for part in first_parts):
        scope = re.sub(r"[^a-zA-Z0-9_-]+", "-", first_parts[0]).strip("-").lower()
        return scope[:20]
    return ""


def cleanup_commit_message(message: str) -> str:
    cleaned = str(message or "").strip().strip("\"'`")
    lines = [line.rstrip() for line in cleaned.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return "chore: update project changes"
    lines[0] = lines[0][:90]
    return "\n".join(lines)


def copy_text(text: str) -> dict[str, Any]:
    script = r'''
    on run argv
        set clipboardText to item 1 of argv
        set the clipboard to clipboardText
    end run
    '''
    result = run_command(["osascript", "-e", script, text], timeout=8)
    return {
        "success": result["returncode"] == 0,
        "stdout": result.get("stdout", "").strip(),
        "error": result.get("stderr", "").strip(),
    }


def run_command(command: list[str], *, cwd: str | None = None, timeout: float = 10) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"returncode": 1, "stdout": "", "stderr": str(error)}


def escape_applescript(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("\"", "\\\"")

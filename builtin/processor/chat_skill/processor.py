import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

import socketio


PROCESSOR_ID = "chat.skill"
DISPLAY_NAME = "Chat Skill Processor"
DEFAULT_FILTER_PROMPT = """You are Radar's cheap pre-filter for coding-skill distillation.

Decide whether this normalized code-agent chat session contains reusable coding-agent learning.
Keep sessions where the user corrected, constrained, clarified, or demonstrated a durable preference.
Skip ordinary one-off task execution with no reusable correction.

Return JSON only:
{"coding_related": true, "reason": "short reason"}"""

DEFAULT_EXTRACTION_PROMPT = """You are Radar, a Coding Memory distillation processor.

Distill only reusable learnings from the normalized code-agent chat session.
Focus on user corrections, repo/user preferences, missed bundled requirements, verification expectations,
tool/provider expectations, and durable workflow constraints.

Return JSON only:
{
  "entries": [
    {
      "scope": "global_user|project",
      "category": "communication|coding_style|architecture_preferences|debugging_workflow|testing_preferences|dependency_policy|anti_patterns|overview|tech_stack|architecture|coding_conventions|testing_conventions|api_patterns|data_model|error_handling|logging_observability|common_workflows|source_evidence",
      "title": "short title",
      "trigger": "when to apply",
      "lesson": "what future agents should do",
      "procedure": ["specific step"],
      "example": "compact example if useful",
      "source_evidence": ["session/message/tool ids or paths"]
    }
  ]
}"""

CODING_KEYWORDS = {
    "api",
    "bug",
    "build",
    "class",
    "cli",
    "code",
    "collector",
    "compile",
    "config",
    "debug",
    "deploy",
    "error",
    "file",
    "fix",
    "function",
    "git",
    "implementation",
    "lint",
    "module",
    "processor",
    "repo",
    "schema",
    "script",
    "service",
    "spec",
    "test",
    "type",
}

CORRECTION_KEYWORDS = {
    "actually",
    "carefully",
    "don't",
    "do not",
    "fix it",
    "forgot",
    "instead",
    "missing",
    "must",
    "no actor",
    "not",
    "should",
    "use",
    "wrong",
}

GLOBAL_CATEGORIES = {
    "communication",
    "coding_style",
    "architecture_preferences",
    "debugging_workflow",
    "testing_preferences",
    "dependency_policy",
    "anti_patterns",
}

PROJECT_CATEGORIES = {
    "overview",
    "tech_stack",
    "architecture",
    "coding_conventions",
    "testing_conventions",
    "dependency_policy",
    "api_patterns",
    "data_model",
    "error_handling",
    "logging_observability",
    "common_workflows",
    "anti_patterns",
    "source_evidence",
}

FILE_HINT_TOKEN_RE = re.compile(r"[A-Za-z0-9_./@+~-]+")
FILE_HINT_EXTENSIONS = {
    ".cjs",
    ".css",
    ".go",
    ".gql",
    ".graphql",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".mdx",
    ".mjs",
    ".proto",
    ".py",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
PATCH_FILE_RE = re.compile(r"(?m)^\*\*\* (?:Add|Update|Delete) File: ([^\n]+)$")
NON_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def now_ms():
    return int(time.time() * 1000)


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def radar_home():
    return Path(os.path.expandvars(os.environ.get("RADAR_HOME", "~/.radar"))).expanduser().resolve()


def short_hash(value, length=12):
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:length]


def safe_slug(value):
    value = NON_SLUG_RE.sub("-", str(value or "").strip().replace("/", "-").replace(":", "-"))
    value = value.strip("-._")
    if not value:
        return "unknown"
    if len(value) > 120:
        return value[:120] + "-" + short_hash(value)
    return value


def source_uri_to_path(uri):
    parsed = urlparse(uri or "")
    if parsed.scheme != "file":
        return ""
    return unquote(parsed.path)


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=True, indent=2, sort_keys=True)
        output.write("\n")


def read_json(path, default):
    try:
        with Path(path).open(encoding="utf-8") as input_file:
            return json.load(input_file)
    except (OSError, json.JSONDecodeError):
        return default


def any_from_json_string(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def append_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(text)


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as input_file:
        for line in input_file:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_collected_files(work_dir):
    root = Path(work_dir)
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.glob("**/*.jsonl")
        if "/state/" not in path.as_posix() and path.is_file()
    )


def discover_collector_work_dirs(collectors_root):
    root = Path(collectors_root)
    if not root.exists():
        return []
    if (root / "state").is_dir() or any(root.glob("**/*.jsonl")):
        return [root]
    return sorted(path for path in root.iterdir() if path.is_dir())


def text_preview(text, limit=6000):
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].strip() + "\n..."


def collect_file_hints(value):
    found = set()

    def visit(child):
        if child is None:
            return
        if isinstance(child, dict):
            for item in child.values():
                visit(item)
            return
        if isinstance(child, list):
            for item in child:
                visit(item)
            return
        text = str(child)
        parsed = any_from_json_string(text)
        if isinstance(parsed, (dict, list)):
            visit(parsed)
            return
        if len(text) > 200000:
            text = text[:200000]
        for match in PATCH_FILE_RE.findall(text):
            add_file_candidate(match)
        for match in FILE_HINT_TOKEN_RE.finditer(text):
            candidate = match.group(0)
            if has_file_extension(candidate):
                add_file_candidate(candidate)

    def has_file_extension(candidate):
        normalized = str(candidate or "").lower()
        return any(normalized.endswith(extension) for extension in FILE_HINT_EXTENSIONS)

    def add_file_candidate(candidate):
        candidate = str(candidate or "").strip().strip("`'\" ,;:()[]{}")
        if not candidate or "\n" in candidate or len(candidate) > 300:
            return
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return
        found.add(str(Path(candidate).as_posix()))

    visit(value)
    return sorted(found)


class NormalizedSession:
    def __init__(self, key):
        self.key = key
        self.source = ""
        self.session_id = ""
        self.cwd = ""
        self.model = ""
        self.source_uri = ""
        self.source_fingerprint = ""
        self.source_fingerprints = set()
        self.started_at = 0
        self.updated_at = 0
        self.messages = []
        self.tool_details = {}
        self.source_refs = []
        self.files_touched = set()
        self._seen_messages = set()
        self._seen_tool_refs = set()
        self._seen_source_refs = set()

    def fingerprint(self):
        fingerprints = sorted(self.source_fingerprints)
        if not fingerprints and self.source_fingerprint:
            fingerprints = [self.source_fingerprint]
        fingerprint_payload = {
            "key": self.key,
            "source_uri": self.source_uri,
            "source_fingerprints": fingerprints,
            "message_ids": sorted(
                str(message.get("id") or "")
                for message in self.messages
                if message.get("id")
            ),
            "tool_ids": sorted(str(tool_id) for tool_id in self.tool_details),
        }
        return short_hash(json.dumps(fingerprint_payload, sort_keys=True), 40)

    def to_dict(self):
        return {
            "key": self.key,
            "source": self.source,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "model": self.model,
            "source_uri": self.source_uri,
            "source_fingerprint": self.source_fingerprint,
            "source_fingerprints": sorted(self.source_fingerprints),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "tool_details": self.tool_details,
            "files_touched": sorted(self.files_touched),
            "source_refs": self.source_refs,
        }


def event_is_transcript_like(event):
    if event.get("collector_id") == "chat.transcript":
        return True
    subject = event.get("subject") or {}
    chat = (event.get("extra_data") or {}).get("chat")
    return subject.get("kind") in {
        "chat_session_summary",
        "chat_message",
        "chat_tool_call",
        "chat_tool_result",
        "normalized_chat_session",
    } or isinstance(chat, dict)


def load_sessions_from_work_dirs(work_dirs):
    sessions = {}
    for work_dir in work_dirs:
        for path in iter_collected_files(work_dir):
            for event in read_jsonl(path):
                if not event_is_transcript_like(event):
                    continue
                provenance = event.get("provenance") or {}
                session_key = provenance.get("session_key")
                if not session_key:
                    continue
                session = sessions.setdefault(session_key, NormalizedSession(session_key))
                source = event.get("source") or {}
                subject = event.get("subject") or {}
                chat = (event.get("extra_data") or {}).get("chat") or {}
                session.source = source.get("app") or session.source
                session.session_id = provenance.get("session_id") or chat.get("session_id") or session.session_id
                session.cwd = chat.get("cwd") or session.cwd
                session.model = chat.get("model") or session.model
                session.source_uri = provenance.get("source_uri") or session.source_uri
                session.source_fingerprint = provenance.get("source_fingerprint") or session.source_fingerprint
                if provenance.get("source_fingerprint"):
                    session.source_fingerprints.add(provenance["source_fingerprint"])
                observed_at = (event.get("time") or {}).get("observed_at") or 0
                if observed_at and not session.started_at:
                    session.started_at = observed_at
                if observed_at and observed_at > session.updated_at:
                    session.updated_at = observed_at
                source_ref = {
                    "collector_id": event.get("collector_id"),
                    "event_id": event.get("id"),
                    "source_uri": provenance.get("source_uri"),
                    "source_fingerprint": provenance.get("source_fingerprint"),
                    "session_key": session_key,
                    "source_message_ids": provenance.get("source_message_ids", []),
                    "tool_ids": provenance.get("tool_ids", []),
                }
                source_ref_key = json.dumps(
                    {
                        "collector_id": source_ref["collector_id"],
                        "source_uri": source_ref["source_uri"],
                        "source_fingerprint": source_ref["source_fingerprint"],
                        "source_message_ids": source_ref["source_message_ids"],
                        "tool_ids": source_ref["tool_ids"],
                    },
                    sort_keys=True,
                )
                if source_ref_key not in session._seen_source_refs:
                    session._seen_source_refs.add(source_ref_key)
                    session.source_refs.append(source_ref)
                kind = subject.get("kind") or chat.get("kind")
                if kind == "chat_session_summary":
                    session.cwd = chat.get("cwd") or session.cwd
                    session.model = chat.get("model") or session.model
                    session.started_at = chat.get("started_at") or session.started_at
                    session.updated_at = chat.get("updated_at") or session.updated_at
                    continue
                if kind == "chat_message":
                    message = {
                        "id": (provenance.get("source_message_ids") or [event.get("anchor", {}).get("id")])[0],
                        "role": chat.get("role", "unknown"),
                        "timestamp": observed_at,
                        "text": (event.get("content") or {}).get("text", ""),
                        "thinking": chat.get("thinking", ""),
                        "source_ref": source_ref,
                    }
                    message_key = json.dumps(
                        {
                            "id": message["id"],
                            "role": message["role"],
                            "text": message["text"],
                            "thinking": message["thinking"],
                        },
                        sort_keys=True,
                    )
                    if message_key in session._seen_messages:
                        continue
                    session._seen_messages.add(message_key)
                    session.messages.append(message)
                    continue
                if kind in {"chat_tool_call", "chat_tool_result"}:
                    tool_id = chat.get("tool_id") or (provenance.get("tool_ids") or [event.get("anchor", {}).get("id")])[0]
                    detail = session.tool_details.setdefault(
                        tool_id,
                        {
                            "tool_id": tool_id,
                            "name": "",
                            "input": None,
                            "output": None,
                            "status": "",
                            "source_refs": [],
                        },
                    )
                    detail["name"] = chat.get("tool_name") or detail.get("name") or ""
                    if kind == "chat_tool_call":
                        detail["input"] = chat.get("tool_input")
                    else:
                        detail["output"] = chat.get("tool_output")
                        if chat.get("is_error"):
                            detail["status"] = "error"
                    tool_ref_key = json.dumps(
                        {
                            "kind": kind,
                            "tool_id": tool_id,
                            "source_uri": source_ref["source_uri"],
                            "source_fingerprint": source_ref["source_fingerprint"],
                            "tool_ids": source_ref["tool_ids"],
                        },
                        sort_keys=True,
                    )
                    if tool_ref_key not in session._seen_tool_refs:
                        session._seen_tool_refs.add(tool_ref_key)
                        detail["source_refs"].append(source_ref)
                    for file_path in collect_file_hints(detail.get("input")) + collect_file_hints(detail.get("output")):
                        session.files_touched.add(file_path)

    for session in sessions.values():
        session.messages.sort(key=lambda item: item.get("timestamp") or 0)
        session.source_refs = unique_source_refs(session.source_refs)
    return sessions


def load_sessions(work_dir):
    return load_sessions_from_work_dirs([Path(work_dir)])


def unique_source_refs(refs):
    seen = set()
    out = []
    for ref in refs:
        key = json.dumps(ref, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def find_git_root(start):
    if not start:
        return ""
    cur = Path(start).expanduser()
    try:
        cur = cur.resolve()
    except OSError:
        return ""
    while True:
        if (cur / ".git").exists():
            return str(cur)
        if cur.parent == cur:
            return ""
        cur = cur.parent


def project_id_for_session(session):
    root = find_git_root(session.cwd)
    if root:
        return safe_slug(root)
    if session.cwd:
        return safe_slug(session.cwd)
    return "unknown-project"


def structural_filter(session):
    text = "\n".join(message.get("text", "") for message in session.messages).lower()
    has_coding_keyword = any(keyword in text for keyword in CODING_KEYWORDS)
    has_correction_keyword = any(keyword in text for keyword in CORRECTION_KEYWORDS)
    has_tools = bool(session.tool_details)
    has_files = bool(session.files_touched)
    coding_related = (has_coding_keyword and (has_correction_keyword or has_tools or has_files)) or has_files
    reason = []
    if has_coding_keyword:
        reason.append("coding keywords")
    if has_correction_keyword:
        reason.append("correction or constraint language")
    if has_tools:
        reason.append("tool usage")
    if has_files:
        reason.append("file references")
    return coding_related, ", ".join(reason) or "no coding signal"


def compact_session(session):
    messages = []
    for message in session.messages:
        if message.get("role") in {"system", "developer"}:
            continue
        messages.append(
            {
                "id": message.get("id"),
                "role": message.get("role"),
                "timestamp": message.get("timestamp"),
                "text": text_preview(message.get("text"), 6000),
                "thinking": text_preview(message.get("thinking"), 2000),
            }
        )
    tool_summary = []
    for tool_id, detail in session.tool_details.items():
        tool_summary.append(
            {
                "tool_id": tool_id,
                "name": detail.get("name"),
                "status": detail.get("status"),
                "files_touched": sorted(set(collect_file_hints(detail.get("input")) + collect_file_hints(detail.get("output")))),
            }
        )
    return {
        "key": session.key,
        "source": session.source,
        "session_id": session.session_id,
        "cwd": session.cwd,
        "model": session.model,
        "source_uri": session.source_uri,
        "source_fingerprint": session.source_fingerprint,
        "files_touched": sorted(session.files_touched),
        "messages": messages,
        "tool_summary": tool_summary,
    }


def llm_config(args, tier):
    if tier == "filter":
        return {
            "api_key": args.filter_llm_api_key,
            "base_url": args.filter_llm_base_url,
            "model": args.filter_llm_model,
            "timeout": args.filter_llm_timeout,
        }
    if tier == "extraction":
        return {
            "api_key": args.extraction_llm_api_key,
            "base_url": args.extraction_llm_base_url,
            "model": args.extraction_llm_model,
            "timeout": args.extraction_llm_timeout,
        }
    raise ValueError(f"unknown llm tier: {tier}")


def llm_enabled(args, tier):
    config = llm_config(args, tier)
    return bool(config["api_key"] and config["base_url"] and config["model"])


def call_llm_json(args, tier, system_prompt, user_payload):
    config = llm_config(args, tier)
    url = config["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return parse_json_object(content)


def parse_json_object(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def llm_filter(args, session):
    payload = json.dumps(compact_session(session), ensure_ascii=True, indent=2)
    try:
        parsed = call_llm_json(
            args,
            "filter",
            args.filter_prompt,
            f"<code_session>\n{payload}\n</code_session>",
        )
    except Exception as error:
        return True, f"llm filter failed; keeping session: {error}"
    return bool(parsed.get("coding_related") or parsed.get("codingRelated")), parsed.get("reason", "")


def fallback_entries(session, filter_reason):
    user_messages = [m for m in session.messages if m.get("role") == "user" and m.get("text")]
    title_source = user_messages[-1]["text"] if user_messages else session.key
    project_id = project_id_for_session(session)
    evidence = source_evidence(session)
    text = "\n".join(m.get("text", "") for m in user_messages).lower()
    category = "common_workflows"
    scope = "project"
    if any(word in text for word in ["test", "verify", "passing"]):
        category = "testing_conventions"
    if any(word in text for word in ["don't", "do not", "wrong", "instead", "fix it"]):
        category = "anti_patterns"
    if not session.cwd:
        scope = "global_user"
        category = "debugging_workflow"
    return [
        {
            "scope": scope,
            "project_id": project_id,
            "category": category,
            "title": truncate_line(title_source, 80),
            "trigger": "A future coding-agent task resembles this transcript or touches the same source files.",
            "lesson": "Review the source evidence before acting; this session contained reusable coding workflow signals.",
            "procedure": [
                "Load the source evidence and inspect referenced files or tool calls before turning the transcript into a stronger rule.",
                f"Apply the filter reason: {filter_reason}.",
            ],
            "example": truncate_line(title_source, 200),
            "source_evidence": evidence,
        }
    ]


def llm_entries(args, session):
    payload = json.dumps(compact_session(session), ensure_ascii=True, indent=2)
    try:
        parsed = call_llm_json(
            args,
            "extraction",
            args.extraction_prompt,
            f"<code_session>\n{payload}\n</code_session>",
        )
    except Exception as error:
        return [], str(error)
    entries = parsed.get("entries")
    if not isinstance(entries, list):
        return [], "llm extraction returned no entries"
    normalized = []
    project_id = project_id_for_session(session)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        scope = entry.get("scope") if entry.get("scope") in {"global_user", "project"} else "project"
        category = safe_category(scope, entry.get("category"))
        if not category:
            continue
        normalized.append(
            {
                "scope": scope,
                "project_id": project_id,
                "category": category,
                "title": truncate_line(entry.get("title") or session.key, 80),
                "trigger": str(entry.get("trigger") or ""),
                "lesson": str(entry.get("lesson") or ""),
                "procedure": entry.get("procedure") if isinstance(entry.get("procedure"), list) else [],
                "example": str(entry.get("example") or ""),
                "source_evidence": entry.get("source_evidence") if isinstance(entry.get("source_evidence"), list) else source_evidence(session),
            }
        )
    return normalized, ""


def safe_category(scope, category):
    category = safe_slug(category or "").replace("-", "_")
    if scope == "global_user":
        return category if category in GLOBAL_CATEGORIES else "debugging_workflow"
    return category if category in PROJECT_CATEGORIES else "common_workflows"


def truncate_line(text, limit):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].strip() + "..."


def source_evidence(session):
    evidence = [
        f"Session: {session.key}",
        f"Source: {session.source_uri}",
        f"Fingerprint: {session.source_fingerprint}",
    ]
    for message in session.messages[:6]:
        if message.get("id"):
            evidence.append(f"Message: {message['id']}")
    for tool_id in list(session.tool_details.keys())[:6]:
        evidence.append(f"Tool: {tool_id}")
    for file_path in sorted(session.files_touched)[:12]:
        evidence.append(f"File: {file_path}")
    return evidence


def ensure_skill_folder(skill_dir):
    skill_dir = Path(skill_dir)
    (skill_dir / "global_user").mkdir(parents=True, exist_ok=True)
    (skill_dir / "projects").mkdir(parents=True, exist_ok=True)
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    write_skill_index(skill_dir)
    write_references(skill_dir)


def write_skill_index(skill_dir):
    content = """---
name: Radar Coding Memory
description: Use before coding, debugging, testing, reviewing, or editing a repository to load user-level and repo-specific lessons distilled by Radar.
source: radar
---

# Radar Coding Memory

Use this skill before coding, debugging, testing, reviewing, or modifying files. It contains Radar-distilled lessons from previous Codex and Claude sessions.

## Usage Workflow

1. Resolve the current git root for the task.
2. Read relevant files under `global_user/` for cross-repo user preferences.
3. Find the matching project folder under `projects/<project_id>/`.
4. Read only category files that match the current task.
5. Treat entries as learned guidance, not canonical source documentation. Verify against the repo when unclear.

## Boundaries

- Current explicit user instructions win over stored memory.
- Do not recursively load every project memory file.
- Keep source evidence compact and traceable.
"""
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    write_json(skill_dir / ".meta.json", {"source": "radar", "updated_at": utc_timestamp()})


def write_references(skill_dir):
    references = {
        "extraction-playbook.md": "# Extraction Playbook\n\nExtract only durable coding-agent learnings. Prefer trigger, lesson, procedure, example, and source evidence.\n",
        "quality-rubric.md": "# Quality Rubric\n\nKeep entries that are durable, triggerable, actionable, specific, evidence-backed, and token-conscious.\n",
    }
    for name, content in references.items():
        path = skill_dir / "references" / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    write_json(skill_dir / "references" / ".radar.json", {"managed_by": "radar", "updated_at": utc_timestamp()})


def write_entries(skill_dir, entries):
    changed = []
    for entry in entries:
        if entry["scope"] == "global_user":
            path = Path(skill_dir) / "global_user" / f"{entry['category']}.md"
            title = entry["category"].replace("_", " ").title()
        else:
            project_dir = Path(skill_dir) / "projects" / safe_slug(entry.get("project_id"))
            project_dir.mkdir(parents=True, exist_ok=True)
            write_json(project_dir / ".radar.json", {"scope": "project", "project_id": safe_slug(entry.get("project_id")), "updated_at": utc_timestamp()})
            path = project_dir / f"{entry['category']}.md"
            title = entry["category"].replace("_", " ").title()
        if not path.exists():
            path.write_text(f"# {title}\n\n", encoding="utf-8")
        append_text(path, render_entry(entry))
        changed.append(str(path))
    return sorted(set(changed))


def render_entry(entry):
    lines = [
        f"## {entry['title'] or 'Untitled learning'}",
        "",
        f"- Trigger: {entry.get('trigger') or 'Review source evidence before applying.'}",
        f"- Lesson: {entry.get('lesson') or 'Use the source evidence to guide future coding-agent behavior.'}",
    ]
    procedure = entry.get("procedure") or []
    if procedure:
        lines.append("- Procedure:")
        for step in procedure:
            lines.append(f"  - {step}")
    if entry.get("example"):
        lines.append(f"- Example: {entry['example']}")
    evidence = entry.get("source_evidence") or []
    if evidence:
        lines.append("- Source Evidence:")
        for item in evidence:
            lines.append(f"  - {item}")
    lines.append("")
    return "\n".join(lines) + "\n"


def processor_state_path(args):
    return Path(args.radar_home) / "processors" / "chat_skill" / "state.json"


def normalized_session_path(args, session):
    return Path(args.radar_home) / "processors" / "chat_skill" / "normalized" / f"{session.key}.json"


def load_state(args):
    return read_json(processor_state_path(args), {"version": 1, "processed": {}})


def save_state(args, state):
    write_json(processor_state_path(args), state)


def process_sessions(args, sessions, input_refs):
    ensure_skill_folder(args.skill_dir)
    state = load_state(args)
    results = []
    for session in sessions.values():
        if not args.force:
            processed = state["processed"].get(session.key)
            if processed and processed.get("fingerprint") == session.fingerprint():
                continue
        source_ok = session.source_uri and session.source_fingerprint
        structural_ok, structural_reason = structural_filter(session)
        filter_reason = structural_reason
        llm_ok = True
        if source_ok and structural_ok and llm_enabled(args, "filter"):
            llm_ok, filter_reason = llm_filter(args, session)
        entries = []
        extraction_error = ""
        if source_ok and structural_ok and llm_ok:
            if llm_enabled(args, "extraction"):
                entries, extraction_error = llm_entries(args, session)
            if not entries:
                entries = fallback_entries(session, filter_reason or structural_reason)
        changed_files = write_entries(args.skill_dir, entries) if entries else []
        write_json(normalized_session_path(args, session), session.to_dict())
        status = "extracted" if changed_files else "skipped"
        if not source_ok:
            status = "skipped"
            filter_reason = "missing source provenance"
        elif not structural_ok:
            status = "skipped"
        result = {
            "id": str(uuid.uuid4()),
            "processor_id": PROCESSOR_ID,
            "input_refs": [str(item) for item in input_refs],
            "source_refs": session.source_refs[:20],
            "kind": "coding_memory_update",
            "created_at": utc_timestamp(),
            "confidence": 0.65 if changed_files else 0.2,
            "privacy": {
                "contains_raw_content": True,
                "redaction_applied": False,
            },
            "payload": {
                "session_key": session.key,
                "status": status,
                "filter_reason": filter_reason,
                "extraction_error": extraction_error,
                "entries": len(entries),
                "changed_files": changed_files,
                "skill_dir": str(args.skill_dir),
            },
        }
        results.append(result)
        state["processed"][session.key] = {
            "fingerprint": session.fingerprint(),
            "status": status,
            "entries": len(entries),
            "changed_files": changed_files,
            "updated_at": now_ms(),
        }
    state["last_completed_at"] = now_ms()
    save_state(args, state)
    return results


def process_work_dirs(args, work_dirs):
    work_dirs = [Path(path) for path in work_dirs]
    sessions = load_sessions_from_work_dirs(work_dirs)
    return process_sessions(args, sessions, work_dirs)


def process_work_dir(args, work_dir):
    return process_work_dirs(args, [work_dir])


def process_collectors_root(args):
    work_dirs = discover_collector_work_dirs(args.collectors_root)
    return process_work_dirs(args, work_dirs)


def register_processor(client):
    return client.call(
        "processor:register",
        {
            "processor_id": PROCESSOR_ID,
            "protocol_version": 1,
            "accepts": ["collector_work_dir", "collector_file"],
            "produces": ["coding_memory_update"],
            "output_targets": ["skill"],
            "capabilities": ["chat_transcript_filtering", "coding_memory_extraction", "skill_writer"],
            "metadata": {"display_name": DISPLAY_NAME},
        },
        timeout=5,
    )


def connect_client(client, coordinator_url, timeout):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            client.connect(coordinator_url)
            return
        except socketio.exceptions.ConnectionError as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"could not connect to coordinator: {last_error}")


def run_socket_processor(args):
    client = socketio.Client()

    @client.on("coordinator:collector_registered")
    def on_collector_registered(payload):
        work_dir = (payload or {}).get("work_dir")
        if not work_dir:
            return
        results = process_work_dir(args, work_dir)
        for result in results:
            client.call("processor:result", result, timeout=5)

    @client.on("coordinator:process_request")
    def on_process_request(payload):
        target = (payload or {}).get("work_dir") or (payload or {}).get("path")
        if not target:
            return
        results = process_work_dir(args, target)
        for result in results:
            client.call("processor:result", result, timeout=5)

    connect_client(client, args.coordinator_url, args.connect_timeout)
    try:
        registration = register_processor(client)
        if not registration.get("ok"):
            raise RuntimeError(f"processor registration failed: {registration}")
        print(f"registered {PROCESSOR_ID}")
        deadline = time.monotonic() + max(args.duration, 0)
        while args.duration <= 0 or time.monotonic() < deadline:
            client.call("processor:heartbeat", {"status": "ok"}, timeout=5)
            time.sleep(max(args.heartbeat_interval, 0.1))
    finally:
        client.disconnect()


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args():
    home = radar_home()
    legacy_api_key = os.environ.get("RADAR_LLM_API_KEY", "")
    legacy_base_url = os.environ.get("RADAR_LLM_BASE_URL", "https://api.openai.com/v1")
    legacy_model = os.environ.get("RADAR_LLM_MODEL", "gpt-4.1-mini")
    legacy_timeout = env_float("RADAR_LLM_TIMEOUT_SECONDS", 60)
    parser = argparse.ArgumentParser(description="Process chat transcript collector output into Radar Coding Memory.")
    parser.add_argument("--radar-home", default=str(home))
    parser.add_argument("--collectors-root", default=os.environ.get("RADAR_COLLECTORS_ROOT", str(home / "collectors")))
    parser.add_argument("--collector-work-dir", default=os.environ.get("RADAR_COLLECTOR_WORK_DIR", ""))
    parser.add_argument("--skill-dir", default=os.environ.get("RADAR_SKILL_DIR", str(home / "skill")))
    parser.add_argument("--coordinator-url", default=os.environ.get("RADAR_COORDINATOR_URL", "http://localhost:5000"))
    parser.add_argument("--duration", type=float, default=env_float("RADAR_CHAT_SKILL_PROCESSOR_DURATION", 10))
    parser.add_argument("--heartbeat-interval", type=float, default=env_float("RADAR_CHAT_SKILL_PROCESSOR_HEARTBEAT_INTERVAL", 2))
    parser.add_argument("--connect-timeout", type=float, default=env_float("RADAR_PROCESSOR_CONNECT_TIMEOUT", 10))
    parser.add_argument("--filter-llm-api-key", default=os.environ.get("RADAR_FILTER_LLM_API_KEY", legacy_api_key))
    parser.add_argument("--filter-llm-base-url", default=os.environ.get("RADAR_FILTER_LLM_BASE_URL", legacy_base_url))
    parser.add_argument("--filter-llm-model", default=os.environ.get("RADAR_FILTER_LLM_MODEL", legacy_model))
    parser.add_argument("--filter-llm-timeout", type=float, default=env_float("RADAR_FILTER_LLM_TIMEOUT_SECONDS", legacy_timeout))
    parser.add_argument("--extraction-llm-api-key", default=os.environ.get("RADAR_EXTRACTION_LLM_API_KEY", legacy_api_key))
    parser.add_argument("--extraction-llm-base-url", default=os.environ.get("RADAR_EXTRACTION_LLM_BASE_URL", legacy_base_url))
    parser.add_argument("--extraction-llm-model", default=os.environ.get("RADAR_EXTRACTION_LLM_MODEL", os.environ.get("RADAR_LLM_EXTRACTION_MODEL", "gpt-4.1")))
    parser.add_argument("--extraction-llm-timeout", type=float, default=env_float("RADAR_EXTRACTION_LLM_TIMEOUT_SECONDS", legacy_timeout))
    parser.add_argument("--filter-prompt", default=os.environ.get("RADAR_CHAT_SKILL_FILTER_PROMPT", DEFAULT_FILTER_PROMPT))
    parser.add_argument("--extraction-prompt", default=os.environ.get("RADAR_CHAT_SKILL_EXTRACTION_PROMPT", DEFAULT_EXTRACTION_PROMPT))
    parser.add_argument("--force", action="store_true", default=env_bool("RADAR_CHAT_SKILL_FORCE", False))
    parser.add_argument("--once", action="store_true", help="Run once over discovered collector data without Socket.IO.")
    return parser.parse_args()


def main():
    args = parse_args()
    args.radar_home = Path(args.radar_home).expanduser().resolve()
    args.skill_dir = Path(args.skill_dir).expanduser().resolve()
    args.collectors_root = Path(args.collectors_root).expanduser().resolve()
    if args.once:
        if args.collector_work_dir:
            results = process_work_dir(args, args.collector_work_dir)
        else:
            results = process_collectors_root(args)
        print(json.dumps({"ok": True, "results": results}, indent=2, sort_keys=True))
        return
    run_socket_processor(args)


if __name__ == "__main__":
    main()

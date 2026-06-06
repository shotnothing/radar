---
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

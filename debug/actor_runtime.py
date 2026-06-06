from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def epoch_ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def safe_file_stem(value: str) -> str:
    return value.replace(".", "_").replace("/", "_")


@dataclass(frozen=True)
class ScriptResult:
    output: dict[str, Any]
    stdout: str
    stderr: str
    exit_code: int


class ActorRuntime:
    """Debug host for script-backed actor packages."""

    def __init__(
        self,
        *,
        actor_paths: list[str | Path],
        state_root: str | Path,
        context_provider: Callable[[], dict[str, Any]],
        api_url: str,
        api_token: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.actor_paths = [Path(path).expanduser().resolve() for path in actor_paths]
        self.state_root = Path(state_root).expanduser().resolve()
        self.context_provider = context_provider
        self.api_url = api_url
        self.api_token = api_token
        self.progress_callback = progress_callback
        self.actors: dict[str, dict[str, Any]] = {}
        self.actor_dirs: dict[str, Path] = {}
        self.pending_actions: dict[str, dict[str, Any]] = {}
        self.records: list[dict[str, Any]] = []

    def refresh(self) -> None:
        actors: dict[str, dict[str, Any]] = {}
        actor_dirs: dict[str, Path] = {}
        for root in self.actor_paths:
            if root.is_file():
                candidates = [root]
            elif (root / "manifest.json").exists():
                candidates = [root / "manifest.json"]
            elif root.exists():
                candidates = sorted(root.glob("*/manifest.json"))
            else:
                candidates = []

            for manifest_path in candidates:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                actor_id = manifest.get("actor_id")
                if not isinstance(actor_id, str) or not actor_id:
                    continue
                actors[actor_id] = manifest
                actor_dirs[actor_id] = manifest_path.parent

        self.actors = actors
        self.actor_dirs = actor_dirs

    def list_actors(self) -> list[dict[str, Any]]:
        return [
            {
                "actor_id": actor_id,
                "path": str(self.actor_dirs[actor_id]),
                "title": manifest.get("title", actor_id),
                "enabled": bool(manifest.get("enabled", True)),
                "activation": manifest.get("activation", {}),
                "trigger": manifest.get("trigger", {}),
            }
            for actor_id, manifest in sorted(self.actors.items())
        ]

    def get_actor(self, actor_id: str) -> dict[str, Any]:
        actor = self.actors.get(actor_id)
        if actor is None:
            raise KeyError(f"unknown actor: {actor_id}")
        return actor

    def should_trigger(
        self,
        actor_id: str,
        *,
        context_override: dict[str, Any] | None = None,
        ignore_filters: bool = False,
    ) -> dict[str, Any]:
        manifest = self.get_actor(actor_id)
        if not manifest.get("enabled", True):
            return {"available": False, "reason": "actor disabled"}

        context = context_override or self.context_provider()
        trigger = manifest.get("trigger", {})
        if not ignore_filters and not self._filters_match(trigger.get("filters", {}), context):
            return {
                "available": False,
                "reason": "filters did not match current context",
                "filtered": True,
                "input": self._build_should_trigger_input(actor_id, manifest, context),
            }

        script = trigger.get("should_trigger")
        if not isinstance(script, dict):
            return {"available": True, "reason": "no should_trigger script configured"}

        input_payload = self._build_should_trigger_input(actor_id, manifest, context)
        result = self._run_script(actor_id, script, input_payload)
        output = result.output
        self._apply_state_update(actor_id, output.get("state_update"))

        if output.get("available"):
            trigger_id = str(uuid.uuid4())
            request = self._build_action_request(
                actor_id,
                manifest,
                trigger_id=trigger_id,
                action_context=ensure_dict(output.get("action_context")),
                context=input_payload,
                presentation=ensure_dict(output.get("presentation")),
            )
            self.pending_actions[trigger_id] = request
            output["trigger_id"] = trigger_id
            output["action_request"] = request
            self._record(
                {
                    "actor_id": actor_id,
                    "trigger_id": trigger_id,
                    "status": "available",
                    "reason": output.get("reason", ""),
                    "created_at": epoch_ms_now(),
                }
            )
        return output

    def run_action(
        self,
        actor_id: str,
        *,
        trigger_id: str | None = None,
        action_context: dict[str, Any] | None = None,
        user_action: dict[str, Any] | None = None,
        context_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest = self.get_actor(actor_id)
        request = self.pending_actions.get(trigger_id or "") if trigger_id else None
        if request is None:
            context = self._build_should_trigger_input(
                actor_id,
                manifest,
                context_override or self.context_provider(),
            )
            trigger_id = trigger_id or str(uuid.uuid4())
            request = self._build_action_request(
                actor_id,
                manifest,
                trigger_id=trigger_id,
                action_context=action_context or {},
                context=context,
                presentation={},
                user_action=user_action,
            )

        progress = {
            "id": str(uuid.uuid4()),
            "actor_id": actor_id,
            "request_id": request["id"],
            "status": "running",
            "created_at": iso_now(),
            "progress": {
                "label": "Running action script",
            },
        }
        self._emit_progress(progress)

        result = self._run_script(actor_id, request["script"], request["input"])
        output = result.output
        self._apply_state_update(actor_id, output.get("state_update"))
        status = "completed" if output.get("success", result.exit_code == 0) else "failed"
        actor_result = {
            "id": str(uuid.uuid4()),
            "actor_id": actor_id,
            "request_id": request["id"],
            "status": status,
            "created_at": iso_now(),
            "payload": {
                "summary": output.get("message", ""),
                "artifacts": output.get("artifacts", []),
                "output": output,
                "stderr": result.stderr,
            },
        }
        self._record(actor_result)
        if trigger_id:
            self.pending_actions.pop(trigger_id, None)
        return actor_result

    def _build_should_trigger_input(
        self,
        actor_id: str,
        manifest: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "timestamp": epoch_ms_now(),
            "actor_id": actor_id,
            "polling_interval_seconds": ensure_dict(manifest.get("trigger")).get(
                "polling_interval_seconds",
                10,
            ),
            "active_context": ensure_dict(context.get("active_context")),
            "browser": ensure_dict(context.get("browser")),
            "state": self._load_state(actor_id),
        }

    def _build_action_request(
        self,
        actor_id: str,
        manifest: dict[str, Any],
        *,
        trigger_id: str,
        action_context: dict[str, Any],
        context: dict[str, Any],
        presentation: dict[str, Any],
        user_action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = ensure_dict(manifest.get("action"))
        title = presentation.get("title") or manifest.get("title") or actor_id
        label = (
            ensure_dict(manifest.get("activation")).get("button_label")
            or presentation.get("button_label")
            or "Run"
        )
        return {
            "id": str(uuid.uuid4()),
            "actor_id": actor_id,
            "title": title,
            "activation_mode": ensure_dict(manifest.get("activation")).get("mode", "manual"),
            "script": {
                "command": action.get("command", []),
                "cwd": str(self.actor_dirs[actor_id]),
                "timeout_seconds": action.get("timeout_seconds", 300),
            },
            "input": {
                "timestamp": epoch_ms_now(),
                "trigger_id": trigger_id,
                "user_action": user_action
                or {
                    "action_id": "run",
                    "action_label": label,
                },
                "active_context": context.get("active_context", {}),
                "browser": context.get("browser", {}),
                "action_context": action_context,
                "state": self._load_state(actor_id),
            },
        }

    def _run_script(
        self,
        actor_id: str,
        script: dict[str, Any],
        input_payload: dict[str, Any],
    ) -> ScriptResult:
        command = script.get("command")
        if not isinstance(command, list) or not command:
            raise ValueError(f"actor {actor_id} script is missing command")

        cwd = Path(script.get("cwd") or self.actor_dirs[actor_id])
        timeout_seconds = float(script.get("timeout_seconds", 30))
        env = os.environ.copy()
        env.update(
            {
                "RADAR_ACTOR_ID": actor_id,
                "RADAR_ACTOR_DIR": str(self.actor_dirs[actor_id]),
                "RADAR_API_URL": self.api_url,
                "RADAR_API_TOKEN": self.api_token,
            }
        )

        result = subprocess.run(
            [str(part) for part in command],
            input=json.dumps(input_payload),
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=timeout_seconds if timeout_seconds > 0 else None,
            check=False,
        )
        output = parse_last_json_object(result.stdout)
        if result.returncode != 0 and not output:
            output = {
                "success": False,
                "available": False,
                "message": result.stderr.strip(),
                "error": result.stderr.strip(),
            }
        return ScriptResult(
            output=output,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )

    def _filters_match(self, filters: dict[str, Any], context: dict[str, Any]) -> bool:
        if not filters:
            return True
        active_context = ensure_dict(context.get("active_context"))
        browser = ensure_dict(context.get("browser"))
        active_tab = ensure_dict(browser.get("active_tab"))
        app_values = [
            str(active_context.get("app_name", "")),
            str(active_context.get("bundle_id", "")),
        ]
        app_patterns = filters.get("app_patterns") or []
        if app_patterns and not any(
            fnmatch.fnmatchcase(value, pattern)
            for value in app_values
            for pattern in app_patterns
        ):
            return False

        url = str(active_tab.get("url") or active_context.get("document_path") or "")
        url_patterns = filters.get("url_patterns") or []
        if url_patterns and not any(fnmatch.fnmatchcase(url, pattern) for pattern in url_patterns):
            return False
        return True

    def _load_state(self, actor_id: str) -> dict[str, Any]:
        path = self._state_path(actor_id)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        state.setdefault("custom_data", {})
        return state

    def _save_state(self, actor_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(actor_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(path)

    def _state_path(self, actor_id: str) -> Path:
        return self.state_root / safe_file_stem(actor_id) / "state.json"

    def _apply_state_update(self, actor_id: str, state_update: Any) -> None:
        if not isinstance(state_update, dict):
            return
        state = self._load_state(actor_id)
        custom_data = ensure_dict(state.get("custom_data"))
        custom_data.update(ensure_dict(state_update.get("custom_data")))
        state["custom_data"] = custom_data
        now = epoch_ms_now()
        if state_update.get("last_available_at"):
            state["last_available_at"] = state_update["last_available_at"]
        if state_update.get("last_action_at"):
            state["last_action_at"] = state_update["last_action_at"]
        state.setdefault("updated_at", now)
        self._save_state(actor_id, state)

    def _emit_progress(self, payload: dict[str, Any]) -> None:
        if self.progress_callback is not None:
            self.progress_callback(payload)

    def _record(self, payload: dict[str, Any]) -> None:
        self.records.append(payload)
        if len(self.records) > 200:
            self.records = self.records[-200:]


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_last_json_object(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    stripped = stdout.strip()
    if stripped:
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return {}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

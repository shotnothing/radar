import argparse
import atexit
import json
import os
import sys
import signal
import subprocess
import time
import uuid
from pathlib import Path

import eventlet

eventlet.monkey_patch()

from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit, join_room

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from debug.active_context import MacOSActiveContextReader
from debug.actor_runtime import ActorRuntime
from debug.chrome_bridge import ChromeBridgeServer

app = Flask(__name__)
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

registry = {
    "collector": {},
    "processor": {},
    "actor": {},
    "debug": {},
}
config = {
    "work_root": Path(os.environ.get("RADAR_HOME", "~/.radar")).expanduser().resolve(),
    "session_id": str(uuid.uuid4()),
    "api_token": os.environ.get("RADAR_API_TOKEN", str(uuid.uuid4())),
}
managed_collectors = []
active_context_reader = MacOSActiveContextReader()
chrome_bridge_server: ChromeBridgeServer | None = None
actor_runtime: ActorRuntime | None = None


def repo_root():
    return Path(__file__).resolve().parents[1]


def split_env_list(value):
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_file_stem(value):
    return value.replace(".", "_").replace("/", "_")


def run_root():
    return config["work_root"] / "run"


def collector_run_root():
    path = run_root() / "collectors"
    path.mkdir(parents=True, exist_ok=True)
    return path


def coordinator_state_path():
    return run_root() / "coordinator.json"


def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    tmp_path.replace(path)


def read_json(path):
    with path.open(encoding="utf-8") as input_file:
        return json.load(input_file)


def process_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not process_is_zombie(pid)


def process_is_zombie(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip().startswith("Z")


def process_command(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def command_matches(record, command_line):
    command = record.get("command") or []
    if not command_line or not command:
        return False

    if len(command) > 1:
        return all(str(part) in command_line for part in command[1:])

    executable = Path(command[0]).name
    return bool(executable and executable in command_line)


def terminate_pid(pid, grace_seconds=5):
    if not process_exists(pid):
        return True

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.2)

    if process_exists(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
    return not process_exists(pid)


def require_field(payload, field):
    value = (payload or {}).get(field)
    if not value:
        raise ValueError(f"missing required field: {field}")
    return value


def register_role(role, role_id, payload):
    record = {
        "role": role,
        "id": role_id,
        "sid": request.sid,
        "protocol_version": payload.get("protocol_version", 1),
        "capabilities": payload.get("capabilities", []),
        "metadata": payload.get("metadata", {}),
    }
    registry[role][request.sid] = record
    join_room(f"{role}s")
    return record


def collector_work_dir(collector_id):
    folder_name = safe_file_stem(collector_id)
    work_dir = config["work_root"] / "collectors" / folder_name
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def load_collector_meta(meta_path):
    path = Path(meta_path)
    if not path.is_absolute():
        path = repo_root() / path
    with path.open(encoding="utf-8") as meta_file:
        meta = json.load(meta_file)
    return path, meta


def collector_command(meta):
    runtime = meta.get("runtime", {})
    command = runtime.get("command")
    if not command:
        raise ValueError("collector meta is missing runtime.command")
    return [command, *runtime.get("args", [])]


def collector_pid_path(collector_id):
    return collector_run_root() / f"{safe_file_stem(collector_id)}.json"


def write_coordinator_state():
    write_json_atomic(
        coordinator_state_path(),
        {
            "pid": os.getpid(),
            "session_id": config["session_id"],
            "started_at": utc_timestamp(),
            "radar_home": str(config["work_root"]),
        },
    )


def write_collector_state(collector_id, meta_path, command, process):
    path = collector_pid_path(collector_id)
    write_json_atomic(
        path,
        {
            "collector_id": collector_id,
            "command": command,
            "meta_path": str(meta_path),
            "pid": process.pid,
            "session_id": config["session_id"],
            "started_at": utc_timestamp(),
        },
    )
    return path


def remove_collector_state(path):
    if path:
        Path(path).unlink(missing_ok=True)


def recover_stale_collectors():
    for state_path in collector_run_root().glob("*.json"):
        try:
            record = read_json(state_path)
            pid = int(record.get("pid"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            state_path.unlink(missing_ok=True)
            continue

        if not process_exists(pid):
            state_path.unlink(missing_ok=True)
            continue

        command_line = process_command(pid)
        if not command_matches(record, command_line):
            print(
                f"not touching stale collector pid {pid}; command does not match "
                f"{state_path}",
                flush=True,
            )
            state_path.unlink(missing_ok=True)
            continue

        collector_id = record.get("collector_id", state_path.stem)
        print(f"recovering stale collector {collector_id} with pid {pid}", flush=True)
        if terminate_pid(pid):
            print(f"stopped stale collector {collector_id}", flush=True)
            state_path.unlink(missing_ok=True)
        else:
            print(f"failed to stop stale collector {collector_id}", flush=True)


def launch_managed_collectors(meta_paths, coordinator_url):
    time.sleep(0.5)
    for meta_path in meta_paths:
        try:
            path, meta = load_collector_meta(meta_path)
            command = collector_command(meta)
            collector_id = meta.get("collector_id", str(path))
            env = os.environ.copy()
            env.setdefault("RADAR_HOME", str(config["work_root"]))
            env["RADAR_COORDINATOR_URL"] = coordinator_url
            env["RADAR_COORDINATOR_SESSION_ID"] = config["session_id"]
            env["RADAR_COORDINATOR_PID"] = str(os.getpid())
            env["RADAR_COLLECTOR_ID"] = collector_id
            process = subprocess.Popen(command, cwd=repo_root(), env=env)
            state_path = write_collector_state(collector_id, path, command, process)
            managed_collectors.append(
                {
                    "collector_id": collector_id,
                    "pid_path": state_path,
                    "process": process,
                }
            )
            print(
                f"started collector {collector_id} with pid {process.pid}",
                flush=True,
            )
        except Exception as error:
            print(f"failed to start collector from {meta_path}: {error}", flush=True)

    while managed_collectors:
        active = []
        for record in managed_collectors:
            process = record["process"]
            exit_code = process.poll()
            if exit_code is None:
                active.append(record)
                continue
            print(
                f"collector {record['collector_id']} exited with code {exit_code}",
                flush=True,
            )
            remove_collector_state(record.get("pid_path"))
        managed_collectors[:] = active
        time.sleep(1)


def stop_managed_collectors():
    for record in list(managed_collectors):
        process = record["process"]
        if process.poll() is not None:
            remove_collector_state(record.get("pid_path"))
            continue
        print(f"stopping collector {record['collector_id']}", flush=True)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        remove_collector_state(record.get("pid_path"))


atexit.register(stop_managed_collectors)


def publish_registry():
    emit("debug:registry_updated", state(), room="debug_clients")


def state():
    return {
        role: list(entries.values())
        for role, entries in registry.items()
        if role != "debug"
    }


@app.get("/debug/state")
def debug_state():
    payload = state()
    if actor_runtime is not None:
        payload["actor_packages"] = actor_runtime.list_actors()
    return jsonify(payload)


@app.get("/debug/context")
def debug_context():
    return jsonify(current_context_snapshot())


@app.get("/debug/chrome_bridge/status")
@app.get("/api/chrome_bridge/status")
def debug_chrome_bridge_status():
    if chrome_bridge_server is None:
        return jsonify({"connected": False, "error": "chrome bridge server not started"})
    return jsonify(chrome_bridge_server.status())


@app.get("/debug/actors")
@app.get("/api/actors")
def debug_actors():
    if actor_runtime is None:
        return jsonify({"actors": []})
    return jsonify({"actors": actor_runtime.list_actors()})


@app.post("/debug/actors/refresh")
@app.post("/api/actors/refresh")
def debug_actors_refresh():
    if request.path.startswith("/api/") and not api_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if actor_runtime is None:
        return jsonify({"ok": False, "error": "actor runtime not started"}), 503
    actor_runtime.refresh()
    return jsonify({"ok": True, "actors": actor_runtime.list_actors()})


@app.post("/debug/actors/<actor_id>/should_trigger")
@app.post("/api/actors/<actor_id>/should_trigger")
def debug_actor_should_trigger(actor_id):
    if request.path.startswith("/api/") and not api_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if actor_runtime is None:
        return jsonify({"ok": False, "error": "actor runtime not started"}), 503
    payload = request_json(default={})
    try:
        output = actor_runtime.should_trigger(
            actor_id,
            context_override=payload.get("context"),
            ignore_filters=bool(payload.get("ignore_filters")),
        )
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    return jsonify({"ok": True, "output": output})


@app.post("/debug/actors/<actor_id>/run")
@app.post("/api/actors/<actor_id>/run")
def debug_actor_run(actor_id):
    if request.path.startswith("/api/") and not api_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if actor_runtime is None:
        return jsonify({"ok": False, "error": "actor runtime not started"}), 503
    payload = request_json(default={})
    try:
        output = actor_runtime.run_action(
            actor_id,
            trigger_id=payload.get("trigger_id"),
            action_context=payload.get("action_context"),
            user_action=payload.get("user_action"),
            context_override=payload.get("context"),
        )
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    socketio.emit("debug:actor_result", output, room="debug_clients")
    return jsonify({"ok": True, "output": output})


@app.get("/api/context/current")
def api_context_current():
    if not api_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    return jsonify({"success": True, **current_context_snapshot()})


@app.post("/api/accessibility/query")
def api_accessibility_query():
    if not api_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    payload = request_json(default={})
    return jsonify(
        active_context_reader.read_accessibility(
            bundle_id=payload.get("bundle_id", ""),
            mode=payload.get("mode", "tree"),
            max_depth=int(payload.get("max_depth", 3) or 3),
        )
    )


@app.post("/api/browser/page_content")
@app.post("/api/page/content")
def api_browser_page_content():
    return invoke_chrome_bridge("page_content")


@app.post("/api/browser/page_action")
@app.post("/api/page/action")
def api_browser_page_action():
    return invoke_chrome_bridge("page_action")


@app.post("/api/desktop/open")
def api_desktop_open():
    if not api_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    payload = request_json(default={})
    target = payload.get("target") or payload.get("url") or payload.get("path") or payload.get("app")
    if not target:
        return jsonify({"success": False, "error": "target is required"}), 400
    result = subprocess.run(["open", str(target)], capture_output=True, text=True, check=False)
    return jsonify(
        {
            "success": result.returncode == 0,
            "error": result.stderr.strip() if result.returncode != 0 else "",
        }
    )


@app.post("/api/clipboard/write")
def api_clipboard_write():
    if not api_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    payload = request_json(default={})
    text = str(payload.get("text", ""))
    result = subprocess.run(["pbcopy"], input=text, capture_output=True, text=True, check=False)
    return jsonify(
        {
            "success": result.returncode == 0,
            "error": result.stderr.strip() if result.returncode != 0 else "",
        }
    )


@app.post("/api/progress")
def api_progress():
    if not api_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    payload = request_json(default={})
    payload.setdefault("created_at", utc_timestamp())
    socketio.emit("debug:actor_progress", payload, room="debug_clients")
    return jsonify({"success": True})


@app.post("/api/view/open")
def api_view_open():
    if not api_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    payload = request_json(default={})
    view_id = str(uuid.uuid4())
    socketio.emit(
        "debug:view_open",
        {
            "id": view_id,
            **payload,
        },
        room="debug_clients",
    )
    return jsonify({"success": True, "view_id": view_id})


def current_context_snapshot():
    snapshot = active_context_reader.read_current()
    bridge_tab = read_chrome_bridge_active_tab()
    if bridge_tab is not None:
        snapshot["browser"] = {
            "connected": True,
            "active_tab": bridge_tab,
        }
        active_context = snapshot.setdefault("active_context", {})
        signals = active_context.setdefault("signals", {})
        active_context["document_path"] = active_context.get("document_path") or bridge_tab.get("url", "")
        signals["url"] = bridge_tab.get("url", "")
        signals["browser_title"] = bridge_tab.get("title", "")
        signals["browser_domain"] = bridge_tab.get("domain", "")
    return snapshot


def read_chrome_bridge_active_tab():
    if chrome_bridge_server is None or not chrome_bridge_server.status().get("connected"):
        return None
    try:
        result = chrome_bridge_server.invoke(
            "page_content",
            [
                {
                    "include_metadata": True,
                }
            ],
            timeout_seconds=2,
        )
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    url = result.get("url")
    if not isinstance(url, str) or not url:
        return None
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    title = metadata.get("title") or ""
    return {
        "url": url,
        "title": title,
        "domain": domain_from_url(url),
    }


def domain_from_url(url):
    if "://" not in url:
        return ""
    return url.split("://", 1)[1].split("/", 1)[0]


def request_json(default=None):
    if not request.data:
        return default
    try:
        payload = request.get_json(force=True)
    except Exception:
        return default
    return payload if payload is not None else default


def api_authorized():
    token = config.get("api_token")
    if not token:
        return True
    return request.headers.get("Authorization") == f"Bearer {token}"


def invoke_chrome_bridge(method):
    if not api_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    if chrome_bridge_server is None:
        return jsonify({"success": False, "error": "chrome bridge server not started"}), 503
    payload = request_json(default={})
    try:
        result = chrome_bridge_server.invoke(method, [payload])
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 503
    if isinstance(result, dict) and result.get("error"):
        return jsonify({"success": False, **result})
    if isinstance(result, dict):
        return jsonify({"success": True, **result})
    return jsonify({"success": True, "result": result})


@socketio.on("connect")
def handle_connect():
    emit("coordinator:hello", {"ok": True, "protocol_version": 1})


@socketio.on("disconnect")
def handle_disconnect():
    changed = False
    for entries in registry.values():
        if entries.pop(request.sid, None):
            changed = True
    if changed:
        emit("debug:registry_updated", state(), room="debug_clients")


@socketio.on("debug:register")
def handle_debug_register(payload=None):
    registry["debug"][request.sid] = {
        "role": "debug",
        "id": (payload or {}).get("client_id", request.sid),
        "sid": request.sid,
    }
    join_room("debug_clients")
    return {"ok": True, "role": "debug", "state": state()}


@socketio.on("debug:command")
def handle_debug_command(payload):
    try:
        target_role = require_field(payload, "target_role")
        event = require_field(payload, "event")
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    data = payload.get("payload", {})
    target_id = payload.get("target_id")

    room = f"{target_role}s"
    if target_id:
        for record in registry.get(target_role, {}).values():
            if record["id"] == target_id:
                socketio.emit(event, data, to=record["sid"])
                return {"ok": True, "sent_to": target_id}
        return {"ok": False, "error": f"unknown {target_role}: {target_id}"}

    socketio.emit(event, data, room=room)
    return {"ok": True, "sent_to": room}


@socketio.on("collector:register")
def handle_collector_register(payload):
    try:
        collector_id = require_field(payload, "collector_id")
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    record = register_role("collector", collector_id, payload)
    work_dir = collector_work_dir(collector_id)
    record["work_dir"] = str(work_dir)
    publish_registry()
    emit(
        "coordinator:collector_registered",
        {
            "collector_id": record["id"],
            "work_dir": record["work_dir"],
            "capabilities": record.get("capabilities", []),
        },
        room="processors",
    )
    return {
        "ok": True,
        "role": "collector",
        "collector_id": record["id"],
        "work_dir": record["work_dir"],
    }


@socketio.on("collector:heartbeat")
def handle_collector_heartbeat(payload=None):
    collector = registry["collector"].get(request.sid, {})
    emit(
        "debug:collector_heartbeat",
        {
            "collector_id": collector.get("id"),
            "status": (payload or {}).get("status", "ok"),
            "permissions": (payload or {}).get("permissions", {}),
        },
        room="debug_clients",
    )
    return {"ok": True}


@socketio.on("processor:register")
def handle_processor_register(payload):
    try:
        processor_id = require_field(payload, "processor_id")
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    record = register_role("processor", processor_id, payload)
    record["accepts"] = payload.get("accepts", [])
    record["produces"] = payload.get("produces", [])
    publish_registry()
    return {"ok": True, "role": "processor", "processor_id": record["id"]}


@socketio.on("processor:heartbeat")
def handle_processor_heartbeat(payload=None):
    processor = registry["processor"].get(request.sid, {})
    emit(
        "debug:processor_heartbeat",
        {
            "processor_id": processor.get("id"),
            "status": (payload or {}).get("status", "ok"),
        },
        room="debug_clients",
    )
    return {"ok": True}


@socketio.on("processor:result")
def handle_processor_result(payload):
    processor = registry["processor"].get(request.sid)
    if not processor:
        return {"ok": False, "error": "processor is not registered"}

    result = dict(payload or {})
    result.setdefault("processor_id", processor["id"])
    emit("coordinator:action_request", result, room="actors")
    emit("debug:processor_result", result, room="debug_clients")
    return {"ok": True, "routed_to_actors": len(registry["actor"])}


@socketio.on("actor:register")
def handle_actor_register(payload):
    try:
        actor_id = require_field(payload, "actor_id")
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    record = register_role("actor", actor_id, payload)
    record["accepts"] = payload.get("accepts", [])
    record["safety_level"] = payload.get("safety_level", "prepare")
    publish_registry()
    return {"ok": True, "role": "actor", "actor_id": record["id"]}


@socketio.on("actor:heartbeat")
def handle_actor_heartbeat(payload=None):
    actor = registry["actor"].get(request.sid, {})
    emit(
        "debug:actor_heartbeat",
        {
            "actor_id": actor.get("id"),
            "status": (payload or {}).get("status", "ok"),
        },
        room="debug_clients",
    )
    return {"ok": True}


@socketio.on("actor:result")
def handle_actor_result(payload):
    actor = registry["actor"].get(request.sid)
    if not actor:
        return {"ok": False, "error": "actor is not registered"}

    result = dict(payload or {})
    result.setdefault("actor_id", actor["id"])
    emit("debug:actor_result", result, room="debug_clients")
    return {"ok": True}


@socketio.on("ping")
def handle_ping():
    emit("pong")


def parse_args():
    parser = argparse.ArgumentParser(description="Radar debug Socket.IO harness")
    parser.add_argument("--host", default=os.environ.get("RADAR_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("RADAR_PORT", 5000)), type=int)
    parser.add_argument(
        "--work-dir",
        default=os.environ.get(
            "RADAR_WORK_DIR",
            os.environ.get("RADAR_HOME", "~/.radar"),
        ),
    )
    parser.add_argument(
        "--collector-meta",
        action="append",
        default=None,
        help="Path to a collector meta.json to launch and manage.",
    )
    parser.add_argument(
        "--actor-path",
        action="append",
        default=None,
        help="Actor package directory, manifest path, or directory containing actor packages.",
    )
    parser.add_argument(
        "--chrome-bridge-port",
        default=int(os.environ.get("RADAR_CHROME_BRIDGE_PORT", 9223)),
        type=int,
        help="Port for the Wingman-compatible Chrome bridge WebSocket server.",
    )
    parser.add_argument(
        "--disable-chrome-bridge",
        action="store_true",
        default=os.environ.get("RADAR_DISABLE_CHROME_BRIDGE", "").lower()
        in {"1", "true", "yes"},
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("RADAR_DEBUG", "").lower() in {"1", "true", "yes"},
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config["work_root"] = Path(args.work_dir).expanduser().resolve()
    config["work_root"].mkdir(parents=True, exist_ok=True)
    recover_stale_collectors()
    write_coordinator_state()

    if not args.disable_chrome_bridge:
        chrome_bridge_server = ChromeBridgeServer(port=args.chrome_bridge_port)
        try:
            chrome_bridge_server.start()
            print(
                f"started chrome bridge ws on ws://127.0.0.1:{args.chrome_bridge_port}/wingman-chrome-bridge-ws",
                flush=True,
            )
        except Exception as error:
            print(f"failed to start chrome bridge: {error}", flush=True)

    host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    api_url = os.environ.get("RADAR_API_URL", f"http://{host}:{args.port}")
    actor_paths = args.actor_path or split_env_list(os.environ.get("RADAR_ACTOR_PATH"))
    if not actor_paths:
        actor_paths = ["builtin/actor"]
    actor_runtime = ActorRuntime(
        actor_paths=actor_paths,
        state_root=config["work_root"] / "actors",
        context_provider=current_context_snapshot,
        api_url=api_url,
        api_token=config["api_token"],
        progress_callback=lambda payload: socketio.emit(
            "debug:actor_progress",
            payload,
            room="debug_clients",
        ),
    )
    actor_runtime.refresh()
    print(
        f"loaded {len(actor_runtime.actors)} actor package(s); RADAR_API_URL={api_url}",
        flush=True,
    )
    print(f"debug actor API token: {config['api_token']}", flush=True)

    collector_meta = args.collector_meta
    if collector_meta is None:
        collector_meta = split_env_list(os.environ.get("RADAR_COLLECTOR_META"))

    if collector_meta:
        coordinator_url = os.environ.get(
            "RADAR_COORDINATOR_URL",
            f"http://{host}:{args.port}",
        )
        eventlet.spawn_after(0.5, launch_managed_collectors, collector_meta, coordinator_url)

    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=False,
    )

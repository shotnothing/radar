import argparse
import atexit
import json
import os
import subprocess
import time
from pathlib import Path

import eventlet

eventlet.monkey_patch()

from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit, join_room

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
}
managed_collectors = []


def repo_root():
    return Path(__file__).resolve().parents[1]


def split_env_list(value):
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


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
    folder_name = collector_id.replace(".", "_").replace("/", "_")
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


def launch_managed_collectors(meta_paths, coordinator_url):
    time.sleep(0.5)
    for meta_path in meta_paths:
        try:
            path, meta = load_collector_meta(meta_path)
            command = collector_command(meta)
            env = os.environ.copy()
            env.setdefault("RADAR_HOME", str(config["work_root"]))
            env["RADAR_COORDINATOR_URL"] = coordinator_url
            process = subprocess.Popen(command, cwd=repo_root(), env=env)
            managed_collectors.append(
                {
                    "collector_id": meta.get("collector_id", str(path)),
                    "process": process,
                }
            )
            print(
                f"started collector {meta.get('collector_id', path)} "
                f"with pid {process.pid}",
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
        managed_collectors[:] = active
        time.sleep(1)


def stop_managed_collectors():
    for record in list(managed_collectors):
        process = record["process"]
        if process.poll() is not None:
            continue
        print(f"stopping collector {record['collector_id']}", flush=True)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


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
    return jsonify(state())


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
        "--debug",
        action="store_true",
        default=os.environ.get("RADAR_DEBUG", "").lower() in {"1", "true", "yes"},
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config["work_root"] = Path(args.work_dir).expanduser().resolve()
    config["work_root"].mkdir(parents=True, exist_ok=True)
    collector_meta = args.collector_meta
    if collector_meta is None:
        collector_meta = split_env_list(os.environ.get("RADAR_COLLECTOR_META"))

    if collector_meta:
        host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
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

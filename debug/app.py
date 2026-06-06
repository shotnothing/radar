import argparse
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
    "work_root": Path("debug/work").resolve(),
}


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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5000, type=int)
    parser.add_argument("--work-dir", default="debug/work")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config["work_root"] = Path(args.work_dir).resolve()
    config["work_root"].mkdir(parents=True, exist_ok=True)
    socketio.run(app, host=args.host, port=args.port, debug=True)

import argparse
import importlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import socketio


COLLECTOR_ID = "seatalk.personal"
DISPLAY_NAME = "SeaTalk Personal Collector"
DEFAULT_SEATALK_ROOT = Path.home() / "Library" / "Application Support" / "SeaTalk"
DEFAULT_SEATALK_RESOURCES = Path("/Applications/SeaTalk.app/Contents/Resources")
PROCESSED_ID_LIMIT = 10000


def observed_time_ms():
    return int(time.time() * 1000)


def current_time_seconds():
    return int(time.time())


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_unix_seconds(value):
    if not value:
        return utc_timestamp()
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def source_uri(path):
    return Path(path).expanduser().resolve().as_uri()


def source_fingerprint(path):
    stat = Path(path).stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def seatalk_message_id(row):
    return f"seatalk:{row['sid']}:{row['mid']}"


def seatalk_source_uri(row):
    return f"seatalk://main_sqlite/chat_message/{row['sid']}/{row['mid']}"


def session_key_for_sid(sid):
    return f"seatalk:{sid}"


def coerce_unix_seconds(value):
    if value is None:
        return 0
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return 0
    if numeric > 10_000_000_000:
        return numeric // 1000
    return numeric


def discover_main_db(root=None):
    root = Path(root or DEFAULT_SEATALK_ROOT).expanduser()
    if not root.exists():
        return None
    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_file()
        and path.name.startswith("main_")
        and path.name.endswith(".sqlite")
        and ".decrypted" not in path.name
    )
    return candidates[0] if candidates else None


def local_user_id_from_db_path(path):
    match = re.search(r"main_(\d+)\.sqlite$", Path(path).name)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def sqlite_ro_uri(path):
    raw_path = str(Path(path).expanduser().resolve())
    return f"file:{quote(raw_path)}?mode=ro"


def load_sqlcipher_module():
    for module_name in ("pysqlcipher3.dbapi2", "sqlcipher3"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    return None


def quote_pragma_string(value):
    return str(value).replace("'", "''")


def open_sqlite_readonly(path, sqlite_key=""):
    if sqlite_key:
        sqlcipher = load_sqlcipher_module()
        if sqlcipher is None:
            raise RuntimeError(
                "SQLCipher support is unavailable; install pysqlcipher3 or pass a readable/decrypted SeaTalk DB"
            )
        connection = sqlcipher.connect(str(Path(path).expanduser().resolve()))
        connection.row_factory = getattr(sqlcipher, "Row", sqlite3.Row)
        connection.execute(f"PRAGMA key = '{quote_pragma_string(sqlite_key)}'")
        connection.execute("PRAGMA query_only = ON")
        return connection

    connection = sqlite3.connect(sqlite_ro_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def extract_key_prefix(resources_dir=None):
    resources = Path(resources_dir or DEFAULT_SEATALK_RESOURCES).expanduser()
    if not resources.exists():
        return ""
    for entry in sorted(resources.iterdir()):
        name = entry.name
        if not entry.is_file():
            continue
        if not name.endswith("_bundle.asar") or name.endswith(".unpacked"):
            continue
        try:
            data = entry.read_bytes()
        except OSError:
            continue
        match = re.search(rb"key='([0-9a-f]+)", data)
        if match:
            return match.group(1).decode("ascii")
    return ""


def resolve_sqlcipher_key(args, db_path):
    if args.sqlite_key:
        return args.sqlite_key
    if args.disable_sqlcipher:
        return ""
    user_id = local_user_id_from_db_path(db_path)
    prefix = extract_key_prefix(args.seatalk_resources_dir)
    if not user_id or not prefix:
        return ""
    return f"{prefix}{user_id}"


def load_json_mapping(path):
    if not path:
        return {}
    p = Path(path).expanduser()
    if not p.exists():
        return {}
    try:
        with p.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_user_map(raw):
    users = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            user_id = int(key)
        except (TypeError, ValueError):
            user_id = int(value.get("id") or value.get("userid") or 0)
        if user_id:
            users[user_id] = value
    return users


def normalize_conversation_map(raw):
    conversations = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            conversations[str(key)] = value
    return conversations


def conversation_type(sid, conversation_info=None):
    if conversation_info and isinstance(conversation_info.get("type"), str):
        return conversation_info["type"]
    if str(sid).startswith("group-"):
        return "group"
    if str(sid).startswith("buddy-"):
        return "direct"
    return "unknown"


def conversation_name(sid, conversation_info=None):
    if conversation_info:
        name = conversation_info.get("name") or conversation_info.get("title")
        if name:
            return str(name)
    return str(sid)


def user_summary(user_id, user_map, self_user_id):
    if user_id == self_user_id:
        fallback = "me"
    else:
        fallback = str(user_id) if user_id else ""
    info = user_map.get(user_id) or {}
    return {
        "user_id": user_id,
        "name": str(info.get("name") or info.get("display_name") or fallback),
        "email": str(info.get("email") or info.get("mail") or ""),
        "is_self": bool(user_id and user_id == self_user_id),
    }


def extract_text_from_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [extract_text_from_value(item) for item in value]
        return "\n".join(part for part in parts if part)
    if not isinstance(value, dict):
        return ""

    direct = value.get("c")
    if isinstance(direct, str) and direct and not value.get("f"):
        return direct
    content = value.get("content")
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    if isinstance(value.get("tx"), str):
        return value["tx"]

    parts = []
    for key in ("c", "e", "es", "f"):
        child = value.get(key)
        text = extract_text_from_value(child)
        if text:
            parts.append(text)
    return "\n".join(parts)


def prettify_content(raw):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return str(raw)
    raw = raw.strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return extract_text_from_value(payload).strip() or raw


def row_to_dict(row):
    return {
        "sid": row["sid"] or "",
        "mid": row["mid"] or "",
        "rmid": row["rmid"] or "",
        "rtmid": row["rtmid"] or "",
        "u": int(row["u"] or 0),
        "c": row["c"] or "",
        "t": row["t"] or "",
        "ts": coerce_unix_seconds(row["ts"]),
    }


def thread_root_for(row):
    rtmid = str(row.get("rtmid") or "").strip()
    rmid = str(row.get("rmid") or "").strip()
    mid = str(row.get("mid") or "").strip()
    if rtmid:
        return rtmid
    if rmid:
        return rmid
    return mid


def is_thread_reply(row):
    mid = str(row.get("mid") or "").strip()
    root = thread_root_for(row)
    return bool(root and mid and root != mid)


def fetch_rows(connection, query, params):
    cursor = connection.execute(query, params)
    return [row_to_dict(row) for row in cursor.fetchall()]


def fetch_new_user_messages(connection, self_user_id, scan_after, limit):
    return fetch_rows(
        connection,
        """
        SELECT sid, mid, rmid, rtmid, u, c, t, ts
          FROM chat_message
         WHERE u = ?
           AND ts >= ?
           AND mid IS NOT NULL
           AND mid != ''
         ORDER BY ts ASC, mid ASC
         LIMIT ?
        """,
        (self_user_id, scan_after, limit),
    )


def fetch_previous_messages(connection, row, limit):
    if limit <= 0:
        return []
    sid = row["sid"]
    ts = row["ts"]
    root_mid = thread_root_for(row) if is_thread_reply(row) else ""
    if root_mid:
        return fetch_rows(
            connection,
            """
            SELECT sid, mid, rmid, rtmid, u, c, t, ts
              FROM chat_message
             WHERE sid = ?
               AND ts < ?
               AND (
                    mid = ?
                    OR rtmid = ?
                    OR ((rtmid IS NULL OR rtmid = '') AND rmid = ?)
               )
             ORDER BY ts DESC, mid DESC
             LIMIT ?
            """,
            (sid, ts, root_mid, root_mid, root_mid, limit),
        )
    return fetch_rows(
        connection,
        """
        SELECT sid, mid, rmid, rtmid, u, c, t, ts
          FROM chat_message
         WHERE sid = ?
           AND ts < ?
         ORDER BY ts DESC, mid DESC
         LIMIT ?
        """,
        (sid, ts, limit),
    )


def fetch_reply_to_message(connection, row):
    rmid = str(row.get("rmid") or "").strip()
    if not rmid or rmid == row.get("mid"):
        return None
    rows = fetch_rows(
        connection,
        """
        SELECT sid, mid, rmid, rtmid, u, c, t, ts
          FROM chat_message
         WHERE sid = ?
           AND mid = ?
         LIMIT 1
        """,
        (row["sid"], rmid),
    )
    return rows[0] if rows else None


def context_message(row, user_map, self_user_id):
    sender = user_summary(row["u"], user_map, self_user_id)
    return {
        "sid": row["sid"],
        "mid": row["mid"],
        "sender_user_id": sender["user_id"],
        "sender_name": sender["name"],
        "sender_email": sender["email"],
        "sender_is_self": sender["is_self"],
        "message_type": row["t"],
        "timestamp": row["ts"],
        "text": prettify_content(row["c"]),
    }


def build_user_message_event(
    row,
    *,
    db_path,
    db_fingerprint,
    self_user_id,
    user_map,
    conversation_map,
    previous_rows,
    reply_to_row,
):
    observed_at = row["ts"] * 1000 if row["ts"] else observed_time_ms()
    message_id = seatalk_message_id(row)
    conversation_info = conversation_map.get(row["sid"], {})
    convo_type = conversation_type(row["sid"], conversation_info)
    convo_name = conversation_name(row["sid"], conversation_info)
    previous_messages = [
        context_message(item, user_map, self_user_id)
        for item in reversed(previous_rows)
    ]
    reply_to_message = (
        context_message(reply_to_row, user_map, self_user_id)
        if reply_to_row
        else None
    )
    text = prettify_content(row["c"])
    root_mid = thread_root_for(row)

    event = {
        "id": str(uuid.uuid4()),
        "collector_id": COLLECTOR_ID,
        "source": {
            "type": "messaging",
            "app": "SeaTalk",
            "format": "seatalk_sqlite_main",
        },
        "time": {
            "observed_at": observed_at,
        },
        "anchor": {
            "id": message_id,
            "type": "communication_message",
            "name": "user_message_sent",
            "occurred_at": iso_from_unix_seconds(row["ts"]),
            "target": {
                "app": "SeaTalk",
                "conversation_id": row["sid"],
                "conversation_name": convo_name,
                "conversation_type": convo_type,
            },
        },
        "subject": {
            "kind": "communication_user_message",
            "title": f"User message in {convo_name}",
        },
        "content": {
            "text": text,
        },
        "context": {
            "conversation_id": row["sid"],
            "conversation_name": convo_name,
            "conversation_type": convo_type,
            "previous_messages": previous_messages,
        },
        "provenance": {
            "source_uri": seatalk_source_uri(row),
            "source_file_uri": source_uri(db_path),
            "source_type": "seatalk_sqlite_main",
            "source_fingerprint": db_fingerprint,
            "session_key": session_key_for_sid(row["sid"]),
            "session_id": row["sid"],
            "source_message_ids": [message_id],
        },
        "artifacts": [
            {
                "id": "seatalk_main_db",
                "kind": "source_database",
                "uri": source_uri(db_path),
                "storage": "external_pointer",
                "mime_type": "application/vnd.sqlite3",
                "extra_data": {
                    "source_fingerprint": db_fingerprint,
                },
            }
        ],
        "extra_data": {
            "seatalk": {
                "sid": row["sid"],
                "mid": row["mid"],
                "rmid": row["rmid"],
                "rtmid": row["rtmid"],
                "thread_root_mid": root_mid,
                "is_thread_reply": is_thread_reply(row),
                "sender_user_id": row["u"],
                "sender_is_self": row["u"] == self_user_id,
                "message_type": row["t"],
                "timestamp": row["ts"],
                "relation_to_user": "sent_by_user",
            }
        },
        "privacy": {
            "contains_raw_content": True,
            "scope": "user_authored_message_with_local_context",
        },
    }
    if reply_to_message:
        event["context"]["reply_to_message"] = reply_to_message
    return event


def load_checkpoint(work_dir):
    path = Path(work_dir) / "state" / "checkpoint.json"
    if not path.exists():
        return {"version": 1, "seatalk": {}}
    try:
        with path.open(encoding="utf-8") as file:
            checkpoint = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "seatalk": {}}
    checkpoint.setdefault("version", 1)
    checkpoint.setdefault("seatalk", {})
    return checkpoint


def save_checkpoint(work_dir, checkpoint):
    path = Path(work_dir) / "state" / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(checkpoint, file, ensure_ascii=True, indent=2, sort_keys=True)
        file.write("\n")


def output_path_for(work_dir, observed_at):
    day_dir = Path(work_dir) / datetime.fromtimestamp(observed_at / 1000).strftime(
        "%Y%m%d"
    )
    artifacts_dir = day_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir / f"{observed_at}.jsonl"


def write_events(work_dir, events):
    if not events:
        return None
    observed_at = observed_time_ms()
    path = output_path_for(work_dir, observed_at)
    with path.open("a", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event, ensure_ascii=True, sort_keys=True))
            file.write("\n")
    return path


def bounded_processed_ids(processed_ids, rows):
    keep = list(processed_ids)
    for row in rows:
        msg_id = seatalk_message_id(row)
        if msg_id not in processed_ids:
            processed_ids.add(msg_id)
            keep.append(msg_id)
    if len(keep) > PROCESSED_ID_LIMIT:
        keep = keep[-PROCESSED_ID_LIMIT:]
    return keep


def resolve_db_path(args):
    if args.seatalk_main_db:
        return Path(args.seatalk_main_db).expanduser()
    return discover_main_db(args.seatalk_root)


def collect_events_from_connection(
    connection,
    args,
    *,
    db_path,
    db_fingerprint,
    self_user_id,
    scan_after,
    user_map,
    conversation_map,
    processed_ids,
):
    events = []
    seen_rows = []
    rows = fetch_new_user_messages(
        connection,
        self_user_id,
        scan_after,
        int(args.batch_limit),
    )
    for row in rows:
        seen_rows.append(row)
        msg_id = seatalk_message_id(row)
        if not args.force_all and msg_id in processed_ids:
            continue
        text = prettify_content(row["c"])
        if not text and not args.emit_empty_messages:
            continue
        previous_rows = fetch_previous_messages(
            connection,
            row,
            int(args.context_message_limit),
        )
        reply_to_row = fetch_reply_to_message(connection, row)
        events.append(
            build_user_message_event(
                row,
                db_path=db_path,
                db_fingerprint=db_fingerprint,
                self_user_id=self_user_id,
                user_map=user_map,
                conversation_map=conversation_map,
                previous_rows=previous_rows,
                reply_to_row=reply_to_row,
            )
        )
    return seen_rows, events


def should_retry_with_sqlcipher(error):
    message = str(error).lower()
    return (
        "not a database" in message
        or "file is encrypted" in message
        or "malformed" in message
    )


def scan_sources(args, work_dir):
    checkpoint = load_checkpoint(work_dir)
    state = checkpoint.setdefault("seatalk", {})
    db_path = resolve_db_path(args)
    if not db_path or not Path(db_path).exists():
        message = f"SeaTalk main database not found: {db_path or args.seatalk_root}"
        state["last_error"] = message
        state["last_scan_at"] = observed_time_ms()
        save_checkpoint(work_dir, checkpoint)
        return {
            "permissions": {"seatalk_main_db_readable": False},
            "messages_seen": 0,
            "messages_changed": 0,
            "events_written": 0,
            "output_path": "",
            "last_error": message,
        }

    db_path = Path(db_path).expanduser().resolve()
    self_user_id = args.self_user_id or local_user_id_from_db_path(db_path)
    if not self_user_id:
        message = "could not determine local SeaTalk user id"
        state["last_error"] = message
        state["last_scan_at"] = observed_time_ms()
        save_checkpoint(work_dir, checkpoint)
        return {
            "permissions": {"seatalk_main_db_readable": True},
            "messages_seen": 0,
            "messages_changed": 0,
            "events_written": 0,
            "output_path": "",
            "last_error": message,
        }

    db_fingerprint = source_fingerprint(db_path)
    previous_last_seen_ts = int(state.get("last_seen_ts") or 0)
    if args.force_all:
        scan_after = 0
    elif previous_last_seen_ts > 0:
        scan_after = max(0, previous_last_seen_ts - int(args.checkpoint_lookback_seconds))
    else:
        scan_after = max(0, current_time_seconds() - int(args.initial_lookback_seconds))

    user_map = normalize_user_map(load_json_mapping(args.user_map))
    conversation_map = normalize_conversation_map(load_json_mapping(args.conversation_map))
    processed_ids = set(state.get("processed_message_ids") or [])
    events = []
    seen_rows = []
    errors = []

    try:
        with open_sqlite_readonly(db_path) as connection:
            seen_rows, events = collect_events_from_connection(
                connection,
                args,
                db_path=db_path,
                db_fingerprint=db_fingerprint,
                self_user_id=self_user_id,
                scan_after=scan_after,
                user_map=user_map,
                conversation_map=conversation_map,
                processed_ids=processed_ids,
            )
    except Exception as error:
        if not should_retry_with_sqlcipher(error):
            errors.append(str(error))
        else:
            sqlite_key = resolve_sqlcipher_key(args, db_path)
            if not sqlite_key:
                errors.append(str(error))
            else:
                try:
                    with open_sqlite_readonly(db_path, sqlite_key=sqlite_key) as connection:
                        seen_rows, events = collect_events_from_connection(
                            connection,
                            args,
                            db_path=db_path,
                            db_fingerprint=db_fingerprint,
                            self_user_id=self_user_id,
                            scan_after=scan_after,
                            user_map=user_map,
                            conversation_map=conversation_map,
                            processed_ids=processed_ids,
                        )
                except Exception as retry_error:
                    errors.append(str(retry_error))

    output_path = write_events(work_dir, events)
    all_seen_for_checkpoint = seen_rows
    if all_seen_for_checkpoint:
        latest = max(
            all_seen_for_checkpoint,
            key=lambda item: (int(item.get("ts") or 0), str(item.get("mid") or "")),
        )
        state["last_seen_ts"] = latest["ts"]
        state["last_seen_mid"] = latest["mid"]
    state["self_user_id"] = self_user_id
    state["source_uri"] = source_uri(db_path)
    state["source_type"] = "seatalk_sqlite_main"
    state["source_fingerprint"] = db_fingerprint
    state["processed_message_ids"] = bounded_processed_ids(processed_ids, seen_rows)
    state["last_scan_at"] = observed_time_ms()
    state["last_error"] = "; ".join(errors[-3:])
    save_checkpoint(work_dir, checkpoint)

    return {
        "permissions": {"seatalk_main_db_readable": not errors},
        "messages_seen": len(seen_rows),
        "messages_changed": len(events),
        "events_written": len(events),
        "output_path": str(output_path) if output_path else "",
        "last_error": state["last_error"],
    }


def register_collector(client):
    return client.call(
        "collector:register",
        {
            "collector_id": COLLECTOR_ID,
            "protocol_version": 1,
            "capabilities": [
                "seatalk_sqlite_main",
                "user_authored_messages",
                "previous_message_context",
                "incremental_scan",
                "source_provenance",
                "heartbeat",
            ],
            "metadata": {
                "display_name": DISPLAY_NAME,
            },
        },
        timeout=5,
    )


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


def run(args):
    client = socketio.Client()
    connect_client(client, args.coordinator_url, args.connect_timeout)
    try:
        registration = register_collector(client)
        if not registration.get("ok"):
            raise RuntimeError(f"collector registration failed: {registration}")
        work_dir = registration["work_dir"]
        print(f"registered {COLLECTOR_ID}")

        last_scan = {}
        deadline = time.monotonic() + max(args.duration, 0)
        next_scan_at = 0
        while True:
            now = time.monotonic()
            if now >= next_scan_at:
                started_at = observed_time_ms()
                last_scan = scan_sources(args, work_dir)
                last_scan["last_scan_started_at"] = started_at
                if last_scan.get("output_path"):
                    print(f"wrote collected SeaTalk data: {last_scan['output_path']}")
                next_scan_at = now + max(args.scan_interval, 1)

            client.call(
                "collector:heartbeat",
                {
                    "status": "error" if last_scan.get("last_error") else "ok",
                    "permissions": last_scan.get("permissions", {}),
                    "last_scan_started_at": last_scan.get("last_scan_started_at", 0),
                    "last_scan_completed_at": observed_time_ms(),
                    "messages_seen": last_scan.get("messages_seen", 0),
                    "messages_changed": last_scan.get("messages_changed", 0),
                    "events_written": last_scan.get("events_written", 0),
                    "last_write": last_scan.get("output_path", ""),
                    "last_error": last_scan.get("last_error", ""),
                },
                timeout=5,
            )

            if args.duration <= 0 or time.monotonic() >= deadline:
                break
            sleep_for = min(
                max(args.heartbeat_interval, 0.1),
                max(deadline - time.monotonic(), 0),
            )
            if sleep_for:
                time.sleep(sleep_for)
    finally:
        client.disconnect()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Register a Radar SeaTalk collector and write user-authored message events."
    )
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://localhost:5000"),
        help="Socket.IO coordinator URL.",
    )
    parser.add_argument(
        "--seatalk-root",
        default=os.environ.get("RADAR_SEATALK_ROOT", str(DEFAULT_SEATALK_ROOT)),
        help="SeaTalk application support directory.",
    )
    parser.add_argument(
        "--seatalk-main-db",
        default=os.environ.get("RADAR_SEATALK_MAIN_DB", ""),
        help="Path to main_<user_id>.sqlite. Defaults to discovery under seatalk-root.",
    )
    parser.add_argument(
        "--seatalk-resources-dir",
        default=os.environ.get(
            "RADAR_SEATALK_RESOURCES_DIR", str(DEFAULT_SEATALK_RESOURCES)
        ),
        help="SeaTalk app Resources directory used to derive the SQLCipher key prefix.",
    )
    parser.add_argument(
        "--sqlite-key",
        default=os.environ.get("RADAR_SEATALK_SQLITE_KEY", ""),
        help="Optional full SQLCipher key for encrypted SeaTalk SQLite files.",
    )
    parser.add_argument(
        "--disable-sqlcipher",
        action="store_true",
        default=env_bool("RADAR_SEATALK_DISABLE_SQLCIPHER", False),
        help="Disable encrypted DB retry and only use standard sqlite3.",
    )
    parser.add_argument(
        "--self-user-id",
        default=env_int("RADAR_SEATALK_SELF_USER_ID", 0),
        type=int,
        help="Local SeaTalk user ID. Defaults to main_<user_id>.sqlite filename.",
    )
    parser.add_argument(
        "--user-map",
        default=os.environ.get("RADAR_SEATALK_USER_MAP", ""),
        help="Optional JSON map of user_id to name/email for test or resolver enrichment.",
    )
    parser.add_argument(
        "--conversation-map",
        default=os.environ.get("RADAR_SEATALK_CONVERSATION_MAP", ""),
        help="Optional JSON map of sid to name/type for test or resolver enrichment.",
    )
    parser.add_argument(
        "--duration",
        default=env_float("RADAR_SEATALK_COLLECTOR_DURATION", 10),
        type=float,
        help="Seconds to stay registered before disconnecting. Use <= 0 to run forever.",
    )
    parser.add_argument(
        "--scan-interval",
        default=env_float("RADAR_SEATALK_SCAN_INTERVAL", 60),
        type=float,
        help="Seconds between SeaTalk scans.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_SEATALK_HEARTBEAT_INTERVAL", 2),
        type=float,
        help="Seconds between collector heartbeats while registered.",
    )
    parser.add_argument(
        "--connect-timeout",
        default=env_float("RADAR_COLLECTOR_CONNECT_TIMEOUT", 10),
        type=float,
        help="Seconds to wait for the coordinator before failing.",
    )
    parser.add_argument(
        "--context-message-limit",
        default=env_int("RADAR_SEATALK_CONTEXT_MESSAGE_LIMIT", 3),
        type=int,
        help="Number of previous messages to attach as local context.",
    )
    parser.add_argument(
        "--checkpoint-lookback-seconds",
        default=env_int("RADAR_SEATALK_CHECKPOINT_LOOKBACK_SECONDS", 300),
        type=int,
        help="Overlap before last checkpoint used to catch late-synced messages.",
    )
    parser.add_argument(
        "--initial-lookback-seconds",
        default=env_int("RADAR_SEATALK_INITIAL_LOOKBACK_SECONDS", 86400),
        type=int,
        help="First-run lookback window. Keeps the initial scan from collecting all history.",
    )
    parser.add_argument(
        "--batch-limit",
        default=env_int("RADAR_SEATALK_BATCH_LIMIT", 500),
        type=int,
        help="Maximum user-authored messages to process per scan.",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        default=env_bool("RADAR_SEATALK_FORCE_ALL", False),
        help="Scan from the beginning and re-emit messages not blocked by current dedupe state.",
    )
    parser.add_argument(
        "--emit-empty-messages",
        action="store_true",
        default=env_bool("RADAR_SEATALK_EMIT_EMPTY_MESSAGES", False),
        help="Emit user-authored rows even when no readable text can be extracted.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())

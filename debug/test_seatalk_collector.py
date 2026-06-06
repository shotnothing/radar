import importlib.util
import atexit
import json
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_PATH = REPO_ROOT / "builtin" / "collector" / "seatalk" / "collector.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("seatalk_collector", COLLECTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def text_payload(text):
    return json.dumps({"c": text})


def create_fixture_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE chat_message (
                sid TEXT,
                mid TEXT,
                rmid TEXT,
                rtmid TEXT,
                u INTEGER,
                c TEXT,
                t TEXT,
                ts INTEGER
            )
            """
        )
        rows = [
            ("group-1", "m1", "", "", 222, text_payload("Can you check this?"), "text", 100),
            ("group-1", "m2", "", "", 123, text_payload("I will check and update later."), "text", 101),
            ("group-1", "m3", "", "", 333, text_payload("Thanks."), "text", 102),
            ("group-1", "m4", "m1", "m1", 222, text_payload("Any update?"), "text", 103),
            ("group-1", "m5", "m4", "m1", 123, text_payload("Still investigating in the thread."), "text", 104),
            ("buddy-9", "d1", "", "", 999, text_payload("Ping"), "text", 105),
            ("buddy-9", "d2", "", "", 123, text_payload("I can do it tomorrow."), "text", 106),
        ]
        connection.executemany(
            """
            INSERT INTO chat_message (sid, mid, rmid, rtmid, u, c, t, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def append_message(path, row):
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO chat_message (sid, mid, rmid, rtmid, u, c, t, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        connection.commit()
    finally:
        connection.close()


def read_written_events(work_dir, started_at):
    files = sorted(
        path
        for path in Path(work_dir).glob("**/*.jsonl")
        if path.stat().st_mtime >= started_at
    )
    events = []
    for path in files:
        with path.open(encoding="utf-8") as data_file:
            for line in data_file:
                events.append(json.loads(line))
    return events


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def make_args(db_path, user_map_path, conversation_map_path):
    return SimpleNamespace(
        seatalk_root=str(db_path.parent),
        seatalk_main_db=str(db_path),
        seatalk_resources_dir=str(db_path.parent),
        sqlite_key="",
        disable_sqlcipher=True,
        self_user_id=0,
        user_map=str(user_map_path),
        conversation_map=str(conversation_map_path),
        checkpoint_lookback_seconds=300,
        initial_lookback_seconds=9999999999,
        batch_limit=100,
        context_message_limit=3,
        force_all=False,
        emit_empty_messages=False,
    )


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def main():
    collector = load_collector()
    root = Path(tempfile.mkdtemp(prefix="radar-seatalk-collector-test-"))
    atexit.register(shutil.rmtree, root, True)
    db_path = root / "main_123.sqlite"
    work_dir = root / "work"
    user_map_path = root / "users.json"
    conversation_map_path = root / "conversations.json"
    if db_path.exists():
        db_path.unlink()
    create_fixture_db(db_path)
    write_json(
        user_map_path,
        {
            "123": {"name": "Radar User", "email": "me@example.com"},
            "222": {"name": "Requester", "email": "requester@example.com"},
            "333": {"name": "Observer", "email": "observer@example.com"},
            "999": {"name": "Buddy", "email": "buddy@example.com"},
        },
    )
    write_json(
        conversation_map_path,
        {
            "group-1": {"name": "Payments Infra", "type": "group"},
            "buddy-9": {"name": "Buddy", "type": "direct"},
        },
    )

    args = make_args(db_path, user_map_path, conversation_map_path)
    started_at = time.time()
    first = collector.scan_sources(args, work_dir)
    events = read_written_events(work_dir, started_at)

    require(first["messages_seen"] == 3, f"expected 3 user rows: {first}")
    require(first["events_written"] == 3, f"expected 3 written events: {first}")
    require(len(events) == 3, f"unexpected event count: {len(events)}")
    require(
        {event["extra_data"]["seatalk"]["mid"] for event in events}
        == {"m2", "m5", "d2"},
        "collector should only emit self-authored messages",
    )

    m2 = next(event for event in events if event["extra_data"]["seatalk"]["mid"] == "m2")
    require(m2["subject"]["kind"] == "communication_user_message", "wrong subject kind")
    require(m2["content"]["text"] == "I will check and update later.", "wrong text")
    require(
        m2["context"]["conversation_name"] == "Payments Infra",
        "missing conversation name",
    )
    require(
        m2["context"]["previous_messages"][0]["mid"] == "m1",
        f"m2 should include previous message: {m2['context']}",
    )
    require(
        m2["context"]["previous_messages"][0]["sender_email"] == "requester@example.com",
        "previous sender should be resolved",
    )

    m5 = next(event for event in events if event["extra_data"]["seatalk"]["mid"] == "m5")
    require(m5["extra_data"]["seatalk"]["is_thread_reply"], "m5 should be a thread reply")
    require(
        [item["mid"] for item in m5["context"]["previous_messages"]] == ["m1", "m4"],
        f"thread context should stay in thread: {m5['context']}",
    )
    require(
        m5["context"]["reply_to_message"]["mid"] == "m4",
        "m5 should include exact reply target",
    )

    checkpoint = json.loads((work_dir / "state" / "checkpoint.json").read_text())
    require(checkpoint["seatalk"]["self_user_id"] == 123, "self user id not checkpointed")
    require(checkpoint["seatalk"]["last_seen_mid"] == "d2", "last seen mid not checkpointed")

    second = collector.scan_sources(args, work_dir)
    require(second["events_written"] == 0, f"second scan should dedupe: {second}")

    append_message(
        db_path,
        ("group-1", "m6", "", "", 222, text_payload("Please confirm."), "text", 107),
    )
    append_message(
        db_path,
        ("group-1", "m7", "", "", 123, text_payload("Confirmed from my side."), "text", 108),
    )
    third_started_at = time.time()
    third = collector.scan_sources(args, work_dir)
    third_events = read_written_events(work_dir, third_started_at)
    require(third["events_written"] == 1, f"third scan should emit one new user message: {third}")
    require(len(third_events) == 1, f"expected one third-scan event: {third_events}")
    require(
        third_events[0]["extra_data"]["seatalk"]["mid"] == "m7",
        "new inbound message should only appear as context, not standalone",
    )
    require(
        third_events[0]["context"]["previous_messages"][-1]["mid"] == "m6",
        "new inbound row should be previous context for m7",
    )

    print(f"fixture_db: {db_path}")
    print(f"work_dir: {work_dir}")
    print(f"initial_events: {len(events)}")
    print(f"incremental_events: {len(third_events)}")


if __name__ == "__main__":
    main()

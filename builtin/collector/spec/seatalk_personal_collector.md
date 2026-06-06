# SeaTalk Personal Collector Spec

The SeaTalk personal collector observes local SeaTalk chat storage and emits
Radar events for messages authored by the local user. Its job is to capture what
the user said, where they said it, and the immediate message context that shaped
the response.

## Goals

- Collect user-authored SeaTalk messages before processors infer tasks,
  commitments, preferences, or intent.
- Avoid collecting all group chat history by default.
- Preserve the conversation, thread, message ID, timestamp, and source database
  provenance for targeted lookup.
- Attach a small previous-message context window so processors can understand
  what the user was replying to.
- Support periodic incremental scans that only emit newly observed messages.

## Source Locations

Default macOS source root:

```text
~/Library/Application Support/SeaTalk
```

The main chat database is expected to be named:

```text
main_<local_user_id>.sqlite
```

The collector should treat the root and database path as configurable. Missing
SeaTalk storage is not fatal; the collector should report it in heartbeat status.
SeaTalk's production database may be encrypted. Wingman's reader derives the
SQLCipher key from the app bundle key prefix plus the local user ID. The Radar
collector should use the same key derivation shape and open the database through
SQLCipher directly.

## Collector Identity

Recommended `meta.json` fields:

```json
{
    "collector_id": "seatalk.personal",
    "display_name": "SeaTalk Personal Collector",
    "description": "Collects user-authored SeaTalk messages with immediate conversation context.",
    "protocol_version": 1,
    "runtime": {
        "command": "python3",
        "args": ["builtin/collector/seatalk/collector.py"]
    },
    "required_permissions": [
        "filesystem_read_seatalk_app_support",
        "filesystem_read_seatalk_app_bundle"
    ],
    "capabilities": [
        "seatalk_sqlite_main",
        "user_authored_messages",
        "previous_message_context",
        "incremental_scan",
        "source_provenance"
    ],
    "emits": ["collected_data.communication_user_message"],
    "default_config": {
        "scan_interval_seconds": 60,
        "seatalk_resources_dir": "/Applications/SeaTalk.app/Contents/Resources",
        "context_message_limit": 3,
        "checkpoint_lookback_seconds": 300,
        "initial_lookback_seconds": 86400
    }
}
```

## Incremental Scan

The collector keeps `state/checkpoint.json` under its assigned `work_dir`.

Recommended checkpoint fields:

```json
{
    "version": 1,
    "seatalk": {
        "source_uri": "file:///Users/example/Library/Application%20Support/SeaTalk/main_123.sqlite",
        "source_type": "seatalk_sqlite_main",
        "source_fingerprint": "12345:1780713574000000000",
        "self_user_id": 123,
        "last_seen_ts": 1780713500,
        "last_seen_mid": "message_mid",
        "processed_message_ids": ["seatalk:group-1:message_mid"],
        "last_scan_at": 1780713574000,
        "last_error": ""
    }
}
```

On each scan:

1. Resolve the local user ID from `main_<local_user_id>.sqlite` unless explicitly
   configured.
2. Query `chat_message` rows where `u = local_user_id`.
3. If a checkpoint exists, scan from `last_seen_ts - checkpoint_lookback_seconds`
   and dedupe by `seatalk:{sid}:{mid}`.
4. If no checkpoint exists, scan only a recent `initial_lookback_seconds` window
   by default.
5. Write one collected event per new user-authored message.
6. Advance the checkpoint only after JSONL events and checkpoint state are
   written.

The overlap window is important because SeaTalk sync can make older messages
visible after the collector has already advanced its timestamp checkpoint.

## Message Query

The primary source rows come from:

```sql
SELECT sid, mid, rmid, rtmid, u, c, t, ts
  FROM chat_message
 WHERE u = :self_user_id
   AND ts >= :scan_after
 ORDER BY ts ASC, mid ASC
 LIMIT :batch_limit
```

Relevant fields:

- `sid`: SeaTalk conversation ID, such as `buddy-123` or `group-456`.
- `mid`: message ID.
- `rmid`: direct reply target when available.
- `rtmid`: thread root message ID when available.
- `u`: sender user ID. For collected rows, this must equal the local user ID.
- `c`: raw SeaTalk message content JSON.
- `t`: message type.
- `ts`: message timestamp in Unix seconds.

## Context Query

For each user-authored message, attach a small context window. The default should
be three previous messages.

For non-thread messages:

```sql
SELECT sid, mid, rmid, rtmid, u, c, t, ts
  FROM chat_message
 WHERE sid = :sid
   AND ts < :message_ts
 ORDER BY ts DESC, mid DESC
 LIMIT :context_message_limit
```

For thread replies, scope context to the same thread:

```sql
SELECT sid, mid, rmid, rtmid, u, c, t, ts
  FROM chat_message
 WHERE sid = :sid
   AND ts < :message_ts
   AND (
        mid = :thread_root_mid
        OR rtmid = :thread_root_mid
        OR ((rtmid IS NULL OR rtmid = '') AND rmid = :thread_root_mid)
   )
 ORDER BY ts DESC, mid DESC
 LIMIT :context_message_limit
```

If `rmid` points to a specific message being replied to, the collector may also
include that exact row as `context.reply_to_message`.

## Event Mapping

Each collected row should produce `subject.kind =
communication_user_message`.

Recommended fields:

- `source.type`: `messaging`.
- `source.app`: `SeaTalk`.
- `source.format`: `seatalk_sqlite_main`.
- `anchor.type`: `communication_message`.
- `anchor.name`: `user_message_sent`.
- `anchor.id`: `seatalk:{sid}:{mid}`.
- `subject.title`: `User message in <conversation name>`.
- `content.text`: extracted readable text from `chat_message.c`.
- `context.conversation_id`: `sid`.
- `context.conversation_type`: `direct`, `group`, or `unknown`.
- `context.previous_messages`: previous local context rows.
- `extra_data.seatalk`: raw Seatalk identifiers and message metadata.
- `provenance.source_message_ids`: `["seatalk:{sid}:{mid}"]`.

## Processor Boundary

The collector should not decide whether a message is important, safe to learn
from, or action-worthy. Processors own task extraction, privacy redaction,
ranking, and learning.

The collector should only enforce source scope:

- Include messages authored by the user.
- Include previous messages only as bounded context for those user-authored
  messages.
- Do not emit inbound direct messages, mentions, or group messages as standalone
  observations by default.

## Heartbeat Status

Collector heartbeats should include high-level state only:

```json
{
    "status": "ok",
    "permissions": {
        "seatalk_main_db_readable": true
    },
    "last_scan_started_at": 1780713574000,
    "last_scan_completed_at": 1780713575000,
    "messages_seen": 12,
    "messages_changed": 2,
    "events_written": 2,
    "last_write": "/Users/example/.radar/collectors/seatalk_personal/20260606/1780713574000.jsonl",
    "last_error": ""
}
```

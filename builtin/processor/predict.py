import argparse
import hashlib
import itertools
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


PROCESSOR_ID = "builtin.predict"
PROTOCOL_VERSION = 1
ESTDEC_ALGORITHM = "NativeEstDec"
DEFAULT_RADAR_HOME = Path("~/.radar").expanduser()
DEFAULT_STATE_DIR = DEFAULT_RADAR_HOME / "processors" / "builtin_predict"

DEFAULT_TEXT_FIELDS = {"title", "kind", "name", "type", "app", "user_action"}
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:-]{1,64}")


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def normalize_value(value):
    text = str(value).strip().lower()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_.:=/-]+", "", text)
    return text[:120]


def stable_json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def stable_hash(value):
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()[:16]


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {error}") from error


def iter_jsonl_files(path):
    root = Path(path)
    if root.is_file():
        yield root
        return
    for candidate in sorted(root.rglob("*.jsonl")):
        if candidate.is_file():
            yield candidate


def source_refs_for_event(event):
    refs = []
    collector_id = event.get("collector_id")
    event_id = event.get("id")
    if collector_id or event_id:
        refs.append(
            {
                "collector_id": collector_id,
                "event_id": event_id,
                "source_uri": event.get("source_uri")
                or event.get("source", {}).get("uri"),
                "source_fingerprint": event.get("source_fingerprint"),
                "session_key": event.get("session_key"),
                "source_message_ids": event.get("source_message_ids", []),
                "tool_ids": event.get("tool_ids", []),
            }
        )
    return [ref for ref in refs if any(value for value in ref.values())]


def is_compact_normalized_event(event):
    return (
        isinstance(event, dict)
        and isinstance(event.get("collector_id"), str)
        and isinstance(event.get("payload"), str)
        and "timestamp" in event
    )


@dataclass
class EventNormalizer:
    text_fields: set[str] = field(default_factory=lambda: set(DEFAULT_TEXT_FIELDS))
    include_content_hash: bool = False
    max_text_tokens: int = 12

    def normalize(self, event):
        if is_compact_normalized_event(event):
            payload = event["payload"].strip()
            return [payload] if payload else []

        items = set()
        self._walk(event, (), items)

        if self.include_content_hash and "content" in event:
            items.add(f"content_hash={stable_hash(event['content'])}")

        return sorted(items)

    def _walk(self, value, path, items):
        if isinstance(value, dict):
            for key, child in value.items():
                self._walk(child, (*path, key), items)
            return

        if isinstance(value, list):
            for child in value[:20]:
                self._walk(child, path, items)
            return

        if value is None or isinstance(value, bool):
            return

        key = path[-1] if path else "value"
        prefix = ".".join(path[-3:])
        normalized = normalize_value(value)
        if not normalized:
            return

        if isinstance(value, (int, float)):
            items.add(f"{prefix}=number")
            return

        if key in self.text_fields:
            items.add(f"{prefix}={normalized}")
            return

        if key == "text":
            tokens = TOKEN_RE.findall(str(value).lower())[: self.max_text_tokens]
            for token in tokens:
                items.add(f"{prefix}:token={token}")
            return

        if len(normalized) <= 80:
            items.add(f"{prefix}={normalized}")


@dataclass
class ItemDictionary:
    item_to_id: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        with path.open(encoding="utf-8") as input_file:
            data = json.load(input_file)
        return cls({str(key): int(value) for key, value in data.get("item_to_id", {}).items()})

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8") as output_file:
            json.dump(
                {"item_to_id": self.item_to_id, "id_to_item": self.id_to_item()},
                output_file,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            output_file.write("\n")
        tmp_path.replace(path)

    def id_to_item(self):
        return {str(item_id): item for item, item_id in self.item_to_id.items()}

    def encode(self, items):
        ids = []
        for item in sorted(set(items)):
            if item not in self.item_to_id:
                self.item_to_id[item] = len(self.item_to_id) + 1
            ids.append(self.item_to_id[item])
        return sorted(ids)

    def decode(self, item_ids):
        mapping = {item_id: item for item, item_id in self.item_to_id.items()}
        return [mapping.get(item_id, str(item_id)) for item_id in item_ids]


@dataclass
class EncodedEvent:
    event: dict
    source_file: str
    line_number: int
    items: list[str]
    item_ids: list[int]

    def transaction_line(self):
        return " ".join(str(item_id) for item_id in self.item_ids)


class TransactionStore:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, encoded_events):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with self.path.open("a", encoding="utf-8") as output_file:
            for encoded in encoded_events:
                if not encoded.item_ids:
                    continue
                output_file.write(encoded.transaction_line())
                output_file.write("\n")
                count += 1
        return count


@dataclass
class PredictionConfig:
    # Demo defaults assume about 0.5 normalized events/sec and a 5 minute half-life.
    min_support: float = 0.01
    min_confidence: float = 0.01
    min_transactions_before_prediction: int = 30
    min_pattern_decayed_count: float = 3.5
    decay_rate: float = 0.0046209812
    max_items: int = 10000
    max_pattern_size: int = 5
    max_transaction_items: int = 32
    max_candidates_per_transaction: int = 2000
    prune_below_support: float = 0.001
    prune_every: int = 100
    min_count: float = 0.01

    @classmethod
    def from_env(cls):
        defaults = cls()
        return cls(
            min_support=env_float("RADAR_ESTDEC_MIN_SUPPORT", defaults.min_support),
            min_confidence=env_float(
                "RADAR_PREDICT_MIN_CONFIDENCE",
                defaults.min_confidence,
            ),
            min_transactions_before_prediction=env_int(
                "RADAR_PREDICT_MIN_TRANSACTIONS",
                defaults.min_transactions_before_prediction,
            ),
            min_pattern_decayed_count=env_float(
                "RADAR_PREDICT_MIN_PATTERN_DECAYED_COUNT",
                defaults.min_pattern_decayed_count,
            ),
            decay_rate=env_float("RADAR_ESTDEC_DECAY_RATE", defaults.decay_rate),
            max_items=env_int("RADAR_ESTDEC_MAX_ITEMS", defaults.max_items),
            max_pattern_size=env_int(
                "RADAR_ESTDEC_MAX_PATTERN_SIZE",
                defaults.max_pattern_size,
            ),
            max_transaction_items=env_int(
                "RADAR_ESTDEC_MAX_TRANSACTION_ITEMS",
                defaults.max_transaction_items,
            ),
            max_candidates_per_transaction=env_int(
                "RADAR_ESTDEC_MAX_CANDIDATES_PER_TRANSACTION",
                defaults.max_candidates_per_transaction,
            ),
            prune_below_support=env_float(
                "RADAR_ESTDEC_PRUNE_BELOW_SUPPORT",
                defaults.prune_below_support,
            ),
            prune_every=env_int("RADAR_ESTDEC_PRUNE_EVERY", defaults.prune_every),
            min_count=env_float("RADAR_ESTDEC_MIN_COUNT", defaults.min_count),
        )


class EstDecRunner:
    def __init__(self, config, model_path):
        self.config = config
        self.model_path = Path(model_path)
        self.model = self._load()

    def update(self, encoded_events, dictionary):
        for encoded in encoded_events:
            if encoded.item_ids:
                self._observe(encoded.item_ids)

        self._prune()
        self._save()
        patterns = self._patterns(dictionary)
        return {
            "available": True,
            "patterns": patterns,
            "transactions_seen": self.model["transactions_seen"],
            "effective_transaction_count": self.model["effective_transaction_count"],
            "pattern_count": len(self.model["counts"]),
        }

    def _load(self):
        if not self.model_path.exists():
            return {
                "transactions_seen": 0,
                "effective_transaction_count": 0.0,
                "counts": {},
            }
        with self.model_path.open(encoding="utf-8") as input_file:
            data = json.load(input_file)
        return {
            "transactions_seen": int(data.get("transactions_seen", 0)),
            "effective_transaction_count": float(data.get("effective_transaction_count", 0.0)),
            "counts": {str(key): float(value) for key, value in data.get("counts", {}).items()},
        }

    def _save(self):
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.model_path.with_suffix(f"{self.model_path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8") as output_file:
            json.dump(self.model, output_file, ensure_ascii=True, indent=2, sort_keys=True)
            output_file.write("\n")
        tmp_path.replace(self.model_path)

    def _observe(self, item_ids):
        decay = math.exp(-max(self.config.decay_rate, 0.0))
        counts = self.model["counts"]
        for key in list(counts):
            counts[key] *= decay

        self.model["effective_transaction_count"] *= decay
        self.model["effective_transaction_count"] += 1.0
        self.model["transactions_seen"] += 1

        for pattern in self._candidate_patterns(item_ids):
            key = self._pattern_key(pattern)
            counts[key] = counts.get(key, 0.0) + 1.0

        if self.model["transactions_seen"] % max(self.config.prune_every, 1) == 0:
            self._prune()

    def _candidate_patterns(self, item_ids):
        limited_ids = sorted(set(item_ids))[: max(self.config.max_transaction_items, 1)]
        emitted = 0
        max_pattern_size = max(self.config.max_pattern_size, 1)
        max_candidates = max(self.config.max_candidates_per_transaction, 1)
        for size in range(1, max_pattern_size + 1):
            for pattern in itertools.combinations(limited_ids, size):
                yield pattern
                emitted += 1
                if emitted >= max_candidates:
                    return

    def _prune(self):
        total = self.model["effective_transaction_count"]
        if total <= 0:
            self.model["counts"] = {}
            return

        min_count = max(
            self.config.min_count,
            total * min(self.config.prune_below_support, self.config.min_support),
        )
        self.model["counts"] = {
            key: count
            for key, count in self.model["counts"].items()
            if count >= min_count
        }

    def _patterns(self, dictionary):
        total = self.model["effective_transaction_count"]
        if total <= 0:
            return []

        patterns = []
        for key, count in self.model["counts"].items():
            item_ids = self._parse_pattern_key(key)
            support = count / total
            if support < self.config.min_support:
                continue
            if count < self.config.min_pattern_decayed_count:
                continue
            patterns.append(
                {
                    "item_ids": item_ids,
                    "items": dictionary.decode(item_ids),
                    "decayed_count": round(count, 6),
                    "support": round(support, 6),
                    "size": len(item_ids),
                }
            )

        patterns.sort(key=lambda pattern: (-pattern["support"], -pattern["size"], pattern["items"]))
        return patterns[: self.config.max_items]

    @staticmethod
    def _pattern_key(item_ids):
        return " ".join(str(item_id) for item_id in item_ids)

    @staticmethod
    def _parse_pattern_key(key):
        return [int(token) for token in key.split() if token]


class PredictProcessor:
    def __init__(self, state_dir=None, normalizer=None, prediction_config=None):
        self.state_dir = Path(state_dir or os.environ.get("RADAR_PREDICT_STATE_DIR", DEFAULT_STATE_DIR))
        self.dictionary_path = self.state_dir / "item_dictionary.json"
        self.model_path = self.state_dir / "estdec_model.json"
        self.stream_path = self.state_dir / "spmf_stream.txt"
        self.dictionary = ItemDictionary.load(self.dictionary_path)
        self.normalizer = normalizer or EventNormalizer()
        self.estdec = EstDecRunner(
            prediction_config or PredictionConfig.from_env(),
            self.model_path,
        )

    def reset(self):
        for path in (self.dictionary_path, self.model_path, self.stream_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self.dictionary = ItemDictionary()
        self.estdec = EstDecRunner(self.estdec.config, self.model_path)

    def process_payloads(self, payloads, input_ref="payloads"):
        encoded = []
        for index, payload in enumerate(payloads, start=1):
            payload = str(payload or "").strip()
            if not payload:
                continue
            item_ids = self.dictionary.encode([payload])
            encoded.append(
                EncodedEvent(
                    event={"payload": payload},
                    source_file=str(input_ref),
                    line_number=index,
                    items=[payload],
                    item_ids=item_ids,
                )
            )
        return self._process_encoded(input_ref, encoded)

    def process_payload(self, payload, input_ref="payload"):
        return self.process_payloads([payload], input_ref=input_ref)

    def snapshot(self, input_ref="model"):
        estdec_result = {
            "available": True,
            "patterns": self.estdec._patterns(self.dictionary),
            "transactions_seen": self.estdec.model["transactions_seen"],
            "effective_transaction_count": self.estdec.model["effective_transaction_count"],
            "pattern_count": len(self.estdec.model["counts"]),
        }
        return self._build_result(input_ref, [], 0, estdec_result)

    def process_path(self, input_ref):
        encoded = []
        for jsonl_file in iter_jsonl_files(input_ref):
            for line_number, event in read_jsonl(jsonl_file):
                items = self.normalizer.normalize(event)
                item_ids = self.dictionary.encode(items)
                encoded.append(
                    EncodedEvent(
                        event=event,
                        source_file=str(jsonl_file),
                        line_number=line_number,
                        items=items,
                        item_ids=item_ids,
                    )
                )

        return self._process_encoded(input_ref, encoded)

    def _process_encoded(self, input_ref, encoded):
        written = TransactionStore(self.stream_path).append(encoded)
        self.dictionary.save(self.dictionary_path)
        estdec_result = self.estdec.update(encoded, self.dictionary)
        return self._build_result(input_ref, encoded, written, estdec_result)

    def _build_result(self, input_ref, encoded, written, estdec_result):
        source_refs = []
        for encoded_event in encoded[:25]:
            source_refs.extend(source_refs_for_event(encoded_event.event))

        patterns = estdec_result.get("patterns", [])
        confidence = round(max((pattern.get("support", 0.0) for pattern in patterns), default=0.0), 6)
        transactions_seen = int(estdec_result.get("transactions_seen", 0) or 0)
        warmup_remaining = max(
            self.estdec.config.min_transactions_before_prediction - transactions_seen,
            0,
        )
        ready = bool(
            patterns
            and warmup_remaining == 0
            and confidence >= self.estdec.config.min_confidence
        )
        if warmup_remaining > 0:
            status = "learning"
            reason = "minimum transaction history has not been reached"
        elif not patterns:
            status = "learning"
            reason = "no pattern satisfies support and decayed-count thresholds"
        elif confidence < self.estdec.config.min_confidence:
            status = "learning"
            reason = "top pattern confidence is below threshold"
        else:
            status = "ready"
            reason = ""

        return {
            "id": str(uuid.uuid4()),
            "processor_id": PROCESSOR_ID,
            "input_refs": [str(input_ref)],
            "source_refs": source_refs,
            "kind": "prediction_set",
            "created_at": utc_timestamp(),
            "confidence": confidence if ready else 0.0,
            "privacy": {
                "contains_raw_content": False,
                "redaction_applied": True,
            },
            "payload": {
                "algorithm": "estdec",
                "implementation": "native_python",
                "estdec_algorithm": ESTDEC_ALGORITHM,
                "events_processed": len(encoded),
                "transactions_appended": written,
                "dictionary_size": len(self.dictionary.item_to_id),
                "stream_path": str(self.stream_path),
                "dictionary_path": str(self.dictionary_path),
                "model_path": str(self.model_path),
                "estdec_available": estdec_result.get("available", False),
                "ready": ready,
                "status": status,
                "reason": reason,
                "confidence": confidence,
                "min_confidence": self.estdec.config.min_confidence,
                "min_transactions_before_prediction": self.estdec.config.min_transactions_before_prediction,
                "warmup_remaining": warmup_remaining,
                "min_pattern_decayed_count": self.estdec.config.min_pattern_decayed_count,
                "min_support": self.estdec.config.min_support,
                "transactions_seen": transactions_seen,
                "effective_transaction_count": estdec_result.get("effective_transaction_count", 0.0),
                "tracked_pattern_count": estdec_result.get("pattern_count", 0),
                "patterns": patterns if ready else [],
                "candidate_patterns": patterns,
                "error": estdec_result.get("error"),
            },
        }


def register_processor(client):
    return client.call(
        "processor:register",
        {
            "processor_id": PROCESSOR_ID,
            "protocol_version": PROTOCOL_VERSION,
            "accepts": ["collector_work_dir", "collector_file"],
            "produces": ["prediction_set"],
            "capabilities": ["event_normalization", "stream_pattern_detection", "estdec"],
            "metadata": {
                "display_name": "Predict Processor",
                "algorithm": "Native EstDec-style decayed frequent itemsets",
            },
        },
        timeout=5,
    )


def pid_exists(pid):
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start_coordinator_watchdog(pid):
    if not pid:
        return

    def watch():
        while True:
            if not pid_exists(pid):
                print("coordinator process exited; exiting orphaned processor", flush=True)
                os._exit(0)
            time.sleep(0.5)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()


def connect_client(client, coordinator_url, timeout):
    import socketio

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
    import socketio

    start_coordinator_watchdog(args.coordinator_pid)
    processor = PredictProcessor(state_dir=args.state_dir)
    client = socketio.Client()

    @client.on("coordinator:process_request")
    def on_process_request(payload):
        input_ref = (payload or {}).get("path") or (payload or {}).get("work_dir") or (payload or {}).get("file")
        if not input_ref:
            return
        result = processor.process_path(input_ref)
        client.call("processor:result", result, timeout=args.heartbeat_timeout)

    connect_client(client, args.coordinator_url, args.connect_timeout)
    try:
        registration = register_processor(client)
        if not registration.get("ok"):
            raise RuntimeError(f"processor registration failed: {registration}")
        print(f"registered {PROCESSOR_ID}")

        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        while deadline is None or time.monotonic() < deadline:
            if args.coordinator_pid and not pid_exists(args.coordinator_pid):
                print("coordinator process exited; exiting orphaned processor")
                break
            client.call("processor:heartbeat", {"status": "ok"}, timeout=args.heartbeat_timeout)
            time.sleep(max(args.heartbeat_interval, 0.1))
    finally:
        if client.connected:
            client.disconnect()


def run_once(args):
    processor = PredictProcessor(state_dir=args.state_dir)
    result = processor.process_path(args.input)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))


def parse_args():
    parser = argparse.ArgumentParser(description="Normalize Radar events and run a bundled EstDec-style processor.")
    parser.add_argument("--input", help="JSONL file or folder to process once. If omitted, runs as a Socket.IO processor.")
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("RADAR_PREDICT_STATE_DIR", str(DEFAULT_STATE_DIR)),
        help="Directory for the SPMF transaction stream and item dictionary.",
    )
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://localhost:5000"),
        help="Socket.IO coordinator URL.",
    )
    parser.add_argument(
        "--duration",
        default=env_float("RADAR_PREDICT_DURATION", 0),
        type=float,
        help="Seconds to stay registered. Use <= 0 to run forever.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_PROCESSOR_HEARTBEAT_INTERVAL", 2),
        type=float,
        help="Seconds between processor heartbeats.",
    )
    parser.add_argument(
        "--heartbeat-timeout",
        default=env_float("RADAR_PROCESSOR_HEARTBEAT_TIMEOUT", 5),
        type=float,
        help="Seconds to wait for coordinator acknowledgements.",
    )
    parser.add_argument(
        "--connect-timeout",
        default=env_float("RADAR_PROCESSOR_CONNECT_TIMEOUT", 10),
        type=float,
        help="Seconds to wait for the coordinator before failing.",
    )
    parser.add_argument(
        "--coordinator-pid",
        default=env_int("RADAR_COORDINATOR_PID", 0),
        type=int,
        help="Coordinator process PID. If it exits, this processor exits as orphaned.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.input:
        run_once(args)
    else:
        run_socket_processor(args)


if __name__ == "__main__":
    main()

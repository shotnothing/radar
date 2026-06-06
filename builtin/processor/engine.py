import argparse
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from builtin.processor.predict import PredictProcessor


DEFAULT_NORMALIZERS_ROOT = Path(__file__).resolve().parent / "normalizers"
DEFAULT_STATE_DIR = Path("~/.radar/processors/builtin_engine").expanduser()


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def expand_path(value):
    return Path(os.path.expandvars(str(value))).expanduser()


def default_collectors_root():
    configured = os.environ.get("RADAR_HOME")
    if configured:
        return expand_path(configured) / "collectors"

    debug_root = REPO_ROOT / "debug" / "work" / "collectors"
    if debug_root.exists():
        return debug_root

    return Path("~/.radar/collectors").expanduser()


def safe_module_name(value):
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_") or "normalizer"


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as input_file:
            return json.load(input_file)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=True, indent=2, sort_keys=True)
        output_file.write("\n")
    tmp_path.replace(path)


def append_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            output_file.write("\n")


def iter_collector_jsonl_files(collectors_root):
    root = Path(collectors_root)
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*.jsonl")
        if path.is_file() and "state" not in path.parts
    )


@dataclass
class LoadedNormalizer:
    collector_id: str
    path: Path
    module: object

    def normalize_event(self, event):
        raw = self.module.normalize_event(event)
        if isinstance(raw, str):
            return {
                "collector_id": self.collector_id,
                "timestamp": 0,
                "payload": raw,
            }
        if not isinstance(raw, dict):
            return None

        payload = str(raw.get("payload") or "").strip()
        if not payload:
            return None

        return {
            "collector_id": str(raw.get("collector_id") or self.collector_id),
            "timestamp": raw.get("timestamp") or 0,
            "payload": payload,
        }


def load_normalizer(collector_id, path):
    module_path = Path(path) / "normalizer.py"
    if not module_path.exists():
        return None

    module_name = f"radar_normalizer_{safe_module_name(collector_id)}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "normalize_event"):
        return None
    return LoadedNormalizer(collector_id=collector_id, path=module_path, module=module)


def load_normalizers(normalizers_root):
    root = Path(normalizers_root)
    if not root.exists():
        return {}

    normalizers = {}
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        normalizer = load_normalizer(candidate.name, candidate)
        if normalizer:
            normalizers[normalizer.collector_id] = normalizer
    return normalizers


class ProcessorEngine:
    def __init__(
        self,
        *,
        collectors_root,
        normalizers_root=DEFAULT_NORMALIZERS_ROOT,
        state_dir=DEFAULT_STATE_DIR,
        predict_state_dir=None,
    ):
        self.collectors_root = Path(collectors_root)
        self.normalizers_root = Path(normalizers_root)
        self.state_dir = Path(state_dir)
        self.checkpoint_path = self.state_dir / "checkpoint.json"
        self.normalized_log_path = self.state_dir / "normalized_events.jsonl"
        self.last_result_path = self.state_dir / "last_result.json"
        self.predict = PredictProcessor(
            state_dir=predict_state_dir or self.state_dir / "predict"
        )
        self.checkpoint = read_json(
            self.checkpoint_path,
            {"version": 1, "files": {}},
        )

    def reset(self):
        self.checkpoint = {"version": 1, "files": {}}
        write_json_atomic(self.checkpoint_path, self.checkpoint)

    def scan_once(self):
        normalizers = load_normalizers(self.normalizers_root)
        normalized_records = []
        files_seen = 0
        events_seen = 0
        skipped_without_normalizer = 0
        skipped_without_payload = 0
        errors = []

        for path in iter_collector_jsonl_files(self.collectors_root):
            files_seen += 1
            records, file_errors = self._read_new_records(path)
            errors.extend(file_errors)
            for event in records:
                events_seen += 1
                collector_id = event.get("collector_id")
                normalizer = normalizers.get(collector_id)
                if normalizer is None:
                    skipped_without_normalizer += 1
                    continue
                try:
                    normalized = normalizer.normalize_event(event)
                except Exception as error:  # pragma: no cover - runtime guard.
                    errors.append(f"{path}: normalizer {collector_id} failed: {error}")
                    continue
                if not normalized:
                    skipped_without_payload += 1
                    continue
                normalized_records.append(normalized)

        prediction = None
        if normalized_records:
            append_jsonl(self.normalized_log_path, normalized_records)
            prediction = self.predict.process_payloads(
                [record["payload"] for record in normalized_records],
                input_ref=str(self.normalized_log_path),
            )
            write_json_atomic(self.last_result_path, prediction)

        self.checkpoint["updated_at"] = utc_timestamp()
        write_json_atomic(self.checkpoint_path, self.checkpoint)

        return {
            "created_at": utc_timestamp(),
            "collectors_root": str(self.collectors_root),
            "normalizers_root": str(self.normalizers_root),
            "state_dir": str(self.state_dir),
            "files_seen": files_seen,
            "events_seen": events_seen,
            "normalized_count": len(normalized_records),
            "skipped_without_normalizer": skipped_without_normalizer,
            "skipped_without_payload": skipped_without_payload,
            "normalized_log_path": str(self.normalized_log_path),
            "last_result_path": str(self.last_result_path) if prediction else "",
            "prediction": prediction,
            "errors": errors[-10:],
        }

    def _read_new_records(self, path):
        path = Path(path)
        key = str(path)
        file_state = self.checkpoint.setdefault("files", {}).setdefault(key, {})
        offset = int(file_state.get("offset") or 0)
        errors = []
        records = []

        try:
            size = path.stat().st_size
            if offset > size:
                offset = 0
            with path.open("r", encoding="utf-8") as input_file:
                input_file.seek(offset)
                while True:
                    line = input_file.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as error:
                        errors.append(f"{path}: invalid JSONL: {error}")
                        continue
                    if isinstance(value, dict):
                        records.append(value)
                file_state["offset"] = input_file.tell()
                file_state["size"] = size
                file_state["updated_at"] = utc_timestamp()
        except OSError as error:
            errors.append(f"{path}: {error}")

        return records, errors


def run_watch(args):
    engine = ProcessorEngine(
        collectors_root=args.collectors_root,
        normalizers_root=args.normalizers_root,
        state_dir=args.state_dir,
        predict_state_dir=args.predict_state_dir,
    )
    if args.reset:
        engine.reset()

    deadline = None if args.duration <= 0 else time.monotonic() + args.duration
    while True:
        result = engine.scan_once()
        if args.print_empty or result["normalized_count"]:
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            sys.stdout.flush()

        if args.once:
            return
        if deadline is not None and time.monotonic() >= deadline:
            return
        time.sleep(max(args.interval, 0.1))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Watch collector outputs, normalize by collector id, and feed predict strings."
    )
    parser.add_argument(
        "--collectors-root",
        default=os.environ.get("RADAR_COLLECTORS_ROOT", str(default_collectors_root())),
        help="Root containing collector work directories.",
    )
    parser.add_argument(
        "--normalizers-root",
        default=os.environ.get("RADAR_NORMALIZERS_ROOT", str(DEFAULT_NORMALIZERS_ROOT)),
        help="Root containing collector-id normalizer directories.",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("RADAR_ENGINE_STATE_DIR", str(DEFAULT_STATE_DIR)),
        help="Engine checkpoint and normalized output directory.",
    )
    parser.add_argument(
        "--predict-state-dir",
        default=os.environ.get("RADAR_PREDICT_STATE_DIR"),
        help="Optional predict model state directory. Defaults under engine state.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=env_float("RADAR_ENGINE_INTERVAL", 2),
        help="Seconds between scans while watching.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=env_float("RADAR_ENGINE_DURATION", 0),
        help="Seconds to watch. Use <= 0 to run until interrupted.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan and exit.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset file offsets before scanning.",
    )
    parser.add_argument(
        "--print-empty",
        action="store_true",
        help="Print scan summaries even when no new records are normalized.",
    )
    return parser.parse_args()


def main():
    run_watch(parse_args())


if __name__ == "__main__":
    main()

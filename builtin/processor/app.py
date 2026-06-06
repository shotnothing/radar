import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify
from flask_socketio import SocketIO, emit


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from builtin.processor.engine import (
    DEFAULT_NORMALIZERS_ROOT,
    ProcessorEngine,
    apply_predict_config_overrides,
    default_collectors_root,
    env_float,
    iter_collector_jsonl_files,
    utc_timestamp,
    write_json_atomic,
)


DEFAULT_APP_STATE_DIR = Path("~/.radar/processors/builtin_processor_app").expanduser()
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5060


app = Flask(__name__)
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
runtime = {
    "started_at": utc_timestamp(),
    "engine": None,
    "last_prediction": None,
    "last_scan": None,
    "last_learning": None,
    "last_error": "",
    "connected_clients": 0,
}


def mark_existing_collector_files_seen(engine):
    engine.checkpoint = {"version": 1, "files": {}, "updated_at": utc_timestamp()}
    for path in iter_collector_jsonl_files(engine.collectors_root):
        try:
            size = Path(path).stat().st_size
        except OSError:
            continue
        engine.checkpoint["files"][str(path)] = {
            "offset": size,
            "size": size,
            "updated_at": utc_timestamp(),
        }
    write_json_atomic(engine.checkpoint_path, engine.checkpoint)


def compact_prediction(scan_result, max_patterns):
    prediction = scan_result.get("prediction") or {}
    payload = prediction.get("payload") or {}
    patterns = payload.get("patterns") or []
    return {
        "type": "prediction_generated",
        "created_at": scan_result.get("created_at"),
        "prediction_id": prediction.get("id"),
        "confidence": prediction.get("confidence", 0.0),
        "min_confidence": payload.get("min_confidence", 0.0),
        "normalized_count": scan_result.get("normalized_count", 0),
        "events_processed": payload.get("events_processed", 0),
        "transactions_seen": payload.get("transactions_seen", 0),
        "min_transactions_before_prediction": payload.get(
            "min_transactions_before_prediction",
            0,
        ),
        "min_pattern_decayed_count": payload.get("min_pattern_decayed_count", 0.0),
        "min_support": payload.get("min_support", 0.0),
        "dictionary_size": payload.get("dictionary_size", 0),
        "effective_transaction_count": payload.get("effective_transaction_count", 0),
        "patterns": patterns[:max_patterns],
        "normalized_log_path": scan_result.get("normalized_log_path"),
        "last_result_path": scan_result.get("last_result_path"),
        "errors": scan_result.get("errors") or [],
    }


def scan_loop(args):
    apply_predict_config_overrides(args)
    engine = ProcessorEngine(
        collectors_root=args.collectors_root,
        normalizers_root=args.normalizers_root,
        state_dir=args.state_dir,
        predict_state_dir=args.predict_state_dir,
    )
    runtime["engine"] = engine

    if args.tail_existing:
        if args.reset:
            engine.reset()
            mark_existing_collector_files_seen(engine)
        elif not engine.checkpoint_path.exists():
            mark_existing_collector_files_seen(engine)
    elif args.replay_existing or args.reset:
        engine.reset()

    while True:
        try:
            scan_result = engine.scan_once()
            learning = scan_result.get("learning") or {}
            if learning.get("status"):
                runtime["last_learning"] = learning
            runtime["last_scan"] = {
                "created_at": scan_result.get("created_at"),
                "files_seen": scan_result.get("files_seen", 0),
                "events_seen": scan_result.get("events_seen", 0),
                "normalized_count": scan_result.get("normalized_count", 0),
                "learning": runtime["last_learning"] or learning,
                "errors": scan_result.get("errors") or [],
            }
            runtime["last_error"] = ""

            if scan_result.get("prediction"):
                event = (
                    scan_result["prediction"]
                    if args.full
                    else compact_prediction(scan_result, args.max_patterns)
                )
                runtime["last_prediction"] = event
                socketio.emit("prediction_generated", event)
        except Exception as error:  # pragma: no cover - defensive app loop.
            runtime["last_error"] = str(error)
            socketio.emit(
                "processor_error",
                {
                    "type": "processor_error",
                    "created_at": utc_timestamp(),
                    "error": str(error),
                },
            )

        time.sleep(max(args.interval, 0.1))


@app.get("/health")
def health():
    engine = runtime.get("engine")
    return jsonify(
        {
            "ok": True,
            "started_at": runtime["started_at"],
            "connected_clients": runtime["connected_clients"],
            "last_error": runtime["last_error"],
            "last_scan": runtime["last_scan"],
            "has_last_prediction": runtime["last_prediction"] is not None,
            "collectors_root": str(engine.collectors_root) if engine else "",
            "normalizers_root": str(engine.normalizers_root) if engine else "",
            "state_dir": str(engine.state_dir) if engine else "",
        }
    )


@app.get("/last_prediction")
def last_prediction():
    return jsonify(runtime["last_prediction"] or {})


@socketio.on("connect")
def handle_connect():
    runtime["connected_clients"] += 1
    emit(
        "processor_connected",
        {
            "type": "processor_connected",
            "created_at": utc_timestamp(),
            "has_last_prediction": runtime["last_prediction"] is not None,
        },
    )
    if runtime["last_prediction"] is not None:
        emit("prediction_generated", runtime["last_prediction"])


@socketio.on("disconnect")
def handle_disconnect():
    runtime["connected_clients"] = max(0, runtime["connected_clients"] - 1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Socket.IO app that broadcasts processor predictions."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("RADAR_PROCESSOR_APP_HOST", DEFAULT_HOST),
        help="Host to bind.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("RADAR_PROCESSOR_APP_PORT", DEFAULT_PORT)),
        help="Port to bind.",
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
        default=os.environ.get("RADAR_PROCESSOR_APP_STATE_DIR", str(DEFAULT_APP_STATE_DIR)),
        help="App checkpoint and normalized output directory.",
    )
    parser.add_argument(
        "--predict-state-dir",
        default=os.environ.get("RADAR_PROCESSOR_APP_PREDICT_STATE_DIR"),
        help="Optional predict model state directory. Defaults under app state.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=env_float("RADAR_PROCESSOR_APP_INTERVAL", 1),
        help="Seconds between collector scans.",
    )
    parser.add_argument(
        "--max-patterns",
        type=int,
        default=10,
        help="Maximum patterns to include in compact emitted events.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset app offsets before startup processing.",
    )
    parser.add_argument(
        "--replay-existing",
        action="store_true",
        help="Force reprocessing existing collector records on startup.",
    )
    parser.add_argument(
        "--tail-existing",
        action="store_true",
        help="Do not learn from existing collector records on first startup; only watch new appended records.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Emit the full prediction result instead of a compact event.",
    )
    parser.add_argument(
        "--min-transactions-before-prediction",
        type=int,
        help="Minimum total learned transactions required before predictions are emitted.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        help="Minimum top-pattern confidence required before predictions are emitted.",
    )
    parser.add_argument(
        "--min-pattern-decayed-count",
        type=float,
        help="Minimum decayed count required for a pattern to be eligible.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    worker = threading.Thread(target=scan_loop, args=(args,), daemon=True)
    worker.start()
    socketio.run(app, host=args.host, port=args.port, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()

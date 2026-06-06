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
from builtin.processor.llm import LLMClient, LLMError, OPENAI_API_KEY_ENV


DEFAULT_APP_STATE_DIR = Path("~/.radar/processors/builtin_processor_app").expanduser()
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5060
LLM_HISTORY_LIMIT = 4


app = Flask(__name__)
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
runtime = {
    "started_at": utc_timestamp(),
    "engine": None,
    "last_prediction": None,
    "last_scan": None,
    "last_learning": None,
    "last_error": "",
    "args": None,
    "llm_client": None,
    "connected_clients": 0,
}


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


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


def read_recent_normalized_history(path, limit=LLM_HISTORY_LIMIT):
    path = Path(path)
    if limit <= 0 or not path.exists():
        return []

    records = []
    try:
        with path.open(encoding="utf-8") as input_file:
            for line in input_file:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                records.append(
                    {
                        "collector_id": record.get("collector_id"),
                        "timestamp": record.get("timestamp"),
                        "payload": record.get("payload"),
                    }
                )
    except OSError:
        return []
    return records[-limit:]


def llm_enabled(args):
    return bool(not args.disable_llm and os.environ.get(OPENAI_API_KEY_ENV))


def llm_client(args):
    current = runtime.get("llm_client")
    if current is None:
        current = LLMClient(base_url=args.llm_base_url, timeout=args.llm_timeout)
        runtime["llm_client"] = current
    return current


def enrich_prediction_with_llm(event, scan_result, args):
    if not llm_enabled(args):
        event["llm"] = {
            "enabled": False,
            "status": (
                "missing_api_key"
                if not os.environ.get(OPENAI_API_KEY_ENV)
                else "disabled"
            ),
        }
        return event

    history = read_recent_normalized_history(scan_result.get("normalized_log_path"))
    context = {
        "created_at": scan_result.get("created_at"),
        "normalized_count": scan_result.get("normalized_count", 0),
        "events_seen": scan_result.get("events_seen", 0),
    }
    try:
        client = llm_client(args)
        review = client.review_prediction(event, history, context=context)
        event["llm"] = {
            "enabled": True,
            "status": "ok",
            "review": {
                "makes_sense": review.makes_sense,
                "should_show": review.should_show,
                "confidence": review.confidence,
                "reason": review.reason,
                "relevant_history_indices": review.relevant_history_indices,
            },
        }
        event["should_show"] = review.should_show
        if review.should_show:
            suggestion = client.suggest_action(event, history, context=context)
            event["suggested_action"] = {
                "title": suggestion.title,
                "body": suggestion.body,
                "action_text": suggestion.action_text,
                "primary_action": suggestion.action_text,
            }
        return event
    except LLMError as error:
        event["llm"] = {
            "enabled": True,
            "status": "error",
            "error": str(error),
        }
        return event


def scan_loop(args):
    runtime["args"] = args
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
            socketio.emit(
                "processor_scan",
                {
                    "type": "processor_scan",
                    **runtime["last_scan"],
                    "has_last_prediction": runtime["last_prediction"] is not None,
                    "collectors_root": str(engine.collectors_root),
                    "normalizers_root": str(engine.normalizers_root),
                    "state_dir": str(engine.state_dir),
                },
            )

            if scan_result.get("prediction"):
                event = (
                    scan_result["prediction"]
                    if args.full
                    else compact_prediction(scan_result, args.max_patterns)
                )
                event = enrich_prediction_with_llm(event, scan_result, args)
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
    args = runtime.get("args")
    return jsonify(
        {
            "ok": True,
            "started_at": runtime["started_at"],
            "connected_clients": runtime["connected_clients"],
            "last_error": runtime["last_error"],
            "last_scan": runtime["last_scan"],
            "has_last_prediction": runtime["last_prediction"] is not None,
            "llm_enabled": (
                llm_enabled(args) if args else bool(os.environ.get(OPENAI_API_KEY_ENV))
            ),
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
    if runtime["last_scan"] is not None:
        engine = runtime.get("engine")
        emit(
            "processor_scan",
            {
                "type": "processor_scan",
                **runtime["last_scan"],
                "has_last_prediction": runtime["last_prediction"] is not None,
                "collectors_root": str(engine.collectors_root) if engine else "",
                "normalizers_root": str(engine.normalizers_root) if engine else "",
                "state_dir": str(engine.state_dir) if engine else "",
            },
        )


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
        "--disable-llm",
        action="store_true",
        help="Disable LLM prediction review and suggested action generation.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=env_float("RADAR_PROCESSOR_APP_LLM_TIMEOUT", 30),
        help="Seconds to wait for each LLM request.",
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

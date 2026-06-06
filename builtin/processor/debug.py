import argparse
import json
import os
import sys
import time
from pathlib import Path


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
    read_json,
    utc_timestamp,
    write_json_atomic,
)


DEFAULT_DEBUG_STATE_DIR = REPO_ROOT / "debug" / "work" / "processors" / "predict_debug"


def purge_debug_state_files(args):
    state_dir = Path(args.state_dir)
    predict_state_dir = Path(args.predict_state_dir) if args.predict_state_dir else state_dir / "predict"
    paths = [
        state_dir / "checkpoint.json",
        state_dir / "normalized_events.jsonl",
        state_dir / "last_result.json",
        predict_state_dir / "item_dictionary.json",
        predict_state_dir / "estdec_model.json",
        predict_state_dir / "spmf_stream.txt",
    ]
    removed = []
    for path in paths:
        for candidate in (path, path.with_suffix(f"{path.suffix}.tmp")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue
            removed.append(str(candidate))
    return removed


def count_complete_entries(path, offset=0):
    path = Path(path)
    try:
        size = path.stat().st_size
        if offset < 0 or offset > size:
            offset = 0
        count = 0
        with path.open("rb") as input_file:
            input_file.seek(offset)
            for line in input_file:
                if line.endswith(b"\n") and line.strip():
                    count += 1
        return count
    except OSError:
        return 0


def count_available_normalizers(normalizers_root):
    root = Path(normalizers_root)
    if not root.exists():
        return 0
    return sum(
        1
        for candidate in root.iterdir()
        if candidate.is_dir() and (candidate / "normalizer.py").exists()
    )


def build_started_event(engine, args, purged_paths):
    return {
        "type": "debug_started",
        "created_at": utc_timestamp(),
        "collectors_root": str(engine.collectors_root),
        "normalizers_root": str(engine.normalizers_root),
        "state_dir": str(engine.state_dir),
        "mode": "replay_existing" if args.replay_existing else "tail",
        "purged_state": bool(args.purge_state),
        "purged_path_count": len(purged_paths),
    }


def build_stats_event(engine, args, purged_paths):
    collector_paths = list(iter_collector_jsonl_files(engine.collectors_root))
    checkpoint_files = engine.checkpoint.get("files") or {}
    collector_bytes = 0
    collector_history_entries = 0
    collector_pending_entries = 0
    collector_pending_bytes = 0

    for path in collector_paths:
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
        checkpoint = checkpoint_files.get(str(path)) or {}
        offset = int(checkpoint.get("offset") or 0)
        if offset < 0 or offset > size:
            offset = 0
        collector_bytes += size
        collector_history_entries += count_complete_entries(path)
        collector_pending_entries += count_complete_entries(path, offset=offset)
        collector_pending_bytes += max(size - offset, 0)

    last_result = read_json(engine.last_result_path, {})
    last_payload = (last_result or {}).get("payload") or {}
    model = engine.predict.estdec.model
    config = engine.predict.estdec.config
    transactions_seen = int(model.get("transactions_seen", 0) or 0)

    event = build_started_event(engine, args, purged_paths)
    event.update(
        {
            "type": "debug_stats",
            "stats": {
                "collector_file_count": len(collector_paths),
                "collector_history_entries": collector_history_entries,
                "collector_pending_entries": collector_pending_entries,
                "collector_pending_bytes": collector_pending_bytes,
                "collector_bytes": collector_bytes,
                "checkpoint_file_count": len(checkpoint_files),
                "available_normalizer_count": count_available_normalizers(
                    engine.normalizers_root
                ),
                "normalized_history_entries": count_complete_entries(
                    engine.normalized_log_path
                ),
                "predict_stream_entries": count_complete_entries(
                    engine.predict.stream_path
                ),
                "dictionary_size": len(engine.predict.dictionary.item_to_id),
                "transactions_seen": transactions_seen,
                "effective_transaction_count": model.get(
                    "effective_transaction_count",
                    0.0,
                ),
                "tracked_pattern_count": len(model.get("counts") or {}),
                "warmup_remaining": max(
                    config.min_transactions_before_prediction - transactions_seen,
                    0,
                ),
                "last_result_ready": bool(last_payload.get("ready", False)),
                "last_result_status": last_payload.get("status", ""),
                "last_result_confidence": last_payload.get("confidence", 0.0),
            },
            "config": {
                "min_transactions_before_prediction": config.min_transactions_before_prediction,
                "min_confidence": config.min_confidence,
                "min_pattern_decayed_count": config.min_pattern_decayed_count,
                "min_support": config.min_support,
            },
        }
    )
    return event


def print_event(event, args):
    if args.jsonl:
        print(json.dumps(event, ensure_ascii=True, sort_keys=True), flush=True)
        return
    print(json.dumps(event, ensure_ascii=True, indent=2, sort_keys=True), flush=True)


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


def print_prediction(scan_result, args):
    output = scan_result["prediction"] if args.full else compact_prediction(
        scan_result,
        args.max_patterns,
    )
    print_event(output, args)


def run(args):
    apply_predict_config_overrides(args)
    purged_paths = purge_debug_state_files(args) if args.purge_state else []
    engine = ProcessorEngine(
        collectors_root=args.collectors_root,
        normalizers_root=args.normalizers_root,
        state_dir=args.state_dir,
        predict_state_dir=args.predict_state_dir,
    )

    if args.replay_existing:
        engine.reset()
    elif args.reset:
        engine.reset()
        mark_existing_collector_files_seen(engine)
    elif not engine.checkpoint_path.exists():
        mark_existing_collector_files_seen(engine)

    if args.print_started:
        print_event(build_started_event(engine, args, purged_paths), args)
    if args.stats:
        print_event(build_stats_event(engine, args, purged_paths), args)

    deadline = None if args.duration <= 0 else time.monotonic() + args.duration
    while True:
        scan_result = engine.scan_once()
        if scan_result.get("prediction"):
            print_prediction(scan_result, args)
        elif args.print_empty:
            print_event(
                {
                    "type": "no_prediction",
                    "created_at": scan_result.get("created_at"),
                    "files_seen": scan_result.get("files_seen", 0),
                    "events_seen": scan_result.get("events_seen", 0),
                    "learning": scan_result.get("learning") or {},
                    "errors": scan_result.get("errors") or [],
                },
                args,
            )

        if args.once:
            return
        if deadline is not None and time.monotonic() >= deadline:
            return
        time.sleep(max(args.interval, 0.1))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Print each new prediction generated by the processor engine."
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
        default=os.environ.get("RADAR_DEBUG_PROCESSOR_STATE_DIR", str(DEFAULT_DEBUG_STATE_DIR)),
        help="Debug checkpoint and normalized output directory.",
    )
    parser.add_argument(
        "--predict-state-dir",
        default=os.environ.get("RADAR_DEBUG_PREDICT_STATE_DIR"),
        help="Optional predict model state directory. Defaults under debug state.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=env_float("RADAR_DEBUG_PROCESSOR_INTERVAL", 1),
        help="Seconds between collector scans.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=env_float("RADAR_DEBUG_PROCESSOR_DURATION", 0),
        help="Seconds to run. Use <= 0 to run until interrupted.",
    )
    parser.add_argument(
        "--max-patterns",
        type=int,
        default=10,
        help="Maximum patterns to print in compact mode.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Scan once and exit.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset debug offsets, then tail from the current end of existing collector files.",
    )
    parser.add_argument(
        "--purge-state",
        "--clean-slate",
        dest="purge_state",
        action="store_true",
        help=(
            "Delete previous debug processor state before starting. "
            "This clears the checkpoint, normalized log, last result, and predict model files."
        ),
    )
    parser.add_argument(
        "--replay-existing",
        action="store_true",
        help="Process existing collector records instead of tailing only new records.",
    )
    parser.add_argument(
        "--print-empty",
        action="store_true",
        help="Print scan ticks even when no prediction is generated.",
    )
    parser.add_argument(
        "--print-started",
        action="store_true",
        help="Print a lightweight startup event before watching.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print startup statistics before watching.",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print each event on one line.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the full prediction result instead of a compact debug event.",
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
    run(parse_args())


if __name__ == "__main__":
    main()

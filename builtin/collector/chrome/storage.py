"""JSONL storage for collected Chrome observations."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .observation import coerce_epoch_ms


class JsonlObservationStore:
    """Store observations under work_dir/yyyymmdd/artifacts/*.jsonl."""

    def __init__(self, work_dir: str | Path, split_ms: int = 3_600_000) -> None:
        if split_ms <= 0:
            raise ValueError("split_ms must be positive")
        self.work_dir = Path(work_dir)
        self.split_ms = split_ms

    def write(self, observation: dict[str, Any]) -> Path:
        path = self.path_for(observation)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            json.dump(observation, file, ensure_ascii=False, separators=(",", ":"))
            file.write("\n")
        return path

    def path_for(self, observation: dict[str, Any]) -> Path:
        observed_at = None
        time_map = observation.get("time")
        if isinstance(time_map, dict):
            observed_at = time_map.get("observed_at")
        observed_ms = coerce_epoch_ms(observed_at)
        bucket_ms = observed_ms - (observed_ms % self.split_ms)
        day = datetime.fromtimestamp(observed_ms / 1000).strftime("%Y%m%d")
        return self.work_dir / day / "artifacts" / f"{bucket_ms}.jsonl"

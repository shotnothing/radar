"""JSONL storage for collector observations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .observation import CHROME_COLLECTOR_ID, coerce_epoch_ms


class JsonlObservationStore:
    """Store observations under /collector_name/yyyymmdd/artifacts/*.jsonl."""

    def __init__(
        self,
        root: str | Path,
        collector_id: str = CHROME_COLLECTOR_ID,
        split_seconds: int = 3600,
    ) -> None:
        if split_seconds <= 0:
            raise ValueError("split_seconds must be positive")
        self.root = Path(root)
        self.collector_id = collector_id
        self.split_seconds = split_seconds

    def write(self, observation: dict[str, Any]) -> Path:
        path = self.path_for(observation)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            json.dump(observation, file, ensure_ascii=False, separators=(",", ":"))
            file.write("\n")
        return path

    def path_for(self, observation: dict[str, Any]) -> Path:
        observed_at = coerce_epoch_ms(
            observation.get("time", {}).get("observedAt")
            if isinstance(observation.get("time"), dict)
            else None
        )
        epoch_seconds = observed_at // 1000
        bucket = epoch_seconds - (epoch_seconds % self.split_seconds)
        day = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y%m%d")
        return (
            self.root
            / self.collector_id
            / day
            / "artifacts"
            / f"{bucket}.jsonl"
        )

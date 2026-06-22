from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceLogger:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / "trace.jsonl"
        self._write_lock = threading.Lock()

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        encoded = json.dumps(record, ensure_ascii=False) + "\n"
        with self._write_lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(encoded)

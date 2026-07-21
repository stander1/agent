from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class TraceLogger:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / "trace.jsonl"
        self._write_lock = threading.Lock()
        self._context_provider: Callable[[], dict[str, Any]] | None = None

    def set_context_provider(
        self,
        provider: Callable[[], dict[str, Any]] | None,
    ) -> None:
        self._context_provider = provider

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        enriched_payload = dict(payload)
        if self._context_provider is not None:
            try:
                context = self._context_provider()
            except Exception:
                context = {}
            if isinstance(context, dict):
                for key, value in context.items():
                    enriched_payload.setdefault(key, value)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "payload": enriched_payload,
        }
        encoded = json.dumps(record, ensure_ascii=False) + "\n"
        with self._write_lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(encoded)

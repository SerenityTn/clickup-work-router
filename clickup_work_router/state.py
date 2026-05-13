"""Small ingestion/dedupe/recent-event state store.

This is intentionally not an execution queue or task engine.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import threading
from typing import Any


class RouterState:
    def __init__(self, max_events: int = 100) -> None:
        self.seen: set[str] = set()
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self.last_error: str = ""
        self._lock = threading.Lock()

    def mark_seen(self, key: str) -> bool:
        with self._lock:
            if key in self.seen:
                return False
            self.seen.add(key)
            return True

    def has_seen(self, key: str) -> bool:
        with self._lock:
            return key in self.seen

    def forget_seen(self, key: str) -> None:
        with self._lock:
            self.seen.discard(key)

    def record_event(self, workspace: str, event_id: str, status: str, summary: str, extra: Any = None) -> None:
        if is_dataclass(extra):
            extra = asdict(extra)
        self.events.appendleft({
            "time": datetime.now(timezone.utc).isoformat(),
            "workspace": workspace,
            "event_id": event_id,
            "status": status,
            "summary": summary,
            "extra": extra or {},
        })

    def record_error(self, message: str) -> None:
        self.last_error = str(message)[:500]

    def recent(self, workspace: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        items = [event for event in self.events if workspace is None or event.get("workspace") == workspace]
        return items[:limit]


STATE = RouterState()

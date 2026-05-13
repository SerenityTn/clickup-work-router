"""Stable dedupe keys for external events."""
from __future__ import annotations

import hashlib

from clickup_work_router.models import WorkEvent


def stable_event_key(event: WorkEvent, profile: str) -> str:
    comment_id = ""
    if isinstance(event.raw, dict):
        comment_id = str(event.raw.get("comment_id") or "")
        payload = event.raw.get("payload") if isinstance(event.raw.get("payload"), dict) else {}
        history_id = payload.get("history_item", {}).get("id") if isinstance(payload.get("history_item"), dict) else ""
        comment_id = comment_id or str(history_id or "")
    parts = [
        event.source_system,
        event.event_id,
        event.work_item.issue_id,
        profile,
        event.event_type,
        comment_id,
    ]
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()

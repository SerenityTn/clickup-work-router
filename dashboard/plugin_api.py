"""Read-mostly dashboard API helpers for ClickUp Work Router.

The functions are framework-neutral so Hermes/FastAPI can mount them later, and
pytest can exercise redaction without importing dashboard runtime packages.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

# Hermes imports this file directly from dashboard/plugin_api.py. Add the
# plugin root so sibling package `clickup_work_router` is importable when the
# plugin is discovered through ~/.hermes/plugins/<name> symlink.
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

try:
    from fastapi import APIRouter
except Exception:  # pragma: no cover - lets unit tests run without FastAPI
    APIRouter = None  # type: ignore[assignment]

from clickup_work_router.config import LEGACY_DEFAULT_WORKSPACE, load_config
from clickup_work_router.state import STATE, STATE_DB_ENV

router = APIRouter() if APIRouter is not None else None

_SECRET_RE = re.compile(r"(token|secret|authorization|password|signature|connection|string)", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            # Preserve explicit secret-presence maps (booleans only) while still
            # redacting actual token/secret-bearing fields elsewhere.
            if str(key) == "secrets" and isinstance(item, dict):
                out[key] = {str(k): bool(v) for k, v in item.items()}
            elif _SECRET_RE.search(str(key)):
                out[key] = bool(item) if isinstance(item, str) else "<redacted>"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and ("Bearer " in value or len(value) > 80):
        return "<redacted>"
    return value


def _attach_shared_state() -> None:
    """Read the router service's state file when one is configured.

    The dashboard runs inside the Hermes gateway, not the router process, so its
    in-memory store is always empty. Pointing it at the service's SQLite file
    (``CLICKUP_ROUTER_STATE_DB``) shows real events, links, and reconcile runs.
    """
    path = os.environ.get(STATE_DB_ENV, "").strip()
    if path and STATE.path != path:
        STATE.configure(path)


def get_status(workspace: str = LEGACY_DEFAULT_WORKSPACE, env_file: str | None = None) -> dict[str, Any]:
    _attach_shared_state()
    cfg = load_config(env_file=env_file, workspace=workspace)
    workspaces = {name: ws.sanitized(os.environ) for name, ws in cfg.workspaces.items()}
    last = STATE.recent(workspace, 1)
    return redact({
        "service": {"name": "clickup-work-router", "ok": True},
        "configured_workspaces": list(cfg.workspaces),
        "workspaces": workspaces,
        "last_event_time": last[0]["time"] if last else "",
        "last_error_summary": STATE.last_error,
        "recent_events": STATE.recent(workspace, 10),
        "links": STATE.link_counts(workspace),
        "reconcile": STATE.get_meta(f"reconcile:{workspace}") or {},
    })


def get_workspaces(env_file: str | None = None) -> list[dict[str, Any]]:
    cfg = load_config(env_file=env_file)
    return [redact(ws.sanitized(os.environ)) for ws in cfg.workspaces.values()]


def get_events(workspace: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
    _attach_shared_state()
    return redact(STATE.recent(workspace, limit))


def get_mappings(workspace: str = LEGACY_DEFAULT_WORKSPACE, env_file: str | None = None) -> dict[str, Any]:
    cfg = load_config(env_file=env_file, workspace=workspace)
    ws = cfg.get_workspace(workspace)
    return redact(ws.profile_map)


if router is not None:
    @router.get("/status")
    async def status(workspace: str = LEGACY_DEFAULT_WORKSPACE):
        return get_status(workspace=workspace)

    @router.get("/workspaces")
    async def workspaces():
        return get_workspaces()

    @router.get("/events")
    async def events(workspace: str | None = None, limit: int = 25):
        return get_events(workspace=workspace, limit=limit)

    @router.get("/mappings")
    async def mappings(workspace: str = LEGACY_DEFAULT_WORKSPACE):
        return get_mappings(workspace=workspace)

"""No-credential, no-Kanban-mutation preview harness."""
from __future__ import annotations

import argparse
import json
import threading
import urllib.request
import uuid
from typing import Any
from http.server import ThreadingHTTPServer

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.service import RouterApp, make_handler
from clickup_work_router.state import STATE


class _SyntheticClickUp:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task

    def fetch_task(self, task_id: str) -> dict[str, Any]:
        task = dict(self.task)
        task["id"] = task_id
        return task

    def fetch_comments(self, task_id: str) -> list[dict[str, Any]]:
        del task_id
        return []


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def run_preview(profile: str = "founder") -> dict[str, Any]:
    """Exercise the real loopback HTTP path while forcing dry-run dispatch."""
    event_id = f"preview-{uuid.uuid4().hex}"
    task = {
        "id": "synthetic-preview-task",
        "name": "ClickUp Work Router synthetic preview",
        "list": {"id": "synthetic-list"},
        "status": {"status": "open"},
        "assignees": [{"id": "synthetic-user", "username": "Preview User"}],
    }
    workspace = WorkspaceConfig(
        name="preview",
        company_slug="preview",
        webhook_path="/clickup/preview",
        list_ids=["synthetic-list"],
        kanban_board="preview-no-write",
        dry_run=True,
    )
    workspace.profile_map["clickup_user_ids"] = {"synthetic-user": profile}
    app = RouterApp(workspace, clickup_client=_SyntheticClickUp(task), runtime_env={})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, log_requests=False))
    thread = threading.Thread(target=server.serve_forever, name="clickup-router-preview", daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    routing: dict[str, Any] = {}
    try:
        health = _get_json(f"{base}/health")
        routing = _post_json(
            f"{base}{workspace.webhook_path}",
            {"event": "taskCreated", "event_id": event_id, "task": task},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if routing.get("external_ref"):
            STATE.forget_seen(str(routing["external_ref"]))
    return {
        "ok": bool(health.get("ok") and routing.get("ok")),
        "health": health,
        "routing": routing,
        "dry_run_enforced": workspace.dry_run and bool(routing.get("dry_run")),
        "server_stopped": not thread.is_alive(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="founder")
    args = parser.parse_args(argv)
    result = run_preview(args.profile)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] and result["server_stopped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

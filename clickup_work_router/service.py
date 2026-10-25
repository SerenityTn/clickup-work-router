"""Minimal stdlib HTTP service entrypoint for side-by-side testing."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from clickup_work_router.adapters.clickup import ClickUpClient, build_work_event, extract_task_id, verify_signature
from clickup_work_router.config import LEGACY_DEFAULT_WORKSPACE, WorkspaceConfig, apply_env_file_to_environ, load_config
from clickup_work_router.core.comment_triage import classify_comment_intent
from clickup_work_router.core.routing import route_event
from clickup_work_router.dispatch.kanban import (
    append_comment_to_kanban,
    append_note_to_kanban,
    dispatch_direct_reply,
    dispatch_to_kanban,
    external_ref,
    find_active_kanban_task,
)
from clickup_work_router.models import RouteDecision, WorkEvent
from clickup_work_router.reconcile import ReconcileLoop, Reconciler
from clickup_work_router.state import STATE
from clickup_work_router.writeback import ClickUpWriteback

_COMMENT_EVENTS = {"taskCommentPosted", "taskCommentUpdated", "comment", "comment_created"}
_CREATE_EVENTS = {"taskCreated", "task_created"}


def parse_request_path(raw_path: str) -> tuple[str, dict[str, list[str]]]:
    parsed = urllib.parse.urlparse(raw_path)
    return parsed.path, urllib.parse.parse_qs(parsed.query)


def router_token_is_valid(headers: Any, query: dict[str, list[str]], expected: str) -> bool:
    if not expected:
        return True
    provided = headers.get("X-Router-Token") or (query.get("token") or [""])[0]
    return hmac.compare_digest(str(provided), expected)


def redact_request_target(target: str) -> str:
    return re.sub(r"([?&]token=)[^\s&]+", r"\1[REDACTED]", target)


class RouterApp:
    def __init__(self, workspace: WorkspaceConfig, clickup_client: Any | None = None, runtime_env: dict[str, str] | None = None) -> None:
        self.workspace = workspace
        self.runtime_env = dict(os.environ if runtime_env is None else runtime_env)
        token = self.runtime_env.get(workspace.clickup_token_env, "")
        self.clickup = clickup_client or (ClickUpClient(token) if token else None)
        self.writeback = ClickUpWriteback(workspace, self.clickup)

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "workspace": self.workspace.name,
            "board": self.workspace.kanban_board,
            "dry_run": self.workspace.dry_run,
            "secrets": self.workspace.secret_presence(self.runtime_env),
            "last_error": STATE.last_error,
            "state_persistent": STATE.path != ":memory:",
            "links": STATE.link_counts(self.workspace.name),
            "reconcile": STATE.get_meta(f"reconcile:{self.workspace.name}"),
        }

    def build_reconciler(self) -> Reconciler:
        return Reconciler(
            self.workspace,
            state=STATE,
            clickup=self.clickup,
            writeback=self.writeback,
            dispatch_payload=self.handle_payload,
            team_id=self.runtime_env.get(self.workspace.clickup_team_id_env, ""),
        )

    def _release(self, decision: RouteDecision, claimed: bool) -> None:
        if decision.dedupe_key and claimed:
            STATE.forget_seen(decision.dedupe_key)

    def _handle_unassign(self, event: WorkEvent, decision: RouteDecision, claimed: bool) -> dict[str, Any]:
        """A human removed an agent in ClickUp: tell its active Kanban task."""
        active_task_id = find_active_kanban_task(self.workspace, decision)
        if not active_task_id:
            STATE.record_event(self.workspace.name, event.event_id, "ignored", f"{decision.reason}; no active Kanban task")
            return {"ok": True, "status": "ignored", "reason": f"{decision.reason}; no active Kanban task"}
        note = (
            f"ClickUp assignee {decision.profile} was removed by a human.\n"
            f"Task: {event.work_item.url}\n"
            "Stop starting new work, record progress, and hand off or complete this task."
        )
        result = append_note_to_kanban(self.workspace, active_task_id, note)
        if not result.ok:
            self._release(decision, claimed)
            STATE.record_error(result.stderr or result.stdout or "Kanban unassign note failed")
        STATE.record_event(self.workspace.name, event.event_id, "unassign", decision.reason, {"external_ref": active_task_id, "dry_run": result.dry_run, "ok": result.ok})
        return {"ok": result.ok, "status": "unassign", "profile": decision.profile, "external_ref": active_task_id, "dry_run": result.dry_run}

    def _already_active(self, event: WorkEvent, decision: RouteDecision, *, include_done: bool = False) -> str:
        """Kanban task id already handling this ClickUp task for this profile."""
        if STATE.has_open_link(self.workspace.name, event.work_item.issue_id, decision.profile):
            return "linked"
        existing = find_active_kanban_task(self.workspace, decision, include_done=include_done)
        if existing:
            # Adopt work created before links were persisted so it is reconciled too.
            STATE.add_link(self.workspace.name, event.work_item.issue_id, decision.profile, external_ref(self.workspace, decision), existing)
        return existing or ""

    def handle_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = extract_task_id(payload)
        if self.clickup:
            task = self.clickup.fetch_task(task_id)
            if str(payload.get("event") or payload.get("event_type") or "").startswith("taskComment") and not isinstance(payload.get("comment"), dict):
                comments = self.clickup.fetch_comments(task_id)
                if comments:
                    latest = max(comments, key=lambda c: int(c.get("date") or 0) if str(c.get("date") or "").isdigit() else 0)
                    payload = dict(payload)
                    payload["comment"] = latest
        else:
            task = payload.get("task") if isinstance(payload.get("task"), dict) else {"id": task_id, "name": payload.get("name", "Synthetic ClickUp task")}
        event = build_work_event(payload, task, self.workspace.name)
        decision = route_event(event, self.workspace)
        dedupe_claimed = False
        if decision.dedupe_key:
            dedupe_claimed = STATE.mark_seen(decision.dedupe_key)
            if not dedupe_claimed:
                STATE.record_event(self.workspace.name, event.event_id, "deduped", decision.reason)
                return {"ok": True, "status": "deduped", "reason": decision.reason}
        if decision.action == "unassign":
            return self._handle_unassign(event, decision, dedupe_claimed)
        if not decision.should_dispatch:
            STATE.record_event(self.workspace.name, event.event_id, "ignored", decision.reason)
            return {"ok": True, "status": "ignored", "reason": decision.reason}

        is_comment = bool(event.comment_text) or event.event_type in _COMMENT_EVENTS
        is_fresh_create = event.event_type in _CREATE_EVENTS and not payload.get("router_sweep")
        if not is_comment and not is_fresh_create:
            # Assignee changes, list sweeps, and opted-in task updates must not
            # duplicate work that is already executing for this task/profile.
            existing = self._already_active(event, decision, include_done=bool(payload.get("router_sweep")))
            if existing:
                STATE.record_event(self.workspace.name, event.event_id, "already_active", decision.reason, {"external_ref": existing})
                return {"ok": True, "status": "already_active", "profile": decision.profile, "external_ref": existing}
        if is_comment and decision.should_dispatch:
            active_task_id = find_active_kanban_task(self.workspace, decision)
            intent = classify_comment_intent(event.comment_text, has_active_task=bool(active_task_id))
            if intent == "direct_reply":
                result = dispatch_direct_reply(self.workspace, decision)
                if not result.ok and decision.dedupe_key and dedupe_claimed:
                    STATE.forget_seen(decision.dedupe_key)
                if not result.ok:
                    STATE.record_error(result.stderr or result.stdout or "Direct ClickUp reply dispatch failed")
                STATE.record_event(self.workspace.name, event.event_id, "direct_reply", decision.reason, {"external_ref": result.external_ref, "dry_run": result.dry_run, "ok": result.ok})
                return {"ok": result.ok, "status": "direct_reply", "profile": decision.profile, "external_ref": result.external_ref, "dry_run": result.dry_run}
            if intent == "append_to_active" and active_task_id:
                result = append_comment_to_kanban(self.workspace, decision, active_task_id)
                if not result.ok and decision.dedupe_key and dedupe_claimed:
                    STATE.forget_seen(decision.dedupe_key)
                if not result.ok:
                    STATE.record_error(result.stderr or result.stdout or "Kanban comment append failed")
                STATE.record_event(self.workspace.name, event.event_id, "append_to_active", decision.reason, {"external_ref": result.external_ref, "dry_run": result.dry_run, "ok": result.ok})
                return {"ok": result.ok, "status": "append_to_active", "profile": decision.profile, "external_ref": result.external_ref, "dry_run": result.dry_run}

        result = dispatch_to_kanban(self.workspace, decision)
        if not result.ok:
            self._release(decision, dedupe_claimed)
            STATE.record_error(result.stderr or result.stdout or "Kanban dispatch failed")
            STATE.record_event(self.workspace.name, event.event_id, decision.action, decision.reason, {"external_ref": result.external_ref, "dry_run": result.dry_run, "ok": False})
            return {"ok": False, "status": decision.action, "profile": decision.profile, "external_ref": result.external_ref, "dry_run": result.dry_run}

        kanban_task_id = str(getattr(result, "kanban_task_id", "") or "")
        if not result.dry_run:
            STATE.add_link(self.workspace.name, event.work_item.issue_id, decision.profile, external_ref(self.workspace, decision), kanban_task_id, "ready")
        writeback = self.writeback.on_dispatch(decision, kanban_task_id, is_comment=is_comment)
        if not writeback.ok:
            STATE.record_error(next((a.error for a in writeback.actions if a.error), "ClickUp write-back failed"))
        STATE.record_event(
            self.workspace.name, event.event_id, decision.action, decision.reason,
            {"external_ref": result.external_ref, "kanban_task_id": kanban_task_id, "dry_run": result.dry_run, "ok": True, "writeback_ok": writeback.ok},
        )
        return {
            "ok": True,
            "status": decision.action,
            "profile": decision.profile,
            "external_ref": result.external_ref,
            "kanban_task_id": kanban_task_id,
            "dry_run": result.dry_run,
            "writeback": writeback.to_dict(),
        }


def make_handler(app: RouterApp, *, log_requests: bool = True):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, data: dict[str, Any]) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, app.health())
            else:
                self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            request_path, query = parse_request_path(self.path)
            if request_path != app.workspace.webhook_path:
                self._send(404, {"ok": False, "error": "not found"})
                return
            router_token = app.runtime_env.get("ROUTER_SHARED_TOKEN", "").strip()
            if not router_token_is_valid(self.headers, query, router_token):
                self._send(401, {"ok": False, "error": "router_token_mismatch"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            signature = self.headers.get("X-Signature") or self.headers.get("X-ClickUp-Signature")
            secret = app.runtime_env.get(app.workspace.webhook_secret_env, "")
            if not secret and not app.workspace.dry_run:
                self._send(401, {"ok": False, "error": "webhook secret is required when DRY_RUN=false"})
                return
            if secret and not verify_signature(body, signature, secret):
                self._send(401, {"ok": False, "error": "invalid signature"})
                return
            try:
                payload = json.loads(body.decode() or "{}")
                response = app.handle_payload(payload)
                self._send(200 if response.get("ok", False) else 502, response)
            except Exception as exc:  # keep service alive, no secrets
                STATE.record_error(str(exc))
                self._send(500, {"ok": False, "error": str(exc)[:300]})

        def log_message(self, fmt: str, *args: Any) -> None:
            if log_requests:
                message = fmt % args
                print(f"{self.address_string()} - {redact_request_target(message)}")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=LEGACY_DEFAULT_WORKSPACE)
    parser.add_argument("--env-file")
    parser.add_argument("--config")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--state-db", help="SQLite file for dedupe keys, links, and events (default: in-memory)")
    parser.add_argument("--reconcile-interval", type=int, help="seconds between reconcile passes; 0 disables")
    args = parser.parse_args(argv)
    if args.env_file:
        apply_env_file_to_environ(args.env_file)
    cfg = load_config(args.config, env_file=args.env_file, workspace=args.workspace)
    ws = cfg.get_workspace(args.workspace)
    state_db = args.state_db or ws.state_db
    if state_db:
        STATE.configure(state_db)
    app = RouterApp(ws)
    server = ThreadingHTTPServer((args.host, ws.port), make_handler(app))
    interval = ws.reconcile_interval_seconds if args.reconcile_interval is None else args.reconcile_interval
    loop = None
    if interval > 0 and not ws.dry_run:
        loop = ReconcileLoop(app.build_reconciler(), interval)
        loop.start()
    print(
        f"clickup-work-router listening on http://{args.host}:{ws.port}{ws.webhook_path} dry_run={ws.dry_run} "
        f"state={'persistent' if state_db else 'in-memory'} reconcile={'every %ss' % interval if loop else 'off'}"
    )
    try:
        server.serve_forever()
    finally:
        if loop:
            loop.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from clickup_work_router.config import WorkspaceConfig, apply_env_file_to_environ, load_config
from clickup_work_router.core.comment_triage import classify_comment_intent
from clickup_work_router.core.routing import route_event
from clickup_work_router.dispatch.kanban import append_comment_to_kanban, dispatch_direct_reply, dispatch_to_kanban, find_active_kanban_task
from clickup_work_router.state import STATE


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
    def __init__(self, workspace: WorkspaceConfig, clickup_client: Any | None = None) -> None:
        self.workspace = workspace
        token = os.environ.get(workspace.clickup_token_env, "")
        self.clickup = clickup_client or (ClickUpClient(token) if token else None)

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "workspace": self.workspace.name,
            "board": self.workspace.kanban_board,
            "dry_run": self.workspace.dry_run,
            "secrets": self.workspace.secret_presence(),
            "last_error": STATE.last_error,
        }

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
        if not decision.should_dispatch:
            STATE.record_event(self.workspace.name, event.event_id, "ignored", decision.reason)
            return {"ok": True, "status": "ignored", "reason": decision.reason}

        is_comment = bool(event.comment_text) or event.event_type in {"taskCommentPosted", "taskCommentUpdated", "comment", "comment_created"}
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
        if not result.ok and decision.dedupe_key and dedupe_claimed:
            STATE.forget_seen(decision.dedupe_key)
        if not result.ok:
            STATE.record_error(result.stderr or result.stdout or "Kanban dispatch failed")
        STATE.record_event(self.workspace.name, event.event_id, decision.action, decision.reason, {"external_ref": result.external_ref, "dry_run": result.dry_run, "ok": result.ok})
        return {"ok": result.ok, "status": decision.action, "profile": decision.profile, "external_ref": result.external_ref, "dry_run": result.dry_run}


def make_handler(app: RouterApp):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, data: dict[str, Any]) -> None:
            body = json.dumps(data).encode()
            self.send_response(code)
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
            router_token = os.environ.get("ROUTER_SHARED_TOKEN", "").strip()
            if not router_token_is_valid(self.headers, query, router_token):
                self._send(401, {"ok": False, "error": "router_token_mismatch"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            signature = self.headers.get("X-Signature") or self.headers.get("X-ClickUp-Signature")
            secret = os.environ.get(app.workspace.webhook_secret_env, "")
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
            message = fmt % args
            print(f"{self.address_string()} - {redact_request_target(message)}")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="chessmentor")
    parser.add_argument("--env-file")
    parser.add_argument("--config")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    if args.env_file:
        apply_env_file_to_environ(args.env_file)
    cfg = load_config(args.config, env_file=args.env_file, workspace=args.workspace)
    ws = cfg.get_workspace(args.workspace)
    server = ThreadingHTTPServer((args.host, ws.port), make_handler(RouterApp(ws)))
    print(f"clickup-work-router listening on http://{args.host}:{ws.port}{ws.webhook_path} dry_run={ws.dry_run}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

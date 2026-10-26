"""ClickUp webhook lifecycle: health checks, reactivation, and registration.

ClickUp suspends a webhook after repeated delivery failures and then silently
stops sending events. A maintained human layer has to notice that, so the
reconciler calls :func:`check_webhook_health` on every pass and can reactivate a
failing or suspended webhook.

Command-line use (credentials come from the env file, never from arguments)::

    python -m clickup_work_router.webhooks --workspace example --env-file F status
    python -m clickup_work_router.webhooks --workspace example --env-file F ensure \\
        --endpoint https://router.example/clickup/example --secret-out ~/.hermes/secret

``ensure`` reactivates a matching webhook or, with ``--create``, registers one.
A newly created webhook's signing secret is written only to ``--secret-out``
with owner-only permissions and is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from clickup_work_router.adapters.clickup import ClickUpClient
from clickup_work_router.config import LEGACY_DEFAULT_WORKSPACE, WorkspaceConfig, apply_env_file_to_environ, load_config, redact_url_token

# Events the router acts on. Status changes are reconciled by polling instead.
DEFAULT_EVENTS = ["taskCreated", "taskAssigneeUpdated", "taskCommentPosted", "taskCommentUpdated"]
HEALTHY = "active"


def _strip_query(url: str) -> str:
    return (url or "").split("?", 1)[0].rstrip("/")


def find_webhook(webhooks: list[dict[str, Any]], endpoint: str) -> dict[str, Any] | None:
    """Match on the endpoint without its query string, which may hold a token."""
    target = _strip_query(endpoint)
    for hook in webhooks:
        if isinstance(hook, dict) and _strip_query(str(hook.get("endpoint") or "")) == target:
            return hook
    return None


def _health(hook: dict[str, Any]) -> tuple[str, int]:
    health = hook.get("health") if isinstance(hook.get("health"), dict) else {}
    return str(health.get("status") or "unknown"), int(health.get("fail_count") or 0)


def check_webhook_health(config: WorkspaceConfig, client: Any, team_id: str, *, reactivate: bool | None = None) -> dict[str, Any]:
    """Report the registered webhook's health and optionally reactivate it."""
    endpoint = config.webhook_endpoint
    if not endpoint:
        return {"checked": False, "reason": "CLICKUP_WEBHOOK_ENDPOINT not configured"}
    if not team_id:
        return {"checked": False, "reason": "CLICKUP_TEAM_ID not configured"}
    hook = find_webhook(client.list_webhooks(team_id), endpoint)
    if hook is None:
        return {"checked": True, "registered": False, "endpoint": redact_url_token(endpoint), "status": "missing"}
    status, fail_count = _health(hook)
    report: dict[str, Any] = {
        "checked": True,
        "registered": True,
        "webhook_id": str(hook.get("id") or ""),
        "endpoint": redact_url_token(endpoint),
        "status": status,
        "fail_count": fail_count,
        "events": list(hook.get("events") or []),
        "reactivated": False,
    }
    should_reactivate = config.webhook_auto_reactivate if reactivate is None else reactivate
    if status != HEALTHY and should_reactivate and not config.dry_run:
        client.update_webhook(str(hook.get("id")), str(hook.get("endpoint") or endpoint), list(hook.get("events") or DEFAULT_EVENTS), status=HEALTHY)
        report["reactivated"] = True
    return report


def ensure_webhook(config: WorkspaceConfig, client: Any, team_id: str, *, create: bool, secret_out: str | None, events: list[str] | None = None) -> dict[str, Any]:
    report = check_webhook_health(config, client, team_id, reactivate=True)
    if not report.get("checked") or report.get("registered"):
        return report
    if not create:
        report["hint"] = "re-run with --create --secret-out PATH to register it"
        return report
    if not secret_out:
        raise SystemExit("--secret-out is required with --create so the signing secret is stored, not printed")
    if config.dry_run:
        report["would_create"] = True
        return report
    scope = {"list_id": config.list_ids[0]} if len(config.list_ids) == 1 else {}
    created = client.create_webhook(team_id, config.webhook_endpoint, events or DEFAULT_EVENTS, **scope)
    hook = created.get("webhook") if isinstance(created.get("webhook"), dict) else created
    secret = str(hook.get("secret") or "")
    if secret:
        path = Path(secret_out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(f"CLICKUP_WEBHOOK_SECRET={secret}\n")
    report.update({"registered": True, "created": True, "webhook_id": str(created.get("id") or hook.get("id") or ""), "secret_written": bool(secret), "status": HEALTHY})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or repair the ClickUp webhook for a workspace.")
    parser.add_argument("--workspace", default=LEGACY_DEFAULT_WORKSPACE)
    parser.add_argument("--env-file")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show webhook registration and health")
    ensure = sub.add_parser("ensure", help="reactivate, or with --create register, the webhook")
    ensure.add_argument("--endpoint", help="public URL ClickUp should deliver to (overrides CLICKUP_WEBHOOK_ENDPOINT)")
    ensure.add_argument("--create", action="store_true")
    ensure.add_argument("--secret-out")
    args = parser.parse_args(argv)
    if args.env_file:
        apply_env_file_to_environ(args.env_file)
    ws = load_config(args.config, env_file=args.env_file, workspace=args.workspace).get_workspace(args.workspace)
    if getattr(args, "endpoint", None):
        ws.webhook_endpoint = args.endpoint
    token = os.environ.get(ws.clickup_token_env, "")
    if not token:
        raise SystemExit(f"{ws.clickup_token_env} is not set")
    client = ClickUpClient(token)
    team_id = os.environ.get(ws.clickup_team_id_env, "")
    if args.command == "status":
        report = check_webhook_health(ws, client, team_id, reactivate=False)
    else:
        report = ensure_webhook(ws, client, team_id, create=args.create, secret_out=args.secret_out)
    print(json.dumps(report, indent=2))
    healthy = report.get("status") == HEALTHY or report.get("reactivated") or report.get("would_create")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())

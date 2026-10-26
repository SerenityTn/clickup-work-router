"""Proactive reconciliation between Hermes Kanban execution and ClickUp.

Webhooks only tell the router what humans did. This loop closes the other
direction and covers what webhooks miss. Each pass:

1. polls every open ClickUp<->Kanban link and mirrors milestones back to
   ClickUp: completion (comment plus optional status move), blocked (comment,
   repeated while it stays blocked), and ready-for-review (comment);
2. tells the Kanban task when a human closed its ClickUp task;
3. sweeps configured ClickUp lists for recently updated tasks assigned to a
   mapped agent that never reached the router, which catches dropped webhooks;
4. checks the ClickUp webhook's health and reactivates it if ClickUp suspended it.

It is a sync loop, not a queue: Hermes Kanban stays the only execution engine,
and the reconciler never creates, retries, or completes Kanban work itself.
It does nothing in dry-run mode.

Run one pass by hand with::

    python -m clickup_work_router.reconcile --workspace example --env-file F --state-db PATH
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import threading
import time
from typing import Any, Callable

from clickup_work_router.adapters.clickup import _is_closed, _users
from clickup_work_router.config import LEGACY_DEFAULT_WORKSPACE, WorkspaceConfig, apply_env_file_to_environ, load_config
from clickup_work_router.core.profile_mapping import ProfileMapper
from clickup_work_router.dispatch.kanban import (
    KANBAN_ACTIVE_STATUSES,
    KANBAN_ARCHIVED_STATUSES,
    KANBAN_DONE_STATUSES,
    append_note_to_kanban,
    show_kanban_task,
)
from clickup_work_router.state import LINK_ARCHIVED, LINK_CLOSED_IN_CLICKUP, LINK_DONE, LINK_MISSING, STATE, Link, RouterState
from clickup_work_router.webhooks import check_webhook_health
from clickup_work_router.writeback import ClickUpWriteback

SEEN_RETENTION_SECONDS = 30 * 24 * 3600
MISSING_LINK_GRACE_SECONDS = 3600
SWEEP_MAX_PAGES = 5


def _latest_event_time(info: dict[str, Any], kind: str) -> float:
    times = [float(e.get("created_at") or 0) for e in info.get("events") or [] if isinstance(e, dict) and e.get("kind") == kind]
    return max(times, default=0.0)


def _task_summary(info: dict[str, Any]) -> str:
    task = info.get("task") or {}
    return str(info.get("latest_summary") or task.get("result") or "")


class Reconciler:
    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        state: RouterState = STATE,
        clickup: Any | None = None,
        writeback: ClickUpWriteback | None = None,
        dispatch_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        team_id: str = "",
        runner: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.state = state
        self.clickup = clickup
        self.writeback = writeback or ClickUpWriteback(config, clickup)
        self.dispatch_payload = dispatch_payload
        self.team_id = team_id
        self.runner = runner
        self.clock = clock

    def run_once(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "time": datetime.now(timezone.utc).isoformat(),
            "checked": 0,
            "completed": 0,
            "blocked_notified": 0,
            "review_notified": 0,
            "closed_in_clickup": 0,
            "missing": 0,
            "swept": 0,
            "errors": [],
        }
        if self.config.dry_run:
            summary["skipped"] = "dry_run"
        else:
            self._reconcile_links(summary)
            self._sweep_lists(summary)
            self._check_webhook(summary)
            self.state.prune_seen(SEEN_RETENTION_SECONDS)
        summary["errors"] = summary["errors"][:10]
        if summary["errors"]:
            self.state.record_error(f"reconcile: {summary['errors'][0]}")
        self.state.set_meta(f"reconcile:{self.config.name}", summary)
        return summary

    # -- 1 + 2: mirror Kanban milestones into ClickUp ------------------------
    def _reconcile_links(self, summary: dict[str, Any]) -> None:
        for link in self.state.open_links(self.config.name, limit=self.config.reconcile_max_links):
            summary["checked"] += 1
            try:
                self._reconcile_link(link, summary)
            except Exception as exc:  # one bad link must not stop the pass
                summary["errors"].append(f"{link.clickup_task_id}: {str(exc)[:200]}")

    def _reconcile_link(self, link: Link, summary: dict[str, Any]) -> None:
        now = self.clock()
        if not link.kanban_task_id:
            if now - link.created_at > MISSING_LINK_GRACE_SECONDS:
                self.state.update_link(link.id, state=LINK_MISSING)
                summary["missing"] += 1
            return
        info = show_kanban_task(self.config, link.kanban_task_id, runner=self.runner)
        if info is None:
            summary["errors"].append(f"{link.clickup_task_id}: Kanban task {link.kanban_task_id} not readable")
            return
        status = str(info["task"].get("status") or "")
        if status != link.kanban_status:
            self.state.update_link(link.id, kanban_status=status)

        if status in KANBAN_DONE_STATUSES:
            result = self.writeback.on_kanban_done(link.clickup_task_id, link.profile, link.kanban_task_id, _task_summary(info))
            if result.ok:
                self.state.update_link(link.id, state=LINK_DONE, notified_status=status, notified_at=now)
                summary["completed"] += 1
            else:
                summary["errors"].append(f"{link.clickup_task_id}: completion write-back failed")
            return
        if status in KANBAN_ARCHIVED_STATUSES:
            self.state.update_link(link.id, state=LINK_ARCHIVED)
            return

        if status == "blocked":
            blocked_at = _latest_event_time(info, "blocked")
            first = link.notified_status != "blocked" or blocked_at > link.notified_at
            renotify = self.config.blocked_renotify_hours * 3600
            reminder = not first and renotify > 0 and now - link.notified_at >= renotify
            if first or reminder:
                result = self.writeback.on_kanban_blocked(link.clickup_task_id, link.profile, link.kanban_task_id, _task_summary(info), reminder=reminder)
                if result.ok:
                    self.state.update_link(link.id, notified_status="blocked", notified_at=now)
                    summary["blocked_notified"] += 1
        elif status == "review":
            if link.notified_status != "review":
                result = self.writeback.on_kanban_review(link.clickup_task_id, link.profile, link.kanban_task_id, _task_summary(info))
                if result.ok:
                    self.state.update_link(link.id, notified_status="review", notified_at=now)
                    summary["review_notified"] += 1
        elif link.notified_status in {"blocked", "review"}:
            # Work resumed; a later block or review is a new episode worth telling humans about.
            self.state.update_link(link.id, notified_status=status)

        if status in KANBAN_ACTIVE_STATUSES and self.clickup is not None:
            task = self.clickup.fetch_task(link.clickup_task_id)
            if bool(task.get("archived") or task.get("closed") or _is_closed(task.get("status"))):
                note = (
                    "ClickUp task was closed by a human while this Kanban task is still active.\n"
                    f"ClickUp task: {task.get('url') or link.clickup_task_id}\n"
                    "Stop new work, record anything unfinished, and complete or archive this task."
                )
                if append_note_to_kanban(self.config, link.kanban_task_id, note, runner=self.runner).ok:
                    self.state.update_link(link.id, state=LINK_CLOSED_IN_CLICKUP)
                    summary["closed_in_clickup"] += 1

    # -- 3: catch tasks whose webhooks never arrived -------------------------
    def _sweep_lists(self, summary: dict[str, Any]) -> None:
        if not (self.config.sweep_lists and self.config.list_ids and self.clickup is not None and self.dispatch_payload):
            return
        if self.state.path == ":memory:":
            # Without persisted links a restart forgets what was routed, and a
            # sweep could re-dispatch work. Sweeps need a state file.
            summary["sweep"] = "skipped: requires CLICKUP_ROUTER_STATE_DB"
            return
        known = self.state.known_task_ids(self.config.name)
        mapper = ProfileMapper(self.config)
        cutoff_ms = int((self.clock() - self.config.sweep_lookback_hours * 3600) * 1000)
        for list_id in self.config.list_ids:
            for page in range(SWEEP_MAX_PAGES):
                try:
                    data = self.clickup.list_tasks(list_id, updated_after_ms=cutoff_ms, page=page)
                except Exception as exc:
                    summary["errors"].append(f"sweep {list_id}: {str(exc)[:200]}")
                    break
                for task in data.get("tasks") or []:
                    task_id = str(task.get("id") or "")
                    if not task_id or task_id in known:
                        continue
                    if not any(mapper.profile_for_user(user) for user in _users(task.get("assignees"))):
                        continue
                    response = self.dispatch_payload({"event": "taskCreated", "event_id": f"sweep:{task_id}", "task_id": task_id, "task": task, "router_sweep": True})
                    known.add(task_id)
                    if response.get("ok") and response.get("status") in {"dispatch", "blocked"}:
                        summary["swept"] += 1
                if data.get("last_page", True):
                    break

    # -- 4: keep the webhook alive -------------------------------------------
    def _check_webhook(self, summary: dict[str, Any]) -> None:
        if self.clickup is None or not self.config.webhook_endpoint:
            return
        try:
            report = check_webhook_health(self.config, self.clickup, self.team_id)
        except Exception as exc:
            summary["errors"].append(f"webhook health: {str(exc)[:200]}")
            return
        summary["webhook"] = report
        if report.get("checked") and report.get("status") != "active" and not report.get("reactivated"):
            summary["errors"].append(f"webhook {report.get('status')}")


class ReconcileLoop:
    """Background thread running :meth:`Reconciler.run_once` on an interval."""

    def __init__(self, reconciler: Reconciler, interval_seconds: float, first_delay_seconds: float = 10.0) -> None:
        self.reconciler = reconciler
        self.interval = interval_seconds
        self.first_delay = min(first_delay_seconds, interval_seconds)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="clickup-router-reconcile", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        delay = self.first_delay
        while not self._stop.wait(delay):
            try:
                self.reconciler.run_once()
            except Exception as exc:  # keep the loop alive; surface via health
                self.reconciler.state.record_error(f"reconcile: {str(exc)[:300]}")
            delay = self.interval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one ClickUp/Kanban reconciliation pass.")
    parser.add_argument("--workspace", default=LEGACY_DEFAULT_WORKSPACE)
    parser.add_argument("--env-file")
    parser.add_argument("--config")
    parser.add_argument("--state-db", help="SQLite state file shared with the running service")
    args = parser.parse_args(argv)
    if args.env_file:
        apply_env_file_to_environ(args.env_file)
    ws = load_config(args.config, env_file=args.env_file, workspace=args.workspace).get_workspace(args.workspace)
    state_db = args.state_db or ws.state_db
    if not state_db:
        raise SystemExit("--state-db or CLICKUP_ROUTER_STATE_DB is required; links live in the service's state file")
    STATE.configure(state_db)
    from clickup_work_router.service import RouterApp  # local: service imports this module

    app = RouterApp(ws)
    print(json.dumps(app.build_reconciler().run_once(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

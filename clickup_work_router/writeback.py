"""ClickUp write-back: keep the human layer current with Kanban execution.

ClickUp is where humans look, so the router mirrors execution milestones there:

- when work is routed, an acknowledgement comment and an optional status move;
- when Kanban work blocks, needs review, or completes, a concise comment and,
  on completion, an optional status move.

Every comment starts with ``ROUTER_COMMENT_MARKER`` so the router ignores the
webhook ClickUp sends back for it. In dry-run mode nothing is written; the
planned actions are returned instead so previews show exactly what would happen.
Write-back failures are reported but never undo a successful Kanban dispatch.
Comments are plain text because ClickUp's ``comment_text`` does not render markdown.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.core.routing import ROUTER_COMMENT_MARKER
from clickup_work_router.models import RouteDecision

_MAX_SUMMARY_CHARS = 1500


@dataclass
class WritebackAction:
    kind: str  # "comment" | "status"
    task_id: str
    detail: str
    ok: bool = True
    error: str = ""


@dataclass
class WritebackResult:
    ok: bool = True
    dry_run: bool = False
    skipped_reason: str = ""
    actions: list[WritebackAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip(text: str, limit: int = _MAX_SUMMARY_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class ClickUpWriteback:
    def __init__(self, config: WorkspaceConfig, client: Any | None) -> None:
        self.config = config
        self.client = client

    # -- public milestones -------------------------------------------------
    def on_dispatch(self, decision: RouteDecision, kanban_task_id: str = "", *, is_comment: bool = False) -> WritebackResult:
        if decision.event is None:
            return WritebackResult(ok=False, skipped_reason="route decision has no event")
        task_id = decision.event.work_item.issue_id
        comment = None
        if self.config.comment_on_dispatch:
            where = f"Kanban task {kanban_task_id}" if kanban_task_id else "Hermes Kanban"
            lines = [f"{ROUTER_COMMENT_MARKER} Routed to {decision.profile} ({where} on board {self.config.kanban_board})."]
            if decision.reason:
                lines.append(f"Why: {decision.reason}.")
            if decision.action == "blocked":
                lines.append(decision.dependency_summary)
                lines.append("Execution starts once the blockers above are closed.")
            comment = "\n".join(lines)
        status = None
        wants_status = self.config.comment_routing.update_status_on_comment_trigger if is_comment else self.config.update_status_on_trigger
        if wants_status and decision.action == "dispatch" and self.config.status_on_trigger:
            current = decision.event.work_item.status.strip().lower()
            if current != self.config.status_on_trigger.strip().lower():
                status = self.config.status_on_trigger
        return self._apply(task_id, comment=comment, status=status)

    def on_kanban_done(self, clickup_task_id: str, profile: str, kanban_task_id: str, summary: str) -> WritebackResult:
        lines = [f"{ROUTER_COMMENT_MARKER} {profile} finished Kanban task {kanban_task_id}."]
        if summary:
            lines.append("")
            lines.append(_clip(summary))
        status = self.config.complete_status if self.config.update_status_on_complete and self.config.complete_status else None
        return self._apply(clickup_task_id, comment="\n".join(lines), status=status)

    def on_kanban_blocked(self, clickup_task_id: str, profile: str, kanban_task_id: str, reason: str, *, reminder: bool = False) -> WritebackResult:
        prefix = "is still blocked" if reminder else "is blocked"
        lines = [f"{ROUTER_COMMENT_MARKER} {profile} {prefix} on Kanban task {kanban_task_id}."]
        if reason:
            lines.append(f"Reason: {_clip(reason, 500)}")
        lines.append("Reply here to unblock; the comment is forwarded to the active task.")
        return self._apply(clickup_task_id, comment="\n".join(lines))

    def on_kanban_review(self, clickup_task_id: str, profile: str, kanban_task_id: str, summary: str) -> WritebackResult:
        lines = [f"{ROUTER_COMMENT_MARKER} {profile} marked Kanban task {kanban_task_id} ready for review."]
        if summary:
            lines.append("")
            lines.append(_clip(summary))
        return self._apply(clickup_task_id, comment="\n".join(lines), status=self.config.review_status or None)

    # -- mechanics -----------------------------------------------------------
    def _apply(self, task_id: str, *, comment: str | None = None, status: str | None = None) -> WritebackResult:
        planned: list[WritebackAction] = []
        if comment:
            planned.append(WritebackAction("comment", task_id, comment))
        if status:
            planned.append(WritebackAction("status", task_id, status))
        if not planned:
            return WritebackResult(skipped_reason="nothing to write")
        if not self.config.writeback_enabled:
            return WritebackResult(skipped_reason="ClickUp write-back disabled", actions=planned)
        if self.config.dry_run:
            return WritebackResult(dry_run=True, actions=planned)
        if self.client is None:
            return WritebackResult(skipped_reason="no ClickUp API token configured", actions=planned)
        ok = True
        for action in planned:
            try:
                if action.kind == "comment":
                    self.client.post_comment(task_id, action.detail)
                else:
                    self.client.update_status(task_id, action.detail)
            except Exception as exc:  # report, never raise into the webhook path
                action.ok = False
                action.error = str(exc)[:300]
                ok = False
        return WritebackResult(ok=ok, actions=planned)

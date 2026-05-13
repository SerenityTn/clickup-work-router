"""Hermes Kanban dispatch backend.

v1 shells out to `hermes kanban --board <board> create ...`. Tests can use
dry_run=True to inspect the command without mutating any production board.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
from typing import Any

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.core.prompt_builder import build_prompt
from clickup_work_router.models import RouteDecision


@dataclass
class DispatchResult:
    ok: bool
    command: list[str]
    title: str
    body: str
    external_ref: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    dry_run: bool = False


def external_ref(config: WorkspaceConfig, decision: RouteDecision) -> str:
    if decision.event is None:
        raise ValueError("route decision has no event")
    return f"clickup:{config.company_slug or config.name}:{decision.event.work_item.issue_id}:{decision.profile}"


def build_kanban_payload(config: WorkspaceConfig, decision: RouteDecision) -> tuple[str, str, str]:
    if decision.event is None:
        raise ValueError("route decision has no event")
    ref = external_ref(config, decision)
    item = decision.event.work_item
    title = f"[ClickUp] {item.title}"
    body = build_prompt(decision)
    marker = f"\n\nExternal-Ref: {ref}\nTarget-Profile: {decision.profile}\nSource-URL: {item.url}\n"
    # Use the per-event dedupe key for Kanban idempotency when present. The
    # human-readable External-Ref stays task/profile scoped for traceability, but
    # comment routes need separate Kanban tasks so a new human comment is not
    # collapsed into an older completed task for the same ClickUp task/profile.
    idempotency_key = decision.dedupe_key or ref
    return title, body + marker, idempotency_key


def _run_command(cmd: list[str], runner: Any = None, timeout: int = 120):
    run = runner or subprocess.run
    try:
        return run(cmd, text=True, capture_output=True, check=False, timeout=timeout)
    except TypeError:
        return run(cmd, text=True, capture_output=True, check=False)


def _cli_error(stdout: str, stderr: str, returncode: int) -> bool:
    combined = f"{stdout}\n{stderr}".lower()
    return returncode != 0 or any(
        marker in combined
        for marker in (
            "kanban: board",
            "does not exist",
            "usage: hermes kanban",
            "error:",
            "traceback",
        )
    )


def find_active_kanban_task(config: WorkspaceConfig, decision: RouteDecision, runner: Any = None) -> str:
    """Return the active Kanban task id for this ClickUp task/profile, if any."""
    ref = external_ref(config, decision)
    hermes_bin = os.environ.get("HERMES_BIN", "hermes")
    cmd = [hermes_bin, "kanban", "--board", config.kanban_board, "list", "--assignee", decision.profile, "--json"]
    if config.dry_run:
        return ""
    proc = _run_command(cmd, runner=runner, timeout=60)
    stdout = getattr(proc, "stdout", "") or ""
    stderr = getattr(proc, "stderr", "") or ""
    if _cli_error(stdout, stderr, getattr(proc, "returncode", 1)):
        return ""
    try:
        tasks = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    active_statuses = {"ready", "running", "todo", "triage", "blocked"}
    matches = [
        task for task in tasks
        if isinstance(task, dict)
        and task.get("status") in active_statuses
        and f"External-Ref: {ref}" in str(task.get("body") or "")
    ]
    if not matches:
        return ""
    matches.sort(key=lambda task: int(task.get("created_at") or 0), reverse=True)
    return str(matches[0].get("id") or "")


def append_comment_to_kanban(config: WorkspaceConfig, decision: RouteDecision, task_id: str, runner: Any = None) -> DispatchResult:
    """Append a human ClickUp comment to the active Kanban task context."""
    if decision.event is None:
        raise ValueError("route decision has no event")
    author = decision.event.author.display_name or decision.event.author.username if decision.event.author else "human"
    text = (
        "ClickUp human comment forwarded as execution context.\n\n"
        f"Task: {decision.event.work_item.url}\n"
        f"From: {author}\n"
        f"Comment: {decision.event.comment_text}\n"
    )
    hermes_bin = os.environ.get("HERMES_BIN", "hermes")
    cmd = [hermes_bin, "kanban", "--board", config.kanban_board, "comment", task_id, text, "--author", "clickup-work-router"]
    if config.dry_run:
        return DispatchResult(True, cmd, f"append:{task_id}", text, task_id, dry_run=True)
    proc = _run_command(cmd, runner=runner, timeout=60)
    stdout = getattr(proc, "stdout", "") or ""
    stderr = getattr(proc, "stderr", "") or ""
    ok = not _cli_error(stdout, stderr, getattr(proc, "returncode", 1))
    return DispatchResult(ok, cmd, f"append:{task_id}", text, task_id, stdout=stdout, stderr=stderr, returncode=getattr(proc, "returncode", 1), dry_run=False)


def dispatch_direct_reply(config: WorkspaceConfig, decision: RouteDecision, runner: Any = None) -> DispatchResult:
    """Spawn the profile directly for a concise ClickUp reply, without Kanban."""
    if decision.event is None:
        raise ValueError("route decision has no event")
    item = decision.event.work_item
    prompt = (
        "Reply directly in ClickUp; do not create a Kanban task unless the live task clearly requires new execution.\n\n"
        f"Task: {item.url}\n"
        f"Task ID: {item.issue_id}\n"
        f"Title: {item.title}\n"
        f"Human comment: {decision.event.comment_text}\n\n"
        "Instructions: read the live task and recent comments, then post a concise natural ClickUp reply as the assigned role. "
        "If the comment is only a question/status/approval, answer it directly. If it materially changes execution, record the decision in ClickUp and leave any execution to the existing task/router."
    )
    hermes_bin = os.environ.get("HERMES_BIN", "hermes")
    cmd = [hermes_bin, "-p", decision.profile, "-s", "clickup-ai-team-workflow", "chat", "-q", prompt]
    if config.dry_run:
        return DispatchResult(True, cmd, f"direct-reply:{item.issue_id}", prompt, decision.dedupe_key or external_ref(config, decision), dry_run=True)
    if runner is not None:
        proc = _run_command(cmd, runner=runner, timeout=60)
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        ok = not _cli_error(stdout, stderr, getattr(proc, "returncode", 1))
        return DispatchResult(ok, cmd, f"direct-reply:{item.issue_id}", prompt, decision.dedupe_key or external_ref(config, decision), stdout=stdout, stderr=stderr, returncode=getattr(proc, "returncode", 1), dry_run=False)
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return DispatchResult(False, cmd, f"direct-reply:{item.issue_id}", prompt, decision.dedupe_key or external_ref(config, decision), stderr=str(exc), returncode=1, dry_run=False)
    return DispatchResult(True, cmd, f"direct-reply:{item.issue_id}", prompt, decision.dedupe_key or external_ref(config, decision), dry_run=False)


def dispatch_to_kanban(config: WorkspaceConfig, decision: RouteDecision, dry_run: bool | None = None, runner: Any = None) -> DispatchResult:
    title, body, ref = build_kanban_payload(config, decision)
    hermes_bin = os.environ.get("HERMES_BIN", "hermes")
    cmd = [
        hermes_bin, "kanban", "--board", config.kanban_board, "create",
        title,
        "--body", body,
        "--assignee", decision.profile,
        "--idempotency-key", ref,
        "--created-by", "clickup-work-router",
    ]
    use_dry_run = config.dry_run if dry_run is None else dry_run
    if use_dry_run:
        return DispatchResult(True, cmd, title, body, ref, dry_run=True)
    run = runner or subprocess.run
    try:
        proc = run(cmd, text=True, capture_output=True, check=False, timeout=120)
    except TypeError:
        proc = run(cmd, text=True, capture_output=True, check=False)
    except subprocess.TimeoutExpired as exc:
        return DispatchResult(
            ok=False,
            command=cmd,
            title=title,
            body=body,
            external_ref=ref,
            stdout=getattr(exc, "stdout", "") or "",
            stderr=f"Hermes Kanban dispatch timed out after {exc.timeout}s",
            returncode=124,
            dry_run=False,
        )
    stdout = getattr(proc, "stdout", "") or ""
    stderr = getattr(proc, "stderr", "") or ""
    combined = f"{stdout}\n{stderr}".lower()
    # Some Hermes Kanban validation paths currently print a `kanban: ...` error
    # but still exit 0. Treat those as dispatch failures so webhook delivery does
    # not appear successful while no Kanban task was created.
    cli_reported_error = any(
        marker in combined
        for marker in (
            "kanban: board",
            "does not exist",
            "usage: hermes kanban",
            "error:",
            "traceback",
        )
    )
    return DispatchResult(
        ok=(proc.returncode == 0 and not cli_reported_error),
        command=cmd,
        title=title,
        body=body,
        external_ref=ref,
        stdout=stdout,
        stderr=stderr,
        returncode=getattr(proc, "returncode", 1),
        dry_run=False,
    )

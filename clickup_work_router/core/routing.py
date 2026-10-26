"""Provider-neutral routing decisions."""
from __future__ import annotations

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.core.comment_triage import is_meaningful_cross_agent_handoff
from clickup_work_router.core.dedupe import stable_event_key
from clickup_work_router.core.profile_mapping import ProfileMapper
from clickup_work_router.models import RouteDecision, WorkEvent


_TERMINAL = {"closed", "complete", "completed", "done"}
_COMMENT_EVENTS = {"taskCommentPosted", "taskCommentUpdated", "comment", "comment_created"}
_CREATE_EVENTS = {"taskCreated", "task_created"}
_ASSIGNEE_EVENTS = {"taskAssigneeUpdated"}

# Every comment the router writes to ClickUp starts with this marker. ClickUp
# echoes those comments back as webhooks, often authored by a human-owned API
# token, so the marker is the loop guard that does not depend on user mapping.
ROUTER_COMMENT_MARKER = "[clickup-work-router]"


def dependency_summary(event: WorkEvent) -> tuple[bool, str]:
    blockers = [d for d in event.work_item.dependencies if d.relation == "blocks_current" and not d.is_closed]
    if blockers:
        lines = ["Open upstream blockers prevent execution:"]
        lines.extend(f"- {d.issue_key or d.issue_id}: {d.title or d.status or 'open'}" for d in blockers)
        return True, "\n".join(lines)
    downstream = [d for d in event.work_item.dependencies if d.relation == "current_blocks" and not d.is_closed]
    if downstream:
        return False, "Current task blocks downstream tasks, but downstream dependents are not blockers for this task."
    return False, "No open upstream blockers detected."


def _route_assignee_change(event: WorkEvent, mapper: ProfileMapper, author_agent_profile: str) -> RouteDecision | None:
    """Route a ClickUp assignee change, or return None to fall through to ignore."""
    for user in event.assignees_added:
        profile = mapper.profile_for_user(user)
        if not profile:
            continue
        if author_agent_profile and profile == author_agent_profile:
            return RouteDecision("ignore", reason="loop prevention: agent assigned itself", event=event)
        reason = f"ClickUp assignee added {user.id}"
        if author_agent_profile:
            reason = f"cross-agent handoff from {author_agent_profile}; {reason}"
        return _finish(event, profile, reason)
    for user in event.assignees_removed:
        profile = mapper.profile_for_user(user)
        if profile:
            decision = RouteDecision("unassign", profile=profile, reason=f"ClickUp assignee removed {user.id}", event=event)
            decision.dedupe_key = stable_event_key(event, profile)
            return decision
    return None


def _finish(event: WorkEvent, profile: str, reason: str) -> RouteDecision:
    blocked, dep_summary = dependency_summary(event)
    action = "blocked" if blocked else "dispatch"
    decision = RouteDecision(action=action, profile=profile, reason=reason, dependency_summary=dep_summary, event=event)
    decision.dedupe_key = stable_event_key(event, profile)
    return decision


def route_event(event: WorkEvent, config: WorkspaceConfig) -> RouteDecision:
    mapper = ProfileMapper(config)
    item = event.work_item
    if config.list_ids and item.list_id not in config.list_ids:
        return RouteDecision("ignore", reason="task outside configured ClickUp list ids", event=event)
    if item.is_closed or item.status.strip().lower() in _TERMINAL:
        return RouteDecision("ignore", reason="task is closed/completed", event=event)
    is_comment = event.event_type in _COMMENT_EVENTS or bool(event.comment_text)
    is_create = event.event_type in _CREATE_EVENTS
    is_assignee_change = event.event_type in _ASSIGNEE_EVENTS
    if is_comment and event.comment_text.lstrip().startswith(ROUTER_COMMENT_MARKER):
        return RouteDecision("ignore", reason="loop prevention: router-authored ClickUp comment", event=event)
    author_agent_profile = mapper.agent_profile_for_user(event.author)

    if is_assignee_change and not is_comment:
        if not config.route_assignee_changes:
            return RouteDecision("ignore", reason="assignee-change routing disabled", event=event)
        decision = _route_assignee_change(event, mapper, author_agent_profile)
        return decision or RouteDecision("ignore", reason="assignee change did not involve a mapped profile", event=event)

    if author_agent_profile and not is_create and not is_comment:
        return RouteDecision("ignore", reason="loop prevention: agent-authored non-comment update", event=event)

    profile = ""
    reason = ""
    if not is_comment and not config.route_all_task_updates and not is_create:
        return RouteDecision("ignore", reason="task update routing disabled for non-create events", event=event)
    if is_comment and config.comment_routing.mention_routing:
        for alias in event.mentioned_aliases:
            profile = mapper.profile_for_alias(alias)
            if profile:
                reason = f"explicit comment mention @{alias}"
                break
    if not profile and (not is_comment or config.assignment_source == "assignee"):
        for user in item.assignees:
            profile = mapper.profile_for_user(user)
            if profile:
                reason = f"native ClickUp assignee {user.id}"
                break
    if not profile and is_comment and config.comment_routing.watcher_routing:
        candidates = item.assignees if config.comment_routing.watcher_only_assignees else item.watchers
        for user in candidates:
            profile = mapper.profile_for_user(user)
            if profile:
                reason = "comment watcher/assignee routing fallback"
                break
    if not profile:
        return RouteDecision("ignore", reason="no mapped target profile", event=event)
    if author_agent_profile and not is_create:
        if profile == author_agent_profile:
            return RouteDecision("ignore", reason="loop prevention: agent-authored comment on own task", event=event)
        if not event.mentioned_aliases and not is_meaningful_cross_agent_handoff(event.comment_text):
            return RouteDecision("ignore", reason="loop prevention: agent-authored cross-agent comment lacks handoff signal", event=event)
        reason = f"cross-agent handoff from {author_agent_profile}; {reason}"
    return _finish(event, profile, reason)

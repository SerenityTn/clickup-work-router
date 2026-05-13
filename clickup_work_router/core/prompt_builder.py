"""Build the prompt/body contract sent to Hermes Kanban."""
from __future__ import annotations

from clickup_work_router.models import RouteDecision, WorkEvent


def _users(users) -> str:
    if not users:
        return "none"
    return ", ".join(filter(None, [(u.display_name or u.username or u.email or u.id) for u in users]))


def build_prompt(decision: RouteDecision, recent_comments: list[str] | None = None) -> str:
    event = decision.event
    if event is None:
        raise ValueError("route decision has no event")
    item = event.work_item
    comments = recent_comments or ([event.comment_text] if event.comment_text else [])
    deps = decision.dependency_summary or "No open upstream blockers detected."
    lines = [
        "# ClickUp Work Router -> Hermes Kanban Contract",
        "",
        "## 1. Trigger source and event reason",
        f"Source system: {event.source_system}",
        f"Event type: {event.event_type}",
        f"Assignment reason: {decision.reason}",
        "",
        "## 2. External task",
        f"Task ID: {item.issue_id}",
        f"Task URL: {item.url}",
        f"Title: {item.title}",
        f"Status: {item.status}",
        f"List ID: {item.list_id}",
        f"Project ID: {item.project_id}",
        "",
        "## 3. Target Hermes profile",
        f"Profile: {decision.profile}",
        "",
        "## 4. Native external assignees/watchers",
        f"Assignees: {_users(item.assignees)}",
        f"Watchers: {_users(item.watchers)}",
        "",
        "## 5. Recent comments (oldest to newest)",
    ]
    if comments:
        lines.extend(f"- {c}" for c in comments)
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## 6. Dependency gate summary",
        deps,
        "",
        "## 7. Operating rules",
        "- Treat the ClickUp task as the external assignment source of truth.",
        "- Respect dependencies; if blocked, post concise blocker context externally.",
        "- Collaborate through ClickUp for human-visible blockers, handoffs, and completion summaries.",
        "- Use Hermes Kanban for internal execution state; do not create a parallel queue (use Hermes Kanban for internal execution state).",
        "- Do not expose secrets, credentials, auth headers, or private tokens.",
        "- Do not ask the founder to solve agent-resolvable issues.",
        "",
        "## 8. Completion requirements",
        "- Summarize deliverables completed.",
        "- Include verification evidence such as tests, commands, or review links.",
        "- State risks/gaps clearly.",
        "- Name the next owner/task if follow-up is needed.",
    ])
    return "\n".join(lines)

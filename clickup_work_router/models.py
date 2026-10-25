"""Provider-neutral models for ClickUp Work Router."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ExternalUser:
    id: str
    username: str = ""
    email: str = ""
    display_name: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "ExternalUser | None":
        if not data:
            return None
        ident = data.get("id") or data.get("userid") or data.get("user_id") or data.get("email") or ""
        return cls(
            id=str(ident),
            username=str(data.get("username") or data.get("userName") or ""),
            email=str(data.get("email") or ""),
            display_name=str(data.get("display_name") or data.get("name") or data.get("username") or ""),
        )


@dataclass
class ExternalDependency:
    issue_id: str
    issue_key: str = ""
    url: str = ""
    title: str = ""
    status: str = ""
    is_closed: bool = False
    relation: Literal["blocks_current", "current_blocks", "related", "unknown"] = "unknown"


@dataclass
class WorkItem:
    source_system: str
    workspace_id: str
    project_id: str
    list_id: str
    issue_id: str
    issue_key: str
    url: str
    title: str
    description: str
    status: str
    is_closed: bool
    assignees: list[ExternalUser]
    watchers: list[ExternalUser]
    dependencies: list[ExternalDependency]
    custom_fields: dict[str, Any]
    raw: dict[str, Any]


@dataclass
class WorkEvent:
    source_system: str
    event_type: str
    event_id: str
    event_time: str
    payload_hash: str
    work_item: WorkItem
    author: ExternalUser | None
    comment_text: str = ""
    mentioned_aliases: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    assignees_added: list[ExternalUser] = field(default_factory=list)
    assignees_removed: list[ExternalUser] = field(default_factory=list)


@dataclass
class RouteDecision:
    action: Literal["dispatch", "ignore", "blocked", "unassign"]
    profile: str = ""
    reason: str = ""
    dedupe_key: str = ""
    dependency_summary: str = ""
    event: WorkEvent | None = None

    @property
    def should_dispatch(self) -> bool:
        return self.action in {"dispatch", "blocked"} and bool(self.profile)

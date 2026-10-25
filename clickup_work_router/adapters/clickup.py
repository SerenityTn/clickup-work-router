"""ClickUp adapter: payload parsing and minimal REST client seam."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from clickup_work_router.models import ExternalDependency, ExternalUser, WorkEvent, WorkItem
from clickup_work_router.security import verify_clickup_signature

_ALIAS_RE = re.compile(r"(?<![\w.-])@([A-Za-z][A-Za-z0-9_-]{1,63})")


def verify_signature(body: bytes, signature: str | None, secret: str | None) -> bool:
    return verify_clickup_signature(body, signature, secret)


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def extract_task_id(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get("task_id"),
        payload.get("taskId"),
        payload.get("id") if str(payload.get("event", "")).lower().startswith("task") else None,
        (payload.get("task") or {}).get("id") if isinstance(payload.get("task"), dict) else None,
        (payload.get("history_items") or [{}])[0].get("task_id") if isinstance(payload.get("history_items"), list) and payload.get("history_items") else None,
        (payload.get("history_item") or {}).get("task_id") if isinstance(payload.get("history_item"), dict) else None,
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    raise ValueError("ClickUp payload did not include a task id")


def _first_history_item(payload: dict[str, Any]) -> dict[str, Any]:
    history = payload.get("history_item") if isinstance(payload.get("history_item"), dict) else {}
    if history:
        return history
    history_items = payload.get("history_items")
    if isinstance(history_items, list) and history_items and isinstance(history_items[0], dict):
        return history_items[0]
    return {}


def extract_comment(payload: dict[str, Any]) -> tuple[str, ExternalUser | None, str]:
    comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
    history = _first_history_item(payload)
    history_comment = history.get("comment") if isinstance(history.get("comment"), dict) else {}
    text = (
        comment.get("text") or comment.get("comment_text") or comment.get("comment")
        or history_comment.get("text") or history_comment.get("comment_text") or history_comment.get("comment")
        or payload.get("comment_text") or history.get("comment_text") or history.get("text") or ""
    )
    author_data = (
        comment.get("user") or comment.get("author")
        or history_comment.get("user") or history_comment.get("author")
        or payload.get("user") or history.get("user")
    )
    comment_id = str(comment.get("id") or payload.get("comment_id") or history_comment.get("id") or history.get("id") or "")
    return str(text), ExternalUser.from_mapping(author_data), comment_id


def extract_explicit_mentions(payload: dict[str, Any], comment_text: str = "") -> list[str]:
    """Extract explicit textual aliases and explicit mention containers only.

    Deliberately ignores arbitrary user/assignee/watcher objects in the payload to
    avoid loop-prone false positives.
    """
    aliases: set[str] = {match.group(1).lower() for match in _ALIAS_RE.finditer(comment_text or "")}
    containers: list[Any] = []
    comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
    for key in ("mentions", "mentioned_users", "mention_users"):
        if key in payload:
            containers.append(payload[key])
        if key in comment:
            containers.append(comment[key])
    for container in containers:
        if not isinstance(container, list):
            continue
        for item in container:
            if isinstance(item, str):
                aliases.add(item.lstrip("@").lower())
            elif isinstance(item, dict):
                alias = item.get("alias") or item.get("username") or item.get("name")
                if alias:
                    aliases.add(str(alias).lstrip("@").lower())
    return sorted(aliases)


def extract_assignee_changes(payload: dict[str, Any]) -> tuple[list[ExternalUser], list[ExternalUser]]:
    """Return (added, removed) users from ClickUp ``taskAssigneeUpdated`` history.

    ClickUp reports one history item per change: ``assignee_add`` carries the
    new user in ``after`` and ``assignee_rem`` carries the removed user in
    ``before``.
    """
    items: list[Any] = []
    if isinstance(payload.get("history_items"), list):
        items.extend(payload["history_items"])
    if isinstance(payload.get("history_item"), dict):
        items.append(payload["history_item"])
    added: list[ExternalUser] = []
    removed: list[ExternalUser] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("field") or "")
        if field_name == "assignee_add":
            user = ExternalUser.from_mapping(item.get("after") if isinstance(item.get("after"), dict) else None)
            if user:
                added.append(user)
        elif field_name == "assignee_rem":
            user = ExternalUser.from_mapping(item.get("before") if isinstance(item.get("before"), dict) else None)
            if user:
                removed.append(user)
    return added, removed


def _users(items: Any) -> list[ExternalUser]:
    if not isinstance(items, list):
        return []
    out: list[ExternalUser] = []
    for item in items:
        if isinstance(item, dict):
            user = ExternalUser.from_mapping(item)
            if user:
                out.append(user)
    return out


def _is_closed(status: Any) -> bool:
    if isinstance(status, dict):
        if "type" in status and str(status.get("type")).lower() == "closed":
            return True
        status = status.get("status") or status.get("name") or ""
    return str(status).strip().lower() in {"closed", "complete", "completed", "done"}


def extract_dependencies(task: dict[str, Any]) -> list[ExternalDependency]:
    deps: list[ExternalDependency] = []
    current_id = str(task.get("id") or task.get("task_id") or "")
    for item in task.get("dependencies") or []:
        if not isinstance(item, dict):
            continue
        # ClickUp dependency rows often include the current task_id plus either:
        # - depends_on: upstream task blocking the current task
        # - dependency_of: downstream task blocked by the current task
        # When ClickUp returns a downstream row in the current task's dependency
        # list, it can be shaped as {task_id: downstream, depends_on: current}.
        # For routing, preserve direction and use the *other* task id.
        relation = "unknown"
        task_id = str(item.get("task_id") or "")
        depends_on = str(item.get("depends_on") or "")
        dependency_of = str(item.get("dependency_of") or "")
        if dependency_of:
            relation = "current_blocks"
            dep_id = dependency_of
        elif depends_on and current_id and depends_on == current_id and task_id and task_id != current_id:
            relation = "current_blocks"
            dep_id = task_id
        elif depends_on:
            relation = "blocks_current"
            dep_id = depends_on
        elif item.get("type") in {"blocking", "dependency_of"}:
            relation = "current_blocks"
            dep_id = task_id or item.get("id") or ""
        elif item.get("type") in {"waiting_on", "depends_on"}:
            relation = "blocks_current"
            dep_id = task_id or item.get("id") or ""
        else:
            dep_id = item.get("id") or task_id or ""
        deps.append(ExternalDependency(
            issue_id=str(dep_id),
            issue_key=str(item.get("custom_id") or item.get("issue_key") or dep_id),
            url=str(item.get("url") or ""),
            title=str(item.get("name") or item.get("title") or ""),
            status=str((item.get("status") or {}).get("status") if isinstance(item.get("status"), dict) else item.get("status") or ""),
            is_closed=bool(item.get("is_closed") or item.get("closed") or _is_closed(item.get("status"))),
            relation=relation,  # type: ignore[arg-type]
        ))
    return deps


def task_to_work_item(task: dict[str, Any], workspace_id: str, source_system: str = "clickup") -> WorkItem:
    status_obj = task.get("status")
    status = (status_obj or {}).get("status") if isinstance(status_obj, dict) else status_obj or ""
    list_obj = task.get("list") if isinstance(task.get("list"), dict) else {}
    project_obj = task.get("project") if isinstance(task.get("project"), dict) else {}
    return WorkItem(
        source_system=source_system,
        workspace_id=workspace_id,
        project_id=str(project_obj.get("id") or task.get("project_id") or ""),
        list_id=str(list_obj.get("id") or task.get("list_id") or ""),
        issue_id=str(task.get("id") or task.get("task_id") or ""),
        issue_key=str(task.get("custom_id") or task.get("id") or ""),
        url=str(task.get("url") or ""),
        title=str(task.get("name") or task.get("title") or ""),
        description=str(task.get("description") or task.get("text_content") or ""),
        status=str(status),
        is_closed=bool(task.get("archived") or task.get("closed") or _is_closed(status_obj)),
        assignees=_users(task.get("assignees")),
        watchers=_users(task.get("watchers") or task.get("subscribers")),
        dependencies=extract_dependencies(task),
        custom_fields={str(f.get("name") or f.get("id")): f.get("value") for f in task.get("custom_fields") or [] if isinstance(f, dict)},
        raw=task,
    )


def build_work_event(payload: dict[str, Any], task: dict[str, Any], workspace_id: str) -> WorkEvent:
    comment_text, author, comment_id = extract_comment(payload)
    mentions = extract_explicit_mentions(payload, comment_text)
    added, removed = extract_assignee_changes(payload)
    event_id = str(payload.get("event_id") or comment_id or payload.get("id") or extract_task_id(payload))
    event_time = str(payload.get("date") or payload.get("event_time") or "")
    history_items = payload.get("history_items")
    if not event_time and isinstance(history_items, list) and history_items:
        first_history = history_items[0]
        if isinstance(first_history, dict):
            event_time = str(first_history.get("date") or first_history.get("event_time") or "")
    return WorkEvent(
        source_system="clickup",
        event_type=str(payload.get("event") or payload.get("event_type") or "unknown"),
        event_id=event_id,
        event_time=event_time,
        payload_hash=payload_hash(payload),
        work_item=task_to_work_item(task, workspace_id),
        author=author,
        comment_text=comment_text,
        mentioned_aliases=mentions,
        raw={"payload": payload, "comment_id": comment_id},
        assignees_added=added,
        assignees_removed=removed,
    )


class ClickUpClient:
    def __init__(self, token: str, base_url: str = "https://api.clickup.com/api/v2") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, data: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(data).encode() if data is not None else None
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Authorization": self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - configured API client seam
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ClickUp API error {exc.code} for {method} {path}") from exc

    def fetch_task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/task/{task_id}")

    def fetch_comments(self, task_id: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/task/{task_id}/comment")
        return list(data.get("comments") or [])

    def update_status(self, task_id: str, status: str) -> dict[str, Any]:
        return self._request("PUT", f"/task/{task_id}", {"status": status})

    def post_comment(self, task_id: str, text: str, notify_all: bool = False) -> dict[str, Any]:
        return self._request("POST", f"/task/{task_id}/comment", {"comment_text": text, "notify_all": notify_all})

    def list_tasks(self, list_id: str, *, updated_after_ms: int | None = None, page: int = 0) -> dict[str, Any]:
        """One page of open tasks in a list, optionally only recently updated ones."""
        query: dict[str, Any] = {"include_closed": "false", "subtasks": "true", "page": page}
        if updated_after_ms:
            query["date_updated_gt"] = int(updated_after_ms)
        return self._request("GET", f"/list/{list_id}/task", query=query)

    def list_webhooks(self, team_id: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/team/{team_id}/webhook")
        return list(data.get("webhooks") or [])

    def create_webhook(self, team_id: str, endpoint: str, events: list[str], **scope: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"endpoint": endpoint, "events": events}
        payload.update({k: v for k, v in scope.items() if v})
        return self._request("POST", f"/team/{team_id}/webhook", payload)

    def update_webhook(self, webhook_id: str, endpoint: str, events: list[str], status: str = "active") -> dict[str, Any]:
        return self._request("PUT", f"/webhook/{webhook_id}", {"endpoint": endpoint, "events": events, "status": status})

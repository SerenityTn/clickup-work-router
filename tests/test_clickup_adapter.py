import json

from clickup_work_router.adapters.clickup import (
    build_work_event,
    extract_comment,
    extract_dependencies,
    extract_explicit_mentions,
    extract_task_id,
)
from clickup_work_router.security import compute_clickup_signature, verify_clickup_signature


def test_signature_accepts_valid_hmac_and_rejects_invalid():
    body = b'{"task_id":"abc"}'
    sig = compute_clickup_signature(body, "secret")
    assert verify_clickup_signature(body, sig, "secret")
    assert verify_clickup_signature(body, "sha256=" + sig, "secret")
    assert not verify_clickup_signature(body, "bad", "secret")


def test_task_id_extraction_shapes():
    assert extract_task_id({"task_id": "a"}) == "a"
    assert extract_task_id({"taskId": "b"}) == "b"
    assert extract_task_id({"task": {"id": "c"}}) == "c"
    assert extract_task_id({"history_items": [{"task_id": "d"}]}) == "d"


def test_comment_extraction_shapes():
    text, author, comment_id = extract_comment({"comment": {"id": "c1", "text": "hello", "user": {"id": 7, "username": "ann"}}})
    assert text == "hello"
    assert author and author.id == "7"
    assert comment_id == "c1"


def test_mention_parsing_ignores_arbitrary_users():
    payload = {
        "comment": {"text": "please @product-lead review", "mentions": [{"alias": "ceo"}]},
        "user": {"username": "founder"},
        "assignees": [{"username": "growth-specialist"}],
        "watchers": [{"username": "founding-engineer"}],
    }
    aliases = extract_explicit_mentions(payload, payload["comment"]["text"])
    assert aliases == ["ceo", "product-lead"]
    assert "growth-specialist" not in aliases
    assert "founding-engineer" not in aliases


def test_build_work_event_from_task_payload():
    payload = {"event": "taskCommentPosted", "task_id": "t1", "comment": {"text": "@ceo hi"}}
    task = {"id": "t1", "name": "Task", "list": {"id": "l1"}, "status": {"status": "open"}}
    event = build_work_event(payload, task, "ws")
    assert event.work_item.issue_id == "t1"
    assert event.comment_text == "@ceo hi"
    assert event.mentioned_aliases == ["ceo"]


def test_extract_dependencies_preserves_clickup_dependency_direction():
    deps = extract_dependencies({
        "id": "current",
        "dependencies": [
            {"task_id": "current", "depends_on": "upstream", "type": 1},
            {"task_id": "downstream", "depends_on": "current", "type": 1},
        ],
    })

    assert [(dep.issue_id, dep.relation) for dep in deps] == [
        ("upstream", "blocks_current"),
        ("downstream", "current_blocks"),
    ]


def test_assignee_history_items_become_added_and_removed_users():
    payload = {
        "event": "taskAssigneeUpdated",
        "task_id": "t1",
        "history_items": [
            {"id": "h1", "field": "assignee_add", "user": {"id": "human1"}, "after": {"id": "u2", "username": "Growth"}},
            {"id": "h2", "field": "assignee_rem", "user": {"id": "human1"}, "before": {"id": "u1"}},
        ],
    }
    event = build_work_event(payload, {"id": "t1", "name": "T", "list": {"id": "l1"}, "status": {"status": "open"}}, "ws")

    assert [user.id for user in event.assignees_added] == ["u2"]
    assert [user.id for user in event.assignees_removed] == ["u1"]
    assert event.author.id == "human1"

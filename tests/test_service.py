import threading
import time
from types import SimpleNamespace

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.models import ExternalUser
from clickup_work_router.service import RouterApp, parse_request_path, redact_request_target, router_token_is_valid


def _task_payload(comment_text="human comment"):
    return {
        "event": "taskCommentPosted",
        "event_id": "comment-1",
        "comment": {"id": "c1", "comment_text": comment_text, "user": {"id": "human1", "username": "Khaireddine"}},
        "task": {
            "id": "t1",
            "name": "Task",
            "list": {"id": "l1"},
            "status": {"status": "open"},
            "assignees": [{"id": "u1"}],
        },
    }
from clickup_work_router.state import STATE


def test_router_app_copies_runtime_environment():
    runtime_env = {}
    app = RouterApp(WorkspaceConfig(name="preview"), runtime_env=runtime_env)

    runtime_env["CLICKUP_API_TOKEN"] = "added-after-construction"

    assert app.health()["secrets"]["clickup_token"] is False


def test_dispatch_failure_does_not_poison_dedupe(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"u1": "product-lead-example"}
    app = RouterApp(cfg)
    payload = {
        "event": "taskCreated",
        "event_id": "event-1",
        "task": {
            "id": "t1",
            "name": "Task",
            "list": {"id": "l1"},
            "status": {"status": "open"},
            "assignees": [{"id": "u1"}],
        },
    }

    monkeypatch.setattr(
        "clickup_work_router.service.dispatch_to_kanban",
        lambda workspace, decision: SimpleNamespace(ok=False, external_ref="clickup:example:t1:product-lead-example", dry_run=False, stderr="boom", stdout=""),
    )

    response = app.handle_payload(payload)

    assert response["ok"] is False
    assert not STATE.has_seen("clickup:example:t1:product-lead-example:event-1")


def test_successful_dispatch_marks_dedupe(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=True)
    cfg.profile_map["clickup_user_ids"] = {"u1": "product-lead-example"}
    app = RouterApp(cfg)
    payload = {
        "event": "taskCreated",
        "event_id": "event-1",
        "task": {
            "id": "t1",
            "name": "Task",
            "list": {"id": "l1"},
            "status": {"status": "open"},
            "assignees": [{"id": "u1"}],
        },
    }

    response = app.handle_payload(payload)
    duplicate = app.handle_payload(payload)

    assert response["ok"] is True
    assert duplicate["status"] == "deduped"


def test_parse_request_path_ignores_router_token_query():
    path, query = parse_request_path("/clickup/example?token=secret&x=1")

    assert path == "/clickup/example"
    assert query["token"] == ["secret"]


def test_router_token_accepts_query_or_header():
    assert router_token_is_valid({}, {"token": ["secret"]}, "secret") is True
    assert router_token_is_valid({"X-Router-Token": "secret"}, {}, "secret") is True
    assert router_token_is_valid({}, {"token": ["wrong"]}, "secret") is False
    assert router_token_is_valid({}, {}, "") is True


def test_log_redacts_router_token_query():
    message = '"POST /clickup/example?token=secret-value&x=1 HTTP/1.1" 401 -'

    assert redact_request_target(message) == '"POST /clickup/example?token=[REDACTED]&x=1 HTTP/1.1" 401 -'


def test_comment_webhook_hydrates_latest_comment_author_for_loop_prevention(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"agent1": "growth-specialist-example"}
    cfg.profile_map["agent_user_ids"] = {"agent1": "growth-specialist-example"}

    class FakeClickUp:
        def fetch_task(self, task_id):
            return {
                "id": task_id,
                "name": "Task",
                "list": {"id": "l1"},
                "status": {"status": "open"},
                "assignees": [{"id": "agent1"}],
            }

        def fetch_comments(self, task_id):
            return [{"id": "c1", "date": "1000", "comment_text": "agent reply", "user": {"id": "agent1", "username": "growth-specialist"}}]

    app = RouterApp(cfg, clickup_client=FakeClickUp())
    response = app.handle_payload({"event": "taskCommentPosted", "task_id": "t1", "webhook_id": "webhook-subscription-id"})

    assert response == {"ok": True, "status": "ignored", "reason": "loop prevention: agent-authored comment on own task"}


def test_human_question_uses_direct_reply_not_kanban_task(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"u1": "product-lead-example"}
    calls = []
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision: None)
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: calls.append(("kanban", decision)) or SimpleNamespace(ok=True, external_ref="kanban", dry_run=False, stderr="", stdout=""))
    monkeypatch.setattr("clickup_work_router.service.dispatch_direct_reply", lambda workspace, decision: calls.append(("direct", decision)) or SimpleNamespace(ok=True, external_ref="direct", dry_run=False, stderr="", stdout=""))

    response = RouterApp(cfg).handle_payload(_task_payload("what is this task about?"))

    assert response["status"] == "direct_reply"
    assert [name for name, _ in calls] == ["direct"]


def test_context_comment_appends_to_active_kanban_task(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"u1": "product-lead-example"}
    calls = []
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision: "t_active")
    monkeypatch.setattr("clickup_work_router.service.append_comment_to_kanban", lambda workspace, decision, task_id: calls.append((task_id, decision.event.comment_text)) or SimpleNamespace(ok=True, external_ref=task_id, dry_run=False, stderr="", stdout=""))
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: calls.append(("kanban", decision)) or SimpleNamespace(ok=True, external_ref="kanban", dry_run=False, stderr="", stdout=""))

    response = RouterApp(cfg).handle_payload(_task_payload("approved, proceed with Path B instead"))

    assert response["status"] == "append_to_active"
    assert calls == [("t_active", "approved, proceed with Path B instead")]


def test_context_comment_appends_to_active_kanban_task_even_when_blocked(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"u1": "product-lead-example"}
    calls = []
    payload = _task_payload("can you proceed with this plan?")
    payload["task"]["dependencies"] = [{"task_id": "t1", "depends_on": "blocker1", "type": 1}]
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision: "t_active")
    monkeypatch.setattr("clickup_work_router.service.append_comment_to_kanban", lambda workspace, decision, task_id: calls.append((task_id, decision.action, decision.dependency_summary)) or SimpleNamespace(ok=True, external_ref=task_id, dry_run=False, stderr="", stdout=""))
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: calls.append(("kanban", decision)) or SimpleNamespace(ok=True, external_ref="kanban", dry_run=False, stderr="", stdout=""))

    response = RouterApp(cfg).handle_payload(payload)

    assert response["status"] == "append_to_active"
    assert len(calls) == 1
    assert calls[0][0] == "t_active"
    assert calls[0][1] == "blocked"
    assert "blocker1" in calls[0][2]


def test_new_scope_comment_creates_new_kanban_task(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"u1": "product-lead-example"}
    calls = []
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision: "t_active")
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: calls.append(decision) or SimpleNamespace(ok=True, external_ref="kanban", dry_run=False, stderr="", stdout=""))

    response = RouterApp(cfg).handle_payload(_task_payload("also build a referral landing page"))

    assert response["status"] == "dispatch"
    assert len(calls) == 1


def test_agent_authored_cross_agent_handoff_wakes_target_owner(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"growth1": "growth-specialist-example", "product1": "product-lead-example"}
    cfg.profile_map["agent_user_ids"] = {"growth1": "growth-specialist-example", "product1": "product-lead-example"}
    calls = []
    payload = _task_payload("Product definition is complete; Growth can proceed with Path B.")
    payload["comment"]["user"] = {"id": "product1", "username": "Product-Lead"}
    payload["task"]["assignees"] = [{"id": "growth1"}]
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision: None)
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: calls.append(decision) or SimpleNamespace(ok=True, external_ref="kanban", dry_run=False, stderr="", stdout=""))

    response = RouterApp(cfg).handle_payload(payload)

    assert response["status"] == "dispatch"
    assert response["profile"] == "growth-specialist-example"
    assert calls[0].profile == "growth-specialist-example"
    assert "cross-agent handoff" in calls[0].reason


def test_duplicate_webhook_posts_claim_dedupe_before_spawning_direct_reply(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    STATE.reset()
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"u1": "growth-specialist-example"}
    calls = []

    def slow_direct_reply(workspace, decision):
        calls.append(decision.dedupe_key)
        time.sleep(0.05)
        return SimpleNamespace(ok=True, external_ref="direct", dry_run=False, stderr="", stdout="")

    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision: None)
    monkeypatch.setattr("clickup_work_router.service.dispatch_direct_reply", slow_direct_reply)
    app = RouterApp(cfg)
    payload = _task_payload("what is next?")
    responses = []
    threads = [threading.Thread(target=lambda: responses.append(app.handle_payload(payload))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert sorted(response["status"] for response in responses) == ["deduped", "direct_reply"]


class RecordingClickUp:
    def __init__(self, task):
        self.task = task
        self.calls = []

    def fetch_task(self, task_id):
        return dict(self.task, id=task_id)

    def fetch_comments(self, task_id):
        return []

    def post_comment(self, task_id, text):
        self.calls.append(("comment", task_id, text))

    def update_status(self, task_id, status):
        self.calls.append(("status", task_id, status))


def _live_cfg():
    cfg = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example", dry_run=False)
    cfg.profile_map["clickup_user_ids"] = {"u1": "product-lead-example", "u2": "growth-specialist-example"}
    return cfg


_OPEN_TASK = {"name": "Task", "url": "https://app.clickup.com/t/t1", "list": {"id": "l1"}, "status": {"status": "to do"}, "assignees": [{"id": "u1"}]}


def test_live_dispatch_links_kanban_task_and_writes_back_to_clickup(monkeypatch):
    STATE.reset()
    clickup = RecordingClickUp(_OPEN_TASK)
    monkeypatch.setattr(
        "clickup_work_router.service.dispatch_to_kanban",
        lambda workspace, decision: SimpleNamespace(ok=True, external_ref="key", kanban_task_id="t_abc", dry_run=False, stderr="", stdout=""),
    )

    response = RouterApp(_live_cfg(), clickup_client=clickup).handle_payload({"event": "taskCreated", "event_id": "e1", "task_id": "t1"})

    assert response["status"] == "dispatch"
    assert response["kanban_task_id"] == "t_abc"
    assert response["writeback"]["ok"] is True
    links = STATE.open_links("example")
    assert [(link.clickup_task_id, link.profile, link.kanban_task_id) for link in links] == [("t1", "product-lead-example", "t_abc")]
    assert [call[0] for call in clickup.calls] == ["comment", "status"]
    assert "t_abc" in clickup.calls[0][2]


def test_assignee_change_does_not_duplicate_linked_work(monkeypatch):
    STATE.reset()
    STATE.add_link("example", "t1", "growth-specialist-example", "ref", "t_existing")
    calls = []
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: calls.append(decision))
    payload = {"event": "taskAssigneeUpdated", "task_id": "t1", "history_items": [{"id": "h1", "field": "assignee_add", "user": {"id": "human1"}, "after": {"id": "u2"}}]}

    response = RouterApp(_live_cfg(), clickup_client=RecordingClickUp(_OPEN_TASK)).handle_payload(payload)

    assert response["status"] == "already_active"
    assert calls == []


def test_assignee_change_adopts_unlinked_active_kanban_task(monkeypatch):
    STATE.reset()
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision, include_done=False: "t_legacy")
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    payload = {"event": "taskAssigneeUpdated", "task_id": "t1", "history_items": [{"id": "h1", "field": "assignee_add", "after": {"id": "u2"}}]}

    response = RouterApp(_live_cfg(), clickup_client=RecordingClickUp(_OPEN_TASK)).handle_payload(payload)

    assert response == {"ok": True, "status": "already_active", "profile": "growth-specialist-example", "external_ref": "t_legacy"}
    assert STATE.open_links("example")[0].kanban_task_id == "t_legacy"


def test_assignee_added_to_unlinked_task_dispatches_new_work(monkeypatch):
    STATE.reset()
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision, include_done=False: "")
    monkeypatch.setattr(
        "clickup_work_router.service.dispatch_to_kanban",
        lambda workspace, decision: SimpleNamespace(ok=True, external_ref="key", kanban_task_id="t_new", dry_run=False, stderr="", stdout=""),
    )
    payload = {"event": "taskAssigneeUpdated", "task_id": "t1", "history_items": [{"id": "h1", "field": "assignee_add", "after": {"id": "u2"}}]}

    response = RouterApp(_live_cfg(), clickup_client=RecordingClickUp(_OPEN_TASK)).handle_payload(payload)

    assert response["status"] == "dispatch"
    assert response["profile"] == "growth-specialist-example"


def test_assignee_removal_notifies_active_kanban_task(monkeypatch):
    STATE.reset()
    notes = []
    monkeypatch.setattr("clickup_work_router.service.find_active_kanban_task", lambda workspace, decision: "t_active")
    monkeypatch.setattr(
        "clickup_work_router.service.append_note_to_kanban",
        lambda workspace, task_id, text: notes.append((task_id, text)) or SimpleNamespace(ok=True, dry_run=False, stderr="", stdout=""),
    )
    payload = {"event": "taskAssigneeUpdated", "task_id": "t1", "history_items": [{"id": "h2", "field": "assignee_rem", "before": {"id": "u1"}}]}

    response = RouterApp(_live_cfg(), clickup_client=RecordingClickUp(_OPEN_TASK)).handle_payload(payload)

    assert response["status"] == "unassign"
    assert notes[0][0] == "t_active" and "removed by a human" in notes[0][1]


def test_dry_run_dispatch_reports_planned_writeback_without_links():
    STATE.reset()
    cfg = _live_cfg()
    cfg.dry_run = True
    clickup = RecordingClickUp(_OPEN_TASK)

    response = RouterApp(cfg, clickup_client=clickup).handle_payload({"event": "taskCreated", "event_id": "e1", "task_id": "t1"})

    assert response["dry_run"] is True
    assert [action["kind"] for action in response["writeback"]["actions"]] == ["comment", "status"]
    assert clickup.calls == []
    assert STATE.open_links("example") == []


def test_health_reports_links_and_reconcile_summary():
    STATE.reset()
    STATE.add_link("example", "t1", "p", "ref", "t_abc")
    STATE.set_meta("reconcile:example", {"checked": 1})

    health = RouterApp(_live_cfg(), runtime_env={}).health()

    assert health["links"] == {"open": 1}
    assert health["reconcile"] == {"checked": 1}
    assert health["state_persistent"] is False


def test_sweep_payload_treats_done_kanban_work_as_handled(monkeypatch):
    STATE.reset()
    lookups = []
    monkeypatch.setattr(
        "clickup_work_router.service.find_active_kanban_task",
        lambda workspace, decision, include_done=False: lookups.append(include_done) or "t_done",
    )
    monkeypatch.setattr("clickup_work_router.service.dispatch_to_kanban", lambda workspace, decision: (_ for _ in ()).throw(AssertionError("must not dispatch")))

    response = RouterApp(_live_cfg(), clickup_client=RecordingClickUp(_OPEN_TASK)).handle_payload({"event": "taskCreated", "event_id": "sweep:t1", "task_id": "t1", "router_sweep": True})

    assert response["status"] == "already_active"
    assert lookups == [True]

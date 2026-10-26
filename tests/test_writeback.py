from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.core.routing import ROUTER_COMMENT_MARKER
from clickup_work_router.models import ExternalUser, RouteDecision, WorkEvent, WorkItem
from clickup_work_router.writeback import ClickUpWriteback


class FakeClickUp:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def post_comment(self, task_id, text):
        if self.fail:
            raise RuntimeError("ClickUp API error 500 for POST /task/t1/comment")
        self.calls.append(("comment", task_id, text))

    def update_status(self, task_id, status):
        self.calls.append(("status", task_id, status))


def decision(action="dispatch", status="to do", deps="No open upstream blockers detected."):
    item = WorkItem("clickup", "ws", "p", "l1", "t1", "T1", "https://app.clickup.com/t/t1", "Build", "", status, False, [ExternalUser("u1")], [], [], {}, {})
    event = WorkEvent("clickup", "taskCreated", "e1", "", "h", item, None)
    return RouteDecision(action, profile="product-lead-example", reason="native ClickUp assignee u1", dependency_summary=deps, event=event)


def cfg(**kw):
    defaults = dict(name="example", company_slug="example", kanban_board="example", dry_run=False)
    defaults.update(kw)
    return WorkspaceConfig(**defaults)


def test_dispatch_posts_marked_ack_and_moves_status():
    client = FakeClickUp()
    result = ClickUpWriteback(cfg(), client).on_dispatch(decision(), "t_abc")

    assert result.ok is True
    kinds = [call[0] for call in client.calls]
    assert kinds == ["comment", "status"]
    assert client.calls[0][2].startswith(ROUTER_COMMENT_MARKER)
    assert "t_abc" in client.calls[0][2]
    assert client.calls[1] == ("status", "t1", "in progress")


def test_blocked_dispatch_lists_blockers_and_keeps_status():
    client = FakeClickUp()
    ClickUpWriteback(cfg(), client).on_dispatch(decision("blocked", deps="Open upstream blockers prevent execution:\n- B1: open"), "t_abc")

    assert [call[0] for call in client.calls] == ["comment"]
    assert "B1" in client.calls[0][2]


def test_status_is_not_rewritten_when_already_current_or_comment_triggered():
    client = FakeClickUp()
    wb = ClickUpWriteback(cfg(), client)
    wb.on_dispatch(decision(status="In Progress"))
    wb.on_dispatch(decision(), is_comment=True)

    assert [call[0] for call in client.calls] == ["comment", "comment"]


def test_dry_run_plans_without_calling_clickup():
    client = FakeClickUp()
    result = ClickUpWriteback(cfg(dry_run=True), client).on_dispatch(decision())

    assert result.dry_run is True
    assert [action.kind for action in result.actions] == ["comment", "status"]
    assert client.calls == []


def test_disabled_or_tokenless_writeback_is_skipped_not_failed():
    assert ClickUpWriteback(cfg(writeback_enabled=False), FakeClickUp()).on_dispatch(decision()).skipped_reason == "ClickUp write-back disabled"
    tokenless = ClickUpWriteback(cfg(), None).on_dispatch(decision())
    assert tokenless.ok is True
    assert tokenless.skipped_reason == "no ClickUp API token configured"


def test_api_failure_is_reported_not_raised():
    result = ClickUpWriteback(cfg(), FakeClickUp(fail=True)).on_dispatch(decision())

    assert result.ok is False
    assert "500" in result.actions[0].error


def test_completion_comment_includes_summary_and_complete_status():
    client = FakeClickUp()
    ClickUpWriteback(cfg(), client).on_kanban_done("t1", "product-lead-example", "t_abc", "Shipped; 12 tests pass")

    assert "Shipped; 12 tests pass" in client.calls[0][2]
    assert client.calls[1] == ("status", "t1", "complete")

    client = FakeClickUp()
    ClickUpWriteback(cfg(update_status_on_complete=False), client).on_kanban_done("t1", "p", "t_abc", "")
    assert [call[0] for call in client.calls] == ["comment"]


def test_review_uses_optional_review_status():
    client = FakeClickUp()
    ClickUpWriteback(cfg(review_status="review"), client).on_kanban_review("t1", "p", "t_abc", "PR ready")

    assert client.calls[-1] == ("status", "t1", "review")

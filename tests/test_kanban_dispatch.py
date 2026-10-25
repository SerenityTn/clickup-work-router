from types import SimpleNamespace

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.dispatch.kanban import dispatch_to_kanban
from clickup_work_router.models import ExternalUser, RouteDecision, WorkEvent, WorkItem


def test_kanban_dispatch_prompt_contains_contract(monkeypatch):
    monkeypatch.delenv("HERMES_BIN", raising=False)
    cfg = WorkspaceConfig(name="example", company_slug="example", kanban_board="example", dry_run=True)
    item = WorkItem("clickup", "ws", "p", "l", "t1", "T1", "https://app.clickup.com/t/t1", "Build thing", "", "open", False, [ExternalUser("u")], [], [], {}, {})
    event = WorkEvent("clickup", "taskCreated", "e", "", "h", item, None)
    decision = RouteDecision("dispatch", profile="product-lead-example", reason="native assignee", event=event, dependency_summary="No blockers")
    result = dispatch_to_kanban(cfg, decision)
    assert result.dry_run is True
    assert result.title == "[ClickUp] Build thing"
    assert result.external_ref == "clickup:example:t1:product-lead-example"
    assert "ClickUp Work Router -> Hermes Kanban Contract" in result.body
    assert "use Hermes Kanban for internal execution state" in result.body
    assert "External-Ref: clickup:example:t1:product-lead-example" in result.body
    assert result.command[:5] == ["hermes", "kanban", "--board", "example", "create"]
    assert result.command[5] == "[ClickUp] Build thing"
    assert "--idempotency-key" in result.command
    assert "--external-ref" not in result.command


def test_kanban_dispatch_uses_event_dedupe_key_for_idempotency(monkeypatch):
    monkeypatch.delenv("HERMES_BIN", raising=False)
    cfg = WorkspaceConfig(name="example", company_slug="example", kanban_board="example", dry_run=True)
    item = WorkItem("clickup", "ws", "p", "l", "t1", "T1", "https://app.clickup.com/t/t1", "Build thing", "", "open", False, [ExternalUser("u")], [], [], {}, {})
    event = WorkEvent("clickup", "taskCommentPosted", "e", "", "h", item, None, comment_text="new human comment")
    decision = RouteDecision("dispatch", profile="growth-specialist-example", reason="comment fallback", event=event, dependency_summary="No blockers")
    decision.dedupe_key = "event-specific-key"

    result = dispatch_to_kanban(cfg, decision)

    assert result.external_ref == "event-specific-key"
    assert "External-Ref: clickup:example:t1:growth-specialist-example" in result.body
    assert result.command[result.command.index("--idempotency-key") + 1] == "event-specific-key"


def test_kanban_dispatch_respects_hermes_bin_env(monkeypatch):
    monkeypatch.setenv("HERMES_BIN", "/synthetic/bin/hermes")
    cfg = WorkspaceConfig(name="example", company_slug="example", kanban_board="example", dry_run=True)
    item = WorkItem("clickup", "ws", "p", "l", "t1", "T1", "https://app.clickup.com/t/t1", "Build thing", "", "open", False, [ExternalUser("u")], [], [], {}, {})
    event = WorkEvent("clickup", "taskCreated", "e", "", "h", item, None)
    decision = RouteDecision("dispatch", profile="product-lead-example", reason="native assignee", event=event, dependency_summary="No blockers")

    result = dispatch_to_kanban(cfg, decision)

    assert result.command[0] == "/synthetic/bin/hermes"


def test_kanban_dispatch_treats_cli_error_text_as_failure(monkeypatch):
    monkeypatch.delenv("HERMES_BIN", raising=False)
    cfg = WorkspaceConfig(name="example", company_slug="example", kanban_board="missing-board", dry_run=False)
    item = WorkItem("clickup", "ws", "p", "l", "t1", "T1", "https://app.clickup.com/t/t1", "Build thing", "", "open", False, [ExternalUser("u")], [], [], {}, {})
    event = WorkEvent("clickup", "taskCommentPosted", "e", "", "h", item, None, comment_text="@growth-specialist ping")
    decision = RouteDecision("dispatch", profile="growth-specialist-example", reason="explicit mention", event=event, dependency_summary="No blockers")

    def fake_runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="kanban: board 'missing-board' does not exist.", stderr="")

    result = dispatch_to_kanban(cfg, decision, runner=fake_runner)

    assert result.ok is False
    assert "does not exist" in result.stdout


def _decision(comment_text=""):
    item = WorkItem("clickup", "ws", "p", "l", "t1", "T1", "https://app.clickup.com/t/t1", "Fix error: login loop", "", "open", False, [ExternalUser("u")], [], [], {}, {})
    event = WorkEvent("clickup", "taskCreated", "e", "", "h", item, None, comment_text=comment_text)
    return RouteDecision("dispatch", profile="product-lead-example", reason="native assignee", event=event, dependency_summary="No blockers")


def test_kanban_dispatch_captures_task_id_from_json_even_when_body_mentions_errors(monkeypatch):
    import json

    monkeypatch.delenv("HERMES_BIN", raising=False)
    cfg = WorkspaceConfig(name="example", company_slug="example", kanban_board="example", dry_run=False)

    def fake_runner(cmd, **kwargs):
        assert cmd[-1] == "--json"
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "t_86007e9a", "status": "ready", "title": cmd[5], "body": "Traceback / error: in body text"}), stderr="")

    result = dispatch_to_kanban(cfg, _decision("error: the build failed"), runner=fake_runner)

    assert result.ok is True
    assert result.kanban_task_id == "t_86007e9a"


def test_show_kanban_task_and_active_lookup_cover_review_status(monkeypatch):
    import json

    from clickup_work_router.dispatch.kanban import find_active_kanban_task, show_kanban_task

    monkeypatch.delenv("HERMES_BIN", raising=False)
    cfg = WorkspaceConfig(name="example", company_slug="example", kanban_board="example", dry_run=False)
    listing = [{"id": "t_review", "status": "review", "created_at": 2, "body": "error: x\nExternal-Ref: clickup:example:t1:product-lead-example"}]

    def runner(cmd, **kwargs):
        if cmd[4] == "list":
            return SimpleNamespace(returncode=0, stdout=json.dumps(listing), stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"id": cmd[5], "status": "done"}}), stderr="")

    assert find_active_kanban_task(cfg, _decision(), runner=runner) == "t_review"
    assert show_kanban_task(cfg, "t_review", runner=runner)["task"]["status"] == "done"


def test_direct_reply_skill_is_configurable():
    from clickup_work_router.dispatch.kanban import dispatch_direct_reply

    cfg = WorkspaceConfig(name="example", company_slug="example", kanban_board="example", dry_run=True, direct_reply_skill="")
    assert "-s" not in dispatch_direct_reply(cfg, _decision("what?")).command
    cfg.direct_reply_skill = "team-skill"
    command = dispatch_direct_reply(cfg, _decision("what?")).command
    assert command[command.index("-s") + 1] == "team-skill"

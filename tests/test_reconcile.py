import json
from types import SimpleNamespace

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.reconcile import MISSING_LINK_GRACE_SECONDS, Reconciler
from clickup_work_router.state import LINK_CLOSED_IN_CLICKUP, LINK_DONE, LINK_MISSING, RouterState

NOW = 1_800_000_000.0


class FakeKanban:
    """Stands in for the `hermes kanban` CLI via the runner seam."""

    def __init__(self, tasks):
        self.tasks = tasks
        self.commands = []

    def __call__(self, cmd, **kwargs):
        self.commands.append(cmd)
        action = cmd[4]
        if action == "show":
            info = self.tasks.get(cmd[5])
            if info is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="error: unknown task")
            return SimpleNamespace(returncode=0, stdout=json.dumps(info), stderr="")
        if action == "comment":
            return SimpleNamespace(returncode=0, stdout=f"Commented on {cmd[5]}", stderr="")
        raise AssertionError(f"unexpected kanban command {cmd}")


class FakeClickUp:
    def __init__(self, tasks=None, lists=None, webhooks=None):
        self.tasks = tasks or {}
        self.lists = lists or {}
        self.webhooks = webhooks or []
        self.calls = []

    def fetch_task(self, task_id):
        return self.tasks.get(task_id, {"id": task_id, "status": {"status": "in progress", "type": "custom"}})

    def post_comment(self, task_id, text):
        self.calls.append(("comment", task_id, text))

    def update_status(self, task_id, status):
        self.calls.append(("status", task_id, status))

    def list_tasks(self, list_id, updated_after_ms=None, page=0):
        self.calls.append(("list_tasks", list_id, updated_after_ms))
        return {"tasks": self.lists.get(list_id, []), "last_page": True}

    def list_webhooks(self, team_id):
        return self.webhooks

    def update_webhook(self, webhook_id, endpoint, events, status="active"):
        self.calls.append(("update_webhook", webhook_id, status))


def kanban_info(status, summary="", blocked_at=0):
    events = [{"kind": "blocked", "created_at": blocked_at}] if blocked_at else []
    return {"task": {"id": "t_abc", "status": status, "result": None}, "latest_summary": summary, "events": events}


def make(tasks, clickup=None, clock=NOW, state=None, **cfg_kw):
    cfg_defaults = dict(name="example", company_slug="example", kanban_board="example", dry_run=False, sweep_lists=False)
    cfg_defaults.update(cfg_kw)
    config = WorkspaceConfig(**cfg_defaults)
    state = state or RouterState()
    kanban = FakeKanban(tasks)
    clickup = clickup if clickup is not None else FakeClickUp()
    reconciler = Reconciler(config, state=state, clickup=clickup, runner=kanban, clock=lambda: clock)
    return reconciler, state, kanban, clickup


def test_done_kanban_task_posts_completion_and_closes_link():
    reconciler, state, _, clickup = make({"t_abc": kanban_info("done", "Shipped, tests green")})
    state.add_link("example", "t1", "product-lead-example", "ref", "t_abc", "running")

    summary = reconciler.run_once()

    assert summary["completed"] == 1
    assert state.open_links("example") == []
    assert state.link_counts("example") == {LINK_DONE: 1}
    assert clickup.calls[0][0] == "comment" and "Shipped, tests green" in clickup.calls[0][2]
    assert clickup.calls[1] == ("status", "t1", "complete")
    assert reconciler.run_once()["completed"] == 0


def test_blocked_task_notifies_once_then_reminds_after_interval():
    tasks = {"t_abc": kanban_info("blocked", "need API key", blocked_at=NOW - 60)}
    reconciler, state, kanban, clickup = make(tasks)
    state.add_link("example", "t1", "p", "ref", "t_abc", "running")

    assert reconciler.run_once()["blocked_notified"] == 1
    assert reconciler.run_once()["blocked_notified"] == 0
    assert "need API key" in clickup.calls[0][2]

    later = Reconciler(reconciler.config, state=state, clickup=clickup, runner=kanban, clock=lambda: NOW + 25 * 3600)
    assert later.run_once()["blocked_notified"] == 1
    assert "still blocked" in clickup.calls[-1][2]


def test_review_notifies_once_and_resets_when_work_resumes():
    tasks = {"t_abc": kanban_info("review", "PR #12 ready")}
    reconciler, state, _, clickup = make(tasks)
    state.add_link("example", "t1", "p", "ref", "t_abc", "running")

    assert reconciler.run_once()["review_notified"] == 1
    assert reconciler.run_once()["review_notified"] == 0
    tasks["t_abc"] = kanban_info("running")
    reconciler.run_once()
    tasks["t_abc"] = kanban_info("review", "second round")
    assert reconciler.run_once()["review_notified"] == 1


def test_human_closing_clickup_task_notifies_active_kanban_task():
    clickup = FakeClickUp(tasks={"t1": {"id": "t1", "url": "https://app.clickup.com/t/t1", "status": {"status": "done", "type": "closed"}}})
    reconciler, state, kanban, _ = make({"t_abc": kanban_info("running")}, clickup=clickup)
    state.add_link("example", "t1", "p", "ref", "t_abc", "running")

    summary = reconciler.run_once()

    assert summary["closed_in_clickup"] == 1
    assert state.link_counts("example") == {LINK_CLOSED_IN_CLICKUP: 1}
    comment = [cmd for cmd in kanban.commands if cmd[4] == "comment"][0]
    assert comment[5] == "t_abc" and "closed by a human" in comment[6]


def test_links_without_kanban_id_become_missing_after_grace():
    reconciler, state, _, _ = make({}, clock=NOW + MISSING_LINK_GRACE_SECONDS + 1)
    state.add_link("example", "t1", "p", "ref", "")
    # add_link stamps real time; force it old relative to the fake clock.
    state._conn.execute("UPDATE links SET created_at = ?", (NOW,))

    assert reconciler.run_once()["missing"] == 1
    assert state.link_counts("example") == {LINK_MISSING: 1}


def test_unreadable_kanban_task_is_reported_without_stopping_the_pass():
    reconciler, state, _, _ = make({"t_ok": kanban_info("done")})
    state.add_link("example", "t1", "p", "ref", "t_gone")
    state.add_link("example", "t2", "p", "ref2", "t_ok")

    summary = reconciler.run_once()

    assert summary["completed"] == 1
    assert "t_gone" in summary["errors"][0]
    assert "reconcile" in state.last_error


def test_dry_run_reconcile_does_nothing():
    reconciler, state, kanban, clickup = make({"t_abc": kanban_info("done")}, dry_run=True)
    state.add_link("example", "t1", "p", "ref", "t_abc")

    assert reconciler.run_once()["skipped"] == "dry_run"
    assert kanban.commands == [] and clickup.calls == []


def test_sweep_dispatches_unrouted_mapped_tasks_only(tmp_path):
    clickup = FakeClickUp(lists={"l1": [
        {"id": "known", "assignees": [{"id": "u1"}]},
        {"id": "missed", "assignees": [{"id": "u1"}]},
        {"id": "human-only", "assignees": [{"id": "stranger"}]},
    ]})
    reconciler, state, _, _ = make({}, clickup=clickup, state=RouterState(path=tmp_path / "s.sqlite3"), sweep_lists=True, list_ids=["l1"])
    reconciler.config.profile_map["clickup_user_ids"] = {"u1": "product-lead-example"}
    state.add_link("example", "known", "product-lead-example", "ref", "t_known")
    state.update_link(1, state=LINK_DONE)
    dispatched = []
    reconciler.dispatch_payload = lambda payload: dispatched.append(payload) or {"ok": True, "status": "dispatch"}

    summary = reconciler.run_once()

    assert [payload["task_id"] for payload in dispatched] == ["missed"]
    assert dispatched[0]["router_sweep"] is True
    assert summary["swept"] == 1
    lookback_ms = clickup.calls[0][2]
    assert lookback_ms == int((NOW - 24 * 3600) * 1000)


def test_webhook_health_reactivates_suspended_hook():
    clickup = FakeClickUp(webhooks=[{"id": "wh1", "endpoint": "https://router.example/clickup/example?token=abc", "events": ["taskCreated"], "health": {"status": "suspended", "fail_count": 100}}])
    reconciler, _, _, _ = make({}, clickup=clickup, webhook_endpoint="https://router.example/clickup/example?token=abc")
    reconciler.team_id = "team"

    summary = reconciler.run_once()

    assert summary["webhook"]["reactivated"] is True
    assert "abc" not in json.dumps(summary)
    assert ("update_webhook", "wh1", "active") in clickup.calls
    assert summary["errors"] == []


def test_sweep_requires_persistent_state():
    clickup = FakeClickUp(lists={"l1": [{"id": "missed", "assignees": [{"id": "u1"}]}]})
    reconciler, _, _, _ = make({}, clickup=clickup, sweep_lists=True, list_ids=["l1"])
    reconciler.config.profile_map["clickup_user_ids"] = {"u1": "p"}
    reconciler.dispatch_payload = lambda payload: (_ for _ in ()).throw(AssertionError("must not sweep"))

    summary = reconciler.run_once()

    assert summary["swept"] == 0
    assert "CLICKUP_ROUTER_STATE_DB" in summary["sweep"]

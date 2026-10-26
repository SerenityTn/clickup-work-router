import os
import stat

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.webhooks import check_webhook_health, ensure_webhook, find_webhook


class FakeClickUp:
    def __init__(self, webhooks=None):
        self.webhooks = webhooks or []
        self.calls = []

    def list_webhooks(self, team_id):
        return self.webhooks

    def update_webhook(self, webhook_id, endpoint, events, status="active"):
        self.calls.append(("update", webhook_id, status))

    def create_webhook(self, team_id, endpoint, events, **scope):
        self.calls.append(("create", endpoint, tuple(events), scope))
        return {"id": "wh-new", "webhook": {"id": "wh-new", "secret": "synthetic-signing-secret"}}


def cfg(**kw):
    defaults = dict(name="example", dry_run=False, webhook_endpoint="https://router.example/clickup/example?token=t")
    defaults.update(kw)
    return WorkspaceConfig(**defaults)


def test_find_webhook_ignores_query_and_trailing_slash():
    hooks = [{"id": "a", "endpoint": "https://other/x"}, {"id": "b", "endpoint": "https://router.example/clickup/example/"}]

    assert find_webhook(hooks, "https://router.example/clickup/example?token=t")["id"] == "b"


def test_health_check_reports_without_endpoint_or_team():
    assert check_webhook_health(cfg(webhook_endpoint=""), FakeClickUp(), "team")["checked"] is False
    assert check_webhook_health(cfg(), FakeClickUp(), "")["checked"] is False


def test_healthy_hook_is_left_alone_and_failing_hook_respects_flag():
    healthy = FakeClickUp([{"id": "a", "endpoint": "https://router.example/clickup/example", "health": {"status": "active"}}])
    assert check_webhook_health(cfg(), healthy, "team")["status"] == "active"
    assert healthy.calls == []

    failing = FakeClickUp([{"id": "a", "endpoint": "https://router.example/clickup/example", "health": {"status": "failing", "fail_count": 3}}])
    report = check_webhook_health(cfg(webhook_auto_reactivate=False), failing, "team")
    assert report["status"] == "failing" and report["reactivated"] is False
    assert failing.calls == []


def test_ensure_create_writes_secret_privately_and_never_returns_it(tmp_path):
    client = FakeClickUp()
    out = tmp_path / "secrets" / "webhook.env"

    report = ensure_webhook(cfg(list_ids=["l1"]), client, "team", create=True, secret_out=str(out))

    assert report["created"] is True and report["secret_written"] is True
    assert "synthetic-signing-secret" not in str(report)
    assert out.read_text() == "CLICKUP_WEBHOOK_SECRET=synthetic-signing-secret\n"
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert client.calls[0][3] == {"list_id": "l1"}


def test_ensure_without_create_only_hints():
    report = ensure_webhook(cfg(), FakeClickUp(), "team", create=False, secret_out=None)

    assert report["registered"] is False
    assert "--create" in report["hint"]

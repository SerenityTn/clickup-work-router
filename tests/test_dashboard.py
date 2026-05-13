import os

from dashboard.plugin_api import get_status, redact


def test_dashboard_status_redacts_secret_values(monkeypatch):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "super-secret-token-value")
    monkeypatch.setenv("CLICKUP_WEBHOOK_SECRET", "webhook-secret")
    status = get_status("chessmentor")
    text = str(status)
    assert "super-secret-token-value" not in text
    assert "webhook-secret" not in text
    assert status["workspaces"]["chessmentor"]["secrets"]["clickup_token"] is True
    assert status["workspaces"]["chessmentor"]["secrets"]["webhook_secret"] is True


def test_redact_nested_token_like_keys():
    data = {"headers": {"Authorization": "Bearer abc"}, "nested": {"api_token": "abc"}, "ok": "value"}
    redacted = redact(data)
    assert redacted["headers"]["Authorization"] is True
    assert redacted["nested"]["api_token"] is True
    assert redacted["ok"] == "value"

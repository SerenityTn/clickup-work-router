import json
import os
from pathlib import Path

from dashboard.plugin_api import get_status, redact


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_status_redacts_secret_values(monkeypatch):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "super-secret-token-value")
    monkeypatch.setenv("CLICKUP_WEBHOOK_SECRET", "webhook-secret")
    status = get_status("example")
    text = str(status)
    assert "super-secret-token-value" not in text
    assert "webhook-secret" not in text
    assert status["workspaces"]["example"]["secrets"]["clickup_token"] is True
    assert status["workspaces"]["example"]["secrets"]["webhook_secret"] is True


def test_redact_nested_token_like_keys():
    data = {"headers": {"Authorization": "Bearer abc"}, "nested": {"api_token": "abc"}, "ok": "value"}
    redacted = redact(data)
    assert redacted["headers"]["Authorization"] is True
    assert redacted["nested"]["api_token"] is True
    assert redacted["ok"] == "value"


def test_dashboard_bundle_uses_current_plugin_sdk_contract():
    bundle = (REPO_ROOT / "dashboard" / "dist" / "index.js").read_text()

    assert "window.__HERMES_PLUGIN_SDK__" in bundle
    assert 'window.__HERMES_PLUGINS__.register("clickup-work-router"' in bundle
    assert "SDK.fetchJSON" in bundle
    assert "document.body" not in bundle


def test_dashboard_bundle_uses_community_readable_status_cards():
    bundle = (REPO_ROOT / "dashboard" / "dist" / "index.js").read_text()

    assert "WorkspaceCard" in bundle
    assert "No recent routing events." in bundle
    assert "Dry run" in bundle
    assert "Kanban board" in bundle
    assert "Credentials" in bundle
    assert "JsonBlock" not in bundle
    assert 'h("pre"' not in bundle


def test_dashboard_manifest_uses_supported_icon():
    manifest = json.loads((REPO_ROOT / "dashboard" / "manifest.json").read_text())

    assert manifest["icon"] in {
        "Activity", "BarChart3", "Clock", "Code", "Database", "Eye",
        "FileText", "Globe", "Heart", "KeyRound", "MessageSquare",
        "Package", "Puzzle", "Settings", "Shield", "Sparkles", "Star",
        "Terminal", "Wrench", "Zap",
    }


def test_native_desktop_plugin_uses_current_disk_plugin_contract():
    plugin = (REPO_ROOT / "desktop-plugin" / "plugin.js").read_text()

    assert "from '@hermes/plugin-sdk'" in plugin
    assert "from 'react/jsx-runtime'" in plugin
    assert "id: 'clickup-work-router'" in plugin
    assert "ctx.rest('/status')" in plugin
    assert "ROUTES_AREA" in plugin
    assert "SIDEBAR_NAV_AREA" in plugin

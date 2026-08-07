import json
import subprocess
import sys

from clickup_work_router.preview import run_preview


def test_preview_runs_ephemeral_http_dry_run_without_kanban_mutation(monkeypatch):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "must-not-be-used")
    monkeypatch.setenv("CLICKUP_WEBHOOK_SECRET", "must-not-be-required")
    monkeypatch.setenv("ROUTER_SHARED_TOKEN", "must-not-be-required")
    monkeypatch.setenv("HERMES_BIN", "/definitely-not-an-executable")

    def forbid_subprocess(*_args, **_kwargs):
        raise AssertionError("preview must not execute Hermes Kanban")

    monkeypatch.setattr("clickup_work_router.dispatch.kanban.subprocess.run", forbid_subprocess)
    monkeypatch.setattr("clickup_work_router.dispatch.kanban.subprocess.Popen", forbid_subprocess)

    result = run_preview(profile="founder")

    assert result["ok"] is True
    assert result["health"]["dry_run"] is True
    assert result["health"]["secrets"] == {
        "clickup_team_id": False,
        "clickup_token": False,
        "webhook_secret": False,
    }
    assert result["routing"]["status"] == "dispatch"
    assert result["routing"]["profile"] == "founder"
    assert result["routing"]["dry_run"] is True
    assert result["dry_run_enforced"] is True
    assert "kanban_mutated" not in result
    assert result["server_stopped"] is True


def test_preview_cli_stdout_is_machine_readable_json():
    completed = subprocess.run(
        [sys.executable, "-m", "clickup_work_router.preview", "--profile", "founder"],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["ok"] is True

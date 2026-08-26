from pathlib import Path
import os
import re
import subprocess

from clickup_work_router.config import apply_env_file_to_environ, env_bool, load_workspace_from_env
from clickup_work_router.service import RouterApp, router_token_is_valid


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ci_is_least_privilege_and_covers_supported_contracts():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "permissions:\n  contents: read" in workflow
    assert workflow.count("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803") == 3
    assert workflow.count("actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1") == 2
    assert "actions/setup-node@a0853c24544627f65ddf259abe73b1d18a591444" in workflow
    assert "actions/checkout@v" not in workflow
    assert "actions/setup-python@v" not in workflow
    assert "actions/setup-node@v" not in workflow
    assert workflow.count("persist-credentials: false") == 3
    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "pytest -q --ignore=tests/test_isolated_preview.py" in workflow
    assert "hermes-agent==0.19.0" in workflow
    assert "pytest -q tests/test_isolated_preview.py" in workflow
    assert "node --check dashboard/dist/index.js" in workflow
    assert "node --check desktop-plugin/plugin.js" in workflow


def test_contributor_docs_preserve_source_of_truth_and_plugin_boundaries():
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text()

    assert "ClickUp" in contributing and "authoritative" in contributing
    assert "Hermes Kanban" in contributing
    assert "DRY_RUN=true" in contributing
    assert "regular Hermes plugin" in contributing
    assert "native Desktop artifact" in contributing
    assert "must never be copied or enabled automatically" in contributing


def test_example_environment_contains_no_credential_values():
    lines = (REPO_ROOT / "examples" / "workspace.env.example").read_text().splitlines()
    values = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["DRY_RUN"] == "true"
    assert "CLICKUP_ROUTER_HOST" not in values
    assert "CLICKUP_ROUTER_PORT" not in values
    for key in (
        "CLICKUP_TEAM_ID",
        "CLICKUP_API_TOKEN",
        "CLICKUP_WEBHOOK_SECRET",
        "ROUTER_SHARED_TOKEN",
    ):
        assert values[key] == ""


def test_public_readme_does_not_expose_maintainer_workspace_paths():
    readme = (REPO_ROOT / "README.md").read_text()

    maintainer_workspace = "/home/" + "sysadmin/.hermes/profiles/founder/workspace"
    assert maintainer_workspace not in readme


def test_public_docs_use_neutral_examples_and_current_hosted_evidence():
    readme = (REPO_ROOT / "README.md").read_text()
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text()
    compatibility = (REPO_ROOT / "docs" / "hermes-compatibility.md").read_text()

    assert ("Chess" + "Mentor") not in readme
    assert "--profile founder" not in readme
    assert "--profile founder" not in contributing
    assert "profile: founder" not in compatibility
    assert "remains unobserved" not in compatibility
    assert "31227293980" in compatibility


def test_public_readme_documents_native_enablement_and_cleanup():
    readme = (REPO_ROOT / "README.md").read_text()

    assert "hermes plugins enable clickup-work-router --no-allow-tool-override" in readme
    assert "hermes plugins remove clickup-work-router" in readme
    assert "[`SECURITY.md`](SECURITY.md)" in readme


def test_public_readme_side_by_side_command_uses_repository_example():
    readme = (REPO_ROOT / "README.md").read_text()

    assert "--env-file examples/workspace.env.example" in readme
    assert "--env-file ~/.hermes/clickup-work-router/example.env" not in readme
    assert "set -e" in readme
    assert "PORT=$(python3 -c" in readme
    assert 'CLICKUP_ROUTER_PORT="$PORT"' in readme
    assert 'ROUTER_PID=$!' in readme
    assert "trap 'kill \"$ROUTER_PID\" 2>/dev/null || true' EXIT" in readme
    assert "--retry 10" in readme
    assert "--max-time 10" in readme
    assert 'http://127.0.0.1:$PORT/health' in readme
    assert 'kill "$ROUTER_PID"' in readme
    assert 'wait "$ROUTER_PID"' in readme
    assert "PORT=18765" not in readme
    assert "CLICKUP_ROUTER_PORT=8765" not in readme


def test_public_tree_uses_neutral_workspace_examples():
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT
    ).decode().split("\0")
    text_by_path = {}
    for relative in tracked:
        if not relative:
            continue
        try:
            text_by_path[relative] = (REPO_ROOT / relative).read_text()
        except UnicodeDecodeError:
            continue

    maintainer_home = "/home/" + "sysadmin/"
    assert all(maintainer_home not in text for text in text_by_path.values())

    legacy_name = "chess" + "mentor"
    legacy_paths = sorted(
        path for path, text in text_by_path.items() if legacy_name in text.lower()
    )
    assert legacy_paths == ["clickup_work_router/config.py"]

    fixture_paths = [
        REPO_ROOT / "tests" / "test_config.py",
        REPO_ROOT / "tests" / "test_routing.py",
        REPO_ROOT / "tests" / "test_service.py",
    ]
    fixture_text = "\n".join(path.read_text() for path in fixture_paths)
    assert re.findall(r"\b\d{7,}\b", fixture_text) == []


def test_explicit_empty_environment_does_not_fall_back_to_ambient(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_BOOL", "false")

    assert env_bool("SYNTHETIC_BOOL", default=True, env={}) is True


def test_documented_router_token_is_consumed_by_runtime(tmp_path, monkeypatch):
    template = (REPO_ROOT / "examples" / "workspace.env.example").read_text()
    assert "CLICKUP_ROUTER_TOKEN" not in template
    configured = template.replace("ROUTER_SHARED_TOKEN=", "ROUTER_SHARED_TOKEN=synthetic-test-token")
    env_file = tmp_path / "workspace.env"
    env_file.write_text(configured)

    monkeypatch.setattr(os, "environ", {})
    loaded = apply_env_file_to_environ(env_file)
    workspace = load_workspace_from_env("example", environ=loaded)
    app = RouterApp(workspace)
    token = app.runtime_env["ROUTER_SHARED_TOKEN"]

    assert token == "synthetic-test-token"
    assert router_token_is_valid({"X-Router-Token": token}, {}, token) is True
    assert router_token_is_valid({}, {}, token) is False

from pathlib import Path
import os

from clickup_work_router.config import apply_env_file_to_environ, load_workspace_from_env
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
    for key in (
        "CLICKUP_TEAM_ID",
        "CLICKUP_API_TOKEN",
        "CLICKUP_WEBHOOK_SECRET",
        "ROUTER_SHARED_TOKEN",
    ):
        assert values[key] == ""


def test_public_readme_does_not_expose_maintainer_workspace_paths():
    readme = (REPO_ROOT / "README.md").read_text()

    assert "/home/sysadmin/.hermes/profiles/founder/workspace" not in readme


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

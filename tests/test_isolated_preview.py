from pathlib import Path
import json
import subprocess
import sys

from clickup_work_router.isolated_preview import (
    _create_git_snapshot,
    _isolated_env,
    run_isolated_lifecycle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_isolated_lifecycle_uses_native_hermes_installer_and_cleans_up():
    result = run_isolated_lifecycle(REPO_ROOT, profile="example")

    assert result["ok"] is True
    assert result["installer"] == "hermes plugins install"
    assert result["regular_plugin"]["discovered"] is True
    assert result["regular_plugin"]["enabled"] is False
    assert result["desktop_plugin"]["installed"] is True
    assert result["preview"]["routing"]["profile"] == "example"
    assert result["preview"]["routing"]["dry_run"] is True
    assert result["preview"]["dry_run_enforced"] is True
    assert "kanban_mutated" not in result["preview"]
    assert result["regular_plugin"]["module_origin_verified"] is True
    assert result["desktop_plugin"]["runtime_loaded"] is False
    assert result["cleanup"]["regular_removed"] is True
    assert result["cleanup"]["desktop_removed"] is True
    assert result["cleanup"]["temporary_home_removed"] is True


def test_isolated_preview_cli_emits_json_on_failure(tmp_path):
    missing_source = tmp_path / "missing"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "clickup_work_router.isolated_preview",
            "--source-root",
            str(missing_source),
        ],
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["ok"] is False
    assert payload["error_type"] == "FileNotFoundError"
    assert completed.stderr == ""


def test_isolated_lifecycle_ignores_ambient_git_control_environment(tmp_path, monkeypatch):
    escaped_git_dir = tmp_path / "escaped.git"
    monkeypatch.setenv("GIT_DIR", str(escaped_git_dir))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "escaped-worktree"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "attacker.gitconfig"))

    result = run_isolated_lifecycle(REPO_ROOT, profile="example")

    assert result["ok"] is True
    assert not escaped_git_dir.exists()


def test_snapshot_uses_immutable_git_index_not_mutable_worktree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    env_root = tmp_path / "env"
    env_root.mkdir()
    env = _isolated_env(env_root, env_root / "hermes-home")
    git = "git"

    subprocess.run([git, "init", "-q"], cwd=source, env=env, check=True)
    subprocess.run([git, "config", "user.name", "Test"], cwd=source, env=env, check=True)
    subprocess.run([git, "config", "user.email", "test@localhost"], cwd=source, env=env, check=True)
    tracked = source / "tracked.txt"
    tracked.write_text("indexed-safe-content")
    subprocess.run([git, "add", "tracked.txt"], cwd=source, env=env, check=True)
    subprocess.run([git, "commit", "-qm", "safe index"], cwd=source, env=env, check=True)

    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must-not-enter-snapshot")
    tracked.unlink()
    tracked.symlink_to(outside)

    snapshot = tmp_path / "snapshot.git"
    _create_git_snapshot(source, snapshot, git_bin=git, env=env)
    captured = subprocess.run(
        [git, f"--git-dir={snapshot}", "show", "preview:tracked.txt"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert captured.stdout == "indexed-safe-content"
    assert "must-not-enter-snapshot" not in captured.stdout


def test_snapshot_materializes_from_a_shallow_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    env_root = tmp_path / "env"
    env_root.mkdir()
    env = _isolated_env(env_root, env_root / "hermes-home")
    git = "git"

    subprocess.run([git, "init", "-q"], cwd=source, env=env, check=True)
    subprocess.run([git, "config", "user.name", "Test"], cwd=source, env=env, check=True)
    subprocess.run([git, "config", "user.email", "test@localhost"], cwd=source, env=env, check=True)
    (source / "plugin.yaml").write_text("name: shallow-preview\n")
    subprocess.run([git, "add", "plugin.yaml"], cwd=source, env=env, check=True)
    subprocess.run([git, "commit", "-qm", "shallow source"], cwd=source, env=env, check=True)
    head = subprocess.run(
        [git, "rev-parse", "HEAD"], cwd=source, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()
    (source / ".git" / "shallow").write_text(f"{head}\n")

    snapshot = tmp_path / "snapshot.git"
    _create_git_snapshot(source, snapshot, git_bin=git, env=env)
    captured = subprocess.run(
        [git, f"--git-dir={snapshot}", "show", "preview:plugin.yaml"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert captured.stdout == "name: shallow-preview\n"

"""Exercise ClickUp Work Router through Hermes' native installer in a disposable profile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


PLUGIN_ID = "clickup-work-router"


class IsolatedPreviewError(RuntimeError):
    """Raised when a native-install preview checkpoint fails."""


def _run(command: list[str], *, env: dict[str, str], cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "command failed").strip()
        raise IsolatedPreviewError(f"{command[0]} failed with exit {completed.returncode}: {detail[:1000]}")
    return completed


def _isolated_env(root: Path, hermes_home: Path) -> dict[str, str]:
    temp_dir = root / "tmp"
    temp_dir.mkdir()
    home_dir = root / "home"
    home_dir.mkdir()
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home_dir),
        "HERMES_HOME": str(hermes_home),
        "TMPDIR": str(temp_dir),
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONNOUSERSITE": "1",
    }
    for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _create_git_snapshot(source: Path, snapshot: Path, *, git_bin: str, env: dict[str, str]) -> None:
    tree = _run([git_bin, "write-tree"], cwd=source, env=env).stdout.strip()
    commit = _run(
        [git_bin, "commit-tree", tree, "-m", "Disposable isolated preview snapshot"],
        cwd=source,
        env=env,
    ).stdout.strip()
    tree_listing = _run([git_bin, "ls-tree", "-r", commit], cwd=source, env=env).stdout.splitlines()
    symlinks = [line.split("\t", 1)[-1] for line in tree_listing if line.startswith("120000 ")]
    if symlinks:
        raise IsolatedPreviewError(f"staged Git snapshot contains symlinks: {', '.join(symlinks)}")

    _run([git_bin, "init", "--bare", "-q", str(snapshot)], env=env)
    _run(
        [
            git_bin,
            f"--git-dir={snapshot}",
            "fetch",
            "--quiet",
            "--no-tags",
            str(source),
            f"{commit}:refs/heads/preview",
        ],
        env=env,
    )
    installed_commit = _run(
        [git_bin, f"--git-dir={snapshot}", "rev-parse", "--verify", "refs/heads/preview"],
        env=env,
    ).stdout.strip()
    if installed_commit != commit:
        raise IsolatedPreviewError("immutable preview ref does not match the staged-index commit")
    _run(
        [git_bin, f"--git-dir={snapshot}", "symbolic-ref", "HEAD", "refs/heads/preview"],
        env=env,
    )


def run_isolated_lifecycle(source_root: str | Path, *, profile: str = "founder") -> dict[str, Any]:
    """Install, discover, exercise, and remove the plugin inside a random temporary Hermes home."""
    source = Path(source_root).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source Git worktree does not exist: {source}")

    hermes_bin = shutil.which("hermes")
    git_bin = shutil.which("git")
    if not hermes_bin or not git_bin:
        raise IsolatedPreviewError("both hermes and git must be available on PATH")

    result: dict[str, Any]
    temporary_home: Path
    with tempfile.TemporaryDirectory(prefix="clickup-router-preview-") as temporary_root:
        root = Path(temporary_root)
        temporary_home = root / "hermes-home"
        snapshot = root / "source-snapshot"
        env = _isolated_env(root, temporary_home)

        _create_git_snapshot(source, snapshot, git_bin=git_bin, env=env)
        _run([hermes_bin, "plugins", "install", snapshot.as_uri(), "--no-enable"], env=env)

        regular_target = temporary_home / "plugins" / PLUGIN_ID
        discovered_output = _run(
            [hermes_bin, "plugins", "list", "--user", "--no-bundled", "--json"],
            env=env,
        ).stdout
        enabled_output = _run(
            [hermes_bin, "plugins", "list", "--enabled", "--user", "--no-bundled", "--json"],
            env=env,
        ).stdout
        try:
            discovered_plugins = json.loads(discovered_output)
            enabled_plugins = json.loads(enabled_output)
        except json.JSONDecodeError as exc:
            raise IsolatedPreviewError("Hermes plugin discovery did not emit valid JSON") from exc
        if not isinstance(discovered_plugins, list) or not isinstance(enabled_plugins, list):
            raise IsolatedPreviewError("Hermes plugin discovery JSON has an unexpected shape")
        regular_discovered = regular_target.is_dir() and any(
            isinstance(entry, dict) and entry.get("name") == PLUGIN_ID for entry in discovered_plugins
        )
        regular_enabled = any(
            isinstance(entry, dict) and entry.get("name") == PLUGIN_ID for entry in enabled_plugins
        )
        if not regular_discovered or regular_enabled:
            raise IsolatedPreviewError("native Hermes discovery did not report the plugin as installed and disabled")

        desktop_source = regular_target / "desktop-plugin" / "plugin.js"
        if not desktop_source.is_file() or desktop_source.is_symlink():
            raise IsolatedPreviewError("staged snapshot has no regular Desktop plugin artifact")
        desktop_target = temporary_home / "desktop-plugins" / PLUGIN_ID
        desktop_target.mkdir(parents=True)
        shutil.copy2(desktop_source, desktop_target / "plugin.js")
        desktop_installed = (desktop_target / "plugin.js").read_bytes() == desktop_source.read_bytes()
        if not desktop_installed:
            raise IsolatedPreviewError("temporary Desktop artifact verification failed")

        bootstrap = (
            "import pathlib,sys;"
            "root=pathlib.Path(sys.argv[1]).resolve();"
            "sys.path.insert(0,str(root));"
            "import clickup_work_router.preview as module;"
            "origin=pathlib.Path(module.__file__).resolve();"
            "assert origin.is_relative_to(root), f'unexpected module origin: {origin}';"
            "raise SystemExit(module.main(['--profile',sys.argv[2]]))"
        )
        preview_completed = _run(
            [sys.executable, "-I", "-c", bootstrap, str(regular_target), profile],
            env=env,
            cwd=regular_target,
        )
        try:
            preview = json.loads(preview_completed.stdout)
        except json.JSONDecodeError as exc:
            raise IsolatedPreviewError("installed preview did not emit valid JSON") from exc
        if not preview.get("ok") or not preview.get("dry_run_enforced"):
            raise IsolatedPreviewError("installed preview did not preserve the enforced dry-run contract")

        _run([hermes_bin, "plugins", "remove", PLUGIN_ID], env=env)
        shutil.rmtree(desktop_target)
        regular_removed = not regular_target.exists()
        desktop_removed = not desktop_target.exists()
        if not regular_removed or not desktop_removed:
            raise IsolatedPreviewError("isolated cleanup did not remove both plugin surfaces")

        result = {
            "ok": True,
            "installer": "hermes plugins install",
            "regular_plugin": {
                "discovered": regular_discovered,
                "enabled": regular_enabled,
                "module_origin_verified": True,
            },
            "desktop_plugin": {
                "installed": desktop_installed,
                "runtime_loaded": False,
            },
            "preview": preview,
            "cleanup": {
                "regular_removed": regular_removed,
                "desktop_removed": desktop_removed,
                "temporary_home_removed": False,
            },
        }

    result["cleanup"]["temporary_home_removed"] = not temporary_home.exists()
    if not result["cleanup"]["temporary_home_removed"]:
        raise IsolatedPreviewError("temporary Hermes home still exists after lifecycle completion")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--profile", default="founder")
    args = parser.parse_args(argv)
    try:
        result = run_isolated_lifecycle(args.source_root, profile=args.profile)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Configuration loading for ClickUp Work Router.

The core deliberately uses only stdlib. Non-secret config may be provided by a
small JSON file; ChessMentor-compatible environment variables are normalized into
these dataclasses for v1. Secrets are referenced by environment variable name and
never serialized as values.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import os
from pathlib import Path
from typing import Any


_TRUE = {"1", "true", "yes", "on", "y"}


def env_bool(name: str, default: bool = False, env: dict[str, str] | None = None) -> bool:
    value = (env or os.environ).get(name)
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE


@dataclass
class CommentRoutingConfig:
    mention_routing: bool = True
    watcher_routing: bool = True
    watcher_only_assignees: bool = True
    update_status_on_comment_trigger: bool = False


@dataclass
class WorkspaceConfig:
    name: str
    enabled: bool = True
    source_system: str = "clickup"
    company_name: str = ""
    company_slug: str = ""
    clickup_team_id_env: str = "CLICKUP_TEAM_ID"
    clickup_token_env: str = "CLICKUP_API_TOKEN"
    webhook_secret_env: str = "CLICKUP_WEBHOOK_SECRET"
    webhook_path: str = ""
    list_ids: list[str] = field(default_factory=list)
    kanban_board: str = ""
    dispatch_backend: str = "kanban"
    assignment_source: str = "assignee"
    update_status_on_trigger: bool = True
    status_on_trigger: str = "in progress"
    complete_status: str = "complete"
    route_all_task_updates: bool = False
    comment_routing: CommentRoutingConfig = field(default_factory=CommentRoutingConfig)
    profile_map: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "clickup_user_ids": {},
        "emails": {},
        "usernames": {},
        "role_aliases": {},
        "agent_user_ids": {},
    })
    port: int = 8765
    dry_run: bool = True

    def secret_presence(self, env: dict[str, str] | None = None) -> dict[str, bool]:
        source = os.environ if env is None else env
        return {
            "clickup_team_id": bool(source.get(self.clickup_team_id_env)),
            "clickup_token": bool(source.get(self.clickup_token_env)),
            "webhook_secret": bool(source.get(self.webhook_secret_env)),
        }

    def sanitized(self, env: dict[str, str] | None = None) -> dict[str, Any]:
        data = asdict(self)
        data["secrets"] = self.secret_presence(env)
        return data


@dataclass
class RouterConfig:
    enabled: bool = True
    workspaces: dict[str, WorkspaceConfig] = field(default_factory=dict)

    def get_workspace(self, name: str) -> WorkspaceConfig:
        try:
            return self.workspaces[name]
        except KeyError as exc:
            raise KeyError(f"workspace not configured: {name}") from exc


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def _map_from_env(prefix: str, env: dict[str, str]) -> dict[str, str]:
    # Supports PREFIX=key:profile,key2:profile2 and PREFIX_key=profile.
    out: dict[str, str] = {}
    raw = env.get(prefix, "")
    for item in _split_csv(raw):
        if ":" in item:
            k, v = item.split(":", 1)
            out[k.strip()] = v.strip()
    stem = prefix + "_"
    for key, value in env.items():
        if key.startswith(stem) and value:
            out[key[len(stem):].lower()] = value.strip()
    return out


def _json_map_from_env(name: str, env: dict[str, str]) -> dict[str, str]:
    raw = env.get(name, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key).strip(): str(value).strip() for key, value in data.items() if str(key).strip() and str(value).strip()}


def _env_first(env: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = env.get(name)
        if value is not None and value != "":
            return value
    return default


def load_env_file(path: str | Path | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path:
        return result
    p = Path(path).expanduser()
    if not p.exists():
        return result
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def apply_env_file_to_environ(path: str | Path | None, override: bool = False) -> dict[str, str]:
    """Load KEY=VALUE pairs into os.environ for service runtime.

    The config layer keeps secret values out of serializable dataclasses, but the
    live webhook service still needs tokens/secrets in its process environment.
    This helper makes --env-file behave like systemd EnvironmentFile without
    exposing values through dashboard/config objects.
    """
    loaded = load_env_file(path)
    for key, value in loaded.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def load_workspace_from_env(name: str = "chessmentor", env_file: str | Path | None = None, environ: dict[str, str] | None = None) -> WorkspaceConfig:
    env = dict(os.environ if environ is None else environ)
    env.update(load_env_file(env_file))
    slug = env.get("COMPANY_SLUG") or name
    company = env.get("COMPANY_NAME") or ("ChessMentor" if name == "chessmentor" else name)
    list_ids = _split_csv(env.get("CLICKUP_LIST_IDS") or env.get("CLICKUP_LIST_ID") or "")
    role_aliases = {
        "founder": env.get("FOUNDER_PROFILE", "founder"),
        "ceo": env.get("CEO_PROFILE", f"ceo-{slug}"),
        "product-lead": env.get("PRODUCT_LEAD_PROFILE", f"product-lead-{slug}"),
        "founding-engineer": env.get("FOUNDING_ENGINEER_PROFILE", f"founding-engineer-{slug}"),
        "growth-specialist": env.get("GROWTH_SPECIALIST_PROFILE", f"growth-specialist-{slug}"),
    }
    cfg = WorkspaceConfig(
        name=name,
        enabled=env_bool("CLICKUP_ROUTER_ENABLED", True, env),
        company_name=company,
        company_slug=slug,
        webhook_path=_env_first(env, "WEBHOOK_PATH", "ROUTER_PATH", default=f"/clickup/{slug}"),
        list_ids=list_ids,
        kanban_board=env.get("KANBAN_BOARD", slug),
        assignment_source=_env_first(env, "ASSIGNMENT_SOURCE", "CLICKUP_ASSIGNMENT_SOURCE", default="assignee"),
        update_status_on_trigger=env_bool("UPDATE_STATUS_ON_TRIGGER", True, env),
        status_on_trigger=_env_first(env, "STATUS_ON_TRIGGER", "CLICKUP_STATUS_ON_TRIGGER", default="in progress"),
        complete_status=_env_first(env, "COMPLETE_STATUS", "CLICKUP_COMPLETE_STATUS", default="complete"),
        route_all_task_updates=env_bool("ROUTE_ALL_TASK_UPDATES", False, env),
        port=int(_env_first(env, "CLICKUP_ROUTER_PORT", "ROUTER_PORT", "PORT", default="8765")),
        dry_run=env_bool("DRY_RUN", True, env),
    )
    cfg.comment_routing = CommentRoutingConfig(
        mention_routing=env_bool("MENTION_ROUTING", env_bool("COMMENT_MENTION_ROUTING", True, env), env),
        watcher_routing=env_bool("WATCHER_ROUTING", env_bool("COMMENT_WATCHER_ROUTING", True, env), env),
        watcher_only_assignees=env_bool("WATCHER_ONLY_ASSIGNEES", env_bool("COMMENT_WATCHER_ONLY_ASSIGNEES", True, env), env),
        update_status_on_comment_trigger=env_bool("UPDATE_STATUS_ON_COMMENT_TRIGGER", env_bool("COMMENT_MENTION_UPDATE_STATUS", False, env), env),
    )
    clickup_user_map = _json_map_from_env("CLICKUP_ASSIGNEE_PROFILE_MAP_JSON", env)
    legacy_agent_user_map = dict(clickup_user_map)
    clickup_user_map.update(_map_from_env("CLICKUP_USER_PROFILE_MAP", env))
    agent_user_map = legacy_agent_user_map
    agent_user_map.update(_map_from_env("CLICKUP_AGENT_USER_IDS", env))
    cfg.profile_map = {
        "clickup_user_ids": clickup_user_map,
        "emails": _map_from_env("CLICKUP_EMAIL_PROFILE_MAP", env),
        "usernames": _map_from_env("CLICKUP_USERNAME_PROFILE_MAP", env),
        "role_aliases": role_aliases,
        "agent_user_ids": agent_user_map,
    }
    # Current production known founder mapping may be supplied by env; keep no secret here.
    if env.get("FOUNDER_CLICKUP_USER_ID"):
        cfg.profile_map["clickup_user_ids"][env["FOUNDER_CLICKUP_USER_ID"]] = role_aliases["founder"]
    return cfg


def load_config(config_path: str | Path | None = None, env_file: str | Path | None = None, workspace: str = "chessmentor") -> RouterConfig:
    if config_path:
        data = json.loads(Path(config_path).expanduser().read_text())
        workspaces: dict[str, WorkspaceConfig] = {}
        for name, item in data.get("workspaces", {}).items():
            cr = item.pop("comment_routing", {})
            ws = WorkspaceConfig(name=name, **item)
            ws.comment_routing = CommentRoutingConfig(**cr)
            workspaces[name] = ws
        return RouterConfig(enabled=bool(data.get("enabled", True)), workspaces=workspaces)
    ws = load_workspace_from_env(workspace, env_file=env_file)
    return RouterConfig(enabled=True, workspaces={workspace: ws})

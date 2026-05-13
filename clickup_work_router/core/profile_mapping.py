"""Resolve external users/aliases into Hermes profiles."""
from __future__ import annotations

from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.models import ExternalUser


def normalize_key(value: str) -> str:
    return str(value or "").strip().lower()


class ProfileMapper:
    def __init__(self, config: WorkspaceConfig) -> None:
        self.config = config

    def profile_for_user(self, user: ExternalUser | None) -> str:
        if not user:
            return ""
        maps = self.config.profile_map
        if user.id and user.id in maps.get("clickup_user_ids", {}):
            return maps["clickup_user_ids"][user.id]
        email = normalize_key(user.email)
        if email and email in maps.get("emails", {}):
            return maps["emails"][email]
        username = normalize_key(user.username or user.display_name)
        if username and username in maps.get("usernames", {}):
            return maps["usernames"][username]
        return ""

    def profile_for_alias(self, alias: str) -> str:
        alias = normalize_key(alias).lstrip("@")
        aliases = self.config.profile_map.get("role_aliases", {})
        return aliases.get(alias, "") or aliases.get(alias.replace("_", "-"), "")

    def agent_profile_for_user(self, user: ExternalUser | None) -> str:
        if not user:
            return ""
        maps = self.config.profile_map
        ids = maps.get("agent_user_ids", {})
        if user.id and user.id in ids:
            return ids[user.id]
        return ""

    def is_agent_user(self, user: ExternalUser | None) -> bool:
        return bool(self.agent_profile_for_user(user))

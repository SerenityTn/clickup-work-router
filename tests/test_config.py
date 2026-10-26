from clickup_work_router.config import (
    LEGACY_DEFAULT_WORKSPACE,
    apply_env_file_to_environ,
    load_workspace_from_env,
)


def test_load_example_env_compat():
    env = {
        "CLICKUP_LIST_IDS": "l1,l2",
        "KANBAN_BOARD": "example",
        "CLICKUP_USER_PROFILE_MAP": "user-1:founder,u2:product-lead-example",
        "CLICKUP_ROUTER_PORT": "9999",
        "DRY_RUN": "true",
    }
    cfg = load_workspace_from_env("example", environ=env)
    assert cfg.company_slug == "example"
    assert cfg.list_ids == ["l1", "l2"]
    assert cfg.kanban_board == "example"
    assert cfg.profile_map["clickup_user_ids"]["user-1"] == "founder"
    assert cfg.port == 9999
    assert cfg.dry_run is True
    assert cfg.secret_presence(env) == {"clickup_team_id": False, "clickup_token": False, "webhook_secret": False}


def test_loads_legacy_example_env_names():
    env = {
        "COMPANY_SLUG": "example",
        "CLICKUP_LIST_ID": "list-1",
        "CLICKUP_ASSIGNEE_PROFILE_MAP_JSON": '{"user-2":"ceo-example","user-3":"product-lead-example"}',
        "COMMENT_MENTION_ROUTING": "true",
        "COMMENT_WATCHER_ROUTING": "false",
        "COMMENT_WATCHER_ONLY_ASSIGNEES": "false",
        "COMMENT_MENTION_UPDATE_STATUS": "true",
        "CLICKUP_ASSIGNMENT_SOURCE": "custom_field",
        "CLICKUP_STATUS_ON_TRIGGER": "doing",
        "CLICKUP_COMPLETE_STATUS": "shipped",
        "ROUTER_PATH": "/legacy/example",
        "ROUTER_PORT": "8787",
        "DRY_RUN": "false",
    }

    cfg = load_workspace_from_env("example", environ=env)

    assert cfg.profile_map["clickup_user_ids"] == {
        "user-2": "ceo-example",
        "user-3": "product-lead-example",
    }
    assert cfg.profile_map["agent_user_ids"] == {
        "user-2": "ceo-example",
        "user-3": "product-lead-example",
    }
    assert cfg.comment_routing.mention_routing is True
    assert cfg.comment_routing.watcher_routing is False
    assert cfg.comment_routing.watcher_only_assignees is False
    assert cfg.comment_routing.update_status_on_comment_trigger is True
    assert cfg.assignment_source == "custom_field"
    assert cfg.status_on_trigger == "doing"
    assert cfg.complete_status == "shipped"
    assert cfg.webhook_path == "/legacy/example"
    assert cfg.port == 8787
    assert cfg.dry_run is False


def test_environment_overrides_environment_file(tmp_path):
    env_file = tmp_path / "router.env"
    env_file.write_text("DRY_RUN=false\nCLICKUP_ROUTER_PORT=1111\n")

    cfg = load_workspace_from_env(
        "example",
        env_file=env_file,
        environ={"DRY_RUN": "true", "CLICKUP_ROUTER_PORT": "2222"},
    )

    assert cfg.dry_run is True
    assert cfg.port == 2222


def test_implicit_workspace_remains_backward_compatible():
    cfg = load_workspace_from_env(environ={"DRY_RUN": "true"})

    assert cfg.name == LEGACY_DEFAULT_WORKSPACE
    assert cfg.company_name == LEGACY_DEFAULT_WORKSPACE


def test_apply_env_file_to_environ_loads_runtime_secrets(tmp_path, monkeypatch):
    env_file = tmp_path / "router.env"
    env_file.write_text("CLICKUP_API_TOKEN=token-value\nCLICKUP_WEBHOOK_SECRET=secret-value\n")
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pre-test-token")
    monkeypatch.setenv("CLICKUP_WEBHOOK_SECRET", "pre-test-secret")

    loaded = apply_env_file_to_environ(env_file, override=True)

    assert loaded["CLICKUP_API_TOKEN"] == "token-value"
    assert loaded["CLICKUP_WEBHOOK_SECRET"] == "secret-value"
    assert load_workspace_from_env("example").secret_presence() == {
        "clickup_team_id": False,
        "clickup_token": True,
        "webhook_secret": True,
    }


def test_proactive_layer_settings_load_from_env():
    from clickup_work_router.config import load_workspace_from_env

    env = {
        "ROUTE_ASSIGNEE_CHANGES": "false",
        "CLICKUP_WRITEBACK": "false",
        "REVIEW_STATUS": "review",
        "RECONCILE_INTERVAL_SECONDS": "60",
        "BLOCKED_RENOTIFY_HOURS": "6",
        "SWEEP_LOOKBACK_HOURS": "2",
        "CLICKUP_WEBHOOK_ENDPOINT": "https://router.example/clickup/example?token=synthetic",
        "CLICKUP_ROUTER_STATE_DB": "/var/lib/router/state.sqlite3",
        "DIRECT_REPLY_SKILL": "",
    }
    ws = load_workspace_from_env("example", environ=env)

    assert ws.route_assignee_changes is False
    assert ws.writeback_enabled is False
    assert ws.review_status == "review"
    assert ws.reconcile_interval_seconds == 60
    assert ws.blocked_renotify_hours == 6.0
    assert ws.sweep_lookback_hours == 2.0
    assert ws.state_db == "/var/lib/router/state.sqlite3"
    assert ws.direct_reply_skill == ""
    assert "synthetic" not in ws.sanitized(env)["webhook_endpoint"]


def test_proactive_layer_defaults_are_on_but_dry_run_stays_default():
    from clickup_work_router.config import load_workspace_from_env

    ws = load_workspace_from_env("example", environ={})

    assert ws.dry_run is True
    assert ws.writeback_enabled and ws.route_assignee_changes and ws.sweep_lists
    assert ws.reconcile_interval_seconds == 300
    assert ws.direct_reply_skill == "clickup-ai-team-workflow"

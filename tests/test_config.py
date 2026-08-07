from clickup_work_router.config import apply_env_file_to_environ, load_workspace_from_env


def test_load_chessmentor_env_compat():
    env = {
        "CLICKUP_LIST_IDS": "l1,l2",
        "KANBAN_BOARD": "chessmentor",
        "CLICKUP_USER_PROFILE_MAP": "188473659:founder,u2:product-lead-chessmentor",
        "CLICKUP_ROUTER_PORT": "9999",
        "DRY_RUN": "true",
    }
    cfg = load_workspace_from_env("chessmentor", environ=env)
    assert cfg.company_slug == "chessmentor"
    assert cfg.list_ids == ["l1", "l2"]
    assert cfg.kanban_board == "chessmentor"
    assert cfg.profile_map["clickup_user_ids"]["188473659"] == "founder"
    assert cfg.port == 9999
    assert cfg.dry_run is True
    assert cfg.secret_presence(env) == {"clickup_team_id": False, "clickup_token": False, "webhook_secret": False}


def test_loads_legacy_chessmentor_env_names():
    env = {
        "COMPANY_SLUG": "chessmentor",
        "CLICKUP_LIST_ID": "901217753167",
        "CLICKUP_ASSIGNEE_PROFILE_MAP_JSON": '{"296612861":"ceo-chessmentor","99775275":"product-lead-chessmentor"}',
        "COMMENT_MENTION_ROUTING": "true",
        "COMMENT_WATCHER_ROUTING": "false",
        "COMMENT_WATCHER_ONLY_ASSIGNEES": "false",
        "COMMENT_MENTION_UPDATE_STATUS": "true",
        "CLICKUP_ASSIGNMENT_SOURCE": "custom_field",
        "CLICKUP_STATUS_ON_TRIGGER": "doing",
        "CLICKUP_COMPLETE_STATUS": "shipped",
        "ROUTER_PATH": "/legacy/chessmentor",
        "ROUTER_PORT": "8787",
        "DRY_RUN": "false",
    }

    cfg = load_workspace_from_env("chessmentor", environ=env)

    assert cfg.profile_map["clickup_user_ids"] == {
        "296612861": "ceo-chessmentor",
        "99775275": "product-lead-chessmentor",
    }
    assert cfg.profile_map["agent_user_ids"] == {
        "296612861": "ceo-chessmentor",
        "99775275": "product-lead-chessmentor",
    }
    assert cfg.comment_routing.mention_routing is True
    assert cfg.comment_routing.watcher_routing is False
    assert cfg.comment_routing.watcher_only_assignees is False
    assert cfg.comment_routing.update_status_on_comment_trigger is True
    assert cfg.assignment_source == "custom_field"
    assert cfg.status_on_trigger == "doing"
    assert cfg.complete_status == "shipped"
    assert cfg.webhook_path == "/legacy/chessmentor"
    assert cfg.port == 8787
    assert cfg.dry_run is False


def test_apply_env_file_to_environ_loads_runtime_secrets(tmp_path, monkeypatch):
    env_file = tmp_path / "router.env"
    env_file.write_text("CLICKUP_API_TOKEN=token-value\nCLICKUP_WEBHOOK_SECRET=secret-value\n")
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pre-test-token")
    monkeypatch.setenv("CLICKUP_WEBHOOK_SECRET", "pre-test-secret")

    loaded = apply_env_file_to_environ(env_file, override=True)

    assert loaded["CLICKUP_API_TOKEN"] == "token-value"
    assert loaded["CLICKUP_WEBHOOK_SECRET"] == "secret-value"
    assert load_workspace_from_env("chessmentor").secret_presence() == {
        "clickup_team_id": False,
        "clickup_token": True,
        "webhook_secret": True,
    }

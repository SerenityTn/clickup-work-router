# ClickUp Work Router Hermes Plugin

First working v1 skeleton for routing ClickUp webhook work items into Hermes Kanban.

## Scope

This plugin is complementary to the Hermes Kanban kernel. It normalizes ClickUp events, decides a target Hermes profile, and dispatches to native Kanban. It does not implement a durable execution queue, worker lifecycle, retry loop, dependency engine, or replacement Kanban.

## Current status

- stdlib Python package: `clickup_work_router`
- ClickUp webhook signature/payload adapter
- env-compatible ChessMentor config loader
- provider-neutral routing decisions
- stable dedupe keys
- Kanban CLI dispatch with `dry_run` default
- read-mostly dashboard manifest/API/static UI
- systemd user template for side-by-side testing

## Running tests

```bash
cd /home/sysadmin/workspace/hermes-plugins/clickup-work-router
python3 -m pytest -q
```

## Side-by-side service test

Do not stop or modify production routers during skeleton validation.

```bash
cd /home/sysadmin/workspace/hermes-plugins/clickup-work-router
DRY_RUN=true CLICKUP_ROUTER_PORT=8765 python3 -m clickup_work_router.service --workspace chessmentor --env-file ~/.hermes/clickup-webhook-router/chessmentor/.env
curl -s http://127.0.0.1:8765/health
```

The service rejects unsigned webhooks when `DRY_RUN=false`. In dry-run mode only, it may accept unsigned local test payloads so you can validate routing without exposing a production endpoint.

## Configuration

Secrets are never committed. Env names used by v1:

- `CLICKUP_TEAM_ID`
- `CLICKUP_API_TOKEN`
- `CLICKUP_WEBHOOK_SECRET`
- `CLICKUP_LIST_ID` or `CLICKUP_LIST_IDS`
- `KANBAN_BOARD`
- `CLICKUP_USER_PROFILE_MAP` as `clickup_user_id:profile,...`
- `CLICKUP_EMAIL_PROFILE_MAP` as `email:profile,...`
- `CLICKUP_USERNAME_PROFILE_MAP` as `username:profile,...`
- `CLICKUP_AGENT_USER_IDS` as `clickup_user_id:profile,...` for loop prevention
- `DRY_RUN` defaults to `true`

Dashboard/API output reports secret presence booleans only, not values.

## Gaps for v1.1+

- Mount dashboard API in the actual Hermes dashboard runtime.
- Add native Kanban upsert-by-external-ref if CLI support differs from assumed flags.
- Add optional ClickUp status/comment sync policy.
- Add replay endpoint with explicit confirmation.
- Run parity tests against sanitized ChessMentor production-like payloads before cutover.

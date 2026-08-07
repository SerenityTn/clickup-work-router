# ClickUp Work Router Hermes Plugin

First working v1 skeleton for routing ClickUp webhook work items into Hermes Kanban.

## Scope

This plugin is complementary to the Hermes Kanban kernel. It normalizes ClickUp events, decides a target Hermes profile, and dispatches to native Kanban. It does not implement a durable execution queue, worker lifecycle, retry loop, dependency engine, or replacement Kanban.

## Current status

- compatibility assessed against Hermes Agent v0.19.0 and the live Kanban CLI
- stdlib Python package: `clickup_work_router`
- ClickUp webhook signature/payload adapter
- env-compatible workspace config loader
- provider-neutral routing decisions
- stable dedupe keys
- Kanban CLI dispatch with `dry_run` default
- current web-dashboard SDK bundle and read-mostly backend API
- opt-in native Hermes Desktop page using `@hermes/plugin-sdk`
- systemd user template for side-by-side testing

See [`docs/hermes-compatibility.md`](docs/hermes-compatibility.md) for the verified API assessment, dry-run evidence, and remaining risks.

## Running tests

```bash
cd /home/sysadmin/.hermes/profiles/founder/workspace/clickup-work-router
python3 -m pytest -q
```

## Native installation and isolated preview

Use Hermes' supported installer for the Python/dashboard plugin. Keep it disabled until its backend and UI have been reviewed:

```bash
hermes plugins install SerenityTn/clickup-work-router --no-enable
```

The native Desktop page is a separate trusted-JavaScript surface. Installing it remains an explicit opt-in action:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_HOME/desktop-plugins/clickup-work-router"
cp desktop-plugin/plugin.js "$HERMES_HOME/desktop-plugins/clickup-work-router/plugin.js"
```

For development validation, run the fully disposable lifecycle instead of touching a real profile:

```bash
python3 -m clickup_work_router.isolated_preview --profile founder
```

The lifecycle creates an immutable temporary Git commit from the staged index—without copying mutable worktree files—and a random Hermes home, invokes `hermes plugins install file://… --no-enable`, verifies structured native discovery, installs the Desktop artifact only from that installed snapshot into the temporary home without starting a Desktop runtime, submits a synthetic webhook through the no-credential `DRY_RUN=true` preview, removes both surfaces, and destroys the complete temporary home. It does not edit or enable plugins in the active profile. Tests forbid the router's current `subprocess.run` and `subprocess.Popen` dispatch paths during preview rather than claiming to observe external Kanban state.

## Side-by-side service test

Do not stop or modify production routers during skeleton validation.

```bash
cd /home/sysadmin/.hermes/profiles/founder/workspace/clickup-work-router
DRY_RUN=true CLICKUP_ROUTER_PORT=8765 python3 -m clickup_work_router.service --workspace example --env-file ~/.hermes/clickup-work-router/example.env
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

- Add contributor guidance and CI across supported Python versions before a community preview.
- Add native Kanban upsert-by-external-ref if CLI support differs from assumed flags.
- Add optional ClickUp status/comment sync policy.
- Add replay endpoint with explicit confirmation.
- Run parity tests against sanitized ChessMentor production-like payloads before cutover.

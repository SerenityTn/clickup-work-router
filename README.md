# ClickUp Work Router Hermes Plugin

A credential-safe Hermes plugin that normalizes selected ClickUp webhook work, routes it to the appropriate Hermes profile, and dispatches it into native Hermes Kanban. Preview and contributor workflows default to dry-run operation.

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
- current web-dashboard SDK bundle with readable workspace/status cards and read-mostly backend API
- isolated web-dashboard and packaged Linux Desktop runtime mounting verified
- opt-in native Hermes Desktop page using `@hermes/plugin-sdk`
- systemd user template for side-by-side testing

See [`docs/hermes-compatibility.md`](docs/hermes-compatibility.md) for the verified API assessment, dry-run evidence, and remaining risks.

## Running tests

```bash
python3 -m pytest -q
python3 -m compileall -q clickup_work_router dashboard __init__.py
node --check dashboard/dist/index.js
node --check desktop-plugin/plugin.js
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for isolated setup, credential-free preview commands, source-of-truth boundaries, and pull-request expectations. Start synthetic configuration from [`examples/workspace.env.example`](examples/workspace.env.example); keep all credential fields empty and `DRY_RUN=true`.

## Native installation and isolated preview

Use Hermes' supported installer for the Python/dashboard plugin. Installation is disabled by default:

```bash
hermes plugins install SerenityTn/clickup-work-router --no-enable
```

After reviewing the installed code and supplying your own dry-run configuration, enable the regular plugin without granting built-in tool replacement:

```bash
hermes plugins enable clickup-work-router --no-allow-tool-override
```

Plugin activation is a code-execution decision and may require restarting the relevant Hermes process. Do not enable it with production ClickUp credentials until signed-webhook parity has been validated for your payloads.

The native Desktop page is a separate trusted-JavaScript surface. Installing it remains an explicit opt-in action:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_HOME/desktop-plugins/clickup-work-router"
cp desktop-plugin/plugin.js "$HERMES_HOME/desktop-plugins/clickup-work-router/plugin.js"
```

For development validation, run the fully disposable lifecycle instead of touching a real profile:

```bash
python3 -m clickup_work_router.isolated_preview --profile example
```

The lifecycle creates an immutable temporary Git commit from the staged index—without copying mutable worktree files—and a random Hermes home, invokes `hermes plugins install file://… --no-enable`, verifies structured native discovery, installs the Desktop artifact only from that installed snapshot into the temporary home without starting a Desktop runtime, submits a synthetic webhook through the no-credential `DRY_RUN=true` preview, removes both surfaces, and destroys the complete temporary home. It does not edit or enable plugins in the active profile. Tests forbid the router's current `subprocess.run` and `subprocess.Popen` dispatch paths during preview rather than claiming to observe external Kanban state.

## Side-by-side service test

Do not stop or modify production routers during skeleton validation.

```bash
git clone https://github.com/SerenityTn/clickup-work-router.git
cd clickup-work-router
set -e
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
DRY_RUN=true CLICKUP_ROUTER_PORT="$PORT" python3 -m clickup_work_router.service --workspace example --env-file examples/workspace.env.example &
ROUTER_PID=$!
trap 'kill "$ROUTER_PID" 2>/dev/null || true' EXIT
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 0 --max-time 10 "http://127.0.0.1:$PORT/health"
kill "$ROUTER_PID"
wait "$ROUTER_PID" 2>/dev/null || true
trap - EXIT
```

The service rejects unsigned webhooks when `DRY_RUN=false`. In dry-run mode only, it may accept unsigned local test payloads so you can validate routing without exposing a production endpoint.

For backward compatibility, callers that omit `--workspace` still receive the historical implicit workspace. New installations should always pass an explicit neutral workspace name, as above. Changing the implicit default is reserved for a separately documented versioned migration.

## Configuration

Secrets are never committed. Env names used by v1:

- `CLICKUP_TEAM_ID`
- `CLICKUP_API_TOKEN`
- `CLICKUP_WEBHOOK_SECRET`
- `ROUTER_SHARED_TOKEN` for optional router request authentication
- `CLICKUP_ROUTER_PORT` for the service port; loopback binding is controlled by the `--host` CLI argument and defaults to `127.0.0.1`
- `CLICKUP_LIST_ID` or `CLICKUP_LIST_IDS`
- `KANBAN_BOARD`
- `CLICKUP_USER_PROFILE_MAP` as `clickup_user_id:profile,...`
- `CLICKUP_EMAIL_PROFILE_MAP` as `email:profile,...`
- `CLICKUP_USERNAME_PROFILE_MAP` as `username:profile,...`
- `CLICKUP_AGENT_USER_IDS` as `clickup_user_id:profile,...` for loop prevention
- `DRY_RUN` defaults to `true`

Dashboard/API output reports secret presence booleans only, not values.

## Uninstall and cleanup

Remove the regular plugin through Hermes' native lifecycle:

```bash
hermes plugins remove clickup-work-router
```

If you explicitly copied the separate Desktop artifact, remove only that plugin's file and then its now-empty directory:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
rm "$HERMES_HOME/desktop-plugins/clickup-work-router/plugin.js"
rmdir "$HERMES_HOME/desktop-plugins/clickup-work-router"
```

See [`SECURITY.md`](SECURITY.md) for trust boundaries and private vulnerability reporting.

## Gaps for v1.1+

- Add native Kanban upsert-by-external-ref if CLI support differs from assumed flags.
- Add optional ClickUp status/comment sync policy.
- Add replay endpoint with explicit confirmation.
- Run parity tests against sanitized production-like ClickUp payloads before cutover.

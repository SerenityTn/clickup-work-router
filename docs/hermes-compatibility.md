# Hermes compatibility assessment

Assessment target: Hermes Agent v0.19.0 (2026.7.20), upstream `e444d165` with local build `c9de69c6`.

## Result

The routing core is compatible with the current native Kanban CLI. The legacy dashboard bundle was not compatible with the current dashboard plugin SDK and has been migrated. A native desktop-plugin entrypoint is now included separately because the dashboard and desktop plugin systems do not share UI code.

## Source-of-truth boundaries

- ClickUp remains authoritative for customer-delivery work, customer-facing status, and human discussion.
- Hermes Kanban receives selected, normalized execution tasks and internal execution context.
- Obsidian remains the durable synthesis and decision layer; this plugin does not mirror it.

## Kanban CLI compatibility

Verified against live CLI help and JSON output:

- `hermes kanban --board <slug> create <title>` accepts `--body`, `--assignee`, `--idempotency-key`, and `--created-by`.
- `hermes kanban --board <slug> list --assignee <profile> --json` returns a JSON array.
- `hermes kanban --board <slug> comment <task-id> <text> --author <name>` remains valid.
- The router defaults to `DRY_RUN=true`, so webhook smoke tests do not mutate a board.

Current limitation: Hermes Kanban has no dedicated external-reference field. The router therefore stores a stable `External-Ref:` marker in the body and uses the event dedupe key as the Kanban idempotency key.

## Plugin API compatibility

### Web dashboard

The current dashboard contract requires:

- `dashboard/manifest.json` under a regular Hermes plugin directory;
- a bundle that uses `window.__HERMES_PLUGIN_SDK__`;
- registration through `window.__HERMES_PLUGINS__.register(...)`;
- backend requests through `SDK.fetchJSON(...)` so dashboard authentication is included.

The prior bundle mounted directly into `document.body` and used bare `fetch`, so it would not register as a current dashboard page. The migrated bundle follows the current contract. `plugin_api.py` exports a FastAPI `router`, and the manifest's API path passes Hermes' safe relative-path validation.

### Native desktop

The desktop app uses an unrelated disk-plugin SDK. `desktop-plugin/plugin.js` is a plain, uncompiled ESM module using only `@hermes/plugin-sdk` and `react/jsx-runtime`. It contributes a page and sidebar item and reaches the shared backend with `ctx.rest('/status')`.

The desktop renderer plugin is opt-in (`defaultEnabled: false`). The Python backend remains separately gated by Hermes' `plugins.enabled` configuration and mounts only at gateway startup.

## Verified dry-run checkpoint

A local service smoke test used:

- board: `default`;
- synthetic ClickUp task/list/user identifiers;
- mapped target profile: `founder`;
- `DRY_RUN=true`;
- unsigned loopback-only HTTP.

Observed results:

- `GET /health` returned HTTP 200 with `dry_run: true`;
- `POST /clickup/compatibility-smoke` returned HTTP 200;
- routing returned `status: dispatch`, `profile: founder`, and `dry_run: true`;
- the test server was terminated after verification;
- no Hermes Kanban task was created.

## Isolated preview checkpoint

A disposable native-installer lifecycle now provides the bounded integration layer:

- an immutable temporary commit is built directly from the staged Git index and fetched into a disposable bare repository; mutable worktree files are never copied;
- Hermes' supported `plugins install file://… --no-enable` command installs the regular Python/dashboard plugin into a random temporary Hermes home;
- staged Git-tree symlinks are rejected and subprocesses receive a strict temporary-home environment rather than ambient Git controls or credentials;
- structured Hermes JSON discovery proves the regular plugin is installed but absent from the enabled set;
- the native Desktop ESM is byte-verified only inside the disposable home's separate `desktop-plugins` directory; no Desktop runtime is started, so runtime loading is not claimed;
- the installed regular-plugin module origin is asserted before the preview runs;
- the preview uses an ephemeral loopback HTTP server, a synthetic ClickUp adapter, an empty runtime credential environment, and forced `DRY_RUN=true`; tests forbid the current `subprocess.run` and `subprocess.Popen` dispatch paths rather than claiming external Kanban observation;
- Hermes' native remove command removes the regular plugin, the temporary Desktop artifact is removed, and the entire temporary home is destroyed;
- no bespoke persistent-profile installer or recursive ownership-marker deletion logic is shipped.

## Remaining compatibility risks

1. Native desktop and web-dashboard rendering still need evidence from actual Hermes UI sessions; repository checks verify syntax, discovery, API safety, and contract shape but not visual mounting.
2. Enabling the Python backend changes live Hermes plugin configuration and restarting the gateway affects the current runtime, so that checkpoint requires explicit approval.
3. ClickUp API hydration and signed-webhook parity still need sanitized production-like payloads before any non-dry-run cutover.

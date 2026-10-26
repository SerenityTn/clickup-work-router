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
- `create ... --json` prints the created task as JSON. The router records its `id` to link the ClickUp task to the Kanban task. A repeated `--idempotency-key` returns the existing task's id.
- `hermes kanban --board <slug> show <task-id> --json` returns `task`, `latest_summary`, `comments`, and `events`. The reconciler reads `task.status`, `latest_summary` or `task.result`, and the latest `blocked` event time.
- Task statuses are `triage`, `todo`, `ready`, `running`, `blocked`, `review`, `scheduled`, `done`, and `archived`. The router treats all but `done` and `archived` as active.
- JSON output can echo task bodies that contain words such as `error:`. The router trusts valid JSON output and applies its error-text heuristic only to non-JSON output.
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
- mapped target profile: a synthetic test profile;
- `DRY_RUN=true`;
- unsigned loopback-only HTTP.

Observed results:

- `GET /health` returned HTTP 200 with `dry_run: true`;
- `POST /clickup/compatibility-smoke` returned HTTP 200;
- routing returned `status: dispatch`, the configured synthetic profile, and `dry_run: true`;
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

## Isolated web-dashboard runtime evidence

On 2026-08-07 the regular plugin was installed and enabled only inside a disposable Hermes home, then mounted in a real Hermes Agent v0.19.0 dashboard process on loopback:

- the dashboard sidebar discovered `CLICKUP ROUTER`;
- `/clickup-router` mounted the plugin page with `ClickUp Work Router`, a healthy service badge, the redacted workspace payload, and the recent-events payload;
- the authenticated dashboard SDK fetched the gated workspace API successfully;
- browser console and JavaScript error buffers were empty;
- the page renders concise workspace, routing-policy, and credential-presence cards plus an explicit empty recent-events state; no raw payload blocks remain;
- the dashboard process was killed, its disposable Hermes home was removed, and the loopback port was verified closed.

No active-profile plugin was enabled, no active gateway was restarted, and no production ClickUp credentials were used.

## Isolated native Desktop runtime evidence

On 2026-08-07 the packaged Linux Hermes Desktop app was built from the assessed Hermes Agent v0.19.0 source tree (whose Desktop package metadata currently reports `hermes@0.17.0`) and launched under Xvfb with an empty allowlisted environment, a disposable `HOME`, `HERMES_HOME`, Electron user-data directory, and loopback-only Chrome DevTools endpoint:

- the regular backend plugin was installed and enabled only in the disposable profile;
- the Desktop artifact was copied from that installed plugin and explicitly enabled only in isolated Electron local storage;
- the Desktop sidebar discovered `ClickUp Router` and navigation mounted `#/clickup-work-router`;
- the page rendered `ClickUp Work Router`, a healthy service badge, redacted workspace data, and an empty recent-events payload through `ctx.rest('/status')`;
- no renderer exceptions, log errors, alert nodes, or plugin-load failures were observed;
- the Electron/Xvfb process, disposable profile, user data, and debugging listener were removed and verified absent afterward.

The only process diagnostics were expected headless Linux D-Bus warnings and a forced-shutdown diagnostic after evidence collection; they did not affect renderer mounting or plugin API behavior.

## Human-layer end-to-end checkpoint

A local end-to-end run used a disposable `HERMES_HOME`, a real Hermes Agent v0.19.0 Kanban board, a synthetic in-process ClickUp client, and a temporary SQLite state file:

- two synthetic `taskCreated` events created two real Kanban tasks, and the router linked each by its returned id;
- a later `taskAssigneeUpdated` event for an already-linked task returned `already_active` and created nothing;
- after the Kanban CLI completed one task and blocked the other, one reconcile pass posted a completion comment and complete-status move, and a blocked comment;
- after the synthetic ClickUp task was closed, the next pass appended a stop note to the still-active Kanban task;
- a task title containing `error:` did not produce a false dispatch failure;
- the temporary home and state file were removed afterward.

No real ClickUp workspace was contacted. The ClickUp REST calls for write-back, list sweeps, and webhook health are therefore covered by unit tests against recorded request shapes, not by live API evidence.

## Remaining compatibility risks

1. ClickUp API hydration, signed-webhook parity, `taskAssigneeUpdated` history items, write-back calls, list sweeps, and webhook health responses still need sanitized production-like payloads before any non-dry-run cutover.
2. Hosted CI passed on the reviewed PR head (`31226899863`) and again on merged `main` (`31227293980`) across Python 3.11–3.13, JavaScript contracts, and the Hermes Agent 0.19.0 native lifecycle; both final runs had zero check-run annotations.
3. Enabling either UI outside a disposable profile remains an explicit trust/configuration action and is not automated.

# Contributing to ClickUp Work Router

Thanks for helping make selected ClickUp work safely routable into Hermes Kanban.

## Source-of-truth boundaries

- **ClickUp** remains authoritative for customer-delivery work.
- **Hermes Kanban** receives only selected agent-execution work.
- **Obsidian or another durable knowledge system** may capture decisions and synthesis, but is not an execution queue.

Changes must preserve these boundaries. The router must not become a replacement ClickUp client, durable worker queue, or second Kanban implementation.

## Development setup

Requirements:

- Python 3.11–3.13
- Git
- Node.js for JavaScript syntax checks
- Hermes Agent 0.19.0 only when exercising the native isolated lifecycle

Create an isolated environment and install the test dependency:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install pytest==9.1.1
```

The routing core uses the Python standard library. FastAPI is supplied by Hermes when the dashboard backend is mounted.

## Safe validation paths

Run the credential-free routing preview:

```bash
python -m clickup_work_router.preview --profile example
```

It uses synthetic ClickUp data, loopback-only ephemeral HTTP, and enforced dry-run behavior. It must not invoke the current Kanban subprocess dispatch paths.

Run the core suite without installing Hermes:

```bash
python -m pytest -q --ignore=tests/test_isolated_preview.py
```

To exercise the natively installed artifact, install Hermes Agent 0.19.0 in the development environment and run:

```bash
git config user.name "ClickUp Router Test"
git config user.email "clickup-router-test@localhost"
python -m pytest -q tests/test_isolated_preview.py
```

The lifecycle uses Hermes' regular-plugin installer with `--no-enable`, an immutable commit built from the staged Git index, a disposable `HERMES_HOME`, synthetic data, and native cleanup. It never enables or edits the active Hermes profile.

Run every local gate before submitting a pull request:

```bash
python -m pytest -q
python -m compileall -q clickup_work_router dashboard __init__.py
node --check dashboard/dist/index.js
node --check desktop-plugin/plugin.js
git diff --check
```

## Configuration and fixtures

Start from [`examples/workspace.env.example`](examples/workspace.env.example). Keep `DRY_RUN=true` and credentials empty for development. Never commit:

- ClickUp tokens, team IDs, webhook secrets, or signed production payloads
- Router authentication secrets
- Customer names, task text, attachments, or identifiers
- Hermes credentials, provider keys, session data, or active-profile configuration

Tests and documentation must use neutral synthetic payloads. Production-like regression fixtures require prior sanitization and review.

## Plugin surfaces

The repository has two separate trust surfaces:

1. The **regular Hermes plugin** provides the Python backend and web-dashboard extension. Use Hermes' native installer and keep it disabled until reviewed.
2. The **native Desktop artifact** is trusted renderer JavaScript under `desktop-plugin/plugin.js`. It is opt-in and must never be copied or enabled automatically in a persistent profile.

Do not add shell-string interpolation to either surface. Subprocess commands must use argument arrays and an explicitly controlled environment.

## Pull-request expectations

A focused pull request should include:

- The user or contributor problem being solved
- Tests that fail before the behavioral change and pass afterward
- Exact commands and results used for verification
- Any changes to credentials, filesystem, subprocess, plugin-enablement, or source-of-truth boundaries
- Updated documentation when behavior or evidence claims change

Keep routing decisions provider-neutral. Avoid unrelated formatting, dependency, or architecture changes. Do not claim dashboard/Desktop mounting, Kanban non-mutation, or cross-platform behavior without direct evidence for that exact property.

## Security reports

Do not publish exploitable credential, path, signature, subprocess, or plugin-loading findings in a public issue. Use GitHub's private vulnerability-reporting channel when available; otherwise contact the repository owner privately before disclosure. See [`SECURITY.md`](SECURITY.md).

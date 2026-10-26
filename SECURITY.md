# Security Policy

## Supported code

Security fixes target the latest commit on the repository's default branch. Preview branches are development artifacts and may change before publication.

## Reporting a vulnerability

Please do not open a public issue for vulnerabilities involving:

- ClickUp or Hermes credentials
- Webhook signature verification or replay
- Router authentication
- Filesystem boundaries, symlinks, deletion, or temporary directories
- Git environment or configuration escape
- Subprocess execution or Kanban mutation
- Dashboard API authorization or redaction
- Native Desktop plugin loading

Use GitHub's private vulnerability-reporting channel when it is available. If it is unavailable, contact the repository owner privately through their GitHub profile before sharing exploit details.

Include the affected commit, platform, reproduction steps, observed impact, and the smallest safe proof of concept. Remove all real credentials and customer data.

## Security posture

- `DRY_RUN=true` is the development and preview default.
- Unsigned webhooks are accepted only for loopback dry-run validation.
- Secrets are read from runtime configuration and must never appear in repository files, UI payloads, logs, fixtures, or reports.
- Dashboard APIs expose redacted configuration and secret-presence booleans only. Router tokens in `CLICKUP_WEBHOOK_ENDPOINT` are redacted.
- Webhook registration writes a new signing secret only to an explicit owner-only file and never prints it.
- ClickUp write-back is disabled in dry-run mode, and every router comment carries a marker that the router itself ignores to prevent loops.
- The regular Hermes plugin is installed disabled unless a human explicitly enables it.
- Native Desktop JavaScript is a separate trusted surface and remains explicit opt-in.
- The isolated lifecycle uses a staged-index Git snapshot, strict temporary environment, native Hermes install/remove operations, and complete temporary-profile cleanup.

No security claim should be inferred beyond the exact tests and runtime evidence recorded in [`docs/hermes-compatibility.md`](docs/hermes-compatibility.md).

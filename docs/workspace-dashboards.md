# Workspace dashboards and SQL/MCP audit

The viewer has separate, cookie-authenticated overview pages:

- `/dashboard`: the signed-in principal's workspaces, layer/map counts, queued or
  running jobs, and SQL/MCP activity in the last 24 hours.
- `/workspaces/{workspace_id}`: that workspace's layers, maps, recent jobs, SQL/MCP
  audit, and data-change provenance. Owners and explicitly designated administrators
  can open it. Unknown and unauthorized workspace IDs both return 404.
- `/admin`: all workspaces, principal roles/status, and shared catalog counts.
- `/admin/audit`: paginated audit history across workspaces, including deleted ones.
- `/workspaces`: the existing activate/rename/delete manager, with overview/log links.

Root and successful login lead to `/dashboard`. The interface follows the existing
Swedish language. Audit filters select SQL/MCP and success/error/missing completion;
pages contain at most 50 events. Detail lists cap layers at 200, maps at 100, and jobs
and provenance at 25, with those limits stated on the page. Counts in the overview
are exact, independent of these preview limits. Times are displayed in UTC.
Dashboard responses use private/no-store caching and the viewer's strict CSP.

## Upgrade and administrator access

Fresh installations and upgrades run migration 006 through the shared versioned
sequence. Run `/opt/geodata-db/migrate.sh` as the database superuser before starting
updated MCP/viewer images; see [deployment instructions](../deploy/README.md#database-upgrades).
The runner owns transaction boundaries and records applied migrations. The migration
preserves existing administrators and history; it does not invent old MCP events.

Every existing and new principal defaults to `is_admin = false`. There is no
first-key privilege, special admin bearer, browser-supplied role, or public grant
endpoint. An operator explicitly grants/revokes the role in the database:

```sql
SELECT id, name, disabled, is_admin FROM app.api_keys ORDER BY created_at;
UPDATE app.api_keys SET is_admin = true WHERE id = '<chosen principal UUID>';
-- To revoke:
UPDATE app.api_keys SET is_admin = false WHERE id = '<chosen principal UUID>';
```

The viewer rechecks both enabled state and admin role on every request. This grants
read access to the overview/audit pages; existing workspace mutation permissions
are unchanged. Login still uses the existing API-key manager flow. An OAuth MCP
identity appears in the administrator's inventory; this change does not introduce
a separate browser OAuth login.

## Audit semantics and privacy

Each of the eight MCP tools is decorated at its execution boundary. A committed
`app.mcp_calls` start record precedes tool dispatch; a separate
`app.mcp_call_results` record captures success/error and elapsed time. Both are
append-only for application and agent roles. No update or deletion grant is needed.
A UUID correlates the two records without a foreign-key row lock (which would
require UPDATE on the start table).

Records contain principal ID, workspace ID/name, tool/operation, argument **names**,
explicit SQL when supplied, duration, outcome, and available query/job/row-count
identifiers. Every invoked tool response adds `audit_call_id`; query responses
keep their existing `query_id`. SQL query records gain `api_key_id` and
`mcp_call_id`. Existing SQL history is preserved and made append-only.

Workspace new/use/rename/delete completions are attributed to the affected owned
workspace, including successful deletion. Start records retain their original
context; completions supply the final workspace for newly created workspaces.
History has no foreign keys to workspaces or principals, so their removal does
not erase it. Deleted-workspace history remains visible in the admin audit page.

If the start record cannot be saved, the tool is not dispatched. If completion
logging fails after execution, the response preserves the tool result, adds
`audit_warning`, and leaves the start visible as "Utan slutpost". This also makes
crashed/interrupted calls visible. Do not treat a missing completion as proof that
a write did not happen or retry a mutation blindly.

The audit boundary covers invoked tools and their operation/SQL validation
failures. Authentication failures, malformed JSON-RPC, unknown tools, and arguments
rejected by the MCP SDK **before invocation** remain transport errors, not attributed
workspace events. Background job execution remains in jobs/provenance and pgAudit;
the MCP record captures submission or polling rather than the whole job lifetime.
An operation's returned error is recorded as a generic failure marker in the MCP
ledger. SQL errors remain available in query history; job details remain in jobs.

Raw headers, bearer tokens, row payloads, tool result datasets, and source URLs are
not copied into the MCP ledger. SQL text itself is deliberately retained, including
literals and rejected SQL; do not put secrets in SQL. PostgreSQL pgAudit and the
existing provenance ledger remain in place for database-level operations.

The **new** MCP ledger is not SELECT-granted to shared agent roles. Dashboard reads
enforce ownership/admin permission. The repository's existing SQL security boundary
is unchanged: shared `agent_ro` can read other workspaces' data and the historical
`query_log`/provenance tables. These dashboards do not turn that shared SQL role into
tenant isolation. Database owners/superusers can still alter privileges; append-only
application permissions are not tamper-proof storage against operators.

No automatic retention/purge policy is introduced. Operators should set retention,
backup, and storage-monitoring policies appropriate for their deployment.

## Isolated verification

Use a dedicated worktree and `.venv`; never point tests at a deployment database.
Create a fresh PostgreSQL container from `db/Dockerfile`, using a dedicated
container/volume and unused loopback port. The image includes bootstrap scripts
and migrations.

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r tests/requirements.txt
# Set DATABASE_URL_APP, DATABASE_URL_RO, DATABASE_URL_WS to the isolated database.
export GEODATA_TEST_DATABASE_URL="$DATABASE_URL_APP"
export VIEWER_SECRET=isolated-test-cookie-secret
.venv/bin/pytest -q tests/test_workspace_dashboards.py
```

The suite refuses to run without the explicit matching test DSN. It creates
synthetic principals and workspaces and preserves their audit history, so discard
the dedicated database after testing. It tests all tool registrations, successful
and rejected SQL, layer SQL, workspace deletion/switch attribution, concurrent
identities, principal isolation, admin revocation, HTML escaping, pagination,
append-only permissions, and audit-storage failure behavior.

For upgrade verification, run `scripts/migration_test.py` against a built database
image. It checks fresh and legacy schemas, repeated runs and data preservation. Browser verification uses a separately started viewer and MCP service on
unused loopback ports; no worker, external data sources, or production credentials
are needed for dashboard tests.

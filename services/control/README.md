# Service administration

Administrators use `/admin/services` to inspect configured containers and host services.
The page shows runtime state, available CPU/memory data, health checks, uptime and the
latest 30 maintenance actions. SAM3's API health and lazy model-load state are separate.

Only explicitly configured targets and actions are available. The viewer has no Docker
socket, shell or systemd access. It connects over a private Unix socket to a host-local
controller, using a separate token. Users cannot supply a command, container ID, unit
name or probe URL. Start/restart actions require a current admin role, valid CSRF token
and an explicit confirmation. Duplicate request IDs do not execute twice.

The controller writes each accepted request before execution and records its result in
SQLite. A timeout or controller interruption is reported as unknown and is never
automatically retried. Successful execution means the service manager accepted the
operation; use the health fields to check subsequent recovery. Starting a service does
not load SAM3 model weights or run inference.

## Host setup

Run the controller as the deployment operator who already owns the Docker project and
the SAM3 user unit. It supports Linux user systemd services; remote or system-wide units
require a separately reviewed integration.

1. Create a dedicated virtual environment:
   `uv venv services/control/.venv`, then
   `uv pip install --python services/control/.venv/bin/python -r services/control/requirements.txt`.
2. Create a private runtime directory (mode 0700) containing a random token (mode 0600).
   Keep configuration and audit data outside the directory mounted into the viewer.
3. Copy `config.example.json`, set the absolute database/token paths, project labels and
   desired user service unit. The config is operator-owned and not editable in the UI.
4. Run with `SERVICE_CONTROL_CONFIG=/absolute/config.json`:
   `services/control/.venv/bin/uvicorn app:create_app --factory --app-dir services/control --uds /private/runtime/control.sock`.
   Use one process. For persistent operation use a user systemd unit with
   `Restart=on-failure`, `UMask=0077` and `TimeoutStopSec=60`.
5. Mount only the runtime directory read-only into the viewer, for example at
   `/run/geodata-control`, and configure:
   `SERVICE_CONTROL_SOCKET=/run/geodata-control/control.sock` and
   `SERVICE_CONTROL_TOKEN_FILE=/run/geodata-control/token`.

The controller has no TCP listener. Do not publish it through Caddy. Keep audit.sqlite
on persistent storage and include it in operator backups. Neither credentials,
environment variables, raw process output nor container logs are returned by the API.
If the controller is unavailable, the page reports that fact rather than showing a
healthy status. Other dashboard and map routes remain independent of the controller.

The standard allowlist enables maintenance for MCP, worker, viewer and SAM3.
PostgreSQL, object storage and the proxy are monitored only. Eneo and other Docker
projects are outside the allowlist. The page refreshes on demand and snapshots are
cached for five seconds to bound resource sampling.

## Verification

`uv run --python .venv/bin/python pytest tests/test_service_control.py` uses fake
processes and HTTP plus a temporary audit database. The dashboard integration tests
in `tests/test_service_admin.py` require the explicitly isolated PostgreSQL environment
described in `docs/workspace-dashboards.md`. Never point them at deployment data.

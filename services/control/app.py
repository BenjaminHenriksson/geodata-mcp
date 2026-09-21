"""Host-local service control. Fixed targets only; no command or URL from clients."""
import hmac
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def command(args, timeout=8):
    # Never use a shell. stdout/stderr can contain credentials; never return them.
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout).stdout


class Controller:
    def __init__(self, config):
        self.targets = config["targets"]
        self.database = config["database"]
        self.lock = threading.Lock()
        self.snapshot_lock = threading.Lock()
        self.cached = None
        self.cached_at = 0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maintenance")
        for key, target in self.targets.items():
            if not SLUG.fullmatch(key) or target["kind"] not in ("docker", "systemd"):
                raise ValueError("invalid service target")
            if target["kind"] == "docker":
                if not all(SLUG.fullmatch(target[k]) for k in ("project", "service")):
                    raise ValueError("invalid compose target")
            elif not re.fullmatch(r"[a-zA-Z0-9_.@-]+\.service", target["unit"]):
                raise ValueError("invalid service unit")
            if set(target.get("actions", [])) - {"start", "restart"}:
                raise ValueError("unsupported maintenance action")
        with self.db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, service TEXT NOT NULL, action TEXT NOT NULL,
                    actor_id TEXT NOT NULL, actor TEXT NOT NULL, requested_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY REFERENCES actions(id),
                    status TEXT NOT NULL, completed_at TEXT NOT NULL);
            """)
            # An interrupted action might already have run. Record uncertainty; never retry.
            conn.execute("INSERT INTO results SELECT id, 'unknown', ? FROM actions "
                         "WHERE id NOT IN (SELECT id FROM results)", (now(),))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def docker_id(self, target):
        ids = command(["docker", "ps", "-aq", "--filter",
                       "label=com.docker.compose.project=" + target["project"], "--filter",
                       "label=com.docker.compose.service=" + target["service"], "--filter",
                       "label=com.docker.compose.oneoff=False"]).split()
        if len(ids) != 1:
            raise LookupError("service missing or ambiguous")
        return ids[0]

    def inspect(self, key, target):
        row = {"id": key, "name": target["name"], "kind": target["kind"],
               "actions": [] if target.get("disabled_reason") else target.get("actions", []),
               "disabled_reason": target.get("disabled_reason"), "state": "unknown", "health": "unknown"}
        if target["kind"] == "docker":
            ident = self.docker_id(target)
            data = json.loads(command(["docker", "inspect", ident]))[0]
            state = data["State"]
            row.update(state=state["Status"], health=state.get("Health", {}).get("Status", "none") if state.get("Running") else "none",
                       started_at=state.get("StartedAt"), restarts=data.get("RestartCount", 0),
                       image=data["Config"].get("Image"), container_id=ident[:12])
            if state.get("Running"):
                row["_container_id"] = ident
        else:
            fields = "ActiveState,SubState,ActiveEnterTimestampMonotonic,MemoryCurrent,CPUUsageNSec,NRestarts"
            raw = command(["systemctl", "--user", "show", target["unit"], "--property=" + fields])
            data = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            row.update(state=data.get("ActiveState", "unknown"), substate=data.get("SubState"),
                       unit=target["unit"],
                       restarts=int(data.get("NRestarts", "0")))
            started = data.get("ActiveEnterTimestampMonotonic", "0")
            if started.isdigit() and int(started) > 0:
                age = max(0, time.monotonic() - int(started) / 1e6)
                row["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
            if data.get("MemoryCurrent", "").isdigit():
                row["memory_bytes"] = int(data["MemoryCurrent"])
            if data.get("CPUUsageNSec", "").isdigit():
                row["cpu_seconds"] = int(data["CPUUsageNSec"]) / 1e9
        if target.get("health_url") and not target.get("disabled_reason"):
            start = time.monotonic()
            try:
                with (httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client,
                      client.stream("GET", target["health_url"]) as response):
                    response.raise_for_status()
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > 65536:
                            raise ValueError("health response too large")
                health = json.loads(body)
                row["api_health"] = "healthy" if health.get("ok") is True else "unhealthy"
                row["latency_ms"] = round((time.monotonic() - start) * 1000)
                if health.get("backend") in ("mlx", "transformers"):
                    row["backend"] = health["backend"]
                if isinstance(health.get("model_loaded"), bool):
                    row["model_loaded"] = health["model_loaded"]
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                row["api_health"] = "unreachable"
        return row

    def snapshot(self):
        # Bound expensive docker stats calls across simultaneous browser requests.
        with self.snapshot_lock:
            if self.cached is not None and time.monotonic() - self.cached_at < 5:
                return dict(self.cached, history=self.history())
            rows = []
            for key, target in self.targets.items():
                try:
                    rows.append(self.inspect(key, target))
                except (subprocess.SubprocessError, OSError, LookupError, ValueError, TypeError):
                    rows.append({"id": key, "name": target["name"], "kind": target["kind"],
                                 "state": "unknown", "health": "unknown",
                                 "actions": [] if target.get("disabled_reason") else target.get("actions", []),
                                 "disabled_reason": target.get("disabled_reason")})
            ids = [r.pop("_container_id", None) for r in rows]
            running = [i for i in ids if i]
            if running:
                try:
                    raw = command(["docker", "stats", "--no-stream", "--format", "{{json .}}", *running])
                    stats = {s["ID"]: s for s in map(json.loads, raw.splitlines())}
                    for row, ident in zip(rows, ids):
                        if ident and ident[:12] in stats:
                            s = stats[ident[:12]]
                            row.update(cpu=s["CPUPerc"], memory=s["MemUsage"], pids=s["PIDs"])
                except (subprocess.SubprocessError, OSError, ValueError, KeyError):
                    pass  # Status remains valid; unavailable resource metrics stay absent.
            self.cached = {"checked_at": now(), "services": rows}
            self.cached_at = time.monotonic()
            return dict(self.cached, history=self.history())

    def history(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT a.*, COALESCE(r.status,'queued') AS status, r.completed_at "
                "FROM actions a LEFT JOIN results r USING(id) ORDER BY a.rowid DESC LIMIT 30")]

    def submit(self, request):
        target = self.targets.get(request.service)
        if target is None or target.get("disabled_reason") or request.action not in target.get("actions", []):
            raise HTTPException(403, "action not allowed")
        with self.lock, self.db() as conn:
            existing = conn.execute("SELECT * FROM actions WHERE id=?", (str(request.id),)).fetchone()
            if existing:
                if (existing["service"], existing["action"], existing["actor_id"]) != (
                        request.service, request.action, str(request.actor_id)):
                    raise HTTPException(409, "request id already used")
                return {"id": str(request.id), "accepted": True}
            pending = conn.execute(
                "SELECT 1 FROM actions a LEFT JOIN results r USING(id) "
                "WHERE a.service=? AND r.id IS NULL", (request.service,)).fetchone()
            if pending:
                raise HTTPException(409, "service already has a pending action")
            conn.execute("INSERT INTO actions VALUES (?,?,?,?,?,?)",
                         (str(request.id), request.service, request.action, str(request.actor_id),
                          request.actor, now()))
        self.executor.submit(self.perform, str(request.id), request.service, request.action)
        return {"id": str(request.id), "accepted": True}

    def perform(self, ident, key, action):
        status = "success"
        try:
            target = self.targets[key]
            if target["kind"] == "docker":
                container = self.docker_id(target)
                args = ["docker", action]
                if action == "restart":
                    args += ["--time", "20"]
                command([*args, container], timeout=50)
            else:
                command(["systemctl", "--user", action, target["unit"]], timeout=50)
        except subprocess.TimeoutExpired:
            status = "unknown"
        except (subprocess.SubprocessError, OSError, LookupError, ValueError):
            status = "error"
        finally:
            with self.db() as conn:
                conn.execute("INSERT INTO results VALUES (?,?,?)", (ident, status, now()))
            with self.snapshot_lock:
                self.cached = None


class Action(BaseModel):
    id: UUID
    service: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    action: str = Field(pattern=r"^(start|restart)$")
    actor_id: UUID
    actor: str = Field(min_length=1, max_length=200)


def create_app():
    config = json.loads(Path(os.environ["SERVICE_CONTROL_CONFIG"]).read_text())
    token = Path(config["token_file"]).read_text().strip()
    if len(token) < 32:
        raise ValueError("service-control token must contain at least 32 characters")
    controller = Controller(config)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        controller.executor.shutdown(wait=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.controller = controller

    def authorize(authorization: str = Header("")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "unauthorized")

    @app.get("/status", dependencies=[Depends(authorize)])
    def status():
        return controller.snapshot()

    @app.post("/actions", dependencies=[Depends(authorize)], status_code=202)
    def action(request: Action):
        return controller.submit(request)

    return app

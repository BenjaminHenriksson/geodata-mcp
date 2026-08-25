"""Observability wiring for the viewer service (issue #81): structured JSON
logging to stdout and a Prometheus ``/metrics`` endpoint.

Both pieces are additive and dependency-tolerant, so importing and using this
module never changes how the app serves requests:

* ``init_logging()`` uses only the standard library, so it always works. It
  routes the root logger (and uvicorn's own loggers) through a single stdout
  handler that renders one JSON object per line — the shape a container log
  collector / Syslog / central log store expects.
* ``metrics_app()`` returns an ASGI app exposing Prometheus metrics. It needs
  ``prometheus_client``; the import is guarded, so if the package is absent the
  app still starts and ``/metrics`` answers 200 with a plain-text note instead
  of failing the mount.

Nothing here raises on the import path, which is what keeps the change
strictly non-breaking.
"""
import json
import logging
import os
import sys
import time

# Attribute names present on a bare LogRecord. Anything a caller attaches via
# ``logger.info(..., extra={...})`` will not be in this set and is emitted as a
# top-level JSON field, so structured context survives to the central log store.
_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {
    "message", "asctime", "taskName",
}


def _json_safe(value):
    """Return ``value`` unchanged if it is JSON-serialisable, else its repr."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class JsonFormatter(logging.Formatter):
    """Render each log record as a single-line JSON object.

    One object per line ("JSON lines") is deliberately the most portable
    structured-log format: Docker/Podman stdout drivers, journald and Syslog
    forwarders all treat it as one event per line and can index the fields
    downstream without a custom parser.
    """

    default_time_format = "%Y-%m-%dT%H:%M:%S"

    def formatTime(self, record, datefmt=None):  # noqa: N802 (stdlib signature)
        base = time.strftime(self.default_time_format, time.gmtime(record.created))
        return f"{base}.{int(record.msecs):03d}Z"

    def format(self, record):
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        for key, val in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _json_safe(val)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _ServiceFilter(logging.Filter):
    """Stamp a constant ``service`` field on every record, so a collector that
    aggregates all containers can filter log lines by their source service."""

    def __init__(self, service):
        super().__init__()
        self._service = service

    def filter(self, record):
        if not hasattr(record, "service"):
            record.service = self._service
        return True


# Loggers whose own handlers we replace so their output is JSON on stdout too,
# rather than uvicorn's default human-readable format. Routing them through the
# root handler is what makes logging genuinely "centralised" for this service.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def init_logging(level=None, service="viewer"):
    """Configure structured JSON logging to stdout.

    Idempotent: it installs exactly one stdout handler on the root logger,
    replacing any handlers a previous call (or ``logging.basicConfig``) left
    behind, so calling it once per worker at startup is safe.

    Level is taken from the ``level`` argument, else ``$LOG_LEVEL``, else
    ``INFO``. An unrecognised value falls back to ``INFO`` rather than raising,
    keeping startup non-breaking.

    Returns the configured root logger for convenience.
    """
    level_name = str(level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    resolved = logging.getLevelName(level_name)
    lvl = resolved if isinstance(resolved, int) else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_ServiceFilter(service))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(lvl)

    # Let uvicorn's loggers propagate to the root JSON handler instead of
    # emitting their own plain-text lines to a separate stream.
    for name in _UVICORN_LOGGERS:
        uv = logging.getLogger(name)
        for existing in list(uv.handlers):
            uv.removeHandler(existing)
        uv.propagate = True

    return root


def _fallback_metrics_app():
    """Minimal ASGI app used when ``prometheus_client`` is not installed.

    Answers any HTTP request with a plain-text 200 so that mounting ``/metrics``
    never breaks startup and a scraper gets a well-formed (empty) response.
    """
    body = (b"# prometheus_client is not installed in this image; "
            b"no metrics are exported.\n")

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            # Drain lifespan events cleanly if the server sends them to the mount.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type",
                         b"text/plain; version=0.0.4; charset=utf-8")],
        })
        await send({"type": "http.response.body", "body": body})

    return app


def metrics_app():
    """Return an ASGI app that serves Prometheus metrics at its own root.

    The import of ``prometheus_client`` is guarded: if the dependency is absent
    the plain-text fallback is returned instead, so the caller can mount the
    result unconditionally.

    When the process runs under multiple uvicorn workers, set
    ``$PROMETHEUS_MULTIPROC_DIR`` to a shared writable directory so the scrape
    aggregates counters across all workers (prometheus_client multiprocess
    mode). Without it, each worker exposes only its own in-process registry and
    a scrape lands on whichever worker answers.
    """
    try:
        from prometheus_client import make_asgi_app
    except Exception:  # pragma: no cover - exercised only without the dep
        return _fallback_metrics_app()

    mp_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if mp_dir:
        try:
            from prometheus_client import CollectorRegistry, multiprocess
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            return make_asgi_app(registry=registry)
        except Exception:  # pragma: no cover - fall back to the default registry
            logging.getLogger("viewer.obs").warning(
                "prometheus multiprocess mode unavailable; using default registry",
                exc_info=True,
            )

    return make_asgi_app()

# Logs and metrics

The viewer configures JSON logging through
[`services/viewer/obs.py`](../services/viewer/obs.py). Each stdout record includes
`ts`, `level`, `logger`, `msg` and `service`; fields supplied through logging
`extra` are preserved. `LOG_LEVEL` defaults to `INFO`. Uvicorn's viewer logs use
the same formatter.

The worker uses Python logging to stdout. MCP uses its runtime's logging.
These services do not currently share the viewer's JSON formatter.

```sh
docker compose logs --tail=100 viewer mcp worker
```

The viewer exposes Prometheus metrics at `/metrics/`, including the default
Python/process collectors. `/metrics` redirects to the mounted application.
When `prometheus-client` is unavailable, the endpoint returns a plain-text note
instead of metrics. MCP and worker do not expose this endpoint.

With multiple viewer workers, `PROMETHEUS_MULTIPROC_DIR` selects a shared,
writable metrics directory. Without it, a scrape sees the answering worker's
registry. Configure the collector to scrape the viewer's internal port 8001;
central log collection is the deployment platform's responsibility.

OpenTelemetry instrumentation and an OTLP exporter are not bundled. Setting
`OTEL_*` variables alone does not enable tracing in the shipped services.

# MCP server — agent-facing control plane (FastMCP streamable HTTP on :8000)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY services/mcp/requirements.txt .
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
RUN uv venv /app/.venv && uv pip install --python /app/.venv/bin/python -r requirements.txt
ENV PATH="/app/.venv/bin:$PATH"

COPY services/mcp/*.py ./

COPY geodata_common /app/geodata_common

EXPOSE 8000
CMD ["python", "server.py"]

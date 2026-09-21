# MCP server — agent-facing control plane (FastMCP streamable HTTP on :8000)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/mcp/requirements.txt .
RUN pip install -r requirements.txt

COPY services/mcp/*.py ./

COPY geodata_common /app/geodata_common

EXPOSE 8000
CMD ["python", "server.py"]

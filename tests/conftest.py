"""Local regression tests use fake service dependencies, never a live database."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for service in ("mcp", "viewer", "worker"):
    sys.path.append(str(ROOT / "services" / service))
for role in ("APP", "RO", "WS"):
    os.environ.setdefault(
        f"DATABASE_URL_{role}", "postgresql://unused:fixture@localhost/unused"
    )

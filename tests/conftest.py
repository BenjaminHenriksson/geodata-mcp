"""Local regression tests use fake service dependencies, never a live database."""

import os
import sys

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for service in ("mcp", "viewer", "worker"):
    sys.path.append(str(ROOT / "services" / service))
for role in ("APP", "RO", "WS"):
    os.environ.setdefault(
        f"DATABASE_URL_{role}", "postgresql://unused:fixture@localhost/unused"
    )


@pytest.fixture
def vision_endpoint(monkeypatch):
    """No model/provider credentials are needed for the transport regressions."""
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.example.test/v1")
    monkeypatch.setenv("VISION_MODEL", "test-vision-model")
    for name in ("VISION_API_KEY", "VISION_EXTRA_BODY", "VISION_OCR_EXTRA_BODY",
                 "VISION_RESPONSE_FORMAT", "VISION_MAX_OUTPUT_TOKENS", "VISION_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)

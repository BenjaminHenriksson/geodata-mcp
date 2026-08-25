#!/usr/bin/env python3
"""Regenerate services/viewer/openapi.json from the running FastAPI app.

This dumps FastAPI's *own* auto-generated OpenAPI schema (the source of truth for
what the code actually serves) so CI can diff it and catch drift. The curated,
hand-maintained companion document is services/viewer/openapi.yaml.

The viewer is launched as ``main:app`` with services/viewer as the working
directory (see services/viewer/Dockerfile), and its modules import each other
flatly (``import dbq``, ``import page`` …). We therefore put services/viewer on
sys.path and import ``main`` — the same entrypoint uvicorn uses — which is what
``services.viewer.main:app`` refers to. Building app.openapi() performs no
database or network access.

Usage:
    python scripts/dump_openapi.py            # writes services/viewer/openapi.json
    python scripts/dump_openapi.py --check    # non-zero exit if the file is stale
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER_DIR = os.path.join(REPO_ROOT, "services", "viewer")
OUT_PATH = os.path.join(VIEWER_DIR, "openapi.json")


def load_app():
    """Import services/viewer/main.py and return its FastAPI ``app``."""
    if VIEWER_DIR not in sys.path:
        sys.path.insert(0, VIEWER_DIR)
    import importlib

    main = importlib.import_module("main")
    return main.app


def render() -> str:
    app = load_app()
    schema = app.openapi()
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    text = render()

    if check:
        try:
            with open(OUT_PATH, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            current = None
        if current != text:
            sys.stderr.write(
                f"{OUT_PATH} is out of date; run: python scripts/dump_openapi.py\n")
            return 1
        print(f"{OUT_PATH} is up to date.")
        return 0

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        rel = os.path.relpath(OUT_PATH, REPO_ROOT)
    except ValueError:
        rel = OUT_PATH
    paths = len(json.loads(text).get("paths", {}))
    print(f"wrote {rel} ({len(text)} bytes, {paths} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

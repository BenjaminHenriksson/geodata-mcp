"""Configuration — read once from the environment (names fixed by CONTRACTS.md)."""

import os

DATABASE_URL_APP = os.environ["DATABASE_URL_APP"]
DATABASE_URL_RO = os.environ["DATABASE_URL_RO"]
DATABASE_URL_WS = os.environ["DATABASE_URL_WS"]

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://minio:9000")
S3_PUBLIC_ENDPOINT = os.environ.get("S3_PUBLIC_ENDPOINT", "http://localhost:9000")
S3_BUCKET = os.environ.get("S3_BUCKET", "exports")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "geodata")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "")

EMBED_URL = os.environ.get("EMBED_URL", "http://worker:8100/embed")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "unsloth/embeddinggemma-300m")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "256"))

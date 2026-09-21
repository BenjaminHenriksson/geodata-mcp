"""Per-host HTTP credentials for authenticated sources.

The official source list (data_sources.xlsx) includes Lantmäteriet services
that require the municipal account (Basic auth) across several subdomains —
maps.lantmateriet.se (topowebb/ortofoto/fastighet WMS), api.lantmateriet.se
(stac-bild STAC, NGP OGC API). One var covers them all:

    LANTMATERIET_CREDENTIALS="suns0011:<password>"      # any *.lantmateriet.se

Other authenticated hosts get explicit entries (per-host beats the domain var):

    GEODATA_HTTP_CREDENTIALS="host.example.se=user:password,other.se=user:password"

Credentials are environment-only — never stored in the catalog
(catalog.sources.auth_note documents the need, this module supplies the
secret). Applied to capabilities/collections fetches and document downloads
(httpx Basic auth) and to ogr2ogr pulls (GDAL_HTTP_USERPWD, scoped to the
subprocess)."""

import os
import urllib.parse

LM_DOMAIN = "lantmateriet.se"


def _table() -> dict:
    table = {}
    for part in os.environ.get("GEODATA_HTTP_CREDENTIALS", "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        host, cred = part.split("=", 1)
        if ":" in cred:
            table[host.strip().lower()] = cred.strip()
    return table


def userpwd_for(url: str) -> str | None:
    """'user:password' for the url's host, or None."""
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    if not host:
        return None
    cred = _table().get(host)
    if cred:
        return cred
    if host == LM_DOMAIN or host.endswith("." + LM_DOMAIN):
        lm = os.environ.get("LANTMATERIET_CREDENTIALS", "").strip()
        if ":" in lm:
            return lm
    return None


def basic_auth_for(url: str) -> tuple[str, str] | None:
    """httpx-style (user, password) tuple for the url's host, or None."""
    cred = userpwd_for(url)
    if not cred:
        return None
    user, _, password = cred.partition(":")
    return (user, password)


def gdal_env_for(url: str) -> dict | None:
    """Extra env for an ogr2ogr subprocess touching this url, or None."""
    cred = userpwd_for(url)
    return {"GDAL_HTTP_USERPWD": cred} if cred else None

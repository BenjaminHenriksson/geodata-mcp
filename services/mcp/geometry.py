"""Result value conversion: geometry hex-WKB → WKT, truncation, JSON-safe scalars."""

import datetime
import decimal
import uuid

import shapely.wkb

MAX_TEXT = 400
ELLIPSIS = " …(truncated)"


def truncate_text(s: str) -> str:
    if len(s) > MAX_TEXT:
        return s[:MAX_TEXT] + ELLIPSIS
    return s


def hexwkb_to_wkt(value: str) -> str:
    """psycopg returns geometry as a hex-WKB string; convert to (truncated) WKT."""
    try:
        geom = shapely.wkb.loads(bytes.fromhex(value))
        return truncate_text(geom.wkt)
    except Exception:
        return truncate_text(str(value))


def jsonable(value, is_geometry: bool = False):
    """Convert one cell to a JSON-serializable value."""
    if value is None:
        return None
    if is_geometry and isinstance(value, str):
        return hexwkb_to_wkt(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return truncate_text(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview, bytearray)):
        return truncate_text(bytes(value).hex())
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return truncate_text(str(value))


def jsonable_row(row: dict) -> dict:
    return {k: jsonable(v) for k, v in row.items()}


def infer_pg_type(values: list) -> str:
    """Infer a column type (text / double precision / bigint / boolean) from python values."""
    seen = [v for v in values if v is not None]
    if not seen:
        return "text"
    if all(isinstance(v, bool) for v in seen):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in seen):
        return "bigint"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        return "double precision"
    return "text"


def coerce_for_type(value, pg_type: str):
    """Make a python value bind-safe for the inferred column type (text gets str())."""
    if value is None:
        return None
    if pg_type == "text" and not isinstance(value, str):
        return str(value)
    return value

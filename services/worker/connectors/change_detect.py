"""SAM3 orthophoto change detection (change_detect): STAC item search over two
vintages, windowed /vsicurl reads of the COGs, per-window segmentation via the
standalone segmenter service (services/segmenter, HTTP), mask polygonization
and a PostGIS diff into a workspace layer of change candidates plus a per-tile
coverage table.

Masks travel as PNG bytes end to end — no numpy, no gdal_array (the numpy-2 /
GDAL-3.8.5 ABI combination in the worker image is unverified).
"""

import base64
import io
import logging
import os
from datetime import datetime

import httpx
import psycopg
from osgeo import gdal, ogr, osr
from PIL import Image
from psycopg import sql

import dbutil
import netauth

log = logging.getLogger("worker.change_detect")

# Process-global, but the worker's only in-process GDAL use is this module
# (everything else shells out to ogr2ogr).
gdal.UseExceptions()
ogr.UseExceptions()
osr.UseExceptions()

TILE_PX = 1008  # SAM3 native resolution
OVERLAP_PX = 96
MAX_TILES = 128
IOU_UNCHANGED = 0.80
# An all-nodata 1008 px PNG deflates to ~3 KB; any real orthophoto window is
# hundreds of KB. Below this the window is treated as missing imagery.
EMPTY_PNG_BYTES = 4096
STAC_LIMIT = 1000
MAX_STAC_PAGES = 10
HTTP_TIMEOUT = 60.0
SEGMENT_TIMEOUT = 600.0  # first /segment loads the model
HEALTH_TIMEOUT = 10.0


# ── segmenter HTTP ──────────────────────────────────────────────────────────

def _check_segmenter(sam3_url: str) -> None:
    try:
        resp = httpx.get(sam3_url + "/healthz", timeout=HEALTH_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"segmenter not running at {sam3_url} — start services/segmenter on "
            "the host (see services/segmenter/README.md) or set SAM3_URL"
        ) from exc


def _segment(client: httpx.Client, sam3_url: str, png_bytes: bytes,
             concepts: list, threshold: float) -> dict:
    resp = client.post(
        sam3_url + "/segment",
        json={"image_png_b64": base64.b64encode(png_bytes).decode("ascii"),
              "concepts": concepts, "threshold": threshold},
    )
    resp.raise_for_status()
    return resp.json()


# ── area + STAC ─────────────────────────────────────────────────────────────

def _area_bboxes(conn, area_wkt: str):
    """(xmin,ymin,xmax,ymax) of the area in EPSG:3006 and EPSG:4326."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ST_XMin(g6) AS x1, ST_YMin(g6) AS y1,
                       ST_XMax(g6) AS x2, ST_YMax(g6) AS y2,
                       ST_XMin(g4) AS lon1, ST_YMin(g4) AS lat1,
                       ST_XMax(g4) AS lon2, ST_YMax(g4) AS lat2
                  FROM (SELECT ST_Transform(ST_GeomFromText(%s, 3014), 3006) AS g6,
                               ST_Transform(ST_GeomFromText(%s, 3014), 4326) AS g4) t
                """,
                (area_wkt, area_wkt),
            )
            row = cur.fetchone()
    except psycopg.Error as exc:
        conn.rollback()
        raise RuntimeError(f"invalid area_wkt_3014: {exc}") from exc
    return ((row["x1"], row["y1"], row["x2"], row["y2"]),
            (row["lon1"], row["lat1"], row["lon2"], row["lat2"]))


def _stac_root(conn, collection_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.url FROM catalog.datasets d
              JOIN catalog.sources s ON s.id = d.source_id
             WHERE d.external_id = %s AND s.kind = 'stac'
             LIMIT 1
            """,
            (collection_id,),
        )
        row = cur.fetchone()
    if row is None or not row["url"]:
        raise RuntimeError(
            f"collection {collection_id} not in catalog.datasets (source kind 'stac')")
    return row["url"].rstrip("/")


def _stac_items(root: str, collection_id: str, bbox_4326) -> list:
    """POST /search with next-token pagination; rgb/rgbi items with a data
    asset, upplosning and proj:bbox. Metadata endpoints are public."""
    url = root + "/search"
    body = {"bbox": [float(v) for v in bbox_4326],
            "collections": [collection_id], "limit": STAC_LIMIT}
    items = []
    for _ in range(MAX_STAC_PAGES):
        resp = httpx.post(url, json=body, timeout=HTTP_TIMEOUT, follow_redirects=True,
                          headers={"Accept": "application/geo+json, application/json"})
        resp.raise_for_status()
        data = resp.json()
        for feat in data.get("features") or []:
            props = feat.get("properties") or {}
            if props.get("spektraltyp") not in ("rgb", "rgbi"):
                continue
            href = ((feat.get("assets") or {}).get("data") or {}).get("href")
            gsd = props.get("upplosning")
            pbox = props.get("proj:bbox")
            if not href or not gsd or not isinstance(pbox, (list, tuple)) or len(pbox) < 4:
                continue
            items.append({"id": feat.get("id"), "href": href,
                          "datetime": props.get("datetime"),
                          "gsd": float(gsd),
                          "proj_bbox": [float(v) for v in pbox[:4]]})
        next_link = next((ln for ln in data.get("links") or []
                          if isinstance(ln, dict) and ln.get("rel") == "next"), None)
        if not next_link:
            break
        token = next_link.get("body")
        if not isinstance(token, dict) or not token:
            break
        # Merge regardless of the link's merge flag: the token body never
        # carries bbox/collections, which must survive into the next page.
        body.update(token)
        url = next_link.get("href") or url
    if not items:
        raise RuntimeError(f"collection {collection_id} has no rgb imagery over the area")
    return items


def _parse_dt(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _vintage_meta(collection_id: str, items: list) -> dict:
    dts = sorted(i["datetime"] for i in items if i["datetime"])
    months = {d.month for d in (_parse_dt(s) for s in dts) if d}
    return {"collection": collection_id,
            "datetime_min": dts[0] if dts else None,
            "datetime_max": dts[-1] if dts else None,
            "gsd": max(i["gsd"] for i in items),
            "months": sorted(months)}


def _cross_season(months_a: list, months_b: list) -> bool:
    if not months_a or not months_b:
        return False
    return min(min(abs(ma - mb), 12 - abs(ma - mb))
               for ma in months_a for mb in months_b) > 2


# ── window grid ─────────────────────────────────────────────────────────────

def _window_grid(bbox6, proc_gsd: float) -> list:
    """1008 px windows at proc_gsd over the 3006 bbox, 96 px overlap, row-major
    from the top-left corner (uly is the window's max northing)."""
    x1, y1, x2, y2 = bbox6
    tile_m = TILE_PX * proc_gsd
    stride_m = (TILE_PX - OVERLAP_PX) * proc_gsd
    xs = [x1]
    while xs[-1] + tile_m < x2:
        xs.append(xs[-1] + stride_m)
    ys = [y2]
    while ys[-1] - tile_m > y1:
        ys.append(ys[-1] - stride_m)
    windows = []
    for r, uly in enumerate(ys):
        for c, ulx in enumerate(xs):
            windows.append({"tile_id": f"r{r:02d}c{c:02d}", "ulx": ulx, "uly": uly,
                            "lrx": ulx + tile_m, "lry": uly - tile_m})
    return windows


def _filter_windows(conn, windows: list, area_wkt: str) -> list:
    """Keep windows intersecting the area itself — the grid covers the bbox,
    which overshoots for slanted or L-shaped areas. One VALUES round trip."""
    if not windows:
        return windows
    values = sql.SQL(", ").join(sql.SQL("(%s, %s, %s, %s, %s)") for _ in windows)
    query = sql.SQL(
        "SELECT v.tile_id FROM (VALUES {values}) AS v(tile_id, x1, y1, x2, y2) "
        "WHERE ST_Intersects(ST_MakeEnvelope(v.x1, v.y1, v.x2, v.y2, 3006), "
        "ST_Transform(ST_GeomFromText(%s, 3014), 3006))"
    ).format(values=values)
    params = []
    for w in windows:
        params += [w["tile_id"], w["ulx"], w["lry"], w["lrx"], w["uly"]]
    params.append(area_wkt)
    with conn.cursor() as cur:
        cur.execute(query, params)
        keep = {row["tile_id"] for row in cur.fetchall()}
    return [w for w in windows if w["tile_id"] in keep]


def _covers(w: dict, items: list) -> bool:
    """Window intersects at least one selected item's proj:bbox (3006)."""
    for it in items:
        b = it["proj_bbox"]
        if w["lrx"] > b[0] and w["ulx"] < b[2] and w["uly"] > b[1] and w["lry"] < b[3]:
            return True
    return False


# ── GDAL windowed reads + mask polygonization ───────────────────────────────

def _unlink_quiet(path: str) -> None:
    try:
        gdal.Unlink(path)
    except Exception:
        pass


def _vsimem_bytes(path: str) -> bytes:
    fh = gdal.VSIFOpenL(path, "rb")
    if fh is None:
        raise RuntimeError(f"cannot open {path}")
    try:
        gdal.VSIFSeekL(fh, 0, 2)
        size = gdal.VSIFTellL(fh)
        gdal.VSIFSeekL(fh, 0, 0)
        return gdal.VSIFReadL(1, size, fh)
    finally:
        gdal.VSIFCloseL(fh)


# Sentinel distinguishing a failed read (network/COG error -> coverage
# 'error') from genuinely absent imagery (nodata -> 'missing_a'/'missing_b').
_READ_ERROR = object()


def _window_png(vrt, w: dict, path: str):
    """1008x1008 RGB PNG bytes for the window, None when this vintage has no
    usable pixels there (nodata edge), or _READ_ERROR on a failed read."""
    try:
        out = gdal.Translate(path, vrt, format="PNG",
                             projWin=[w["ulx"], w["uly"], w["lrx"], w["lry"]],
                             width=TILE_PX, height=TILE_PX, resampleAlg="bilinear")
    except Exception as exc:
        log.warning("window %s read failed: %s", w["tile_id"], exc)
        _unlink_quiet(path)
        return _READ_ERROR
    if out is None:
        return _READ_ERROR
    out = None
    try:
        data = _vsimem_bytes(path)
    finally:
        _unlink_quiet(path)
        _unlink_quiet(path + ".aux.xml")
    if len(data) < EMPTY_PNG_BYTES:
        return None
    return data


def _mask_polygons(mask_b64: str, w: dict, srs_wkt: str) -> list:
    """Segmenter mask PNG → list of WKB polygons in EPSG:3006, via a
    georeferenced MEM raster and gdal.Polygonize."""
    if not mask_b64:
        return []
    img = Image.open(io.BytesIO(base64.b64decode(mask_b64)))
    if img.mode != "L":
        img = img.convert("L")
    if img.size != (TILE_PX, TILE_PX):
        img = img.resize((TILE_PX, TILE_PX), Image.NEAREST)
    mem = gdal.GetDriverByName("MEM").Create("", TILE_PX, TILE_PX, 1, gdal.GDT_Byte)
    mem.SetGeoTransform((w["ulx"], (w["lrx"] - w["ulx"]) / TILE_PX, 0.0,
                         w["uly"], 0.0, -(w["uly"] - w["lry"]) / TILE_PX))
    mem.SetProjection(srs_wkt)
    band = mem.GetRasterBand(1)
    band.WriteRaster(0, 0, TILE_PX, TILE_PX, img.tobytes())
    srs = osr.SpatialReference()
    srs.ImportFromWkt(srs_wkt)
    vec = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = vec.CreateLayer("mask", srs=srs, geom_type=ogr.wkbPolygon)
    # The band doubles as its own mask: only the 255-valued regions polygonize.
    gdal.Polygonize(band, band, layer, -1)
    out = []
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is not None:
            out.append(bytes(geom.ExportToWkb()))
    vec = None
    mem = None
    return out


def _infer(sam3_url: str, windows: list, items_by_tag: dict, concepts: list,
           threshold: float, statuses: dict, prefix: str):
    """Per-window inference over both vintages. Mutates statuses; returns
    (detection rows for the temp table, model info from the segmenter)."""
    cred = None
    for items in items_by_tag.values():
        for it in items:
            cred = netauth.userpwd_for(it["href"])
            if cred:
                break
        if cred:
            break

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3006)
    srs_wkt = srs.ExportToWkt()

    det_rows = []
    model_info = None
    vrts = {}
    vrt_paths = []
    # SetConfigOption is process-global; fine in the single-threaded job loop,
    # reset in the finally. The credential must never be logged or stored.
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    if cred:
        gdal.SetConfigOption("GDAL_HTTP_AUTH", "BASIC")
        gdal.SetConfigOption("GDAL_HTTP_USERPWD", cred)
    try:
        for tag, items in items_by_tag.items():
            path = f"{prefix}_{tag}.vrt"
            # bandList collapses RGBI to RGB; RGB-only eras pass through.
            vrt = gdal.BuildVRT(path, ["/vsicurl/" + i["href"] for i in items],
                                bandList=[1, 2, 3])
            if vrt is None:
                raise RuntimeError(f"BuildVRT failed for vintage {tag}")
            vrts[tag] = vrt
            vrt_paths.append(path)

        with httpx.Client(timeout=httpx.Timeout(SEGMENT_TIMEOUT)) as client:
            for w in windows:
                if statuses[w["tile_id"]] is not None:
                    continue
                pngs = {}
                for tag in ("a", "b"):
                    png = _window_png(vrts[tag], w,
                                      f"{prefix}_{w['tile_id']}_{tag}.png")
                    if png is _READ_ERROR:
                        statuses[w["tile_id"]] = "error"
                        break
                    if png is None:
                        statuses[w["tile_id"]] = "missing_" + tag
                        break
                    pngs[tag] = png
                if len(pngs) < 2:
                    continue
                # Buffer this window's rows so a failure in vintage b cannot
                # leave half a window in the diff (spurious 'disappeared').
                w_rows = []
                try:
                    for tag in ("a", "b"):
                        data = _segment(client, sam3_url, pngs[tag], concepts, threshold)
                        if model_info is None:
                            model_info = data.get("model")
                        for det in data.get("detections") or []:
                            for wkb in _mask_polygons(det.get("mask_png_b64"), w, srs_wkt):
                                w_rows.append((tag, w["tile_id"], det["concept"],
                                               float(det["score"]), wkb))
                except httpx.HTTPStatusError as exc:
                    # 5xx (backend missing deps, gated weights, load failure)
                    # is persistent — failing every tile as 'error' would end
                    # the job 'done' with zero detections.
                    if exc.response.status_code >= 500:
                        raise RuntimeError(
                            f"segmenter backend failing at {sam3_url} (HTTP "
                            f"{exc.response.status_code}): {exc.response.text[:300]}"
                            " — check services/segmenter logs on the host") from exc
                    statuses[w["tile_id"]] = "error"
                    log.warning("segmenter rejected tile %s: %s", w["tile_id"], exc)
                    continue
                except httpx.TransportError as exc:
                    raise RuntimeError(
                        f"segmenter at {sam3_url} became unreachable mid-run — "
                        "check services/segmenter on the host") from exc
                det_rows.extend(w_rows)
                statuses[w["tile_id"]] = "analyzed"
    finally:
        vrts.clear()
        for path in vrt_paths:
            _unlink_quiet(path)
        gdal.SetConfigOption("GDAL_HTTP_USERPWD", None)
        gdal.SetConfigOption("GDAL_HTTP_AUTH", None)
        gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", None)
    return det_rows, model_info


# ── PostGIS diff + output tables ────────────────────────────────────────────

def _write_outputs(conn, job, schema: str, table: str, cov_table: str,
                   windows: list, statuses: dict, det_rows: list,
                   proc_gsd: float, min_area: float, collection_a: str,
                   collection_b: str, meta_a: dict, meta_b: dict,
                   details: dict) -> dict:
    """One transaction: temp detections → merged objects → classified diff →
    final table + coverage table, indexes, grants, provenance. Returns the
    per-class row counts."""
    tbl = dbutil.qualify(schema, table)
    cov = dbutil.qualify(schema, cov_table)
    with conn.cursor() as cur:
        # geodata_app's role default is 120 s; the union/IoU statements on a
        # full-cap run need more. LOCAL: reverts at this transaction's commit.
        cur.execute("SET LOCAL statement_timeout = '15min'")
        cur.execute(
            "CREATE TEMP TABLE chg_det ("
            " vintage text NOT NULL, tile_id text NOT NULL, concept text NOT NULL,"
            " score real NOT NULL, geom geometry(Polygon, 3014) NOT NULL)"
        )
        if det_rows:
            cur.executemany(
                "INSERT INTO chg_det (vintage, tile_id, concept, score, geom) "
                "VALUES (%s, %s, %s, %s, "
                "ST_Transform(ST_SetSRID(ST_GeomFromWKB(%s), 3006), 3014))",
                det_rows,
            )
        # Morphological opening (kills slivers/misregistration noise) applied
        # to the per-vintage+concept union BEFORE the dump: opening a set of
        # disjoint parts equals opening each part, and dumping afterwards
        # keeps every object a plain Polygon (a negative buffer can split
        # one polygon into several). Empty results dump to zero rows.
        cur.execute(
            """
            CREATE TEMP TABLE chg_obj AS
            WITH merged AS (
                SELECT vintage, concept,
                       ST_Buffer(ST_Buffer(ST_Union(geom), %s), %s) AS geom
                  FROM chg_det
                 GROUP BY vintage, concept
            ), parts AS (
                SELECT vintage, concept, (ST_Dump(geom)).geom AS geom FROM merged
            )
            SELECT row_number() OVER () AS oid, p.vintage, p.concept, p.geom,
                   (SELECT max(d.score) FROM chg_det d
                     WHERE d.vintage = p.vintage AND d.concept = p.concept
                       AND ST_Intersects(d.geom, p.geom)) AS score
              FROM parts p
             WHERE NOT ST_IsEmpty(p.geom) AND ST_Area(p.geom) > 0
            """,
            (-2.0 * proc_gsd, 2.0 * proc_gsd),
        )
        cur.execute(sql.SQL(
            """
            CREATE TABLE {tbl} (
                fid bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                concept text NOT NULL,
                change_class text NOT NULL,
                confidence_a real,
                confidence_b real,
                iou real,
                area_m2 real,
                vintage_a text,
                vintage_b text,
                datetime_a timestamptz,
                datetime_b timestamptz,
                geom geometry(Polygon, 3014)
            )
            """).format(tbl=tbl))
        cur.execute(
            sql.SQL(
                """
                WITH a AS (SELECT * FROM chg_obj WHERE vintage = 'a'),
                     b AS (SELECT * FROM chg_obj WHERE vintage = 'b'),
                     pairs AS (
                         SELECT a.oid AS a_oid, b.oid AS b_oid, b.concept,
                                a.score AS score_a, b.score AS score_b,
                                b.geom AS geom_b,
                                ST_Area(ST_Intersection(a.geom, b.geom))
                                  / NULLIF(ST_Area(ST_Union(a.geom, b.geom)), 0) AS iou
                           FROM a
                           JOIN b ON b.concept = a.concept
                                 AND ST_Intersects(a.geom, b.geom)
                     ),
                     best AS (
                         SELECT * FROM (SELECT p.*, row_number() OVER (
                                            PARTITION BY p.b_oid
                                            ORDER BY p.iou DESC) AS rn
                                          FROM pairs p) ranked
                          WHERE rn = 1
                     ),
                     classified AS (
                         SELECT b.concept, 'appeared' AS change_class,
                                NULL::real AS confidence_a, b.score AS confidence_b,
                                NULL::double precision AS iou, b.geom
                           FROM b
                          WHERE NOT EXISTS (SELECT 1 FROM pairs p WHERE p.b_oid = b.oid)
                         UNION ALL
                         SELECT a.concept, 'disappeared', a.score, NULL::real,
                                NULL::double precision, a.geom
                           FROM a
                          -- best, not pairs: a boundary-graze pair must not
                          -- suppress a demolition claimed by no b-object
                          WHERE NOT EXISTS (SELECT 1 FROM best WHERE best.a_oid = a.oid)
                         UNION ALL
                         SELECT best.concept, 'changed', best.score_a, best.score_b,
                                best.iou, best.geom_b
                           FROM best
                          WHERE best.iou < %s
                     )
                INSERT INTO {tbl} (concept, change_class, confidence_a, confidence_b,
                                   iou, area_m2, vintage_a, vintage_b,
                                   datetime_a, datetime_b, geom)
                SELECT concept, change_class, confidence_a, confidence_b, iou,
                       ST_Area(geom), %s, %s, %s::timestamptz, %s::timestamptz, geom
                  FROM classified
                 WHERE ST_Area(geom) >= %s
                """).format(tbl=tbl),
            (IOU_UNCHANGED, collection_a, collection_b,
             meta_a["datetime_min"], meta_b["datetime_min"], min_area),
        )
        cur.execute(sql.SQL(
            """
            CREATE TABLE {cov} (
                fid bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tile_id text NOT NULL,
                status text NOT NULL,
                gsd_m real,
                geom geometry(Polygon, 3014)
            )
            """).format(cov=cov))
        cur.executemany(
            sql.SQL(
                "INSERT INTO {cov} (tile_id, status, gsd_m, geom) VALUES (%s, %s, %s, "
                "ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 3006), 3014))"
            ).format(cov=cov),
            [(w["tile_id"], statuses[w["tile_id"]], proc_gsd,
              w["ulx"], w["lry"], w["lrx"], w["uly"]) for w in windows],
        )
        cur.execute(sql.SQL("CREATE INDEX ON {tbl} USING GIST (geom)").format(tbl=tbl))
        cur.execute(sql.SQL("CREATE INDEX ON {cov} USING GIST (geom)").format(cov=cov))
        for t in (table, cov_table):
            dbutil.analyze(cur, schema, t)
            dbutil.grant_select(cur, schema, t)
            if dbutil.WS_SCHEMA_RE.match(schema):
                # The job runs as geodata_app; hand workspace outputs to
                # agent_ws so the layer tool can drop, rename or update them.
                cur.execute(sql.SQL("ALTER TABLE {} OWNER TO agent_ws").format(
                    dbutil.qualify(schema, t)))
        dbutil.insert_provenance(
            cur,
            kind="change_detect",
            object_ref=f"{schema}.{table}",
            workspace_id=job.get("workspace_id"),
            input_tables=[],
            job_id=job["id"],
            details=details,
        )
        cur.execute(sql.SQL(
            "SELECT change_class, count(*) AS n FROM {tbl} GROUP BY change_class"
        ).format(tbl=tbl))
        counts = {row["change_class"]: int(row["n"]) for row in cur.fetchall()}
    return counts


# ── job handler ─────────────────────────────────────────────────────────────

def change_detect(conn, job) -> dict:
    """Job handler: SAM3 concept segmentation over two orthophoto vintages,
    mask diff in PostGIS → change-candidate table + _coverage table."""
    payload = job["payload"]
    schema = dbutil.check_schema_name(payload["target_schema"])
    table = dbutil.check_table_name(payload["table_name"])
    cov_table = dbutil.check_table_name(table + "_coverage")
    concepts = [str(c) for c in payload["concepts"]]
    collection_a = payload["collection_a"]
    collection_b = payload["collection_b"]
    threshold = float(payload.get("threshold") or 0.5)
    method = payload.get("method") or "mask_compare"
    if method != "mask_compare":
        raise RuntimeError(f"method {method!r} not implemented (mask_compare only)")
    area_wkt = payload["area_wkt_3014"]

    sam3_url = os.environ.get("SAM3_URL", "http://host.docker.internal:8200").rstrip("/")
    _check_segmenter(sam3_url)

    # Idempotent for the attempt-2 rerun: clear leftover output from a prior
    # attempt of THIS job only. An existing table without our provenance row
    # was created by someone else after the MCP-side collision check — refuse
    # rather than clobber it (mirrors the ingest _ref_table_claim guard).
    with conn.cursor() as cur:
        for name in (table, cov_table):
            exists = cur.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS present", (f"{schema}.{name}",)
            ).fetchone()["present"]
            if not exists:
                continue
            ours = cur.execute(
                "SELECT 1 FROM app.provenance WHERE kind = 'change_detect'"
                " AND object_ref = %s AND job_id = %s",
                (f"{schema}.{table}", job["id"]),
            ).fetchone()
            if not ours:
                raise RuntimeError(
                    f"table {schema}.{name} was created since this job was"
                    " enqueued — drop it or rerun with another table_name")
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(
                dbutil.qualify(schema, name)))
    conn.commit()

    bbox6, bbox4 = _area_bboxes(conn, area_wkt)
    root_a = _stac_root(conn, collection_a)
    root_b = _stac_root(conn, collection_b)
    conn.commit()

    items_a = _stac_items(root_a, collection_a, bbox4)
    items_b = _stac_items(root_b, collection_b, bbox4)
    meta_a = _vintage_meta(collection_a, items_a)
    meta_b = _vintage_meta(collection_b, items_b)
    proc_gsd = max(meta_a["gsd"], meta_b["gsd"])
    min_area = payload.get("min_area_m2")
    min_area = float(min_area) if min_area else 15.0 * (proc_gsd / 0.16) ** 2

    windows = _window_grid(bbox6, proc_gsd)
    if len(windows) > MAX_TILES:
        raise RuntimeError(
            f"{len(windows)} tiles exceed the {MAX_TILES}-tile cap at "
            f"{proc_gsd} m GSD — shrink the area or split the study into multiple runs")
    windows = _filter_windows(conn, windows, area_wkt)
    conn.commit()
    if not windows:
        raise RuntimeError("area produced no analysis windows")

    statuses = {}
    for w in windows:
        if not _covers(w, items_a):
            statuses[w["tile_id"]] = "missing_a"
        elif not _covers(w, items_b):
            statuses[w["tile_id"]] = "missing_b"
        else:
            statuses[w["tile_id"]] = None

    det_rows, model_info = _infer(sam3_url, windows, {"a": items_a, "b": items_b},
                                  concepts, threshold, statuses,
                                  f"/vsimem/chg_{job['id']}")
    for tid, status in statuses.items():
        if status is None:
            statuses[tid] = "error"

    tile_counts = {}
    for status in statuses.values():
        tile_counts[status] = tile_counts.get(status, 0) + 1
    tiles_analyzed = tile_counts.get("analyzed", 0)
    tiles_skipped = len(windows) - tiles_analyzed
    season_note = f"capture months a={meta_a['months']} b={meta_b['months']}"

    details = {
        "collections": {"a": collection_a, "b": collection_b},
        "items": {"a": [{"id": i["id"], "datetime": i["datetime"]} for i in items_a],
                  "b": [{"id": i["id"], "datetime": i["datetime"]} for i in items_b]},
        "concepts": concepts,
        "threshold": threshold,
        "min_area_m2": min_area,
        "proc_gsd": proc_gsd,
        "model": model_info,
        "tile_counts": tile_counts,
        "season_note": season_note,
    }
    counts = _write_outputs(conn, job, schema, table, cov_table, windows, statuses,
                            det_rows, proc_gsd, min_area, collection_a, collection_b,
                            meta_a, meta_b, details)
    conn.commit()

    warnings = []
    if tiles_skipped:
        warnings.append(
            f"coverage incomplete: {tiles_skipped} of {len(windows)} tiles lack "
            "imagery in one vintage or errored — see the coverage table")
    if _cross_season(meta_a["months"], meta_b["months"]):
        warnings.append("cross-season pair — deciduous shadows can masquerade as change")
    if not det_rows:
        warnings.append(
            "the model detected NOTHING for any concept in either vintage — an empty "
            "diff, not evidence of no change; the model grounds English text only "
            "(e.g. 'building', not 'byggnad'), so translate concepts and re-run")

    result = {
        "table": f"{schema}.{table}",
        "coverage_table": f"{schema}.{cov_table}",
        "tiles_analyzed": tiles_analyzed,
        "tiles_skipped": tiles_skipped,
        "appeared": counts.get("appeared", 0),
        "disappeared": counts.get("disappeared", 0),
        "changed": counts.get("changed", 0),
        "proc_gsd": proc_gsd,
        "vintage_a": {k: meta_a[k] for k in ("collection", "datetime_min",
                                             "datetime_max", "gsd")},
        "vintage_b": {k: meta_b[k] for k in ("collection", "datetime_min",
                                             "datetime_max", "gsd")},
        "model": model_info,
    }
    if warnings:
        result["warning"] = "; ".join(warnings)
    return result

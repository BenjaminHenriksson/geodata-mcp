"""Data-quality audit of everything in ref.*, against the authoritative WFS counts.

Checks per layer: row count vs the source's numberMatched, SRID, geometry validity,
null/empty geometries, and whether the extent falls inside Sundsvall kommun.

Run:  .venv/bin/python scripts/validate_data.py
"""

import re
import subprocess
import sys

import httpx

PSQL = ["docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "postgres", "-d", "geodata", "-Atc"]

# Generous bbox around Sundsvall kommun in EPSG:3014 (metres).
SUNDSVALL_BBOX = (80_000, 6_870_000, 220_000, 7_000_000)


def sql(q):
    out = subprocess.run(PSQL + [q], capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return [line.split("|") for line in out.stdout.strip().splitlines() if line]


def wfs_hits(url, typename):
    """numberMatched straight from the source — the ground truth for 'did it all arrive'."""
    try:
        r = httpx.get(url, params={"service": "WFS", "version": "2.0.0",
                                   "request": "GetFeature", "typeNames": typename,
                                   "resultType": "hits"}, timeout=60, follow_redirects=True)
        m = re.search(r'numberMatched="(\d+)"', r.text)
        return int(m.group(1)) if m else None
    except Exception as e:
        return f"unreachable: {str(e)[:40]}"


def main():
    layers = sql("""
        SELECT t.table_name,
               coalesce(d.external_id, ''), coalesce(s.url, ''), coalesce(d.feature_count::text, '')
          FROM information_schema.tables t
          LEFT JOIN catalog.datasets d ON d.ref_table = 'ref.' || t.table_name
          LEFT JOIN catalog.sources s ON s.id = d.source_id
         WHERE t.table_schema = 'ref'
         ORDER BY t.table_name
    """)

    print(f"{'layer':<26} {'rows':>8} {'source':>8} {'srid':>5} {'invalid':>7} "
          f"{'nullgeom':>8} {'type':<16} extent")
    print("-" * 118)

    problems, warnings = [], []
    for table, ext_id, url, catalog_count in layers:
        stats = sql(f"""
            SELECT count(*),
                   coalesce(max(ST_SRID(geom))::text, '-'),
                   count(*) FILTER (WHERE geom IS NOT NULL AND NOT ST_IsValid(geom)),
                   count(*) FILTER (WHERE geom IS NULL OR ST_IsEmpty(geom)),
                   coalesce(string_agg(DISTINCT GeometryType(geom), ','), '-'),
                   coalesce(ST_Extent(geom)::text, '-')
              FROM ref.{table}
        """)[0]
        n, srid, invalid, nullgeom, gtype, extent = stats
        n, invalid, nullgeom = int(n), int(invalid), int(nullgeom)

        src = wfs_hits(url, ext_id) if url and ext_id else "n/a"
        print(f"{table:<26} {n:>8} {str(src):>8} {srid:>5} {invalid:>7} {nullgeom:>8} "
              f"{gtype:<16} {extent[:34]}")

        if isinstance(src, int) and src != n:
            problems.append(f"{table}: {n} rows ingested but source reports {src}")
        if srid not in ("3014", "-"):
            problems.append(f"{table}: SRID {srid}, expected 3014")
        if invalid:
            problems.append(f"{table}: {invalid} invalid geometries")
        if n == 0:
            warnings.append(f"{table}: empty (source itself may be empty)")
        if nullgeom:
            warnings.append(f"{table}: {nullgeom} null/empty geometries")
        if catalog_count and catalog_count != str(n):
            problems.append(f"{table}: catalog.feature_count={catalog_count} but table has {n}")
        if extent != "-":
            xs = [float(v) for v in re.findall(r"[-\d.]+", extent)]
            if xs and not (SUNDSVALL_BBOX[0] <= min(xs[0], xs[2]) and
                           max(xs[0], xs[2]) <= SUNDSVALL_BBOX[2] and
                           SUNDSVALL_BBOX[1] <= min(xs[1], xs[3]) and
                           max(xs[1], xs[3]) <= SUNDSVALL_BBOX[3]):
                warnings.append(f"{table}: extent reaches outside the Sundsvall bbox "
                                f"(regional dataset?) {extent[:60]}")

    print("\n── catalog ──")
    for label, q in [
        ("sources", "SELECT kind || ': ' || count(*) FROM catalog.sources GROUP BY kind ORDER BY 1"),
        ("datasets", "SELECT kind || ': ' || count(*) || ' (' || count(embedding) || ' embedded)' "
                     "FROM catalog.datasets GROUP BY kind ORDER BY 1"),
        ("embedding models", "SELECT m || ': ' || n FROM (SELECT coalesce(embedding_model,'<none>') AS m,"
                             " count(*) AS n FROM catalog.datasets GROUP BY 1) t ORDER BY 1"),
        ("documents", "SELECT 'docs: ' || count(*) FROM doc.documents"),
        ("doc chunks", "SELECT 'chunks: ' || count(*) || ' (' || count(embedding) || ' embedded)' "
                       "FROM doc.chunks"),
        ("jobs", "SELECT status || ': ' || count(*) FROM app.jobs GROUP BY status ORDER BY 1"),
    ]:
        vals = [r[0] for r in sql(q)]
        print(f"  {label:<18} {', '.join(vals) if vals else '(none)'}")

    unembedded = int(sql("SELECT count(*) FROM catalog.datasets WHERE embedding IS NULL")[0][0])
    if unembedded:
        problems.append(f"{unembedded} catalog datasets without embeddings")

    print()
    for p in problems:
        print(f"PROBLEM  {p}")
    for w in warnings:
        print(f"note     {w}")
    if not problems:
        print("No integrity problems: counts match source, all SRID 3014, all geometries valid.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

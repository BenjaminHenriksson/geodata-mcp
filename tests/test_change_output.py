"""Optional disposable PostGIS check, with no network or production volumes."""
import ast
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from connectors.change_boxes import Candidate


@pytest.mark.skipif(not os.getenv("CHANGE_TEST_DB_IMAGE"), reason="Set a local disposable PostGIS image")
def test_vision_output_preserves_observations_and_projected_geometry(tmp_path):
    # Load just the SQL writer so this test does not require GDAL bindings.
    source = Path(__file__).parents[1] / "services/worker/connectors/change_detect.py"
    function = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_write_vision_candidates")
    namespace = {"sql": sql, "Jsonb": Jsonb}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    statements = ["CREATE EXTENSION postgis;"]

    class Cursor:
        def execute(self, query):
            statements.append(query.as_string() + ";")

        def executemany(self, query, rows):
            fragments = query.as_string().split("%s")
            for row in rows:
                assert len(fragments) == len(row) + 1
                statements.append("".join(f + sql.Literal(v).as_string()
                                          for f, v in zip(fragments, row)) + fragments[-1] + ";")

    observation = {"tile_id": "a", "confidence_label": "high", "evidence": "new roof"}
    row = ("a", "building", "appeared", "new_building", "high", "grass", "roof", "new roof",
           619750, 6921950, 619800, 6922000)
    namespace["_write_vision_candidates"](
        Cursor(), sql.Identifier("changes"), [Candidate(row, [observation, {**observation, "tile_id": "b"}],
                                                            "cross_tile_consensus", True,
                                                            [{**observation, "change_type": "demolition"}])],
        "before", "after", {"datetime_min": None}, {"datetime_min": None}, 1,
        "POLYGON((-10000000 -10000000,10000000 -10000000,10000000 10000000,"
        "-10000000 10000000,-10000000 -10000000))")
    statements.append("SELECT observation_count, array_to_string(source_tiles, ','),"
                      " jsonb_array_length(observations), merge_method, ST_SRID(geom),"
                      " area_m2 > 2400 AND area_m2 < 2600, review_required,"
                      " conflicting_observations->0->>'change_type' FROM changes;")
    name = "geodata-change-test-" + uuid.uuid4().hex[:10]
    tmp_path.chmod(0o755)
    run = lambda *args, **kw: subprocess.run(["docker", *args], check=True, capture_output=True,
                                            text=True, timeout=30, **kw)
    try:
        run("run", "-d", "--pull=never", "--name", name, "--network", "none",
            "--tmpfs", "/var/lib/postgresql/data", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            "-v", f"{tmp_path}:/docker-entrypoint-initdb.d:ro", os.environ["CHANGE_TEST_DB_IMAGE"])
        for _ in range(100):
            try:
                run("exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres")
                break
            except subprocess.CalledProcessError:
                time.sleep(.2)
        else:
            pytest.fail(run("logs", name).stdout)
        try:
            output = run("exec", "-i", name, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-At",
                         input="\n".join(statements)).stdout
        except subprocess.CalledProcessError as exc:
            pytest.fail(exc.stderr)
        assert "2|a,b|2|cross_tile_consensus|3014|t|t|demolition" in output
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)

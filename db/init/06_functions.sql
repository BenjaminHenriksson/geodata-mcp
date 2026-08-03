SET ROLE geodata_app;

-- Geocoding as SQL (§8 of the architecture): trigram search over the ingested address layer.
-- The worker creates/refreshes app.address_points (addr text, geom geometry(Point,3014))
-- after ingesting the municipal address dataset; until then these raise a clear error.

CREATE OR REPLACE FUNCTION app.geocode(q text, max_results int DEFAULT 5)
RETURNS TABLE (address text, geom geometry, score real)
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF to_regclass('app.address_points') IS NULL THEN
    RAISE EXCEPTION 'address layer not loaded yet — ingest the municipal address dataset '
                    '(Adressplats) first; the worker then creates app.address_points';
  END IF;
  RETURN QUERY EXECUTE
    'SELECT addr, geom, similarity(addr, $1)::real AS score
       FROM app.address_points
      WHERE addr % $1 OR addr ILIKE ''%'' || $1 || ''%''
      ORDER BY score DESC LIMIT $2'
    USING q, max_results;
END $$;

CREATE OR REPLACE FUNCTION app.reverse_geocode(x float8, y float8)
RETURNS TABLE (address text, geom geometry, distance_m float8)
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF to_regclass('app.address_points') IS NULL THEN
    RAISE EXCEPTION 'address layer not loaded yet — ingest the municipal address dataset '
                    '(Adressplats) first; the worker then creates app.address_points';
  END IF;
  RETURN QUERY EXECUTE
    'SELECT addr, geom, ST_Distance(geom, ST_SetSRID(ST_MakePoint($1,$2),3014)) AS d
       FROM app.address_points
      WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint($1,$2),3014), 500)
      ORDER BY d LIMIT 1'
    USING x, y;
END $$;

RESET ROLE;

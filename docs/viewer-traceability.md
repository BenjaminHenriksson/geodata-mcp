# Map sources and traceability

MapLibre's **Källor & spårbarhet** control opens recorded sources, processing
steps, linked jobs and relevant activity. Swedish is the default; the panel has
an English toggle. Select a vector feature for its analysis evidence and
document/page references. In the panorama controls, **Källa & kameradata**
opens the selected frame's timestamp, camera GPS and current image-relative
view angles. Origo is unchanged.

The panel distinguishes catalogue update time, imagery vintage and recorded
capture dates. A WMS vintage without an acquisition timestamp is shown as
missing a capture date. Model confidence is an uncalibrated judgement, review
boxes are not surveyed building footprints, and missing/failed coverage does
not establish that there was no change. If a feature carries `review_required`,
both its original observations and `conflicting_observations` remain visible.
The flag requests review; it does not adjudicate demolition versus construction.

Document links come from the selected feature's recorded citations or a
document ID stored on that feature. Page numbers open the corresponding PDF
fragment. Missing citations mean no citation is recorded; the viewer does not
infer whether a permit or exemption exists. Source hashes/versions are displayed
when stored. Source URLs keep only public document/service identifier query
parameters and remove authentication parameters; an unusual query-based source
URL may therefore need an explicit public URL recorded upstream.

## Access and scope

- `GET /v/{view_id}/traceability` exposes summaries of catalogue sources and
  provenance for layers in that map. Provenance must belong to the view's
  workspace, or be shared provenance for an explicitly referenced `ref.*`
  layer. Linked jobs must belong to the same workspace.
- Private activity is returned only to the workspace owner with a valid signed
  viewer session. Possession of a map capability, or another workspace's
  session, does not grant activity access. This panel does not use admin access.
- Activity includes layer queries and MCP calls associated with the displayed
  jobs; it is not a complete map-edit or decision history. It omits raw SQL,
  request bodies, principal identifiers and raw errors.
- `GET /v/{view_id}/feature-evidence?layer=...&key=fid&identity=...` uses the
  existing map/layer capability check. Only `fid` or `id` lookups and allowlisted
  evidence fields are available. A document lookup uses the document ID read
  from that authorized feature, never a separate client-supplied document ID.
- Responses are private and not cached. The panel displays at most 50 processing
  entries and 25 activity entries, with an indication when more are available.
  Nested evidence lists are bounded to 100 entries and text to 4,000 characters.
  Data without a stable feature ID falls back to explicitly labelled rendered
  properties.

The viewer presents recorded information. It cannot recover missing source
lineage, provide an original decision absent from the data, establish legal
status, or calibrate imagery geometry/confidence.

## Focused verification

```sh
uv run --no-sync pytest -q tests/test_viewer_traceability.py tests/test_openapi.py tests/test_renderers.py
node --test tests/viewer_traceability.test.cjs
node --test services/viewer/imagery/lifecycle.test.js
```

Authorization tests use synthetic keys and mocked database responses. They
exercise anonymous, unrelated-user and owner access, reject unlisted layers,
and ensure document references come from the authorized feature. Rendering tests
cover conflicting observations, incomplete coverage, absent citations, source
URL filtering, escaped markup and camera-position limitations.


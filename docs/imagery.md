# Street imagery and 3D demonstrations

The MapLibre viewer can display registered streamed Gaussian splats alongside
existing map layers and GPS panorama routes. Origo is unchanged. Configured sites
are available on every map by default; the location selector also lets users fly
directly to a site. Stockholm city map and 2024 orthophoto backgrounds are included
within their geographic coverage, with source attribution. These WMS sources are
operated by Stockholms stad; public access does not imply an unrestricted reuse
licence. Check the city's current terms before redistributing their imagery.

## Assets and configuration

Set `GEODATA_IMAGERY_CATALOG` in **both viewer and MCP** to a JSON catalogue mounted
read-only. [The example](../deploy/imagery-catalog.example.json) contains the recorded
Gubbängen and Skeppsholmen–Kastellholmen scene registrations. Adjust its filesystem
paths to the explicitly published assets. No scene or photo bytes are committed.

Each site needs `id`, `label`, `published: true`, `bounds` in longitude/latitude,
and an `overview` camera. A panorama site supplies a `manifest` with `frames`, and
`preview_roots` maps allowed first path components to read-only directories. Only
`preview_image` entries are served: never the manifest's original `image` fallback.
The recorded Gubbängen manifest combines `webframes` and `preview`; the island
manifest uses `preview`. Mount only those redacted previews and manifests, not
original videos, full-resolution frames, private metadata or a whole home folder.

A splat site additionally supplies `splat_root`, `origin` (longitude/latitude),
`altitude`, row-major `georeference` and `sogToLocal` 4×4 matrices. Keep the supplied
registration unchanged when mounting the existing SOG trees. `bounds` encloses the
transformed model and controls geographic loading. Scene display altitudes are
existing visual registration references, not surveyed elevations or a vertical
datum conversion. Published sites are shared demonstration data, not private
workspace content; do not include confidential imagery in this catalogue.

The viewer's capability check runs before every catalogue, route, preview, crop
and scene-file request. Resolved paths must remain inside their configured roots;
original-media fallback, directory browsing and symlink escapes are rejected.
MCP uses its normal authentication, workspace resolution and audit path.

## Loading and build

The browser initially requests only the small catalogue. Panorama routes, scene
metadata and the 3D renderer are loaded when a site's bounds intersect the map at
zoom 13 or closer. Panoramas load only when selected. Up to two sites share a
one-million-selected-splat target; chunk concurrency is two per site. That target
is not a strict resident-memory limit: chunks can contain extra source points.
Leaving the area or hiding the tab disposes the renderer and scheduler allocations.
Returning automatically restores the scene. HTTP caches can reuse downloaded
assets; no session warm-up or permanent background GPU loop is required.

Splats use an independent transparent WebGL canvas synchronized with MapLibre.
They do not share a depth buffer with ordinary map layers. Photo points show GPS
positions on the map surface; they are not a calibrated camera-height overlay.

The viewer Dockerfile builds the pinned frontend dependencies and includes their
worker assets and licences. For a source checkout:

```sh
cd services/viewer/imagery
npm ci --ignore-scripts
npm test
npm run build
```

Generated files go to `services/viewer/static/imagery-build/` and are not committed.
The small loader, panorama projection and CSS live in `static/imagery/`. Python
perspective rendering uses Pillow and NumPy in the viewer/MCP environments.

## MCP perspective inspection

Discover the `imagery` processor with `analyze(op='describe', id='imagery')`.

```json
{"operation":"list"}
{"operation":"search","site_id":"skeppsholmen","limit":10}
{"operation":"view","site_id":"skeppsholmen","frame_id":"<id from search>","yaw":90,"pitch":0,"hfov":90,"width":1536,"height":1024}
```

Pass these objects as `params` to `analyze(op='run', id='imagery', params=...)`.
Search supports a geographic `bbox` and `offset` pagination. A view returns a JPEG
**MCP image content block** and structured camera metadata immediately, without a
background job or another vision-model call. The consuming application must pass
MCP images to its model; a text-only tool adapter cannot inspect them.

The image is a pinhole perspective view, not the full equirectangular panorama.
Yaw zero points to the source image's horizontal centre; positive yaw looks right
and positive pitch looks up. Metadata includes pixel-centre intrinsics, the
camera-to-panorama rotation, a pixel-to-ray formula, source GPS and acquisition
time. The interactive panorama and server crops use the same projection.

**Grounding limits:** recorded panorama compass headings are uncalibrated. GPS
course is travel direction and is never substituted for camera heading. Camera
GPS does not locate objects in the image. A ray has no distance; without depth,
heading calibration and a valid ground model, precise object ground coordinates
cannot be returned. Redaction, stitching and occlusion can also obscure evidence.
Use adjacent viewpoints and map layers to corroborate observations, retaining
these uncertainties when reporting findings.

## Verification before a future release

Run Python regression tests and the frontend checks above. With read-only assets
mounted in an isolated test instance, verify: no Stockholm model/image requests
while in Sundsvall; both scene registrations; panorama drag/zoom/next/previous;
perspective crop orientation; background switching; existing map layers and style
refresh; scene disposal when leaving; and an authenticated MCP image reaching the
consumer. Deployment is a separate operation; this feature does not alter a live
service, tunnel, database or asset directory on its own.

// k6 load test for the Geodata MCP v2 viewer service.
//
// Verifies the performance ska-krav:
//   #38  p95 response time < 500 ms
//   #39  >= 1000 concurrent users
//   #41  automated, repeatable load test with a pass/fail threshold
//
// The viewer is the public read path (map pages, styles, GeoJSON/MVT data), so
// it is what a citizen- or officer-facing deployment must hold up under load.
// The script drives a realistic "open a map view" session against the endpoints
// in services/viewer/main.py.
//
// ── Run ──────────────────────────────────────────────────────────────────────
//   Install k6:  https://grafana.com/docs/k6/latest/set-up/install-k6/
//
//   # Smoke (a few VUs), against a running stack:
//   BASE_URL=http://localhost:8080 VIEW_ID=<view> k6 run scripts/loadtest.js
//
//   # Full ska-krav run: 1000 concurrent VUs (#39), p95 < 500 ms (#38):
//   BASE_URL=https://geodata.example.se VIEW_ID=<view> LAYER=ref.naturreservat \
//     VUS=1000 DURATION=5m k6 run scripts/loadtest.js
//
// A VIEW_ID is required for the map endpoints; create one with the `map` tool
// (see scripts/mcp_client.py) and pass a layer that belongs to that view as
// LAYER. Without VIEW_ID the script still exercises /healthz and /metrics so it
// can smoke-test an environment, but the p95 target is meant for the full mix.
//
// ── Parameters (environment variables) ───────────────────────────────────────
//   BASE_URL   base URL of the deployment            (default http://localhost:8080)
//   VIEW_ID    id of a map view to load              (default "" -> health-only mix)
//   LAYER      a vector layer ref within that view   (default ref.naturreservat)
//   RENDERER   maplibre | origo | both               (default both)
//   VUS        peak concurrent virtual users (#39)   (default 50)
//   DURATION   steady-state duration at peak VUs     (default 1m)
//   RAMP       ramp-up/ramp-down duration            (default 30s)
//   P95_MS     p95 threshold in ms (#38)             (default 500)

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = (__ENV.BASE_URL || "http://localhost:8080").replace(/\/$/, "");
const VIEW_ID = __ENV.VIEW_ID || "";
const LAYER = __ENV.LAYER || "ref.naturreservat";
const RENDERER = (__ENV.RENDERER || "both").toLowerCase();
const VUS = parseInt(__ENV.VUS || "50", 10);
const DURATION = __ENV.DURATION || "1m";
const RAMP = __ENV.RAMP || "30s";
const P95_MS = parseInt(__ENV.P95_MS || "500", 10);

// Per-endpoint latency, so a slow endpoint is visible in the summary rather than
// hidden inside the global aggregate.
const pageTrend = new Trend("viewer_page_ms", true);
const styleTrend = new Trend("viewer_style_ms", true);
const dataTrend = new Trend("viewer_data_ms", true);

export const options = {
  scenarios: {
    // Ramp to VUS concurrent users, hold, then ramp down. Set VUS=1000 for #39.
    viewer_load: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: RAMP, target: VUS },
        { duration: DURATION, target: VUS },
        { duration: RAMP, target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    // #38: 95th percentile of ALL requests must stay under the target (500 ms).
    http_req_duration: [`p(95)<${P95_MS}`],
    // A run is only meaningful if requests actually succeed.
    http_req_failed: ["rate<0.01"],
    // Per-endpoint p95 for diagnosis (non-abort so the run still completes).
    "viewer_page_ms": [{ threshold: `p(95)<${P95_MS}`, abortOnFail: false }],
    "viewer_data_ms": [{ threshold: `p(95)<${P95_MS}`, abortOnFail: false }],
  },
};

function getChecked(name, url, trend) {
  const res = http.get(url, { tags: { endpoint: name } });
  if (trend) trend.add(res.timings.duration);
  check(res, {
    [`${name} status ok`]: (r) => r.status === 200 || r.status === 204 || r.status === 304,
    [`${name} under ${P95_MS}ms`]: (r) => r.timings.duration < P95_MS,
  });
  return res;
}

export default function () {
  // Always-available liveness endpoints (also let the script smoke a stack with
  // no VIEW_ID set).
  group("health", () => {
    getChecked("healthz", `${BASE_URL}/healthz`);
  });

  if (!VIEW_ID) {
    getChecked("metrics", `${BASE_URL}/metrics`);
    sleep(1);
    return;
  }

  // A realistic map session: load the page, its style/config, then the layer data.
  const wantMaplibre = RENDERER === "maplibre" || RENDERER === "both";
  const wantOrigo = RENDERER === "origo" || RENDERER === "both";

  group("map_view", () => {
    if (wantMaplibre) {
      getChecked("maplibre_page", `${BASE_URL}/v/${VIEW_ID}`, pageTrend);
      getChecked("style_json", `${BASE_URL}/v/${VIEW_ID}/style.json`, styleTrend);
    }
    if (wantOrigo) {
      getChecked("origo_page", `${BASE_URL}/v/${VIEW_ID}?renderer=origo`, pageTrend);
      getChecked("origo_json", `${BASE_URL}/v/${VIEW_ID}/origo.json`, styleTrend);
    }
  });

  group("layer_data", () => {
    // GeoJSON for the layer (capability-checked against the view server-side).
    getChecked(
      "data_geojson",
      `${BASE_URL}/data/${LAYER}.geojson?view=${VIEW_ID}&limit=2000`,
      dataTrend,
    );
    // One vector tile near the Sundsvall extent (z10). 204 (empty tile) counts
    // as success — it still exercised the full query path.
    getChecked(
      "tile_mvt",
      `${BASE_URL}/tiles/${LAYER}/10/535/287.mvt?view=${VIEW_ID}`,
      dataTrend,
    );
  });

  // Think time between simulated user sessions.
  sleep(Math.random() * 2 + 1);
}

// Emit both the standard end-of-test summary and a machine-readable JSON file so
// results can be archived against the template in docs/lasttest.md.
export function handleSummary(data) {
  return {
    stdout: textSummary(data),
    "loadtest-summary.json": JSON.stringify(data, null, 2),
  };
}

// Minimal text summary (avoids importing from a remote URL, which some locked-down
// k6 environments block). Reports the ska-krav-relevant numbers.
function textSummary(data) {
  const m = data.metrics;
  const dur = m.http_req_duration ? m.http_req_duration.values : {};
  const failed = m.http_req_failed ? m.http_req_failed.values.rate : 0;
  const reqs = m.http_reqs ? m.http_reqs.values.count : 0;
  const p95 = dur["p(95)"] !== undefined ? dur["p(95)"].toFixed(1) : "n/a";
  const p99 = dur["p(99)"] !== undefined ? dur["p(99)"].toFixed(1) : "n/a";
  const avg = dur.avg !== undefined ? dur.avg.toFixed(1) : "n/a";
  const maxVus = m.vus_max ? m.vus_max.values.max : VUS;
  const pass = dur["p(95)"] !== undefined && dur["p(95)"] < P95_MS && failed < 0.01;
  return (
    `\n── Geodata MCP viewer load test ──\n` +
    `  peak VUs (concurrency, #39): ${maxVus}\n` +
    `  total requests:              ${reqs}\n` +
    `  failed rate:                 ${(failed * 100).toFixed(2)} %\n` +
    `  latency avg / p95 / p99:     ${avg} / ${p95} / ${p99} ms\n` +
    `  p95 target (#38):            < ${P95_MS} ms\n` +
    `  RESULT:                      ${pass ? "PASS" : "FAIL"}\n`
  );
}

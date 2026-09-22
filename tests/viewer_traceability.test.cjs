const {test} = require("node:test");
const assert = require("node:assert/strict");
const ui = require("../services/viewer/static/traceability.js");
const model = () => ({
  title:"Testkarta", version:2, sources:[], processing:[], jobs:[],
  audit:{access:"owner_login_required", events:[]}
});

test("absent sources/citations remain absent, and private audit requires owner login", () => {
  const html = ui.renderMap(model(), "sv");
  assert.match(html, /Ingen katalogkälla/);
  assert.match(html, /arbetsytans ägare/);
  assert.match(html, /inte en fullständig historik/);
  assert.doesNotMatch(html, /workspace.*#audit/);
  const feature = ui.renderFeature({properties:{fid:1},layer:"ref.plan"}, "sv");
  assert.match(feature, /Ingen dokument- eller sidreferens/);
  assert.doesNotMatch(feature, /inga tillstånd|no permits/i);
});

test("conflicting classifications are preserved and confidence is not presented as verified", () => {
  const html = ui.renderFeature({layer:"changes", properties:{
    geometry_kind:"bbox", area_m2:450, confidence_label:"high",
    observations:JSON.stringify([
      {tile_id:"a",change_type:"demolition",before:"old pavilion",after:"removed roof",evidence:"roof absent"},
      {tile_id:"b",change_type:"new_building",before:"grass",after:"green surface",evidence:"green rectangle"}
    ]),
  }}, "en");
  assert.match(html, /not a calibrated probability/);
  assert.match(html, /not a surveyed building footprint/);
  assert.match(html, /demolition/);
  assert.match(html, /new_building/);
  assert.match(html, /roof absent/);
  assert.match(html, /green rectangle/);
});

test("sources use safe actual page citations and hostile HTML stays inert", () => {
  const html = ui.renderFeature({layer:"ref.evidence", properties:{
    title:"<img src=x onerror=alert(1)>", document_url:"https://source.test/permit.pdf?key=PRIVATE",
    page:7, evidence:"<script>bad</script>", api_key:"SECRET", unknown_field:"PRIVATE"
  }}, "sv");
  assert.match(html, /href="https:\/\/source.test\/permit.pdf#page=7"/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|<img|PRIVATE|SECRET/);
});

test("incomplete coverage cannot look like confirmed no-change", () => {
  const data = model();
  data.jobs=[{id:1,kind:"change_detect",status:"done",details:{tiles_skipped:2,model:{complete:false}}}];
  assert.match(ui.renderMap(data, "sv"), /misslyckade rutor betyder inte att ingen förändring/);
  assert.match(ui.renderFeature({properties:{tile_id:"r4",status:"error"}}, "en"), /not been successfully analyzed/);
});

test("camera coordinates and relative yaw retain their grounding limits", () => {
  const html = ui.renderFeature({layer:"Recorded scene", properties:{
    frame_id:"frame", captured_at:"2026-09-21T12:00Z", camera_longitude:18, camera_latitude:59,
    relative_yaw_deg:90, pitch_deg:0, hfov_deg:80,
  }}, "en");
  assert.match(html, /GPS locates the camera, not objects/);
  assert.match(html, /relative yaw is not a map bearing/);
  assert.match(html, /2026-09-21/);
});

test("source links reject dangerous schemes and embedded credentials", () => {
  for (const value of ["javascript:alert(1)","file:///private","https://user:secret@host.test/a"])
    assert.equal(ui.safeUrl(value),null);
});

test("potential conflicts require review and preserve opposite observations without adjudication", () => {
  const html = ui.renderFeature({properties:{
    review_required:true,confidence_label:"high",change_type:"demolition",
    conflicting_observations:[null,{tile_id:"other",change_type:"new_building",evidence:"green roof"}],
  }}, "en");
  assert.match(html, /Review required/);
  assert.match(html, /does not decide which is correct/);
  assert.match(html, /Potentially conflicting observations/);
  assert.match(html, /new_building/);
  assert.match(html, /green roof/);
});

test("vintage names never imply an exact capture date", () => {
  const html = ui.renderFeature({properties:{vintage_a:"WMS_2022",vintage_b:"WMS_2024"}}, "en");
  assert.match(html, /Exact acquisition date is missing/);
  assert.match(html, /not a verified capture date/);
});

test("public document endpoint identifiers remain usable while access tokens are removed", () => {
  assert.equal(ui.safeUrl("https://source.test/download?documentId=42&version=2&token=PRIVATE"),
    "https://source.test/download?documentId=42&version=2");
  const html = ui.renderFeature({properties:{document_url:"https://source.test/download?documentId=42#page=2",page:7}}, "en");
  assert.match(html, /documentId=42#page=7/);
});

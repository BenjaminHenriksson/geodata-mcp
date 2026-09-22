const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('services/viewer/static/origo-data.js', 'utf8');

function fixture(fetch) {
  const added = [], reports = [];
  class GeoJSON {
    readFeatures(data) {
      return data.features.map(f => ({
        id: f.id, getId() { return this.id; },
        setId(id) { this.id = id; }, get(name) { return f.properties[name]; }
      }));
    }
  }
  const context = {window: {location: {href: 'https://maps.example/v/test'}},
    Origo: {ol: {format: {GeoJSON}}}, URL, AbortSignal, fetch};
  vm.runInNewContext(code, context);
  const viewer = {getProjectionCode: () => 'EPSG:3014',
    getLayer: () => ({getSource: () => ({addFeatures: features => added.push(...features)})})};
  const layers = [{name: 'buildings', projection: 'EPSG:3014',
    source: '/data/ref.buildings.geojson?view=test&limit=5000'}];
  return {added, reports, run: () => context.window.GeodataOrigo.load(viewer, layers,
    (count, done) => reports.push({count, done}))};
}

test('loads all 20,258 features in bounded pages and reports completion', async () => {
  const offsets = [];
  const app = fixture(async url => {
    const offset = Number(url.searchParams.get('offset'));
    assert.equal(url.searchParams.get('view'), 'test');
    offsets.push(offset);
    return {ok: true, json: async () => ({type: 'FeatureCollection',
      features: Array.from({length: Math.min(5000, 20258 - offset)},
        (_, i) => ({properties: {fid: offset + i}}))})};
  });
  await app.run();
  assert.deepEqual(offsets, [0, 5000, 10000, 15000, 20000]);
  assert.equal(app.added.length, 20258);
  assert.equal(new Set(app.added.map(f => f.id)).size, 20258);
  assert.equal(app.added[0].id, 0);
  assert.deepEqual(app.reports.at(-1), {count: 20258, done: true});
});

test('a failed later page does not report a partial map as complete', async () => {
  const app = fixture(async url => Number(url.searchParams.get('offset')) > 0
    ? {ok: false, status: 503}
    : {ok: true, json: async () => ({type: 'FeatureCollection',
      features: Array.from({length: 5000}, () => ({properties: {}}))})});
  await assert.rejects(app.run(), /503/);
  assert.equal(app.added.length, 5000);
  assert.equal(app.reports.some(r => r.done), false);
});

test('invalid data is rejected instead of appearing as an empty layer', async () => {
  const app = fixture(async () => ({ok: true, json: async () => ({detail: 'error'})}));
  await assert.rejects(app.run(), /Invalid layer response/);
  assert.equal(app.reports.some(r => r.done), false);
});

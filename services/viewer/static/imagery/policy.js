// Cheap geographic gating precedes renderer import, SOG metadata and panoramas.
export function intersects(a, b) {
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
}
export function activeSites(sites, bounds, zoom, minZoom=13) {
  if (zoom < minZoom) return [];
  return sites.filter(site => intersects(site.bounds, bounds)).slice(0, 2);
}
export function routeFeatures(site, frames) {
  const features = [], lines = []; let current = [];
  for (const frame of frames) {
    if (frame.break_before && current.length) { if (current.length > 1) lines.push(current); current = []; }
    const coordinates = [frame.longitude, frame.latitude]; current.push(coordinates);
    features.push({type:'Feature', geometry:{type:'Point', coordinates}, properties:{site_id:site.id, frame_id:frame.id}});
  }
  if (current.length > 1) lines.push(current);
  for (const coordinates of lines) features.unshift({type:'Feature', geometry:{type:'LineString', coordinates}, properties:{site_id:site.id}});
  return features;
}

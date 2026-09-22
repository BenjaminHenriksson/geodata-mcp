/* Progressive loading for Origo's GeoJSON layers. Keep the map usable while
 * fetching bounded pages, and surface failures instead of an empty layer. */
window.GeodataOrigo = {
  async load(viewer, layers, report) {
    const format = new Origo.ol.format.GeoJSON();
    let total = 0;
    report(total, false);
    for (const layer of layers) {
      const source = viewer.getLayer(layer.name).getSource();
      const url = new URL(layer.source, window.location.href);
      const limit = Number(url.searchParams.get("limit"));
      if (!(limit > 0)) throw new Error("Missing page size");
      let offset = 0;
      while (true) {
        url.searchParams.set("offset", String(offset));
        const response = await fetch(url, {signal: AbortSignal.timeout(30000)});
        if (!response.ok) throw new Error("Layer request failed: " + response.status);
        const data = await response.json();
        if (data.type !== "FeatureCollection" || !Array.isArray(data.features)) {
          throw new Error("Invalid layer response");
        }
        const features = format.readFeatures(data, {
          dataProjection: layer.projection,
          featureProjection: viewer.getProjectionCode()
        });
        features.forEach((feature, index) => {
          if (feature.getId() === undefined) {
            feature.setId(feature.get("fid") ?? offset + index);
          }
        });
        source.addFeatures(features);
        offset += data.features.length;
        total += data.features.length;
        report(total, false);
        if (data.features.length < limit) break;
      }
    }
    report(total, true);
  }
};

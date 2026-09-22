import * as THREE from 'three';
import {GaussianSplatRenderer, SogStreamScheduler} from 'gaussian-splat-lite';
import {createMapCameraSync, fitMapOverlay} from './map-camera.js';
import {mercatorFrame, siteTransform, siteBudgets} from './map-sites.js';

// Independent transparent canvas preserves the map/layers. It has no shared
// depth buffer with MapLibre; panorama route markers remain above the splats.
export function createSplats(map, configs, report) {
  const canvas = document.createElement('canvas'); canvas.className = 'imagery-splats';
  map.getCanvasContainer().append(canvas);
  let renderer;
  try { renderer = new THREE.WebGLRenderer({canvas, alpha:true, antialias:false, powerPreference:'high-performance'}); }
  catch (error) { canvas.remove(); throw error; }
  renderer.setClearColor(0, 0); renderer.toneMapping = THREE.NoToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(37, 1, 1, 50000);
  const sync = createMapCameraSync(THREE, {coordinateSystem: renderer.coordinateSystem});
  const localFrame = mercatorFrame(maplibregl, configs[0]);
  const splats = new GaussianSplatRenderer({renderer, renderDepth:false, autoStochastic:false, stochastic:false});
  scene.add(splats);
  const entries = []; let disposed = false, drawing = false, lastReport = 0;
  for (const config of configs) {
    const streaming = new SogStreamScheduler({url:config.dataUrl, splatBudget:1_000_000,
      maxConcurrentLoads:2, maxUploadBytesPerUpdate:4*1024*1024,
      onError: () => { if (!disposed) report('3D-data kunde inte laddas. Kartan och panoramabilderna kan fortfarande användas.'); },
      onChange: () => { if (!disposed) map.triggerRepaint(); }});
    streaming.group.matrixAutoUpdate = false;
    streaming.group.matrix.copy(siteTransform(maplibregl, config, localFrame));
    streaming.group.matrixWorldNeedsUpdate = true;
    scene.add(streaming.group);
    const entry = {config, streaming, bounds:null}; entries.push(entry);
    streaming.initialized.then(() => { if (!disposed) { entry.bounds=streaming.getBoundingBox(); map.triggerRepaint(); } }).catch(() => {});
    streaming.firstRenderable.catch(() => {});
  }
  function render() {
    if (disposed || drawing || document.hidden) return;
    drawing = true;
    try {
      fitMapOverlay(renderer, map.getCanvas());
      if (!sync(camera, map.transform, map.transform.mercatorMatrix, localFrame)) return;
      scene.updateMatrixWorld(true);
      const budgets = siteBudgets(entries, camera, 1_000_000);
      entries.forEach((entry, i) => {
        entry.streaming.group.visible = budgets[i] > 0;
        entry.streaming.splatBudget = Math.max(1, budgets[i]);
        entry.streaming.update(camera);
      });
      renderer.render(scene, camera);
      if (performance.now() - lastReport > 1000) {
        lastReport = performance.now();
        const loading = entries.some(e => !e.bounds || e.streaming.stats.loadingChunks);
        report(loading ? 'Laddar 3D-detaljer…' : '3D och gatubilder · klicka på en blå punkt');
      }
      // No permanent render loop for offscreen sites. Scheduler notifications
      // and camera motion restart rendering when data becomes visible again.
      if (budgets.some(Boolean)) map.triggerRepaint();
    } catch (error) { report('3D-visningen avbröts. Kartan kan fortfarande användas.'); dispose(); console.warn('Imagery renderer:', error); }
    finally { drawing = false; }
  }
  function dispose() {
    if (disposed) return; disposed = true;
    map.off('render', render); entries.forEach(e => { e.streaming.dispose(); e.streaming.group.removeFromParent(); });
    splats.dispose(); renderer.dispose(); renderer.forceContextLoss(); canvas.remove();
  }
  canvas.addEventListener('webglcontextlost',event=>{
    if(disposed)return;
    event.preventDefault();report('3D-visningen förlorade grafikkontexten. Kartan kan fortfarande användas.');dispose();
  });
  map.on('render', render); map.triggerRepaint();
  return {dispose};
}

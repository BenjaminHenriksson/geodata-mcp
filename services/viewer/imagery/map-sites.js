import * as THREE from 'three';

// One fixed local metre frame keeps camera precision independent of the selected place.
export function mercatorFrame(maplibre, config, altitude=config.altitude) {
 const m=maplibre.MercatorCoordinate.fromLngLat(config.origin,altitude), k=m.meterInMercatorCoordinateUnits();
 return new THREE.Matrix4().makeTranslation(m.x,m.y,m.z).multiply(new THREE.Matrix4().makeScale(k,-k,k));
}
export function siteTransform(maplibre, config, commonFrame, altitude=config.altitude) {
 return commonFrame.clone().invert().multiply(mercatorFrame(maplibre,config,altitude))
  .multiply(new THREE.Matrix4().set(...config.georeference))
  .multiply(new THREE.Matrix4().set(...config.sogToLocal));
}
export function nearestSite(configs, center) {
 const cos=Math.cos(center.lat*Math.PI/180);
 return configs.reduce((best,cfg)=>{
  const score=Math.hypot((cfg.origin[0]-center.lng)*cos,cfg.origin[1]-center.lat);
  return !best || score<best.score ? {config:cfg,score} : best;
 },null)?.config;
}
export function siteBudgets(entries,camera,budget) {
 const frustum=new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse),camera.coordinateSystem);
 const visible=entries.map(entry=>!!entry.bounds && !entry.bounds.isEmpty() && frustum.intersectsBox(entry.bounds.clone().applyMatrix4(entry.streaming.group.matrixWorld)));
 const count=visible.filter(Boolean).length;
 let left=budget,remaining=count;
 return visible.map(show=>{
  if(!show)return 0;
  const share=Math.floor(left/remaining--);left-=share;return share;
 });
}
export function combinedStats(entries) {
 const total={};
 for(const {streaming} of entries)for(const [key,value] of Object.entries(streaming.stats))if(typeof value==='number')total[key]=(total[key]||0)+value;
 return total;
}

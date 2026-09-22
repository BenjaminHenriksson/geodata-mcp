import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {activeSites, routeFeatures, stockholmView, SUNDSVALL_OVERVIEW} from '../static/imagery/policy.js';
import {siteBudgets, siteTransform, mercatorFrame} from './map-sites.js';

const sites=[{id:'a',bounds:[18,59.2,18.1,59.3]},{id:'b',bounds:[18,59.3,18.1,59.4]}];
test('Stockholm controls require a nearby camera target and geographic zoom',()=>{
  assert.equal(stockholmView([18.08,59.32],14),true);
  assert.equal(stockholmView([18.08,59.32],8),false);
  assert.equal(stockholmView(SUNDSVALL_OVERVIEW.center,16),false);
  assert.equal(stockholmView([17.5,60.3],16),false,'a far-away pitched horizon must not activate controls');
});
test('Sundsvall and country overview do not activate Stockholm assets',()=>{
  assert.deepEqual(activeSites(sites,[17.1,62.2,17.6,62.6],15),[]);
  assert.deepEqual(activeSites(sites,[10,50,25,70],8),[]);
  assert.deepEqual(activeSites(sites,[18.02,59.21,18.09,59.29],16).map(s=>s.id),['a']);
  assert.equal(activeSites([...sites,...sites],[18,59,19,60],14).length,2);
});
test('route lines break across recording/GPS gaps while every photo remains selectable',()=>{
  const frame=(id,break_before=false)=>({id,longitude:18,latitude:59,break_before});
  const result=routeFeatures(sites[0],[frame('1',true),frame('2'),frame('3',true),frame('4')]);
  assert.equal(result.filter(f=>f.geometry.type==='Point').length,4);
  assert.deepEqual(result.filter(f=>f.geometry.type==='LineString').map(f=>f.geometry.coordinates.length),[2,2]);
});
test('visible sites share one budget; offscreen sites get none',()=>{
  const camera=new THREE.PerspectiveCamera(60,1,.1,100);camera.updateProjectionMatrix();camera.updateMatrixWorld(true);
  const make=x=>({bounds:new THREE.Box3(new THREE.Vector3(-1,-1,-1),new THREE.Vector3(1,1,1)),streaming:{group:{matrixWorld:new THREE.Matrix4().makeTranslation(x,0,-10)}}});
  assert.deepEqual(siteBudgets([make(-2),make(2)],camera,1_000_000),[500_000,500_000]);
  assert.deepEqual(siteBudgets([make(100),make(0)],camera,1_000_000),[0,1_000_000]);
  assert.deepEqual(siteBudgets([make(100),make(200)],camera,1_000_000),[0,0]);
});
test('common frame preserves full independent scene registration',()=>{
  const maplibre={MercatorCoordinate:{fromLngLat([lng,lat],alt){const k=1/(2*Math.PI*6371008.8*Math.cos(lat*Math.PI/180));return{x:(lng+180)/360,y:(180-Math.log(Math.tan(Math.PI/4+lat*Math.PI/360))*180/Math.PI)/360,z:alt*k,meterInMercatorCoordinateUnits:()=>k};}}};
  const identity=new THREE.Matrix4().toArray();
  const a={origin:[18.08,59.26],altitude:120,georeference:identity,sogToLocal:identity};
  const b={origin:[18.09,59.32],altitude:132,georeference:[2,0,0,4,0,0,2,5,0,-2,0,6,0,0,0,1],sogToLocal:identity};
  const common=mercatorFrame(maplibre,a);
  const actual=common.clone().multiply(siteTransform(maplibre,b,common));
  const expected=mercatorFrame(maplibre,b).multiply(new THREE.Matrix4().set(...b.georeference));
  actual.elements.forEach((value,i)=>assert.ok(Math.abs(value-expected.elements[i])<1e-12));
});

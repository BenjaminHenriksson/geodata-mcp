import {test} from 'node:test';
import assert from 'node:assert/strict';
import {attachImagery} from '../static/imagery/index.js';

class Element {
  constructor(tag) { this.tag=tag;this.children=[];this.attributes={};this.value='';this.classList={add(){},remove(){}}; }
  append(...items) { this.children.push(...items);items.forEach(item=>item.parentElement=this); }
  add(option) { this.children.push(option); }
  setAttribute(key,value) { this.attributes[key]=value; }
  getAttribute(key) { return this.attributes[key]; }
  remove() {}
}

test('photo routes attach while basemap tiles are pending and unload after leaving',async()=>{
  const names=['document','Option','maplibregl','fetch','ResizeObserver'];
  const original=Object.fromEntries(names.map(name=>[name,globalThis[name]]));
  const markers=[], requests=[], events=new Map(), sources=new Map(), layers=[], flights=[], container=new Element('map');
  const stage=new Element('stage');stage.append(container);
  let bounds=[18,59.3,18.1,59.4],center=[18.05,59.35];
  const map={
    getContainer:()=>container,resize(){},
    getStyle:()=>({layers}), isStyleLoaded:()=>false,
    getBounds:()=>({getWest:()=>bounds[0],getSouth:()=>bounds[1],getEast:()=>bounds[2],getNorth:()=>bounds[3]}),
    getZoom:()=>16,
    getCenter:()=>({lng:center[0],lat:center[1]}),
    getSource:id=>sources.get(id),addSource(id,source){sources.set(id,{...source,setData(value){this.data=value;}});},
    getLayer:id=>layers.find(layer=>layer.id===id),addLayer(layer){layers.push(layer);},
    setLayoutProperty(id,key,value){layers.find(layer=>layer.id===id).layout[key]=value;},
    on(name,fn){events.set(name,fn);},flyTo(pose){flights.push(pose);},
  };
  try{
    globalThis.ResizeObserver=class {observe(){}disconnect(){}};
    globalThis.document={hidden:false,createElement:tag=>new Element(tag),getElementById:()=>null,addEventListener(){},removeEventListener(){}};
    globalThis.Option=class {constructor(label,value){this.label=label;this.value=value;}};
    globalThis.maplibregl={Marker:class {
      constructor({element}){this.element=element;this.removed=false;element.setAttribute('aria-label','Map marker');markers.push(this);}
      setLngLat(){return this;}addTo(){return this;}getElement(){return this.element;}remove(){this.removed=true;}
    }};
    globalThis.fetch=async url=>{
      requests.push(url);
      return {ok:true,json:async()=>url.endsWith('/catalogue')?{sites:[{id:'island',label:'Ön',bounds,overview:{center:[18.05,59.35]},has_panoramas:true,has_splats:false}],basemaps:{bounds:[17.7,59.2,18.3,59.5],layers:[{id:'map',label:'Stockholmskarta'}]}}:
        {frames:[{id:'one',index:0,longitude:18.04,latitude:59.35,utc:'2026-09-21T12:00:00Z',break_before:true},
                 {id:'two',index:1,longitude:18.05,latitude:59.35,utc:'2026-09-21T12:00:10Z'}]}};
    };
    await attachImagery(map,'capability');
    await new Promise(resolve=>setImmediate(resolve));
    const photos=markers.filter(marker=>marker.element.className==='imagery-photo');
    assert.equal(photos.length,2,'raster loading must not suppress the GPS route');
    assert.match(photos[0].element.getAttribute('aria-label'),/Ön: öppna gatubild 1, 2026-09-21/);
    assert.match(photos[1].element.getAttribute('aria-label'),/gatubild 2/);
    assert.equal(sources.get('demo-imagery-routes').data.features.length,3);
    const navigation=container.children.find(el=>el.className==='imagery-navigation');
    const select=navigation.children[0].children[0],background=navigation.children[1].children[0];
    const controls=container.children.find(el=>el.className==='imagery-controls');
    assert.equal(controls.hidden,false);
    assert.ok(select.children.some(option=>option.label==='Sundsvall'));
    assert.equal(background.value,'carto');
    assert.equal(layers.find(layer=>layer.id==='demo-imagery-background-map').layout.visibility,'none');
    background.value='map';background.onchange();
    assert.equal(layers.find(layer=>layer.id==='demo-imagery-background-map').layout.visibility,'visible');
    background.value='carto';background.onchange();
    assert.equal(layers.find(layer=>layer.id==='demo-imagery-background-map').layout.visibility,'none');
    const travel=stage.children.find(el=>el.className==='imagery-panorama').children[0];
    assert.equal(travel.attributes['aria-label'],'Navigera mellan 360-bilder');
    assert.deepEqual(travel.children.filter(el=>el.tag==='button').map(el=>el.textContent),['← Bakåt','Framåt →','Till kartan']);
    for(let i=0;i<2;i++){select.value='island';select.onchange();assert.equal(select.value,'');}
    assert.equal(flights.length,2,'the same location can be chosen again without another intermediate choice');
    select.value='sundsvall';select.onchange();
    assert.deepEqual(flights.at(-1).center,[17.3069,62.3908]);
    // Keep a misleading horizon viewport over Stockholm; the camera target wins.
    center=[17.3,62.4];events.get('moveend')();
    assert.equal(controls.hidden,true);
    assert.notEqual(navigation.hidden,true,'area/basemap navigation is independently reachable');
    assert.equal(background.children[1].disabled,true);
    assert.ok(photos.every(marker=>marker.removed));
    assert.equal(sources.get('demo-imagery-routes').data.features.length,0);
    assert.ok(requests.every(url=>!url.includes('/splat/')&&!url.includes('/panorama/')));
    events.get('remove')();
  }finally{for(const name of names){if(original[name]===undefined)delete globalThis[name];else globalThis[name]=original[name];}}
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {attachImagery} from '../static/imagery/index.js';

class Element {
  constructor(tag) { this.tag=tag;this.children=[];this.attributes={};this.value=''; }
  append(...items) { this.children.push(...items); }
  add(option) { this.children.push(option); }
  setAttribute(key,value) { this.attributes[key]=value; }
  getAttribute(key) { return this.attributes[key]; }
  remove() {}
}

test('photo routes attach while basemap tiles are pending and unload after leaving',async()=>{
  const names=['document','Option','maplibregl','fetch'];
  const original=Object.fromEntries(names.map(name=>[name,globalThis[name]]));
  const markers=[], requests=[], events=new Map(), sources=new Map(), layers=[], flights=[], container=new Element('map');
  let bounds=[18,59.3,18.1,59.4];
  const map={
    getContainer:()=>container,
    getStyle:()=>({layers}), isStyleLoaded:()=>false,
    getBounds:()=>({getWest:()=>bounds[0],getSouth:()=>bounds[1],getEast:()=>bounds[2],getNorth:()=>bounds[3]}),
    getZoom:()=>16,
    getSource:id=>sources.get(id),addSource(id,source){sources.set(id,{...source,setData(value){this.data=value;}});},
    getLayer:id=>layers.find(layer=>layer.id===id),addLayer(layer){layers.push(layer);},
    on(name,fn){events.set(name,fn);},flyTo(pose){flights.push(pose);},
  };
  try{
    globalThis.document={hidden:false,createElement:tag=>new Element(tag),addEventListener(){},removeEventListener(){}};
    globalThis.Option=class {constructor(label,value){this.label=label;this.value=value;}};
    globalThis.maplibregl={Marker:class {
      constructor({element}){this.element=element;this.removed=false;element.setAttribute('aria-label','Map marker');markers.push(this);}
      setLngLat(){return this;}addTo(){return this;}getElement(){return this.element;}remove(){this.removed=true;}
    }};
    globalThis.fetch=async url=>{
      requests.push(url);
      return {ok:true,json:async()=>url.endsWith('/catalogue')?{sites:[{id:'island',label:'Ön',bounds,overview:{center:[18.05,59.35]},has_panoramas:true,has_splats:false}]}:
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
    const select=container.children[0].children[1];
    for(let i=0;i<2;i++){select.value='island';select.onchange();assert.equal(select.value,'');}
    assert.equal(flights.length,2,'the same location can be chosen again without another intermediate choice');
    bounds=[17.1,62.3,17.5,62.5];events.get('moveend')();
    assert.ok(photos.every(marker=>marker.removed));
    assert.equal(sources.get('demo-imagery-routes').data.features.length,0);
    assert.ok(requests.every(url=>!url.includes('/splat/')&&!url.includes('/panorama/')));
    events.get('remove')();
  }finally{for(const name of names){if(original[name]===undefined)delete globalThis[name];else globalThis[name]=original[name];}}
});

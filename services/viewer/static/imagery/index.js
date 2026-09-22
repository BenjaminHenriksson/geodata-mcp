import {activeSites, routeFeatures} from './policy.js';
import {createPanorama} from './panorama.js';

export async function attachImagery(map, viewId) {
  const base=`/v/${encodeURIComponent(viewId)}/imagery`;
  const get=async path=>{const response=await fetch(`${base}${path}`);if(!response.ok)throw Error('Bilddata kunde inte laddas');return response.json();};
  const {sites,basemaps}=await get('/catalogue');if(!sites.length)return;
  const root=document.createElement('section');root.className='imagery-controls';root.setAttribute('aria-label','3D och gatubilder');
  const title=document.createElement('strong');title.textContent='3D och gatubilder';root.append(title);
  const select=document.createElement('select');select.setAttribute('aria-label','Visa demonstrationsområde');
  select.add(new Option('Välj område…',''));sites.forEach(site=>select.add(new Option(site.label,site.id)));root.append(select);
  const background=document.createElement('select');background.setAttribute('aria-label','Bakgrund i Stockholm');
  for(const item of basemaps?.layers||[])background.add(new Option(item.label,item.id));
  if(basemaps)root.append(background);
  const status=document.createElement('span');status.setAttribute('role','status');status.textContent='Laddas när området visas';root.append(status);
  map.getContainer().append(root);
  const routes=new Map(), scenes=new Map(), pending=new Set(), markers=new Map();
  let renderer=null, rendererKey='', generation=0, removed=false, styleTimer=null, current=null, pano=null;
  const panel=document.createElement('section');panel.className='imagery-panorama';panel.hidden=true;panel.setAttribute('aria-label','Gatubild');
  const image=document.createElement('div');image.className='imagery-panorama-image';panel.append(image);
  const tools=document.createElement('div');tools.className='imagery-panorama-tools';panel.append(tools);
  const button=(label,fn)=>{const el=document.createElement('button');el.type='button';el.textContent=label;el.onclick=fn;tools.append(el);return el;};
  const previous=button('Föregående',()=>navigate(-1)), next=button('Nästa',()=>navigate(1));
  const details=document.createElement('span');tools.append(details);
  const crop=document.createElement('a');crop.textContent='Öppna perspektivbild';crop.target='_blank';crop.rel='noopener';tools.append(crop);
  button('Källa & kameradata',()=>{
    if(current)document.dispatchEvent(new CustomEvent('geodata:imagery-evidence',{
      detail:{site:{id:current.site.id,label:current.site.label},frame:current.frame,camera:pano?.state()||{}}}));
  });
  button('Stäng gatubild',closePanorama);
  const note=document.createElement('p');note.textContent='GPS visar kamerans position. Bildens kompassriktning är okänd; objektens markkoordinater kan inte beräknas utan djupdata.';tools.append(note);
  map.getContainer().append(panel);
  function updateCrop(state) {
    if (!current) return;
    const height=Math.max(64,Math.min(2048,Math.round(1536*image.clientHeight/Math.max(1,image.clientWidth))));
    crop.href=`${base}/${current.site.id}/perspective/${current.frame.id}.jpg?${new URLSearchParams({...state,width:1536,height})}`;
  }
  async function openPanorama(site,frame) {
    current={site,frame};panel.hidden=false;
    details.textContent='Laddar gatubild…';
    const all=routes.get(site.id).frames, index=all.indexOf(frame);previous.disabled=index===0;next.disabled=index===all.length-1;
    try {
      if(!pano)pano=createPanorama(image,updateCrop);
      updateCrop(pano.state());
      if(await pano.load(`${base}/${site.id}/panorama/${frame.id}`))details.textContent=`${site.label} · ${index+1}/${all.length} · ${frame.utc?.slice(0,10)||'Datum saknas'}`;
    }catch(error){if(error.name!=='AbortError')details.textContent=error.message;}
  }
  function navigate(step){if(!current)return;const all=routes.get(current.site.id).frames;const frame=all[all.indexOf(current.frame)+step];if(frame)openPanorama(current.site,frame);}
  function closePanorama(){panel.hidden=true;pano?.dispose();pano=null;current=null;}
  function escape(event){if(event.key==='Escape')closePanorama();}document.addEventListener('keydown',escape);
  select.onchange=()=>{const site=sites.find(s=>s.id===select.value);if(site){closePanorama();map.flyTo({...site.overview,duration:1200});select.value='';}};
  const siteMarkers=sites.map(site=>{
    const element=document.createElement('button');element.type='button';element.className='imagery-site';element.textContent=`${site.label} · 3D/360°`;
    element.onclick=()=>{select.value=site.id;select.onchange();};
    const marker=new maplibregl.Marker({element}).setLngLat(site.overview.center).addTo(map);
    element.setAttribute('aria-label',`Visa ${site.label} i 3D`);element.title=`Visa ${site.label} i 3D`;
    return marker;
  });
  function addBackgrounds(){
    if(!basemaps||removed||!map.getStyle()?.layers)return;
    for(const item of basemaps.layers){
      const id=`demo-imagery-background-${item.id}`;
      if(!map.getSource(id))map.addSource(id,{type:'raster',tiles:[`${base}/basemap/${item.id}/{z}/{x}/{y}.png`],
        bounds:basemaps.bounds,tileSize:256,minzoom:10,maxzoom:20,attribution:basemaps.attribution});
      if(!map.getLayer(id)){
        const before=map.getStyle().layers.find(layer=>!['background','raster'].includes(layer.type))?.id;
        map.addLayer({id,type:'raster',source:id,layout:{visibility:background.value===item.id?'visible':'none'},paint:{'raster-fade-duration':150}},before);
      }
      map.setLayoutProperty(id,'visibility',background.value===item.id?'visible':'none');
    }
  }
  background.onchange=addBackgrounds;
  function visibleSites(minZoom=13) {
    const b=map.getBounds();return activeSites(sites,[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()],map.getZoom(),minZoom);
  }
  function updateRoutes(){
    // isStyleLoaded() also waits for raster tiles. A slow WMS must never block
    // GPS markers after the style itself has been parsed.
    if(removed || !map.getStyle()?.layers)return;
    addBackgrounds();
    const selected=visibleSites(), ids=new Set(selected.map(s=>s.id));
    const data={type:'FeatureCollection',features:selected.flatMap(site=>routes.has(site.id)?routeFeatures(site,routes.get(site.id).frames):[])};
    if(map.getSource('demo-imagery-routes'))map.getSource('demo-imagery-routes').setData(data);
    else map.addSource('demo-imagery-routes',{type:'geojson',data});
    if(!map.getLayer('demo-imagery-route-lines'))map.addLayer({id:'demo-imagery-route-lines',type:'line',source:'demo-imagery-routes',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#177bbe','line-width':2,'line-opacity':.6}});
    for(const [id,list] of markers){if(!ids.has(id)){list.forEach(m=>m.remove());markers.delete(id);}}
    for(const site of selected){
      if(markers.has(site.id) || !routes.has(site.id))continue;
      markers.set(site.id,routes.get(site.id).frames.map(frame=>{
        const el=document.createElement('button');el.type='button';el.className='imagery-photo';
        el.onclick=()=>openPanorama(site,frame);
        const marker=new maplibregl.Marker({element:el}).setLngLat([frame.longitude,frame.latitude]).addTo(map);
        // MapLibre assigns a generic label in Marker(); replace it afterwards.
        el.setAttribute('aria-label',`${site.label}: öppna gatubild ${frame.index+1}, ${frame.utc?.slice(0,10)||'datum saknas'}`);
        el.title=el.getAttribute('aria-label');return marker;
      }));
    }
    siteMarkers.forEach((marker,i)=>marker.getElement().hidden=ids.has(sites[i].id));
  }
  async function update(){
    if(removed||document.hidden)return;
    const selected=visibleSites();
    for(const site of selected){
      if(pending.has(site.id))continue;
      const needed=[];
      if(site.has_panoramas&&!routes.has(site.id))needed.push(get(`/${site.id}/route`).then(value=>routes.set(site.id,value)));
      if(site.has_splats&&!scenes.has(site.id))needed.push(get(`/${site.id}/scene`).then(value=>scenes.set(site.id,value)));
      if(needed.length){pending.add(site.id);Promise.all(needed).then(()=>{pending.delete(site.id);update();}).catch(error=>{pending.delete(site.id);status.textContent=error.message;});}
    }
    updateRoutes();
    const ready=selected.filter(s=>scenes.has(s.id)), key=ready.map(s=>s.id).sort().join(',');
    if(key===rendererKey)return;
    rendererKey=key;const ticket=++generation;renderer?.dispose();renderer=null;
    if(!key){status.textContent='Laddas när området visas';return;}
    status.textContent='Laddar 3D…';
    try{
      const {createSplats}=await import('/static/imagery-build/splats.js');
      if(!removed&&ticket===generation)renderer=createSplats(map,ready.map(s=>scenes.get(s.id)),text=>{status.textContent=text;});
    }catch(error){if(ticket===generation)status.textContent='3D-visningen kunde inte startas. Gatubilder finns vid blå punkter.';console.warn('Imagery:',error);}
  }
  // Style polling may replace overlay layers; restore without replacing map data.
  function styleChanged(){clearTimeout(styleTimer);styleTimer=setTimeout(()=>{if(!map.getSource('demo-imagery-routes')||!map.getLayer('demo-imagery-route-lines')||(basemaps&&!map.getSource('demo-imagery-background-map')))updateRoutes();},100);}
  map.on('moveend',update);map.on('zoomend',update);map.on('styledata',styleChanged);
  // Pause and release GPU allocations when the tab is hidden. Return is automatic.
  function visibility(){if(document.hidden){generation++;renderer?.dispose();renderer=null;rendererKey='';}else update();}
  document.addEventListener('visibilitychange',visibility);
  map.on('remove',()=>{removed=true;generation++;renderer?.dispose();closePanorama();siteMarkers.forEach(m=>m.remove());markers.forEach(list=>list.forEach(m=>m.remove()));clearTimeout(styleTimer);document.removeEventListener('keydown',escape);document.removeEventListener('visibilitychange',visibility);root.remove();panel.remove();});
  update();
}

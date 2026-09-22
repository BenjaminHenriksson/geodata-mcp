// Pinhole projection agrees with geodata_common.imagery.camera/render_view.
// No inference of north from GPS course, and no flattened panorama display.
export function createPanorama(container, changed) {
  const canvas = document.createElement('canvas'); canvas.tabIndex = 0;
  canvas.setAttribute('aria-label', 'Panorama. Dra eller använd piltangenter för att se dig omkring.');
  container.append(canvas);
  const gl = canvas.getContext('webgl', {alpha:false, antialias:false});
  if (!gl) {canvas.remove();throw Error('WebGL saknas för panoramavisning');}
  function shader(type, source) {
    const result = gl.createShader(type); gl.shaderSource(result, source); gl.compileShader(result);
    if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) {gl.deleteShader(result);gl.getExtension('WEBGL_lose_context')?.loseContext();canvas.remove();throw Error('Panoramaprojektionen kunde inte skapas');}
    return result;
  }
  const vertex = shader(gl.VERTEX_SHADER, 'attribute vec2 position; void main(){gl_Position=vec4(position,0.,1.);}');
  const fragment = shader(gl.FRAGMENT_SHADER, `precision highp float;
    uniform sampler2D panorama; uniform vec2 size; uniform float yaw; uniform float pitch; uniform float hfov;
    void main(){
      float focal=size.x/(2.*tan(hfov*.5));
      vec3 ray=normalize(vec3((gl_FragCoord.xy-size*.5)/focal,1.));
      vec3 direction=vec3(cos(yaw)*ray.x-sin(yaw)*sin(pitch)*ray.y+sin(yaw)*cos(pitch)*ray.z,
        cos(pitch)*ray.y+sin(pitch)*ray.z,
        -sin(yaw)*ray.x-cos(yaw)*sin(pitch)*ray.y+cos(yaw)*cos(pitch)*ray.z);
      vec2 uv=vec2(atan(direction.x,direction.z)/6.28318530718+.5, .5-asin(clamp(direction.y,-1.,1.))/3.14159265359);
      gl_FragColor=texture2D(panorama,uv);
    }`);
  const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {gl.deleteProgram(program);gl.deleteShader(vertex);gl.deleteShader(fragment);gl.getExtension('WEBGL_lose_context')?.loseContext();canvas.remove();throw Error('Panoramaprojektionen kunde inte länkas');}
  gl.useProgram(program);
  const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, 'position'); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
  const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  const uniforms=Object.fromEntries(['size','yaw','pitch','hfov'].map(k=>[k,gl.getUniformLocation(program,k)]));
  let yaw=0, pitch=0, hfov=100, loaded=false, disposed=false, request=null, dragging=null;
  function state() { return {yaw,pitch,hfov}; }
  function draw() {
    if (!loaded || disposed) return;
    const width=Math.max(1,Math.round(container.clientWidth*Math.min(devicePixelRatio,2)));
    const height=Math.max(1,Math.round(container.clientHeight*Math.min(devicePixelRatio,2)));
    if (canvas.width!==width || canvas.height!==height) {canvas.width=width;canvas.height=height;}
    gl.viewport(0,0,width,height); gl.uniform2f(uniforms.size,width,height);
    gl.uniform1f(uniforms.yaw,yaw*Math.PI/180); gl.uniform1f(uniforms.pitch,pitch*Math.PI/180); gl.uniform1f(uniforms.hfov,hfov*Math.PI/180);
    gl.drawArrays(gl.TRIANGLES,0,6); changed(state());
  }
  function look(dx,dy) {yaw=((yaw+dx+540)%360)-180;pitch=Math.max(-85,Math.min(85,pitch+dy));draw();}
  canvas.addEventListener('pointerdown',e=>{dragging={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);canvas.focus();});
  canvas.addEventListener('pointermove',e=>{if(!dragging)return;look((dragging.x-e.clientX)*hfov/container.clientWidth,(e.clientY-dragging.y)*hfov/container.clientWidth);dragging={x:e.clientX,y:e.clientY};});
  canvas.addEventListener('pointerup',()=>{dragging=null;});canvas.addEventListener('pointercancel',()=>{dragging=null;});
  canvas.addEventListener('wheel',e=>{e.preventDefault();hfov=Math.max(30,Math.min(120,hfov+Math.sign(e.deltaY)*5));draw();},{passive:false});
  canvas.addEventListener('keydown',e=>{const delta={ArrowLeft:[-5,0],ArrowRight:[5,0],ArrowUp:[0,5],ArrowDown:[0,-5]}[e.key];if(delta){e.preventDefault();look(...delta);}});
  const observer=new ResizeObserver(draw);observer.observe(container);
  return {
    state,
    async load(url) {
      request?.abort();const current=new AbortController();request=current;
      const response=await fetch(url,{signal:current.signal});if(!response.ok)throw Error('Panoramabilden kunde inte hämtas');
      const bitmap=await createImageBitmap(await response.blob());
      if(disposed || request!==current){bitmap.close();return false;}
      if(bitmap.width>gl.getParameter(gl.MAX_TEXTURE_SIZE)){bitmap.close();throw Error('Panoramabilden är för stor för denna webbläsare');}
      gl.bindTexture(gl.TEXTURE_2D,texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,bitmap);bitmap.close();
      loaded=true;draw();return true;
    },
    dispose(){disposed=true;request?.abort();observer.disconnect();gl.deleteTexture(texture);gl.deleteBuffer(buffer);gl.deleteProgram(program);gl.deleteShader(vertex);gl.deleteShader(fragment);gl.getExtension('WEBGL_lose_context')?.loseContext();canvas.remove();},
  };
}

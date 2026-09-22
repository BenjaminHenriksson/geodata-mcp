// Recover the full camera from MapLibre's projection and Mercator matrix,
// retaining its roll at pitch=0 and any principal-point offset from padding.
// The scene uses local Z-up coordinates. Renderers must use
// reversedDepthBuffer:false; call this sync before GSL streaming.update/render.
export function createMapCameraSync(THREE, {
  minimumFar = 50000,
  coordinateSystem = THREE.WebGLCoordinateSystem,
} = {}) {
  if (![THREE.WebGLCoordinateSystem, THREE.WebGPUCoordinateSystem].includes(coordinateSystem)) {
    throw new TypeError('Unsupported camera coordinate system');
  }
  if (!Number.isFinite(minimumFar) || minimumFar < 0) {
    throw new RangeError('minimumFar must be finite and nonnegative');
  }
  const projection = new THREE.Matrix4();
  const view = new THREE.Matrix4();
  const combined = new THREE.Matrix4();
  const topRows = [0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14];
  const finiteMatrix = values => values?.length === 16 && Array.from(values).every(Number.isFinite);

  return function syncCamera(camera, transform, vpMatrix, localToMercator) {
    if (!finiteMatrix(transform?.projectionMatrix) || !finiteMatrix(vpMatrix) ||
        !finiteMatrix(localToMercator?.elements) ||
        !(transform.width > 0 && transform.height > 0 && transform.nearZ > 0 &&
          transform.farZ > transform.nearZ)) return false;
    if (camera.reversedDepth) throw new Error('Map camera requires reversedDepthBuffer:false');

    projection.fromArray(transform.projectionMatrix);
    combined.fromArray(vpMatrix).multiply(localToMercator);
    view.copy(projection).invert().multiply(combined);
    const e = view.elements;
    const scale = (Math.hypot(e[0], e[1], e[2]) +
      Math.hypot(e[4], e[5], e[6]) + Math.hypot(e[8], e[9], e[10])) / 3;
    if (!Number.isFinite(scale) || scale <= 0 || !e.every(Number.isFinite)) return false;

    // Remove pixels/metre from the view and express clipping planes in metres.
    for (const index of topRows) e[index] /= scale;
    e[3] = e[7] = e[11] = 0;
    e[15] = 1;
    const near = transform.nearZ / scale;
    const mapFar = transform.farZ / scale;
    const far = Math.max(mapFar, minimumFar);
    if (!Number.isFinite(near) || !Number.isFinite(far) || !(far > near)) return false;
    projection.elements[14] /= scale;
    if (far > mapFar) {
      // An independent overlay includes ground below the map's shallow far
      // plane. Extend depth alone; horizontal alignment/padding stays exact.
      const range = far - near;
      projection.elements[10] = -(far + near) / range;
      projection.elements[14] = -2 * far * near / range;
    }
    if (coordinateSystem === THREE.WebGPUCoordinateSystem) {
      // MapLibre/WebGL clips Z to [-W,+W]; native WebGPU clips to [0,+W].
      // Only change the depth row: Z'=(Z+W)/2. There is no Y-axis flip.
      const p = projection.elements;
      for (let column = 0; column < 4; column++) {
        const i = column * 4;
        p[i + 2] = 0.5 * (p[i + 2] + p[i + 3]);
      }
    }

    // Both Three and GSL may update camera world matrices. These matrices are
    // explicitly owned by the map, not reconstructed from decomposed values.
    camera.matrixAutoUpdate = false;
    camera.matrixWorldAutoUpdate = false;
    camera.matrix.copy(view).invert();
    camera.matrix.decompose(camera.position, camera.quaternion, camera.scale);
    camera.matrixWorld.copy(camera.matrix);
    camera.matrixWorldInverse.copy(view);
    camera.matrixWorldNeedsUpdate = false;
    camera.fov = transform.fov;
    camera.aspect = transform.width / transform.height;
    camera.near = near;
    camera.far = far;
    // Three r186 otherwise regenerates the projection in _updateCamera,
    // discarding the map's padding. Use renderer.coordinateSystem after init.
    camera.coordinateSystem = coordinateSystem;
    camera.projectionMatrix.copy(projection);
    camera.projectionMatrixInverse.copy(projection).invert();
    return { pixelsPerSceneUnit: scale, near, far, coordinateSystem };
  };
}

// GSL decodes native SOG coordinates without flipping axes. The deployed
// SuperSplat entity uses Rz(pi); its camera uses Q(x,y,z)=(-x,z,y) relative to
// Spark. Keeping the map camera in Spark coordinates requires Q^-1 * Rz(pi),
// exactly (x,y,z)->(x,z,-y), a proper -90-degree X rotation (no reflection).
export function createSogToSparkTransform(THREE) {
  return new THREE.Matrix4().set(
    1, 0, 0, 0,
    0, 0, 1, 0,
    0, -1, 0, 0,
    0, 0, 0, 1,
  );
}

// Drawing buffers are device pixels; overlay layout remains CSS pixels.
export function fitMapOverlay(renderer, mapCanvas, dpr = globalThis.devicePixelRatio ?? 1) {
  const width = mapCanvas.clientWidth, height = mapCanvas.clientHeight;
  if (!(width > 0 && height > 0)) return false;
  const ratio = Math.min(Number.isFinite(dpr) && dpr > 0 ? dpr : 1, 2);
  if (renderer.getPixelRatio() !== ratio) renderer.setPixelRatio(ratio);
  const canvas = renderer.domElement;
  if (canvas.width !== Math.floor(width * ratio) ||
      canvas.height !== Math.floor(height * ratio) ||
      canvas.style.width !== `${width}px` || canvas.style.height !== `${height}px`) {
    renderer.setSize(width, height, true);
  }
  return { width, height, pixelRatio: ratio };
}

import {describe, test} from 'node:test';
import assert from 'node:assert/strict';
const expect = value => ({
  toBe: other => assert.equal(value, other),
  toEqual: other => assert.deepEqual(value, other),
  toBeTruthy: () => assert.ok(value),
  toBeLessThan: other => assert.ok(value < other, `${value} < ${other}`),
  toBeCloseTo: (other, digits) => assert.ok(Math.abs(value - other) < 10 ** -digits / 2),
});
import * as THREE from 'three';
import Renderer from 'three/src/renderers/common/Renderer.js';
import { createMapCameraSync, createSogToSparkTransform, fitMapOverlay } from './map-camera.js';

// Camera fixtures/tests adapted from the existing viewer's tests/camera-sync.test.js.
// Their reference is an independent reconstruction of MapLibre 4.7.1's flat-map
// view, rather than the projection/view decomposition performed by the helper.
const RAD = Math.PI / 180;
const mercator = (lng, lat) => [(lng + 180) / 360,
  (180 - Math.log(Math.tan(Math.PI / 4 + lat * RAD / 2)) / RAD) / 360];
const A = new THREE.Matrix4().set(
  .999339715011, -.000450839425, -.000435959946, 0,
  .000452159953, .999335210949, .003031670189, 0,
  .000434590196, -.003031866846, .999335218148, 0,
  0, 0, 0, 1,
);
const Q = new THREE.Matrix4().set(-1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1);
const RZ_PI = new THREE.Matrix4().set(-1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1);
const difference = (a, b) => Math.max(...a.map((v, i) => Math.abs(v - b[i])));
const clip = (point, matrix) => new THREE.Vector4(...point, 1).applyMatrix4(matrix);
const pixel = (point, matrix, width, height) => {
  const p = clip(point, matrix);
  return [p.x / p.w * width / 2, p.y / p.w * height / 2];
};

function fixture({ pitch = 0, bearing = 0, zoom = 19, width = 1440, height = 900,
  altitude = 120, offsetX = 0, offsetY = 0, shallow = false } = {}) {
  const worldSize = 512 * 2 ** zoom, fov = 36.86989764584402;
  const distance = .5 / Math.tan(fov * RAD / 2) * height;
  const nearZ = height / 50, farZ = distance * (shallow ? 1.01 : 50);
  const projection = new THREE.PerspectiveCamera(fov, width / height, nearZ, farZ).projectionMatrix.clone();
  projection.elements[8] = -offsetX * 2 / width;
  projection.elements[9] = offsetY * 2 / height;
  const center = mercator(18.0898, 59.262), origin = mercator(18.086333288, 59.2678144246);
  const k = 1 / (2 * Math.PI * 6371008.8 * Math.cos(59.2678144246 * RAD));
  const L = new THREE.Matrix4().makeTranslation(origin[0], origin[1], altitude * k)
    .multiply(new THREE.Matrix4().makeScale(k, -k, k)).multiply(A);
  const vp = projection.clone()
    .multiply(new THREE.Matrix4().makeScale(1, -1, 1))
    .multiply(new THREE.Matrix4().makeTranslation(0, 0, -distance))
    .multiply(new THREE.Matrix4().makeRotationX(pitch * RAD))
    .multiply(new THREE.Matrix4().makeRotationZ(-bearing * RAD))
    .multiply(new THREE.Matrix4().makeTranslation(-center[0] * worldSize, -center[1] * worldSize, 0))
    .multiply(new THREE.Matrix4().makeScale(worldSize, worldSize, worldSize));
  return { L, vp, width, height, distance, transform: {
    projectionMatrix: projection.elements, nearZ, farZ, fov, width, height,
  } };
}

describe('SOG basis and MapLibre overlay registration', () => {
  test('raw SOG basis is exactly Q inverse times the deployed SuperSplat rotation', () => {
    const B = createSogToSparkTransform(THREE);
    expect(B.determinant()).toBe(1);
    expect(Q.clone().multiply(B).elements).toEqual(RZ_PI.elements);
    expect(new THREE.Vector3(2, 3, 5).applyMatrix4(B).toArray()).toEqual([2, 5, -3]);
    const superCamera = new THREE.Matrix4().compose(new THREE.Vector3(123, 45, -600),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(.7, -.4, .2)), new THREE.Vector3(1, 1, 1));
    const sparkCamera = Q.clone().invert().multiply(superCamera);
    const projection = new THREE.PerspectiveCamera(37, 16 / 9, 1, 50000).projectionMatrix;
    const superMvp = projection.clone().multiply(superCamera.clone().invert()).multiply(RZ_PI);
    const sparkMvp = projection.clone().multiply(sparkCamera.clone().invert()).multiply(B);
    for (const p of [[0, 0, 0], [100, -100, -600], [-250, 25, 800]]) {
      expect(difference(pixel(p, superMvp, 1280, 720), pixel(p, sparkMvp, 1280, 720))).toBeLessThan(1e-9);
    }
  });

  test('top-down rotations, pitch, zoom, viewport shape, altitude and padding agree in both backends', () => {
    let count = 0, worst = 0;
    for (const coordinateSystem of [THREE.WebGLCoordinateSystem, THREE.WebGPUCoordinateSystem]) {
      const sync = createMapCameraSync(THREE, { coordinateSystem });
      const camera = new THREE.PerspectiveCamera();
      for (const pitch of [0, 1e-6, .1, 45, 85])
      for (let bearing = -180; bearing <= 180; bearing += 30)
      for (const zoom of [14, 19.5, 22])
      for (const [width, height] of [[3440, 1287], [1280, 720], [900, 1440]])
      for (const altitude of [0, 120])
      for (const [offsetX, offsetY] of [[0, 0], [.12 * width, -.08 * height]]) {
        const f = fixture({ pitch, bearing, zoom, width, height, altitude, offsetX, offsetY });
        expect(sync(camera, f.transform, f.vp.elements, f.L)).toBeTruthy();
        expect(camera.coordinateSystem).toBe(coordinateSystem);
        const p = camera.projectionMatrix.elements;
        expect(p[0] > 0 && p[5] > 0 && p[11] === -1).toBe(true);
        expect(p[0]).toBe(f.transform.projectionMatrix[0]);
        expect(p[5]).toBe(f.transform.projectionMatrix[5]);
        expect(p[8]).toBe(f.transform.projectionMatrix[8]);
        expect(p[9]).toBe(f.transform.projectionMatrix[9]);
        expect(Math.abs(p[1]) + Math.abs(p[4])).toBeLessThan(1e-10);
        const reference = f.vp.clone().multiply(f.L), inverseReference = reference.clone().invert();
        const actual = camera.projectionMatrix.clone().multiply(camera.matrixWorldInverse);
        for (const ndc of [[0, 0, 0], [-.8, .7, .3], [.6, -.6, .98]]) {
          const point = new THREE.Vector3(...ndc).applyMatrix4(inverseReference).toArray();
          worst = Math.max(worst, difference(pixel(point, actual, width, height), pixel(point, reference, width, height)));
        }
        expect(camera.near > 0 && camera.far > camera.near).toBe(true);
        count++;
      }
    }
    expect(worst).toBeLessThan(1e-5);
    console.log(`Map camera: ${count} backend/pose cases; worst XY error ${worst.toExponential(3)} CSS px`);
  });

  test('Gaussian projection derivatives remain valid near the top-down singularity', () => {
    for (const coordinateSystem of [THREE.WebGLCoordinateSystem, THREE.WebGPUCoordinateSystem])
    for (const pitch of [0, 1e-6, 45, 85])
    for (const bearing of [-180, -90, 0, 90, 180]) {
      const f = fixture({ pitch, bearing, offsetX: 100, offsetY: -70 });
      const camera = new THREE.PerspectiveCamera();
      createMapCameraSync(THREE, { coordinateSystem })(camera, f.transform, f.vp.elements, f.L);
      const point = [30, -40, -700], step = .01;
      const fx = camera.projectionMatrix.elements[0] * f.width / 2;
      const fy = camera.projectionMatrix.elements[5] * f.height / 2;
      const jacobian = [[-fx / point[2], 0], [0, -fy / point[2]],
        [fx * point[0] / point[2] ** 2, fy * point[1] / point[2] ** 2]];
      for (let axis = 0; axis < 3; axis++) {
        const lo = [...point], hi = [...point]; lo[axis] -= step; hi[axis] += step;
        const a = pixel(lo, camera.projectionMatrix, f.width, f.height);
        const b = pixel(hi, camera.projectionMatrix, f.width, f.height);
        expect(difference(b.map((value, i) => (value - a[i]) / (2 * step)), jacobian[axis])).toBeLessThan(1e-8);
      }
    }
  });
});

describe('graphics backend camera and clipping', () => {
  test('WebGPU maps near/far to 0/1, WebGL to -1/1, with identical XY and frustum coverage', () => {
    const f = fixture({ pitch: 0, bearing: 93, shallow: true, offsetX: 173, offsetY: -97 });
    const cameras = [THREE.WebGLCoordinateSystem, THREE.WebGPUCoordinateSystem].map(coordinateSystem => {
      const camera = new THREE.PerspectiveCamera();
      createMapCameraSync(THREE, { coordinateSystem })(camera, f.transform, f.vp.elements, f.L);
      return camera;
    });
    const [gl, gpu] = cameras;
    expect(gl.far).toBe(50000); expect(gpu.far).toBe(50000);
    for (const [camera, nearDepth] of [[gl, -1], [gpu, 0]]) {
      for (const [distance, expected] of [[camera.near, nearDepth], [camera.far, 1]]) {
        const p = clip([0, 0, -distance], camera.projectionMatrix);
        expect(p.z / p.w).toBeCloseTo(expected, 12);
      }
      expect(difference(camera.projectionMatrix.clone().multiply(camera.projectionMatrixInverse).elements,
        new THREE.Matrix4().elements)).toBeLessThan(1e-12);
    }
    const glFrustum = new THREE.Frustum().setFromProjectionMatrix(gl.projectionMatrix, gl.coordinateSystem);
    const gpuFrustum = new THREE.Frustum().setFromProjectionMatrix(gpu.projectionMatrix, gpu.coordinateSystem);
    for (const point of [[0, 0, -.1 * gl.near], [0, 0, -2 * gl.near], [0, 0, -300],
      [0, 0, -gl.far * 1.1], [1e6, 0, -300]]) {
      expect(glFrustum.containsPoint(new THREE.Vector3(...point)))
        .toBe(gpuFrustum.containsPoint(new THREE.Vector3(...point)));
      expect(difference(pixel(point, gl.projectionMatrix, f.width, f.height),
        pixel(point, gpu.projectionMatrix, f.width, f.height))).toBe(0);
    }
    // Ground below MapLibre's near-ground far plane remains inside the overlay.
    const unextended = new THREE.PerspectiveCamera();
    createMapCameraSync(THREE, { minimumFar: 0 })(unextended, f.transform, f.vp.elements, f.L);
    const below = [0, 0, -(unextended.far + 4)];
    const clipped = clip(below, unextended.projectionMatrix), visible = clip(below, gpu.projectionMatrix);
    expect(clipped.z > clipped.w).toBe(true);
    expect(visible.z >= 0 && visible.z <= visible.w).toBe(true);
  });

  test('actual Three r186 preparation preserves padding and manually managed camera matrices', () => {
    const f = fixture({ pitch: 0, bearing: 132, offsetX: 100, offsetY: -70 });
    for (const coordinateSystem of [THREE.WebGLCoordinateSystem, THREE.WebGPUCoordinateSystem]) {
      const camera = new THREE.PerspectiveCamera();
      createMapCameraSync(THREE, { coordinateSystem })(camera, f.transform, f.vp.elements, f.L);
      const world = camera.matrixWorld.toArray(), projection = camera.projectionMatrix.toArray();
      const rendererState = { xr: { isPresenting: false }, coordinateSystem, reversedDepthBuffer: false };
      Renderer.prototype._updateCamera.call(rendererState, camera, false);
      camera.updateWorldMatrix(true, false); // GSL's own camera update call.
      expect(camera.matrixWorld.elements).toEqual(world);
      expect(camera.projectionMatrix.elements).toEqual(projection);
      // Negative control: the backend really would erase custom padding if
      // we forgot to set camera.coordinateSystem before rendering.
      camera.coordinateSystem = coordinateSystem === THREE.WebGPUCoordinateSystem
        ? THREE.WebGLCoordinateSystem : THREE.WebGPUCoordinateSystem;
      Renderer.prototype._updateCamera.call(rendererState, camera, false);
      expect(camera.projectionMatrix.elements[8]).toBe(0);
      expect(camera.projectionMatrix.elements[9]).toBe(0);
    }
  });
});

describe('map overlay CSS and DPR', () => {
  test('Retina and fractional buffers keep CSS dimensions through resize/backend changes', () => {
    for (const backend of ['webgl', 'webgpu']) {
      const canvas = { width: 300, height: 150, style: {} };
      let ratio = 1, calls = 0;
      const renderer = { backend, domElement: canvas, getPixelRatio: () => ratio,
        setPixelRatio(value) { ratio = value; },
        setSize(width, height, updateStyle) {
          calls++; canvas.width = Math.floor(width * ratio); canvas.height = Math.floor(height * ratio);
          if (updateStyle) { canvas.style.width = `${width}px`; canvas.style.height = `${height}px`; }
        } };
      for (const [width, height] of [[1280, 720], [1312, 816], [857, 611]])
      for (const dpr of [1, 1.25, 2, 3, 1]) {
        const mapCanvas = { clientWidth: width, clientHeight: height };
        fitMapOverlay(renderer, mapCanvas, dpr);
        expect(canvas.style).toEqual({ width: `${width}px`, height: `${height}px` });
        expect([canvas.width, canvas.height]).toEqual([Math.floor(width * Math.min(dpr, 2)), Math.floor(height * Math.min(dpr, 2))]);
        const previousCalls = calls;
        fitMapOverlay(renderer, mapCanvas, dpr);
        expect(calls).toBe(previousCalls);
      }
    }
  });
});

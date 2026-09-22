import {build} from 'esbuild';
import {mkdir, cp, copyFile} from 'node:fs/promises';

const out = new URL('../static/imagery-build/', import.meta.url);
await mkdir(out, {recursive: true});
await build({entryPoints: [new URL('./splats.js', import.meta.url).pathname], outfile: new URL('splats.js', out).pathname,
  bundle: true, minify: true, format: 'esm', target: 'es2022', legalComments: 'linked'});
// Workers/WASM are embedded in this pinned release; retain its companion maps.
await cp(new URL('./node_modules/gaussian-splat-lite/dist/assets/', import.meta.url), new URL('assets/', out), {recursive: true});
for (const [pkg, files] of [['three', ['LICENSE']], ['gaussian-splat-lite', ['LICENSE', 'NOTICE', 'THIRD_PARTY_LICENSES.md']]]) {
  for (const file of files) await copyFile(new URL(`./node_modules/${pkg}/${file}`, import.meta.url), new URL(`${pkg}-${file}`, out));
}

import {readFile, copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const {build} = await import(process.env.ESBUILD_MODULE || 'esbuild');
const root = path.dirname(fileURLToPath(import.meta.url));
await build({entryPoints: [path.join(root, 'engine.ts')], bundle: true, platform: 'node',
  format: 'esm', target: 'node24', outfile: path.join(root, 'engine.mjs'), legalComments: 'inline'});
await copyFile(path.join(root, 'vendor/pakku/pakkujs/assets/similarity-gen.wasm'), path.join(root, 'similarity-gen.wasm'));
await copyFile(path.join(root, 'vendor/pakku/LICENSE.txt'), path.join(root, 'LICENSE.pakku.txt'));

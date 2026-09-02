import { build } from 'esbuild';
import { createServer } from 'node:http';
import { mkdir, readFile, copyFile, readdir } from 'node:fs/promises';
import path from 'node:path';

const output = path.resolve('test-results/viewer');
await mkdir(output, { recursive: true });
const nemotron = process.argv.includes('--nemotron');
let fixturePath;
if (nemotron) {
  fixturePath = 'test-results/models/reviewed-plate/verified-40/preview.glb';
} else {
  const fixtures = await readdir('test-results/python');
  const fixture = fixtures.find((name) => name.startsWith('test_assembly_export_and_insta'));
  if (!fixture)
    throw new Error('Run the Python fixture tests with --basetemp=test-results/python first.');
  fixturePath = path.join('test-results/python', fixture, 'verified/preview.glb');
}
await copyFile(fixturePath, path.join(output, 'fixture.glb'));
await build({
  entryPoints: ['tests/viewer-harness.tsx'],
  outfile: path.join(output, 'bundle.js'),
  bundle: true,
  platform: 'browser',
  format: 'esm',
  jsx: 'automatic',
  minify: true,
  alias: { '@': path.resolve('apps/web/src') },
  define: { 'process.env.NODE_ENV': '"production"', __NEMOTRON__: JSON.stringify(nemotron) },
});
const assets = { '/bundle.js': 'text/javascript', '/fixture.glb': 'model/gltf-binary' };
const server = createServer(async (request, response) => {
  try {
    if (request.url === '/') {
      response.writeHead(200, { 'Content-Type': 'text/html' });
      response.end(
        '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Forma renderer verification</title></head><body style="margin:0"><div id="root"></div><script type="module" src="/bundle.js"></script></body></html>',
      );
    } else if (Object.hasOwn(assets, request.url)) {
      response.writeHead(200, { 'Content-Type': assets[request.url] });
      response.end(await readFile(path.join(output, request.url.slice(1))));
    } else {
      response.writeHead(404).end();
    }
  } catch {
    response.writeHead(500).end();
  }
});
server.listen(3001, '127.0.0.1', () =>
  console.log('Read-only renderer verification: http://127.0.0.1:3001'),
);

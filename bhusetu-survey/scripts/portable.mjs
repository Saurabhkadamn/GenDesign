import { readFile, mkdir, writeFile } from 'node:fs/promises';

// Inline Vite's local bundle so the demo also works by opening one HTML file.
let html = await readFile(new URL('../dist/index.html', import.meta.url), 'utf8');
const script = html.match(/<script[^>]+src="([^"]+)"[^>]*><\/script>/);
const style = html.match(/<link[^>]+href="([^"]+\.css)"[^>]*>/);
if (!script || !style) throw new Error('Vite bundle assets were not found');
const js = await readFile(new URL('../dist' + script[1], import.meta.url), 'utf8');
const css = await readFile(new URL('../dist' + style[1], import.meta.url), 'utf8');
html = html.replace(
  script[0],
  () => '<script type="module">' + js.replace(/<\/script/gi, '<\\/script') + '</script>',
);
html = html.replace(style[0], () => '<style>' + css + '</style>');
await mkdir(new URL('../output/', import.meta.url), { recursive: true });
await writeFile(new URL('../output/BhuSetu-Prototype.html', import.meta.url), html);
console.log('Portable demo: output/BhuSetu-Prototype.html');

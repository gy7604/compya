// Editable vector masters; PNG sizes are deterministic build outputs.
import {readFile,writeFile} from 'node:fs/promises';
import {Resvg} from '@resvg/resvg-js';
const icon=await readFile(new URL('./baseball.svg',import.meta.url));
const badge=await readFile(new URL('./badge.svg',import.meta.url));
for (const [name,source,size] of [['icon-192.png',icon,192],['icon-512.png',icon,512],['icon-180.png',icon,180],['badge-96.png',badge,96]]) {
  const result=new Resvg(source,{fitTo:{mode:'width',value:size}}).render();
  await writeFile(new URL('../public/'+name,import.meta.url),result.asPng());
  console.log(`${name}: ${result.width} x ${result.height}`);
}

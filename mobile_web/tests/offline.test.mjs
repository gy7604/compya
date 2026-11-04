import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
test('offline cache never intercepts commands, login, status or game frames', async () => {
  const handlers={};const caches={match:async () => new Response('offline shell')};
  const source=await readFile(new URL('../public/sw.js.txt',import.meta.url),'utf8');
  vm.runInNewContext(source,{self:{location:{origin:'https://example.test'},addEventListener:(name,fn)=>handlers[name]=fn},URL,Response,caches,fetch:async()=>{throw new Error('offline');}});
  for (const [path,method] of [['/api/login','POST'],['/api/command','POST'],['/api/status','GET'],['/api/screen','GET'],['/?key=private','GET']]) {
    let intercepted=false;handlers.fetch({request:{url:'https://example.test'+path,method},respondWith:()=>intercepted=true});assert.equal(intercepted,false,path);
  }
  let response;handlers.fetch({request:{url:'https://example.test/',method:'GET'},respondWith:value=>response=value});
  assert.equal(await (await response).text(),'offline shell');
});

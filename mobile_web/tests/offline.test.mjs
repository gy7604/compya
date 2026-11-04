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

test('push uses the versioned baseball icon and monochrome badge, ignoring payload image URLs',async()=>{
  const handlers={};let notification,completed;
  const source=await readFile(new URL('../public/sw.js.txt',import.meta.url),'utf8');
  vm.runInNewContext(source,{self:{location:{origin:'https://example.test'},addEventListener:(name,fn)=>handlers[name]=fn,registration:{showNotification:async(title,options)=>{notification={title,...options};}}},URL});
  handlers.push({data:{json:()=>({title:'연결 확인',body:'새 아이콘',icon:'https://example.invalid/untrusted.png',badge:'https://example.invalid/untrusted.png'})},waitUntil:value=>completed=value});
  await completed;
  assert.equal(notification.icon,'/icon-192-v23.png');assert.equal(notification.badge,'/badge-96-v23.png');assert.equal(notification.data.url,'/');
});

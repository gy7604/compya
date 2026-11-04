import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {hash, session, authorized, command} from '../protocol.mjs';
import webpush from 'web-push';
import {createECDH,randomBytes} from 'node:crypto';

let source = await readFile(new URL('../worker.mjs', import.meta.url), 'utf8');
for (const [name, path] of [['PAGE','index.html'], ['STYLE','panel.css'], ['SCRIPT','panel.js.txt'], ['SW','sw.js.txt'], ['MANIFEST','manifest.webmanifest']]) {
  const text = await readFile(new URL('../public/' + path, import.meta.url), 'utf8');
  source = source.replace(new RegExp(`import ${name} from '[^']+';`), `const ${name} = ${JSON.stringify(text)};`);
}
for (const [name, path] of [['ICON192','icon-192.png'], ['ICON512','icon-512.png'],['ICON180','icon-180.png'],['BADGE96','badge-96.png']]) {
  const bytes = await readFile(new URL('../public/' + path, import.meta.url));
  source = source.replace(new RegExp(`import ${name} from '[^']+';`), `const ${name} = new Uint8Array(${JSON.stringify([...bytes])}).buffer;`);
}
source = source.replace("'./protocol.mjs'", JSON.stringify(new URL('../protocol.mjs', import.meta.url).href));
for (const file of ['monitor.mjs','push.mjs']) source=source.replace(`'./${file}'`,JSON.stringify(new URL('../'+file,import.meta.url).href));
await mkdir(new URL('../.wrangler/',import.meta.url),{recursive:true});
const generated=new URL('../.wrangler/control-fixture.mjs',import.meta.url);await writeFile(generated,source);
const {default: worker, ControlRoom} = await import(generated.href);
const root = 'https://compya-mobile-control.example.workers.dev';
async function fixture() {
  const owner = 'owner'.padEnd(40, 'x'), agent = 'agent'.padEnd(40, 'y'), data = new Map();
  const env = {OWNER_KEY_HASH: await hash(owner), AGENT_KEY_HASH: await hash(agent), SESSION_KEY: 'session'.repeat(8)};
  const ctx = {blockConcurrencyWhile: fn => fn(), storage: {get: async key => structuredClone(data.get(key)), put: async (key, value) => data.set(key, structuredClone(value)), delete: async key => data.delete(key)}};
  let transactions=Promise.resolve();const pending=[];
  ctx.storage.transaction=fn=>{const result=transactions.then(()=>fn(ctx.storage));transactions=result.catch(()=>{});return result;};
  ctx.storage.setAlarm=async at=>data.set('alarm',at);ctx.storage.deleteAlarm=async()=>data.delete('alarm');ctx.waitUntil=value=>pending.push(value);
  const flush=async()=>{while(pending.length)await Promise.all(pending.splice(0));};
  const room = new ControlRoom(ctx, env); env.CONTROL = {idFromName: name => name, get: () => room};
  const req = (path, body, headers = {}, method) => worker.fetch(new Request(root + path, {method: method || (body === undefined ? 'GET' : 'POST'),
    headers: {'Content-Type': 'application/json', Origin: root, ...headers}, body: body === undefined ? undefined : JSON.stringify(body)}), env);
  const login = await req('/api/login', {key: owner}), cookie = login.headers.get('Set-Cookie').split(';')[0];
  const boot = crypto.randomUUID(), status = {running: false, can_start: true, mode: '리그', status: '대기', secret: 'never expose'};
  const sync = (extra = {}, key = agent) => req('/api/agent/sync', {boot_id: boot, status, ...extra}, {Authorization: 'Bearer ' + key});
  const browser = (path, body, headers = {}, method) => req(path, body, {Cookie: cookie, ...headers}, method);
  return {req, browser, sync, owner, agent, env, data, boot, room, cookie, flush};
}

test('unauthenticated clients cannot view status, game images, or issue commands', async () => {
  const f = await fixture();
  for (const path of ['/api/status', '/api/screen', '/api/command']) assert.equal((await f.req(path, path.endsWith('command') ? {action:'stop'} : undefined)).status, 401);
  assert.equal((await f.sync({}, 'wrong'.padEnd(40,'x'))).status, 401);
  const response = await f.req('/'); assert.equal(response.status, 200); assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.match(response.headers.get('Content-Security-Policy'), /frame-ancestors 'none'/);
});
test('browser commands and login reject missing or foreign Origin, methods, and arbitrary controls', async () => {
  const f = await fixture(); await f.sync();
  assert.equal((await f.browser('/api/command', {action:'stop'}, {Origin:'https://evil.example'})).status,403);
  assert.equal((await f.req('/api/login',{key:f.owner},{Origin:''})).status,403);
  assert.equal((await f.browser('/api/command',undefined,{},'GET')).status,404);
  assert.equal((await f.browser('/api/command',{id:crypto.randomUUID(),action:'shell'})).status,409);
  assert.equal((await f.browser('/api/files')).status,404);
});
test('cookie is signed, HttpOnly and expires; logout drops session cookie', async () => {
  const f = await fixture(), token = await session(f.env.SESSION_KEY, Date.now() - 31*24*3600_000);
  assert.equal(await authorized(new Request(root,{headers:{Cookie:'compya_session='+token}}),f.env.SESSION_KEY),false);
  assert.equal((await f.req('/api/status',undefined,{Cookie:f.cookie+'tampered'})).status,401);
  const login = await f.req('/api/login',{key:f.owner}); assert.match(login.headers.get('Set-Cookie'),/HttpOnly; Secure; SameSite=Strict/);
  assert.match((await f.browser('/api/logout',{})).headers.get('Set-Cookie'),/Max-Age=0/);
});
test('30-day login is explicit and an existing session can be remembered without reentering key', async () => {
  const f = await fixture();
  assert.match((await f.req('/api/login',{key:f.owner,remember:true})).headers.get('Set-Cookie'),/Max-Age=2592000/);
  assert.match((await f.req('/api/login',{key:f.owner,remember:false})).headers.get('Set-Cookie'),/Max-Age=43200/);
  assert.match((await f.browser('/api/session/remember',{})).headers.get('Set-Cookie'),/Max-Age=2592000/);
  assert.equal((await f.req('/api/session/remember',{})).status,401);
  assert.equal((await f.browser('/api/session/remember',{}, {Origin:'https://evil.example'})).status,403);
});
test('PWA manifest, fresh service worker and PNG icons are served with restrictive policy', async () => {
  const f=await fixture();
  const manifest=await (await f.req('/manifest.webmanifest')).json();assert.equal(manifest.display,'standalone');assert.equal(manifest.start_url,'/');
  for (const [path,size] of [['/icon-192-v23.png',192],['/icon-512-v23.png',512],['/icon-180-v23.png',180],['/badge-96-v23.png',96]]) {
    const image=await f.req(path);assert.equal(image.headers.get('Content-Type'),'image/png');
    const raw=new DataView(await image.arrayBuffer());assert.equal(raw.getUint32(16),size);assert.equal(raw.getUint32(20),size);
  }
  const sw=await f.req('/sw.js');assert.equal(sw.headers.get('Cache-Control'),'no-store');assert.match(await sw.text(),/compya-shell-2.0.23/);
  assert.match((await f.req('/')).headers.get('Content-Security-Policy'),/worker-src 'self'; manifest-src 'self'/);
});
test('reboot requires a one-use, current-boot challenge and typed confirmation', async () => {
  const f=await fixture();await f.sync({status:{startup_ready:true,can_start:true}});
  const id=crypto.randomUUID();assert.equal((await f.browser('/api/command',{id,action:'reboot'})).status,403);
  const challenge=await (await f.browser('/api/reboot-challenge',{})).json();
  assert.equal((await f.browser('/api/command',{id,action:'reboot',challenge:challenge.id,confirmation:'PC 재부팅'})).status,202);
  assert.equal((await f.browser('/api/command',{id,action:'reboot',challenge:challenge.id,confirmation:'PC 재부팅'})).status,200);
  assert.equal((await f.browser('/api/command',{id:crypto.randomUUID(),action:'reboot',challenge:challenge.id,confirmation:'PC 재부팅'})).status,403);
  await f.sync({status:{reboot_pending:true,startup_ready:true}});
  assert.equal((await f.browser('/api/command',{id:crypto.randomUUID(),action:'reboot_cancel'})).status,202);
});
test('login failure rate is bounded', async () => {
  const f = await fixture(); for(let i=0;i<8;i++) assert.equal((await f.req('/api/login',{key:'wrong'})).status,401);
  assert.equal((await f.req('/api/login',{key:f.owner})).status,429);
});
test('commands require a current PC, are idempotent, and acknowledgements do not replay', async () => {
  const f = await fixture(), id = crypto.randomUUID();
  assert.equal((await f.browser('/api/command',{id,action:'start'})).status,409);
  await f.sync(); assert.equal((await f.browser('/api/command',{id,action:'start'})).status,202);
  assert.equal((await f.browser('/api/command',{id,action:'start'})).status,200);
  assert.equal(f.data.get('commands').length,1);
  const pending = await (await f.sync()).json(); assert.equal(pending.command.boot_id,f.boot);
  assert.equal((await (await f.sync({acks:[{id,state:'done',message:'시작됨'}]})).json()).command,null);
  assert.equal((await (await f.browser('/api/status')).json()).command.state,'done');
});
test('stop supersedes pending start; expired requests never become fresh after reconnect', async () => {
  const f = await fixture(); await f.sync();
  const id=crypto.randomUUID(); await f.browser('/api/command',{id,action:'start'});
  await f.browser('/api/command',{id:crypto.randomUUID(),action:'stop'});
  assert.equal(f.data.get('commands')[0].state,'superseded');
  f.data.get('commands')[1].expires=Date.now()/1000-1;
  assert.equal((await (await f.sync()).json()).command,null);
});
test('another live instance cannot take control of the current PC; new boot cancels old command', async () => {
  const f = await fixture(); await f.sync(); await f.browser('/api/command',{id:crypto.randomUUID(),action:'start'});
  assert.equal((await f.sync({boot_id:crypto.randomUUID()})).status,409);
  f.room.last=0;
  assert.equal((await (await f.sync({boot_id:crypto.randomUUID()})).json()).command,null);
  assert.equal(f.data.get('commands')[0].state,'expired');
});
test('only fresh JPEGs are served and screen bytes never enter persistent storage', async () => {
  const f = await fixture(); await f.sync({screen:'/9j/2Q==',screen_at:Date.now()/1000});
  const image=await f.browser('/api/screen');assert.equal(image.headers.get('Content-Type'),'image/jpeg');
  assert.equal((await image.arrayBuffer()).byteLength,4);
  assert.equal(JSON.stringify([...f.data.values()]).includes('/9j/'),false);
  f.room.screen.at=Date.now()-60_000;assert.equal((await f.browser('/api/screen')).status,404);
  const status=await (await f.browser('/api/status')).json();assert.equal(status.status.secret,undefined);
});
test('malformed and oversized messages fail closed', async () => {
  const f = await fixture();
  assert.equal((await f.req('/api/login',{key:'x'.repeat(5000)})).status,400);
  assert.equal((await f.sync({screen:'x'.repeat(270000)})).status,400);
  delete f.env.SESSION_KEY;assert.equal((await f.req('/api/status')).status,503);
});

function pushSetup(f) {
  const vapid=webpush.generateVAPIDKeys(), pair=createECDH('prime256v1');pair.generateKeys();
  f.env.VAPID_PUBLIC_KEY=vapid.publicKey;f.env.VAPID_PRIVATE_KEY=vapid.privateKey;
  return {endpoint:'https://fcm.googleapis.com/fcm/send/test-device',keys:{p256dh:pair.getPublicKey().toString('base64url'),auth:randomBytes(16).toString('base64url')}};
}
const records=()=>({schema:1,quality:true,faults:[],ranking:[],seasons:[]});
test('subscription and history are owner-only; endpoints and encryption keys never enter history',async t=>{
  const f=await fixture(), subscription=pushSetup(f);
  assert.equal((await f.req('/api/push/subscribe',subscription)).status,401);
  assert.equal((await f.req('/api/notifications')).status,401);
  assert.equal((await f.browser('/api/push/subscribe',subscription)).status,200);
  assert.equal((await (await f.browser('/api/push/check',{endpoint:subscription.endpoint})).json()).registered,true);
  t.mock.method(globalThis,'fetch',async()=>new Response(null,{status:201}));
  assert.equal((await f.browser('/api/push/test',{})).status,200);await f.flush();
  const history=await (await f.browser('/api/notifications')).text();assert.ok(!history.includes(subscription.endpoint));assert.ok(!history.includes(subscription.keys.auth));assert.match(history,/accepted/);
  assert.equal((await f.browser('/api/push/test',{})).status,429);
  await f.browser('/api/push/unsubscribe',{endpoint:subscription.endpoint});assert.equal((await (await f.browser('/api/push/config')).json()).devices,0);
});
test('server collects records and queues one encrypted push even when phone page is never opened',async t=>{
  const f=await fixture(), subscription=pushSetup(f);await f.browser('/api/push/subscribe',subscription);
  let calls=0;t.mock.method(globalThis,'fetch',async()=>{calls++;return new Response(null,{status:201});});
  const telemetry=records();await f.sync({status:{running:true},telemetry});
  telemetry.seasons.push({id:'verified-season',completed:144,at:new Date().toISOString()});
  await f.sync({status:{running:true},telemetry});await f.flush();
  assert.equal(calls,1);assert.equal(f.data.get('notices')[0].type,'league_done');
  await f.sync({status:{running:true},telemetry});await f.flush();assert.equal(calls,1);
});
test('concurrent event collection cannot lose notices while another push is in flight',async t=>{
  const f=await fixture(), subscription=pushSetup(f);await f.browser('/api/push/subscribe',subscription);
  let release, calls=0;const gate=new Promise(resolve=>release=resolve);
  t.mock.method(globalThis,'fetch',async()=>{calls++;if(calls===1)await gate;return new Response(null,{status:201});});
  const telemetry=records();telemetry.faults.push({id:'run-a',kind:'macro_error',at:new Date().toISOString()});await f.sync({telemetry});
  for(let i=0;!calls && i<100;i++)await new Promise(resolve=>setTimeout(resolve,1));
  assert.ok(calls,'Push request did not reach the provider stub');
  telemetry.faults.push({id:'run-b',kind:'player_error',at:new Date().toISOString()});await f.sync({telemetry});release();await f.flush();
  assert.equal(calls,2);assert.equal(f.data.get('notices').length,2);
  assert.ok(f.data.get('notices').every(notice=>Object.values(notice.deliveries).includes('accepted')));
});
test('a timed-out push is not automatically duplicated; expired subscriptions are removed',async t=>{
  const f=await fixture(), subscription=pushSetup(f);await f.browser('/api/push/subscribe',subscription);
  let calls=0;const mock=t.mock.method(globalThis,'fetch',async()=>{calls++;throw new Error('ambiguous network timeout');});
  const telemetry=records();telemetry.faults.push({id:'run-one',kind:'macro_error',at:new Date().toISOString()});await f.sync({telemetry});await f.flush();
  await f.sync({telemetry});await f.flush();assert.equal(calls,1);assert.ok(Object.values(f.data.get('notices')[0].deliveries).includes('unknown'));
  mock.mock.mockImplementation(async()=>new Response(null,{status:410}));
  telemetry.faults.push({id:'run-two',kind:'macro_error',at:new Date().toISOString()});await f.sync({telemetry});await f.flush();assert.equal(Object.keys(f.data.get('subscriptions')).length,0);
});
test('durable alarm detects lost PC once, while intentional closure suppresses it',async()=>{
  const f=await fixture();await f.sync({status:{running:true}});
  const health=f.data.get('health');health.last=Date.now()-121000;f.data.set('health',health);
  await f.room.alarm();await f.flush();assert.equal(f.data.get('notices')[0].type,'offline');
  await f.room.alarm();await f.flush();assert.equal(f.data.get('notices').length,1);
  await f.sync({status:{running:false,closing:true}});assert.equal(f.data.get('health').watch,false);assert.equal(f.data.has('alarm'),false);
});

test('provider rejection is visible to the owner while private delivery metadata remains hidden',async t=>{
  const f=await fixture(), subscription=pushSetup(f);await f.browser('/api/push/subscribe',subscription);
  let calls=0;t.mock.method(globalThis,'fetch',async()=>{calls++;return new Response(null,{status:403});});
  const response=await f.browser('/api/push/test',{}), sent=await response.json();assert.match(sent.id,/^test:/);await f.flush();
  const history=await (await f.browser('/api/notifications')).text();
  assert.match(history,/HTTP 403/);assert.ok(!history.includes(subscription.endpoint));assert.ok(!history.includes(subscription.keys.auth));assert.ok(!history.includes('delivery_details'));
  await f.flush();assert.equal(calls,1);
});

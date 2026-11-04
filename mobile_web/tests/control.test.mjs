import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {hash, session, authorized, command} from '../protocol.mjs';

let source = await readFile(new URL('../worker.mjs', import.meta.url), 'utf8');
for (const [name, path] of [['PAGE','index.html'], ['STYLE','panel.css'], ['SCRIPT','panel.js.txt']]) {
  const text = await readFile(new URL('../public/' + path, import.meta.url), 'utf8');
  source = source.replace(new RegExp(`import ${name} from '[^']+';`), `const ${name} = ${JSON.stringify(text)};`);
}
source = source.replace("'./protocol.mjs'", JSON.stringify(new URL('../protocol.mjs', import.meta.url).href));
const {default: worker, ControlRoom} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const root = 'https://compya-mobile-control.example.workers.dev';
async function fixture() {
  const owner = 'owner'.padEnd(40, 'x'), agent = 'agent'.padEnd(40, 'y'), data = new Map();
  const env = {OWNER_KEY_HASH: await hash(owner), AGENT_KEY_HASH: await hash(agent), SESSION_KEY: 'session'.repeat(8)};
  const ctx = {blockConcurrencyWhile: fn => fn(), storage: {get: async key => structuredClone(data.get(key)), put: async (key, value) => data.set(key, structuredClone(value)), delete: async key => data.delete(key)}};
  const room = new ControlRoom(ctx, env); env.CONTROL = {idFromName: name => name, get: () => room};
  const req = (path, body, headers = {}, method) => worker.fetch(new Request(root + path, {method: method || (body === undefined ? 'GET' : 'POST'),
    headers: {'Content-Type': 'application/json', Origin: root, ...headers}, body: body === undefined ? undefined : JSON.stringify(body)}), env);
  const login = await req('/api/login', {key: owner}), cookie = login.headers.get('Set-Cookie').split(';')[0];
  const boot = crypto.randomUUID(), status = {running: false, can_start: true, mode: '리그', status: '대기', secret: 'never expose'};
  const sync = (extra = {}, key = agent) => req('/api/agent/sync', {boot_id: boot, status, ...extra}, {Authorization: 'Bearer ' + key});
  const browser = (path, body, headers = {}, method) => req(path, body, {Cookie: cookie, ...headers}, method);
  return {req, browser, sync, owner, agent, env, data, boot, room, cookie};
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
  const f = await fixture(), token = await session(f.env.SESSION_KEY, Date.now() - 13*3600_000);
  assert.equal(await authorized(new Request(root,{headers:{Cookie:'compya_session='+token}}),f.env.SESSION_KEY),false);
  assert.equal((await f.req('/api/status',undefined,{Cookie:f.cookie+'tampered'})).status,401);
  const login = await f.req('/api/login',{key:f.owner}); assert.match(login.headers.get('Set-Cookie'),/HttpOnly; Secure; SameSite=Strict/);
  assert.match((await f.browser('/api/logout',{})).headers.get('Set-Cookie'),/Max-Age=0/);
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

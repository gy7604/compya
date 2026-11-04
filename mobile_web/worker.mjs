import PAGE from './public/index.html';
import STYLE from './public/panel.css';
import SCRIPT from './public/panel.js.txt';
import SW from './public/sw.js.txt';
import MANIFEST from './public/manifest.webmanifest';
import ICON192 from './public/icon-192.png';
import ICON512 from './public/icon-512.png';
import {observeRecords,observeHealth,offlineEvent,OFFLINE_AFTER} from './monitor.mjs';
import {validateSubscription,sendPush,deliverySummary} from './push.mjs';
import {AGE, SESSION_AGE, SHORT_SESSION_AGE, hash, equal, session, authorized, sameOrigin, body, cleanStatus, command} from './protocol.mjs';

const json = (value, status = 200, headers = {}) => Response.json(value, {status, headers});
const headers = {
  'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'DENY', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Content-Security-Policy': "default-src 'none'; script-src 'self'; worker-src 'self'; manifest-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'Strict-Transport-Security': 'max-age=31536000'
};
const cookie = (value, age) => `compya_session=${value}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=${age}`;
const assets = {'/': [PAGE, 'text/html; charset=utf-8'], '/panel.css': [STYLE, 'text/css; charset=utf-8'],
  '/panel.js': [SCRIPT, 'text/javascript; charset=utf-8'], '/sw.js': [SW, 'text/javascript; charset=utf-8'],
  '/manifest.webmanifest': [MANIFEST, 'application/manifest+json'], '/icon-192.png': [ICON192, 'image/png'], '/icon-512.png': [ICON512, 'image/png']};

export default {
  async fetch(request, env) {
    let response;
    try {
      const path = new URL(request.url).pathname;
      if (request.method === 'GET' && (assets[path] || path === '/favicon.ico')) {
        response = path === '/favicon.ico' ? new Response(null, {status: 204}) : new Response(assets[path][0], {headers: {'Content-Type': assets[path][1]}});
      } else if (!env.OWNER_KEY_HASH || !env.AGENT_KEY_HASH || !env.SESSION_KEY) {
        response = json({error: '모바일 연결 준비 중입니다.'}, 503);
      } else if (path === '/api/agent/sync' && request.method === 'POST') {
        const key = request.headers.get('Authorization')?.match(/^Bearer ([A-Za-z0-9_-]{32,80})$/)?.[1];
        if (!key || !equal(await hash(key), env.AGENT_KEY_HASH)) response = json({error: 'Unauthorized'}, 401);
        else response = await env.CONTROL.get(env.CONTROL.idFromName('owner')).fetch(request);
      } else if (path.startsWith('/api/') && ['GET', 'POST'].includes(request.method)) {
        if (request.method === 'POST' && !sameOrigin(request)) response = json({error: '다시 접속해 주세요.'}, 403);
        else if (path === '/api/login' && request.method === 'POST') response = await env.CONTROL.get(env.CONTROL.idFromName('owner')).fetch(request);
        else if (!await authorized(request, env.SESSION_KEY)) response = json({error: '로그인이 필요합니다.'}, 401);
        else if (path === '/api/session/remember' && request.method === 'POST') response = json({ok: true}, 200, {'Set-Cookie': cookie(await session(env.SESSION_KEY), SESSION_AGE)});
        else if (path === '/api/logout' && request.method === 'POST') response = json({ok: true}, 200, {'Set-Cookie': 'compya_session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0'});
        else response = await env.CONTROL.get(env.CONTROL.idFromName('owner')).fetch(request);
      } else response = json({error: 'Not found'}, 404);
    } catch { response = json({error: '요청을 처리하지 못했습니다. 상태를 확인한 뒤 다시 시도하세요.'}, 400); }
    const secured = new Response(response.body, response); for (const [key, value] of Object.entries(headers)) secured.headers.set(key, value);
    return secured;
  }
};

export class ControlRoom {
  constructor(ctx, env) {
    this.ctx = ctx; this.env = env; this.last = 0; this.status = null; this.boot = null; this.viewing = 0; this.screen = null;
    this.delivering=false;
  }
  async appendNotices(events,observed,health) {
    await this.ctx.storage.transaction(async tx=>{
      const notices=await tx.get('notices') || [], subscriptions=await tx.get('subscriptions') || {};
      for (const event of events) {
        if (!notices.some(item=>item.id===event.id)) notices.push({...event,deliveries:Object.fromEntries(Object.keys(subscriptions).map(id=>[id,'pending']))});
      }
      if (events.length) await tx.put('notices',notices.slice(-64));
      if (observed) await tx.put('observed',observed);
      if (health) await tx.put('health',health);
    });
    if (events.length) this.ctx.waitUntil(this.deliver());
  }
  async deliveryState(eventId,id,value,claim=false,detail=null) {
    return this.ctx.storage.transaction(async tx=>{
      const notices=await tx.get('notices') || [], notice=notices.find(item=>item.id===eventId);
      if (!notice || (claim && notice.deliveries[id]!=='pending')) return false;
      notice.deliveries[id]=value;
      if (detail) {notice.delivery_details ||= {};notice.delivery_details[id]=detail;}
      await tx.put('notices',notices);return true;
    });
  }
  async deliver() {
    if (this.delivering) return;
    this.delivering=true;
    try {
      const store=this.ctx.storage, notices=await store.get('notices') || [];
      for (const notice of notices) for (const [id,state] of Object.entries(notice.deliveries || {})) {
        if (state!=='pending') continue;
        const subscriptions=await store.get('subscriptions') || {}, subscription=subscriptions[id];
        if (!subscription) {await this.deliveryState(notice.id,id,'removed');continue;}
        if (!await this.deliveryState(notice.id,id,'sending',true)) continue;
        let outcome='unknown', detail=null;
        try {
          const status=await sendPush(subscription,notice,this.env);
          detail={status};
          outcome=status>=200 && status<300?'accepted':status===404 || status===410?'expired':'failed';
          if (status===404 || status===410) await store.transaction(async tx=>{const current=await tx.get('subscriptions') || {};delete current[id];await tx.put('subscriptions',current);});
        } catch(error) {detail={reason:['prepare','timeout','network'].includes(error?.pushReason)?error.pushReason:'network'};}
        await this.deliveryState(notice.id,id,outcome,false,detail);
      }
    } finally {this.delivering=false;}
    const current=await this.ctx.storage.get('notices') || [];
    if (current.some(item=>Object.values(item.deliveries || {}).includes('pending'))) this.ctx.waitUntil(this.deliver());
  }
  async alarm() {return this.ctx.blockConcurrencyWhile(()=>this.handleAlarm());}
  async handleAlarm() {
    const store=this.ctx.storage, health=await store.get('health'), event=offlineEvent(health,Date.now());
    if (event) {health.notified=true;await this.appendNotices([event],null,health);}
    else if (health?.watch && !health.notified) await store.setAlarm(health.last+OFFLINE_AFTER);
  }
  async fetch(request) {
    return this.ctx.blockConcurrencyWhile(async () => this.handle(request));
  }
  async handle(request) {
    const path = new URL(request.url).pathname, now = Date.now(), store = this.ctx.storage;
    if (this.screen && now - this.screen.at > AGE) this.screen = null;
    if (path === '/api/login' && request.method === 'POST') {
      const attempts = await store.get('login') || {count: 0, until: 0};
      if (attempts.until > now && attempts.count >= 8) return json({error: '로그인 시도가 많습니다. 15분 뒤 다시 시도하세요.'}, 429);
      const data = await body(request);
      if (typeof data.key !== 'string' || !/^[A-Za-z0-9_-]{32,80}$/.test(data.key) || !equal(await hash(data.key), this.env.OWNER_KEY_HASH)) {
        await store.put('login', {count: attempts.until > now ? attempts.count + 1 : 1, until: attempts.until > now ? attempts.until : now + 900_000});
        return json({error: '연결 암호를 확인하세요.'}, 401);
      }
      await store.delete('login');
      const age = data.remember === true ? SESSION_AGE : SHORT_SESSION_AGE;
      return json({ok: true}, 200, {'Set-Cookie': cookie(await session(this.env.SESSION_KEY, now, age), age)});
    }
    if (path === '/api/agent/sync' && request.method === 'POST') {
      const data = await body(request, 260_000);
      if (!/^[a-f0-9-]{36}$/.test(data.boot_id || '')) return json({error: 'Invalid agent'}, 400);
      if (this.boot && this.boot !== data.boot_id && now - this.last < AGE) return json({error: 'Another PC instance is connected'}, 409);
      const status = cleanStatus(data.status);
      this.boot = data.boot_id; this.status = status; this.last = now;
      const health=observeHealth(await store.get('health'),status,data.boot_id,now);
      health.collection_quality=data.telemetry?.quality === true;
      await store.put('health',health);
      if (health.watch) await store.setAlarm(now+OFFLINE_AFTER);else await store.deleteAlarm();
      const oldObserved=await store.get('observed'), observed=observeRecords(oldObserved,data.telemetry,now);
      await this.appendNotices(observed.events,data.telemetry && JSON.stringify(oldObserved)!==JSON.stringify(observed.state) ? observed.state : null);
      if (status.closing) this.boot=null;
      if (typeof data.screen === 'string' && data.screen.length < 240_000 && data.screen.startsWith('/9j/') &&
          Number.isFinite(data.screen_at) && Math.abs(now - data.screen_at * 1000) < 15_000) {
        this.screen = {bytes: data.screen, at: data.screen_at * 1000};
      }
      const queue = await store.get('commands') || [];
      const oldQueue=JSON.stringify(queue);
      for (const receipt of Array.isArray(data.acks) ? data.acks.slice(-32) : []) {
        const item = queue.find(value => value.id === receipt.id && value.boot_id === data.boot_id);
        if (item && ['done', 'rejected', 'unknown'].includes(receipt.state) && ['queued', 'delivered'].includes(item.state)) {
          item.state = receipt.state; item.message = String(receipt.message || '').slice(0, 160);
        }
      }
      for (const item of queue) if (['queued', 'delivered'].includes(item.state) && (item.expires * 1000 < now || item.boot_id !== this.boot)) {
        item.state = 'expired'; item.message = '전달 시간이 지났습니다. 현재 상태를 확인하세요.';
      }
      const pending = queue.find(item => ['queued', 'delivered'].includes(item.state));
      if (pending) pending.state = 'delivered';
      if (JSON.stringify(queue)!==oldQueue) await store.put('commands', queue.slice(-64));
      return json({viewing: this.viewing > now, command: pending || null});
    }
    if (path === '/api/status' && request.method === 'GET') {
      this.viewing = now + AGE;
      const items = await store.get('commands') || [], lastCommand = items.at(-1);
      if (lastCommand && ['queued', 'delivered'].includes(lastCommand.state) && lastCommand.expires * 1000 < now) {
        lastCommand.state = 'expired'; lastCommand.message = '전달 시간이 지났습니다. 현재 상태를 확인하세요.';
        await store.put('commands', items);
      }
      return json({online: now - this.last < AGE && !this.status?.closing, updated_at: this.last || null, status: this.status,
        screen_at: this.screen && now - this.screen.at < AGE ? this.screen.at : null, command: lastCommand || null});
    }
    if (path === '/api/push/config' && request.method === 'GET') {
      const subscriptions=await store.get('subscriptions') || {};
      return json({configured:!!(this.env.VAPID_PUBLIC_KEY && this.env.VAPID_PRIVATE_KEY),publicKey:this.env.VAPID_PUBLIC_KEY || '',devices:Object.keys(subscriptions).length});
    }
    if (path === '/api/push/subscribe' && request.method === 'POST') {
      if (!this.env.VAPID_PUBLIC_KEY || !this.env.VAPID_PRIVATE_KEY) return json({error:'푸시 연결 준비 중입니다.'},503);
      const subscription=validateSubscription(await body(request));const id=await hash(subscription.endpoint), subscriptions=await store.get('subscriptions') || {};
      if (!subscriptions[id] && Object.keys(subscriptions).length>=3) return json({error:'휴대폰은 최대3개까지 연결할 수 있습니다.'},409);
      subscriptions[id]=subscription;await store.put('subscriptions',subscriptions);return json({ok:true});
    }
    if (path === '/api/push/check' && request.method === 'POST') {
      const data=await body(request), subscriptions=await store.get('subscriptions') || {};
      return json({registered:typeof data.endpoint==='string' && !!subscriptions[await hash(data.endpoint)]});
    }
    if (path === '/api/push/unsubscribe' && request.method === 'POST') {
      const data=await body(request), subscriptions=await store.get('subscriptions') || {};
      if (typeof data.endpoint==='string') delete subscriptions[await hash(data.endpoint)];
      await store.put('subscriptions',subscriptions);return json({ok:true});
    }
    if (path === '/api/push/test' && request.method === 'POST') {
      const last=await store.get('last-test') || 0;
      if (now-last<60_000) return json({error:'테스트 알림은1분 뒤 다시 보낼 수 있습니다.'},429);
      const subscriptions=await store.get('subscriptions') || {};
      if (!Object.keys(subscriptions).length) return json({error:'이 휴대폰에서 알림을 먼저 켜세요.'},409);
      const id='test:'+crypto.randomUUID();
      await store.put('last-test',now);await this.appendNotices([{id,type:'test',title:'컴프야 알림 연결 확인',body:'이 알림을 받았다면 PWA 푸시가 연결되었습니다.',at:new Date(now).toISOString()}]);
      return json({ok:true,id,message:'테스트 알림 전송 결과를 확인하고 있습니다.'});
    }
    if (path === '/api/notifications' && request.method === 'GET') {
      const notices=await store.get('notices') || [], observed=await store.get('observed'), health=await store.get('health');
      return json({collecting:observed?.ready===true,last_received_at:health?.last || null,quality:health?.collection_quality===true,items:notices.slice(-20).reverse().map(item=>({id:item.id,type:item.type,title:item.title,body:item.body,at:item.at,...deliverySummary(item)}))});
    }
    if (path === '/api/screen' && request.method === 'GET') {
      this.viewing = now + AGE;
      if (!this.screen || now - this.screen.at > AGE || now - this.last > AGE) { this.screen = null; return json({error: '새 게임 화면을 기다리고 있습니다.'}, 404); }
      return new Response(Uint8Array.from(atob(this.screen.bytes), char => char.charCodeAt(0)), {headers: {'Content-Type': 'image/jpeg', 'X-Captured-At': String(this.screen.at)}});
    }
    if (path === '/api/reboot-challenge' && request.method === 'POST') {
      if (now - this.last >= AGE || !this.status?.startup_ready || this.status.reboot_pending) return json({error: 'PC 자동 연결 상태를 먼저 확인하세요.'}, 409);
      const challenge = {id: crypto.randomUUID(), boot: this.boot, expires: now + 60_000};
      await store.put('reboot-challenge', challenge); return json({id: challenge.id, expires: challenge.expires});
    }
    if (path === '/api/command' && request.method === 'POST') {
      const data = await body(request), queue = await store.get('commands') || [];
      const old = queue.find(item => item.id === data.id); if (old) return json(old);
      if (now - this.last >= AGE) return json({error: 'PC가 연결되어 있지 않습니다.'}, 409);
      let item; try { item = command(data, this.status, this.boot, now); } catch (error) { return json({error: error.message}, 409); }
      if (data.action === 'reboot') {
        const challenge = await store.get('reboot-challenge');
        if (!challenge || challenge.id !== data.challenge || challenge.boot !== this.boot || challenge.expires < now || data.confirmation !== 'PC 재부팅') return json({error: '재부팅 확인 절차를 다시 진행하세요.'}, 403);
        await store.delete('reboot-challenge');
      }
      const pending = queue.find(value => ['queued', 'delivered'].includes(value.state) && value.expires * 1000 > now);
      if (pending) {
        if ((data.action === 'stop' && pending.action === 'start') || (data.action === 'reboot_cancel' && pending.action === 'reboot')) { pending.state = 'superseded'; pending.message = '중지/취소 요청으로 취소됨'; }
        else return json({error: '앞선 명령을 확인하고 있습니다.'}, 409);
      }
      queue.push(item); await store.put('commands', queue.slice(-64)); return json(item, 202);
    }
    return json({error: 'Not found'}, 404);
  }
}

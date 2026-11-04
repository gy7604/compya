import {test} from 'node:test';
import assert from 'node:assert/strict';
import {observeRecords,observeHealth,offlineEvent,OFFLINE_AFTER} from '../monitor.mjs';
import {pushRequest,validateSubscription,sendPush} from '../push.mjs';
import {createECDH,randomBytes} from 'node:crypto';
import webpush from 'web-push';
import ece from 'http_ece';
const now=Date.parse('2026-11-04T14:00:00Z'), at=new Date(now).toISOString();
const empty=()=>({schema:1,quality:true,faults:[],ranking:[],seasons:[]});

test('initial history is baseline; new ranking completion is interpreted and deduplicated on server',()=>{
  const records=empty();records.ranking=[{day:'2026-11-03',status:'done',pending:false,completed:1,wins:3,draws:1,losses:1,missing:0,at}];
  const first=observeRecords(null,records,now);assert.equal(first.events.length,0);
  records.ranking.push({...records.ranking[0],day:'2026-11-04',completed:2,missing:1});
  const next=observeRecords(first.state,records,now);assert.equal(next.events.length,1);assert.match(next.events[0].body,/미집계/);
  assert.equal(observeRecords(next.state,records,now).events.length,0);
});
test('only verified 144-game facts qualify; notices survive immediately starting a new season',()=>{
  const first=observeRecords(null,empty(),now), records=empty();
  records.seasons=[{id:'season-one',completed:143,at}];assert.equal(observeRecords(first.state,records,now).events.length,0);
  records.seasons[0].completed=144;const next=observeRecords(first.state,records,now);assert.equal(next.events[0].type,'league_done');
  assert.equal(observeRecords(next.state,empty(),now).events.length,0);assert.equal(observeRecords(next.state,records,now).events.length,0);
});
test('unknown quality, ancient facts, unfinished rewards and invalid aggregate totals cannot send completion',()=>{
  const first=observeRecords(null,empty(),now), records=empty();
  records.faults=[{id:'bad',kind:'macro_error',at:'2020-01-01'}];
  records.ranking=[{day:'2026-11-04',status:'done',pending:true,completed:1,wins:3,draws:0,losses:0,missing:0,at}];
  assert.equal(observeRecords(first.state,records,now).events.length,0);
  records.ranking[0].pending=false;records.ranking[0].wins=6;assert.equal(observeRecords(first.state,records,now).events.length,0);
  records.faults[0].at=at;records.quality=false;
  const result=observeRecords(first.state,records,now);assert.equal(result.events.length,1);assert.equal(result.events[0].type,'macro_error');
});
test('fresh terminal faults qualify even on first connection; manual stop does not arm offline alarm',()=>{
  const records=empty();records.faults=[{id:'run-one',kind:'player_error',at}];
  const next=observeRecords(null,records,now);assert.equal(next.events[0].type,'player_error');assert.equal(observeRecords(next.state,records,now).events.length,0);
  for (const status of [{running:false},{running:true,dry:true},{running:true,reboot_pending:true},{running:true,stopping:true},{running:true,closing:true}])
    assert.equal(offlineEvent(observeHealth(null,status,'boot',now),now+OFFLINE_AFTER),null);
  const health=observeHealth(null,{running:true},'boot',now);assert.equal(offlineEvent(health,now+OFFLINE_AFTER-1),null);
  assert.equal(offlineEvent(health,now+OFFLINE_AFTER).type,'offline');health.notified=true;assert.equal(offlineEvent(health,now+OFFLINE_AFTER*2),null);
  const connected=observeHealth(health,{running:true},'boot',now+OFFLINE_AFTER*3);assert.notEqual(connected.episode,health.episode);
});
test('push endpoints reject local network, redirects-to-arbitrary URLs and malformed key material',()=>{
  const pair=createECDH('prime256v1');pair.generateKeys();const keys={p256dh:pair.getPublicKey().toString('base64url'),auth:randomBytes(16).toString('base64url')};
  for (const endpoint of ['http://fcm.googleapis.com/fcm/send/a','https://127.0.0.1/a','https://fcm.googleapis.com.evil.test/a','https://user@fcm.googleapis.com/a','https://fcm.googleapis.com:444/a'])
    assert.throws(()=>validateSubscription({endpoint,keys}));
  assert.equal(validateSubscription({endpoint:'https://web.push.apple.com/test',keys}).endpoint,'https://web.push.apple.com/test');
  assert.throws(()=>validateSubscription({endpoint:'https://fcm.googleapis.com/a',keys:{...keys,p256dh:'A'.repeat(87)}}));
});
test('real web-push encryption decrypts with browser key, VAPID is attached, and network errors are bounded',async()=>{
  const pair=createECDH('prime256v1');pair.generateKeys();const auth=randomBytes(16).toString('base64url'), vapid=webpush.generateVAPIDKeys();
  const subscription={endpoint:'https://fcm.googleapis.com/fcm/send/synthetic-test',keys:{p256dh:pair.getPublicKey().toString('base64url'),auth}};
  const env={VAPID_PUBLIC_KEY:vapid.publicKey,VAPID_PRIVATE_KEY:vapid.privateKey}, event={id:'event-1',type:'macro_error',title:'매크로 확인 필요',body:'테스트 기록',at};
  const request=await pushRequest(subscription,event,env);assert.equal(request.headers['Content-Encoding'],'aes128gcm');assert.match(request.headers.Authorization,/vapid/);
  const decoded=JSON.parse(ece.decrypt(request.body,{version:'aes128gcm',privateKey:pair,authSecret:auth}).toString());assert.equal(decoded.body,event.body);assert.equal(decoded.url,'/');
  let count=0;const status=await sendPush(subscription,event,env,async(url,options)=>{count++;assert.equal(options.redirect,'error');assert.ok(options.signal);return new Response(null,{status:201});});
  assert.equal(status,201);assert.equal(count,1);
});

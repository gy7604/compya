import webpush from 'web-push';
import {ECDH} from 'node:crypto';
import {hash} from './protocol.mjs';

export function validateSubscription(value) {
  const url=new URL(value?.endpoint);
  const hosts=['fcm.googleapis.com','updates.push.services.mozilla.com','push.services.mozilla.com'];
  const allowed=hosts.includes(url.hostname) || /^[a-z0-9-]+\.push\.apple\.com$/.test(url.hostname) || /^[a-z0-9-]+\.notify\.windows\.com$/.test(url.hostname);
  if (!allowed || url.protocol!=='https:' || url.port || url.username || url.password || url.hash || url.href.length>2048) throw new Error('지원하지 않는 푸시 주소입니다.');
  if (!/^[A-Za-z0-9_-]{87}={0,2}$/.test(value.keys?.p256dh || '') || !/^[A-Za-z0-9_-]{22}={0,2}$/.test(value.keys?.auth || '')) throw new Error('푸시 연결 키를 확인하세요.');
  const key=Buffer.from(value.keys.p256dh,'base64url'), auth=Buffer.from(value.keys.auth,'base64url');
  if (key.length!==65 || key[0]!==4 || auth.length!==16) throw new Error('푸시 연결 키 길이를 확인하세요.');
  ECDH.convertKey(key,'prime256v1');
  return {endpoint:url.href,keys:{p256dh:value.keys.p256dh,auth:value.keys.auth}};
}

export async function pushRequest(subscription,event,env) {
  const topic=(await hash(event.id)).slice(0,32);
  return webpush.generateRequestDetails(subscription,JSON.stringify({title:event.title,body:event.body,tag:topic,url:'/'}),{
    TTL:600,urgency:event.type==='ranking_done'||event.type==='league_done'?'normal':'high',topic,
    vapidDetails:{subject:'https://compya-mobile-control.jangkj88.workers.dev',publicKey:env.VAPID_PUBLIC_KEY,privateKey:env.VAPID_PRIVATE_KEY}
  });
}

export async function sendPush(subscription,event,env,fetcher=fetch) {
  const request=await pushRequest(validateSubscription(subscription),event,env);
  const response=await fetcher(request.endpoint,{method:'POST',headers:request.headers,body:request.body,redirect:'error',signal:AbortSignal.timeout(8000)});
  if (response.body) await response.body.cancel();
  return response.status;
}

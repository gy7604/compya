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
  let request;
  try {request=await pushRequest(validateSubscription(subscription),event,env);}
  catch {throw Object.assign(new Error('Push preparation failed'),{pushReason:'prepare'});}
  let response;
  // Workers rejects redirect:'error' before sending anything. Manual mode keeps
  // redirects visible as non-success responses without forwarding keys or data.
  try {response=await fetcher(request.endpoint,{method:'POST',headers:request.headers,body:request.body,redirect:'manual',signal:AbortSignal.timeout(8000)});}
  catch(error) {throw Object.assign(new Error('Push transport result unknown'),{pushReason:error?.name==='TimeoutError' || error?.name==='AbortError'?'timeout':'network'});}
  if (response.body) await response.body.cancel().catch(()=>{});
  return response.status;
}

export function deliverySummary(notice) {
  const states=Object.values(notice.deliveries || {}), details=Object.values(notice.delivery_details || {});
  if (!states.length) return {push:'no-device',push_message:'휴대폰 알림 연결 전 기록'};
  if (states.includes('accepted')) return {push:'accepted',push_message:'푸시 서버 접수 완료 · 휴대폰 수신을 확인하세요.'};
  if (states.some(value=>value==='pending' || value==='sending')) return {push:'unconfirmed',push_message:'푸시 서버로 전송 중'};
  if (states.includes('expired')) return {push:'unconfirmed',push_message:'휴대폰 알림 연결이 만료됐습니다. 알림을 다시 켜세요.'};
  const status=details.find(value=>Number.isInteger(value.status))?.status;
  if (status) return {push:'unconfirmed',push_message:`푸시 서버가 요청을 거절했습니다 (HTTP ${status}).`};
  const reason=details.find(value=>value.reason)?.reason;
  const messages={prepare:'알림 암호화 준비 실패 · 서버 확인 필요',timeout:'전송 응답 시간 초과 · 중복 방지를 위해 자동 재전송하지 않습니다.',network:'전송 연결 오류 · 중복 방지를 위해 자동 재전송하지 않습니다.'};
  return {push:'unconfirmed',push_message:messages[reason] || '이전 전송 결과 확인 필요 · 새 테스트 알림을 보내세요.'};
}

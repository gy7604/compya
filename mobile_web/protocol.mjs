export const AGE = 30_000;
export const SESSION_AGE = 30 * 24 * 3600;
export const SHORT_SESSION_AGE = 12 * 3600;
const encoder = new TextEncoder();
export const hash = async value => hex(await crypto.subtle.digest('SHA-256', encoder.encode(value)));
const hex = value => [...new Uint8Array(value)].map(v => v.toString(16).padStart(2, '0')).join('');
export function equal(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let n = 0; for (let i = 0; i < a.length; i++) n |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return n === 0;
}
function encode(value) { return btoa(JSON.stringify(value)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', ''); }
async function sign(secret, value) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, encoder.encode(value)));
}
export async function session(secret, now = Date.now(), age = SESSION_AGE) {
  const value = encode({exp: Math.floor(now / 1000) + age, nonce: crypto.randomUUID()});
  return value + '.' + await sign(secret, value);
}
export async function authorized(request, secret, now = Date.now()) {
  const cookie = (request.headers.get('Cookie') || '').match(/(?:^|;\s*)compya_session=([A-Za-z0-9_.-]+)/)?.[1];
  if (!cookie || cookie.length > 512) return false;
  const [body, signature] = cookie.split('.');
  if (!equal(signature, await sign(secret, body))) return false;
  try { const data = JSON.parse(atob(body.replaceAll('-', '+').replaceAll('_', '/'))); return data.exp > now / 1000 && data.exp <= now / 1000 + SESSION_AGE + 1; }
  catch { return false; }
}
export function sameOrigin(request) {
  return request.headers.get('Origin') === new URL(request.url).origin && request.headers.get('Sec-Fetch-Site') !== 'cross-site';
}
export async function body(request, limit = 4096) {
  if (!request.headers.get('Content-Type')?.startsWith('application/json')) throw new Error('JSON required');
  if (+request.headers.get('Content-Length') > limit) throw new Error('Body too large');
  const reader = request.body?.getReader(); if (!reader) throw new Error('Body required');
  let count = 0; const chunks = [];
  while (true) { const {done, value} = await reader.read(); if (done) break; count += value.length; if (count > limit) { await reader.cancel(); throw new Error('Body too large'); } chunks.push(value); }
  const joined = new Uint8Array(count); let offset = 0; for (const value of chunks) { joined.set(value, offset); offset += value.length; }
  return JSON.parse(new TextDecoder().decode(joined));
}
export function cleanStatus(value) {
  if (!value || typeof value !== 'object') throw new Error('Invalid status');
  const result = {};
  for (const key of ['version', 'mode', 'status', 'league_count', 'season', 'timing', 'blocked']) result[key] = String(value[key] || '').slice(0, 240);
  for (const key of ['running', 'stopping', 'can_start', 'dry', 'startup_ready', 'reboot_pending']) result[key] = value[key] === true;
  return result;
}
export function command(data, current, boot, now) {
  if (!/^[a-f0-9-]{36}$/.test(data.id || '') || !['start', 'stop', 'reboot', 'reboot_cancel'].includes(data.action)) throw new Error('허용되지 않은 명령입니다.');
  if (!current || !boot) throw new Error('PC 연결을 먼저 확인하세요.');
  if (data.action === 'start' && (!current.can_start || current.running)) throw new Error(current.blocked || '이미 실행 중입니다.');
  if (data.action === 'reboot' && (!current.startup_ready || current.reboot_pending)) throw new Error('PC 자동 연결 설정 또는 재부팅 예약을 확인하세요.');
  if (data.action === 'reboot_cancel' && !current.reboot_pending) throw new Error('현재 재부팅 예약이 없습니다.');
  return {id: data.id, action: data.action, boot_id: boot, expires: (now + AGE) / 1000, state: 'queued', message: 'PC에 전달 중'};
}

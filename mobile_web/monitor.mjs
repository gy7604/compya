// Server-side interpretation of typed PC records. No game decisions on the handset.
export const OFFLINE_AFTER = 120_000;
const recent = (at, now) => Number.isFinite(Date.parse(at)) && Date.parse(at) <= now + 60_000 && Date.parse(at) >= now - 86400_000;
const identity = value => typeof value === 'string' && /^[a-zA-Z0-9-]{1,80}$/.test(value);
export function observeRecords(previous, records, now) {
  const state = structuredClone(previous || {ready:false, seen:{}}), events = [];
  if (!records || records.schema !== 1 || typeof records.quality !== 'boolean' || !['faults','ranking','seasons'].every(key => Array.isArray(records[key]) && records[key].length <= 24)) return {state, events};
  const found=[];
  for (const item of records.faults) {
    if (!identity(item.id) || !['macro_error','player_error','unclean_exit'].includes(item.kind)) continue;
    const titles = {macro_error:'매크로 확인 필요',player_error:'플레이어 자동 복구 실패',unclean_exit:'이전 매크로 실행 종료 확인 필요'};
    const messages = {macro_error:'예상치 못한 오류로 매크로가 중지되었습니다. 모바일에서 현재 상태를 확인하세요.',
      player_error:'설정된 자동 복구 횟수 안에 플레이어·게임으로 복귀하지 못했습니다.',unclean_exit:'이전 실행이 정상 종료 기록 없이 끝났습니다. 현재 연결과 경기 상태를 확인하세요.'};
    found.push({id:`${item.kind}:${item.id}`,type:item.kind,title:titles[item.kind],body:messages[item.kind],at:item.at});
  }
  for (const item of records.quality ? records.seasons : []) {
    if (identity(item.id) && item.completed === 144) found.push({id:`league:${item.id}`,type:'league_done',title:'리그 144경기 완료',body:'확인된 정규시즌 144경기를 모두 완료했습니다.',at:item.at});
  }
  for (const item of records.quality ? records.ranking : []) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(item.day || '') || item.status !== 'done' || item.pending !== false) continue;
    if (!['completed','wins','draws','losses','missing'].every(key => Number.isInteger(item[key]) && item[key] >= 0 && item[key] <= 2500) || item.missing > item.completed) continue;
    const count = item.wins + item.draws + item.losses;
    const known=item.completed-item.missing;
    if (count<known || count>known*5) continue;
    const body = item.missing ? `확인된 ${count}경기 · ${item.wins}승 ${item.draws}무 ${item.losses}패 · ${item.missing}묶음 미집계` : `총 ${count}경기 · ${item.wins}승 ${item.draws}무 ${item.losses}패`;
    found.push({id:`ranking:${item.day}`,type:'ranking_done',title:'랭킹챌린지 하루 일정 완료',body:`${item.day} · ${body}`,at:item.at});
  }
  for (const event of found) {
    if (state.seen[event.id]) continue;
    state.seen[event.id]=now;
    if ((state.ready || (['macro_error','player_error','unclean_exit'].includes(event.type) && Date.parse(event.at)>now-300_000)) && recent(event.at,now)) events.push(event);
  }
  state.ready=true;
  state.seen=Object.fromEntries(Object.entries(state.seen).sort((a,b)=>a[1]-b[1]).slice(-256));
  return {state,events};
}

export function observeHealth(previous,status,boot,now) {
  const watch=status.running && !status.dry && !status.stopping && !status.reboot_pending && !status.closing;
  return {last:now,watch,episode:!previous || previous.notified || (!previous.watch && watch) ? crypto.randomUUID() : previous.episode,notified:false,boot};
}
export function offlineEvent(health,now) {
  if (!health?.watch || health.notified || now-health.last < OFFLINE_AFTER) return null;
  return {id:'offline:'+health.episode,type:'offline',title:'PC 연결 확인 필요',body:'실행 중이던 PC에서 2분 이상 상태가 도착하지 않습니다. PC 전원·인터넷·프로그램 상태를 확인하세요.',at:new Date(now).toISOString()};
}

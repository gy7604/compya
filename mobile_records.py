"""Typed, minimal observations for server-side decisions. No screenshots or raw logs."""
import json
import threading
import uuid
from datetime import datetime,timezone
from pathlib import Path

LOCK=threading.RLock()
def stamp():return datetime.now(timezone.utc).isoformat()

def read(path):
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Record must be an object')
    return value

def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temp.replace(path)

class RunTelemetry:
    def __init__(self,root):
        self.path=Path(root)/'mobile_telemetry.json';self.data={'faults':[],'run':{}}
        try:
            data=read(self.path)
            if isinstance(data.get('run'),dict) and isinstance(data.get('faults'),list):self.data=data
        except (OSError,ValueError):pass
        with LOCK:
            if self.data.get('run',{}).get('phase')=='running':self.failure('unclean_exit')

    def save(self):
        try:write(self.path,self.data)
        except (OSError,ValueError):pass  # Telemetry must never stop a game.

    def begin(self,dry):
        with LOCK:
            self.data['run']=dict(id=str(uuid.uuid4()),phase='running',dry=bool(dry),at=stamp());self.save()

    def expected(self,phase):
        with LOCK:
            self.data.setdefault('run',{}).update(phase=phase,at=stamp());self.save()

    def failure(self,kind):
        with LOCK:
            run=self.data.get('run',{})
            if run.get('dry') or run.get('phase') in ('manual','closed','planned_reboot','error'):return
            if not run.get('id'):return
            self.data.setdefault('faults',[]).append(dict(id=run['id'],kind=kind,at=stamp()))
            self.data['faults']=self.data['faults'][-24:];run.update(phase='error',at=stamp());self.save()

    def finish(self):
        with LOCK:
            if self.data.get('run',{}).get('phase')=='running':self.expected('finished')


def record_season(root,data):
    """Keep a verified 144th result even if the next season starts immediately."""
    with LOCK:
        path=Path(root)/'mobile_facts.json'
        try:
            facts=read(path) if path.exists() else {'seasons':[]}
            if data['completed']!=144 or data['phase']!='completed':return
            if not any(item['id']==data['season_id'] for item in facts['seasons']):
                facts['seasons'].append(dict(id=data['season_id'],completed=144,at=data['confirmed_at']))
                facts['seasons']=facts['seasons'][-12:];write(path,facts)
        except (OSError,ValueError,KeyError,TypeError):pass


class RecordReader:
    def __init__(self,root):self.root=Path(root)

    def snapshot(self):
        result=dict(schema=1,faults=[],ranking=[],seasons=[],quality=True)
        for name,key in (('mobile_telemetry.json','faults'),('mobile_facts.json','seasons')):
            try:
                if (self.root/name).exists():
                    data=read(self.root/name)
                    result[key]=[{k:str(v.get(k,''))[:80] for k in ('id','kind','at')} | ({'completed':v.get('completed')} if key=='seasons' else {}) for v in data.get(key,[])[-24:]]
            except (OSError,ValueError,TypeError,AttributeError):result['quality']=False
        try:
            path=self.root/'ranking_daily.json'
            days=read(path).get('days',{}) if path.exists() else {}
            for day,entry in sorted(days.items())[-7:]:
                if not isinstance(entry,dict):continue
                results=entry.get('results',{});wins=draws=losses=known=0;completed=entry.get('completed',0)
                if type(completed) is not int or not 0<=completed<=500:continue
                for batch in range(1,completed+1):
                    values=results.get(str(batch))
                    if isinstance(values,list) and len(values)==3 and all(type(n) is int and n>=0 for n in values) and 1<=sum(values)<=5:
                        wins+=values[0];draws+=values[1];losses+=values[2];known+=1
                result['ranking'].append(dict(day=day,status=entry.get('status'),at=entry.get('finished_at',''),
                    completed=completed,wins=wins,draws=draws,losses=losses,missing=completed-known,
                    pending=bool(entry.get('pending') or entry.get('reward_pending'))))
        except (OSError,ValueError,TypeError,AttributeError):result['quality']=False
        return result

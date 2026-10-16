"""Atomic registry updates used by the ranking setup's inline capture tools."""
import copy
from registry import new_button


def save_rank_image(app,selected_id,label,path,roi,size):
    existing=next((r for r in app.records if r['id']==selected_id),None)
    record=copy.deepcopy(existing) if existing and existing.get('kind','normal')!='normal' else new_button()
    if not existing or existing.get('kind','normal')=='normal':
        base='랭킹 · '+label;name=base;n=2
        while any(r['name']==name for r in app.records):name=f'{base} {n}';n+=1
        record['name']=name
    record.update(template=app.registry.portable(path),roi=roi,size=size,enabled=True,kind=existing.get('kind') if existing and existing.get('kind') in ('step','condition') else 'condition')
    # Coordinates must be picked again after a capture at a different resolution.
    if record.get('click_mode')=='fixed' and record.get('click_size')!=size:
        record.update(click_mode='center',click_point=None,click_size=None)
    records=[record if r['id']==record['id'] else r for r in app.records]
    if not any(r['id']==record['id'] for r in app.records):records.append(record)
    app.registry.save(records);app.records=records
    return record


def save_rank_point(app,selected_id,point,size):
    record=next((r for r in app.records if r['id']==selected_id),None)
    if record is None:raise ValueError('먼저 이미지를 등록하세요.')
    if record.get('size') and record['size']!=size:raise ValueError('이미지 해상도와 다릅니다. 이미지를 다시 캡처하세요.')
    record=dict(record,click_mode='fixed',click_point=point,click_size=size)
    records=[record if r['id']==selected_id else r for r in app.records]
    app.registry.save(records);app.records=records
    return record

"""Local-only last stop frame and matching context. Never part of releases."""
import json
from pathlib import Path


def write_stop_snapshot(root, png, records, results, pending, dry, context=None, previous_png=None):
    folder = Path(root)/'logs'
    folder.mkdir(exist_ok=True)
    path = folder/'last_stop.png'
    path.write_bytes(png)
    previous=folder/'last_stop_previous.png'
    if previous_png is not None:previous.write_bytes(previous_png)
    elif previous.exists():previous.unlink()
    details = dict(mode='모의 실행' if dry else '실제 실행', pending=pending,
                   buttons=[dict(id=key, name=records[key]['name'], matched=result[0],
                                 score=result[2], threshold=records[key]['threshold'],
                                 candidate=result[1], roi=records[key]['roi']) for key, result in results.items()])
    details['context']=context or {}
    details['previous_frame_available']=previous_png is not None
    for item in details['buttons']:
        record=records[item['id']]
        item['click_mode']=record.get('click_mode','center')
        item['click_point']=record.get('click_point')
    (folder/'last_stop.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    return path

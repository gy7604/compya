"""Local-only last stop frame and matching context. Never part of releases."""
import json
from pathlib import Path


def write_stop_snapshot(root, png, records, results, pending, dry):
    folder = Path(root)/'logs'
    folder.mkdir(exist_ok=True)
    path = folder/'last_stop.png'
    path.write_bytes(png)
    details = dict(mode='모의 실행' if dry else '실제 실행', pending=pending,
                   buttons=[dict(id=key, name=records[key]['name'], matched=result[0],
                                 score=result[2], threshold=records[key]['threshold'],
                                 candidate=result[1], roi=records[key]['roi']) for key, result in results.items()])
    (folder/'last_stop.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    return path

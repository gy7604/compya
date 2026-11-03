"""Bootstrap fixed bundled resources even when an older updater skips new root files."""
from pathlib import Path

from ball_guard import FILES as BALL_FILES
from league_bonus import FILES as BONUS_FILES

FILES=('PriceOCR.exe','coach_advice.png','league_next_match.png','league_round_label.png','league_play_hud.png')+BALL_FILES+BONUS_FILES


def ensure_runtime_resources(root):
    root=Path(root);bundled=root/'_internal'/'compya_resources';errors=[]
    for name in FILES:
        target=root/name if name.endswith('.exe') else root/'assets'/name
        source=bundled/name
        if target.exists() or not source.is_file():continue
        try:
            payload=source.read_bytes()
            signature=b'MZ' if name.endswith('.exe') else b'\x89PNG\r\n\x1a\n'
            if not payload.startswith(signature):raise ValueError('번들 파일 형식 확인 실패')
            target.parent.mkdir(parents=True,exist_ok=True)
            # Exclusive creation preserves user-owned files, including a racing writer.
            with target.open('xb') as output:output.write(payload)
        except FileExistsError:pass
        except (OSError,ValueError):errors.append(name)
    return errors

"""Detection cadence; confirmation and duplicate-click latches stay independent."""
MODES = {
    '빠름 · 1회 인식': (1, 0.0),
    '균형 · 2회 확인': (2, 0.10),
    '안정적 · 3회 확인': (3, 0.25),
}
DEFAULT_MODE = '안정적 · 3회 확인'


def speed_settings(mode):
    if mode not in MODES:
        raise ValueError('인식 반응속도를 목록에서 선택하세요.')
    return MODES[mode]

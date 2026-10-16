"""Independent image recognition location and optional fixed ADB tap target."""


def click_point(record, matched_center, screen_size):
    width, height = screen_size
    if record.get('click_mode', 'center') == 'fixed':
        expected = record.get('click_size')
        if expected is None or tuple(expected) != tuple(screen_size):
            raise ValueError(f'{record["name"]}: 클릭 좌표를 등록한 해상도와 다릅니다. 클릭 위치를 다시 선택하세요.')
        point = record.get('click_point')
    else:
        point = matched_center
    if point is None or len(point) != 2 or any(type(value) is not int for value in point):
        raise ValueError('클릭 좌표는 X,Y 정수여야 합니다.')
    x, y = point
    if not 0 <= x < width or not 0 <= y < height:
        raise ValueError('클릭 좌표가 현재 게임 화면을 벗어납니다.')
    return x, y

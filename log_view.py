"""Display-only summaries. The file log always retains the original message."""


def concise_status(message):
    text=message.split(' · ',2)
    if message.startswith(('실제 실행 · ','모의 실행 · ')):
        return ' · '.join(text[:2])[:100]
    return message.split(' 좌표=',1)[0].split(' 인식위치=',1)[0][:100]


def important_message(message):
    # Filter routine polling and individual taps, never exception messages.
    if message.startswith(('안전 중지','중지 화면 저장 실패','업데이트 실패')):
        return message
    quiet=('실제 실행 · ','모의 실행 · ','클릭 보류 · ','랭킹 대기 · ',
           '재접속 대기 · ','재접속 화면 해상도 전환 대기 · ',
           '일반 클릭 우선순위 · ','반응속도 · ','마지막 인식 · ',
           '탭 #','모의 탭 #','일반 경기 버튼 대기는 유지')
    if message.startswith(quiet):
        return None
    return message.split(' 좌표=',1)[0]

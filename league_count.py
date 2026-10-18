"""Count completed league games from distinct stable play/result screens."""
class LeagueCounter:
    def __init__(self, start='', result='', frames=2):
        self.start, self.result, self.frames = start, result, frames
        self.completed = 0
        self.armed = False
        self.play_frames = self.result_frames = self.absent = 0
        self.ready = True

    def observe(self, found):
        if not self.start or not self.result or self.start == self.result:
            return False
        result = found.get(self.result, False)
        self.absent = 0 if result else self.absent + 1
        if self.absent >= self.frames:
            self.ready = True
        play = found.get(self.start, False) and not result
        self.play_frames = self.play_frames + 1 if play else 0
        if self.ready and self.play_frames >= self.frames:
            self.armed = True
        self.result_frames = self.result_frames + 1 if result else 0
        if self.armed and self.result_frames >= self.frames:
            self.completed += 1
            self.armed = self.ready = False
            return True
        return False

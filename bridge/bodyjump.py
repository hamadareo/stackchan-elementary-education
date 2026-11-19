"""からだジャンプ: 機器のカメラ画像の「顔の高さ」の動きから、子どもが実際にジャンプしたかを見つける(チャリはしり用)。

- 顔の中心の高さ(上が小さい)を、少し前の「いつもの高さ」(中央値)と比べる。顔の大きさ(縦)の何割か以上、すばやく上がったらジャンプ。
- ゆっくりの動き(近づく・背のび)や、小さなゆれ(検出のぶれ)では反応しない。ジャンプのあと、しばらくは次を数えない。
- ジャンプで顔が画面の上に出てしまったときも、直前まで上がっていれば、ジャンプとする。
純粋な計算だけ(画像・時計は外から渡す)。しきい値は、実機で調整する。
"""
from __future__ import annotations

from collections import deque
from statistics import median

RISE = 0.35          # いつもの高さから、顔の縦の長さのこの割合以上、上がったら
WINDOW = 1.2         # いつもの高さを見る時間(秒)
RECENT = 0.30        # この秒数より新しい点は「いま」。古い点が「いつも」
MIN_GAP = 0.8        # ジャンプのあとの、数えない時間(秒)
UP_SPEED = 0.10      # 直前(0.2秒前)から、顔の縦の長さのこの割合以上、上がっているときだけ(下りは数えない)
LOST_TOP = 0.30      # 顔が消える直前に、画面の上から このくらい(画面の高さの割合)にいれば、上に出たとみなす


class JumpDetector:
    def __init__(self, rise: float = RISE, min_gap: float = MIN_GAP):
        self.rise, self.min_gap = rise, min_gap
        self.pts: deque = deque()                      # (t, 顔の中心の y, 顔の縦の長さ)
        self.last_jump = -1e9
        self.last_seen = None                          # 最後に見えた (t, y, fh, H)

    def reset(self) -> None:
        self.pts.clear()
        self.last_jump = -1e9
        self.last_seen = None

    def _trim(self, t: float) -> None:
        while self.pts and t - self.pts[0][0] > WINDOW:
            self.pts.popleft()

    def update(self, t: float, cy: "float | None", fh: "float | None", frame_h: float = 120.0) -> bool:
        """1 枚ぶんの結果を入れる。cy=顔の中心の y(ピクセル。見えなければ None) / fh=顔の縦の長さ / frame_h=画像の高さ。ジャンプなら True。"""
        if cy is None or not fh or fh <= 0:
            return self._lost(t)
        self.last_seen = (t, cy, fh, frame_h)
        self.pts.append((t, cy, fh))
        self._trim(t)
        if t - self.last_jump < self.min_gap:
            return False
        old = [p for p in self.pts if t - p[0] > RECENT]
        if len(old) < 3:
            return False
        base = median(p[1] for p in old)
        size = median(p[2] for p in old)
        near = [p for p in self.pts if 0.12 <= t - p[0] <= 0.3]            # 0.2 秒ほど前
        if not near:
            return False
        prev = near[0][1]
        if base - cy >= self.rise * size and prev - cy >= UP_SPEED * size:
            self.last_jump = t
            return True
        return False

    def _lost(self, t: float) -> bool:
        """顔が見えない。直前まで画面の上のほうにいて、上がっていたなら、上に出たジャンプ。"""
        ls = self.last_seen
        if ls is None or t - ls[0] > 0.45 or t - self.last_jump < self.min_gap:
            return False
        _t, y, fh, H = ls
        if y > H * LOST_TOP:
            return False
        old = [p for p in self.pts if ls[0] - p[0] > RECENT]
        if len(old) < 3:
            return False
        base = median(p[1] for p in old)
        if base - y >= 0.15 * fh:
            self.last_jump = t
            self.last_seen = None
            return True
        return False

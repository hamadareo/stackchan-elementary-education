"""顔の識別(誰か)と、手を振る動きの検出。

- People: 登録した人の顔の特徴(SFace の 128 次元)を ~/.sokutsu_faces.json に持ち、見えた顔が誰かを当てる。
  特徴ベクトルだけを保存する(顔の画像は保存しない)。このサーバーの外へは出さない。
- WaveDetector: 顔のそばで手が動き続けたら「手を振った」とする(カメラの画像の差だけで見る)。
"""
from __future__ import annotations

import json
import os
import time
from collections import deque

import numpy as np

THRESH = 0.363            # SFace の推奨しきい値(コサイン類似度)。これ以上なら同じ人
MAX_SAMPLES = 15


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0


class People:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("SC_FACES", os.path.expanduser("~/.sokutsu_faces.json"))
        self.db: dict[str, list[np.ndarray]] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.load(open(self.path))
            self.db = {n: [np.asarray(v, np.float32) for v in vs] for n, vs in raw.items() if vs}
        except (OSError, ValueError):
            self.db = {}

    def save(self) -> None:
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # 本人以外には読ませない
            with os.fdopen(fd, "w") as f:
                json.dump({n: [v.round(5).tolist() for v in vs] for n, vs in self.db.items()}, f)
        except OSError:
            pass

    def add(self, name: str, embs) -> None:
        lst = self.db.setdefault(name, [])
        lst.extend(np.asarray(e, np.float32) for e in embs)
        del lst[:-MAX_SAMPLES]
        self.save()

    def remove(self, name: str) -> bool:
        ok = self.db.pop(name, None) is not None
        if ok:
            self.save()
        return ok

    def names(self) -> list:
        return [{"name": n, "samples": len(v)} for n, v in sorted(self.db.items())]

    def identify(self, emb) -> "tuple[str, float]":
        """(名前, 類似度)。しきい値に届かなければ名前は空。"""
        best, score = "", -1.0
        e = np.asarray(emb, np.float32)
        for n, vs in self.db.items():
            s = max((cosine(e, v) for v in vs), default=-1.0)
            if s > score:
                best, score = n, s
        return (best, score) if score >= THRESH else ("", max(score, 0.0))


class Voter:
    """直近の判定の多数決(1枚の見間違いで名前が変わらないように)。"""

    def __init__(self, n: int = 5, need: int = 2):
        self.h, self.need = deque(maxlen=n), need

    def add(self, name: str) -> str:
        self.h.append(name)
        known = [x for x in self.h if x]
        if not known:
            return ""
        top = max(set(known), key=known.count)
        return top if known.count(top) >= self.need else ""

    def reset(self) -> None:
        self.h.clear()


class WaveDetector:
    """顔の横や上で手が動き続けたら True。画像(グレー 80x60 に縮小)と顔の枠を毎フレーム渡す。

    手を振る ≒ 顔は動かないのに、そのまわりの小さな範囲がしばらく動き続ける。
    画面全体が動く(カメラが動いた・歩いた)ときと、顔そのものが動くときは数えない。
    """

    W, H = 80, 60

    def __init__(self, window_s: float = 3.4, need_active: int = 4, cooldown_s: float = 10.0):
        self.window_s, self.need_active, self.cooldown_s = window_s, need_active, cooldown_s
        self.prev = None
        self.hist: deque = deque(maxlen=16)        # (t, active, cx)
        self.last_face = None
        self.last_fire = 0.0

    def reset(self) -> None:
        self.prev, self.last_face = None, None
        self.hist.clear()

    def update(self, gray: np.ndarray, face, now: float | None = None) -> bool:
        """gray: 80x60 の uint8。face: (x0,y0,x1,y1) 同じ座標系。顔が無いときは None。"""
        now = time.time() if now is None else now
        prev, self.prev = self.prev, gray.astype(np.int16)
        if face is None or prev is None:
            self.hist.clear()
            self.last_face = face
            return False
        x0, y0, x1, y1 = face
        fw, fh = x1 - x0, y1 - y0
        moved = self.last_face is not None and abs((x0 + x1) / 2 - (self.last_face[0] + self.last_face[2]) / 2) > 0.35 * fw
        self.last_face = face
        d = np.abs(gray.astype(np.int16) - prev) > 22
        total = float(d.mean())
        m = np.zeros_like(d)
        cx_f = (x0 + x1) / 2
        rx0, rx1 = int(max(0, cx_f - 2.4 * fw)), int(min(self.W, cx_f + 2.4 * fw))     # 顔の左右 2.4 顔ぶん
        ry0, ry1 = int(max(0, y0 - 0.9 * fh)), int(min(self.H, y1 + 1.8 * fh))         # 頭の上から胸のあたり
        m[ry0:ry1, rx0:rx1] = d[ry0:ry1, rx0:rx1]
        m[int(max(0, y0 - 0.15 * fh)):int(min(self.H, y1 + 0.15 * fh)), int(max(0, x0 - 0.25 * fw)):int(min(self.W, x1 + 0.25 * fw))] = False   # 顔の中は数えない
        area = float(m.sum())
        frac = area / float(self.W * self.H)
        active = (not moved) and total < 0.30 and 0.004 < frac < 0.20
        cx = float(np.nonzero(m)[1].mean()) / self.W if active else 0.0
        self.hist.append((now, active, cx))
        recent = [(t, a, c) for t, a, c in self.hist if now - t <= self.window_s]
        act = [c for _, a, c in recent if a]
        if len(act) >= self.need_active and now - self.last_fire > self.cooldown_s:
            # 動いた頻度が高い(まばらな動きは手を振るではない): 窓の中の 55% 以上のフレームで動きがある
            if len(act) >= 0.55 * len(recent):
                self.last_fire = now
                self.hist.clear()
                return True
        return False

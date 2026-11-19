"""授業の進行役: 先生が管理画面で「どの機器で、どの遊びを始めるか」「中止」を決める(ロボットは、押されるまで何もしない)。

- 開始: その機器で、いま動いている遊びがあればすぐ止めて(声も止める)、止まりきるのを待ってから、選んだ遊びを始める。
  = 「中止してすぐ次の機能に入る」「遊びの途中で別の遊びに切り替える」が、ボタン 1 回でできる。
- 中止: その機器(省略なら全機器)の、どの遊びでもすぐ止める。声は機器側でも止める。
- 状態: 機器ごとに「待機中 / 何をしている / 子どもの番か」を 1 つにまとめて、管理画面に返す。
"""
from __future__ import annotations

import time

KINDS = [("shiritori", "しりとり"), ("janken", "じゃんけん"), ("riddle", "なぞなぞ"), ("hoi", "あっちむいてホイ"), ("omikuji", "おみくじ"), ("mimic", "ものまねボイス")]
LABEL = dict(KINDS)
STOP_WAIT_S = 5.0                 # 止めた遊びが、完全に終わるのを待つ最長の秒数


class Lesson:
    def __init__(self, hub, clock=time.time, sleep=time.sleep):
        self.hub = hub
        self.clock, self.sleep = clock, sleep

    # ---- 機器ごとの「いまの遊び」
    def hubs(self) -> list:
        return [h for h in (getattr(self.hub, "game", None), getattr(self.hub, "janken", None), getattr(self.hub, "quiz", None)) if h is not None]

    def current(self, dev: str) -> str:
        """機器 dev で動いている遊びの種類(なければ "")。"""
        g = getattr(self.hub, "game", None)
        if g is not None and g.dev_busy(dev):
            return "shiritori"
        j = getattr(self.hub, "janken", None)
        if j is not None and j.dev_busy(dev):
            return "janken"
        q = getattr(self.hub, "quiz", None)
        if q is not None and q.dev_busy(dev):
            return getattr(q.games.get(dev), "kind", "") or "riddle"
        return ""

    def busy(self, dev: str) -> bool:
        return any(h.dev_busy(dev) for h in self.hubs())

    # ---- 中止
    def stop(self, dev: "str | None" = None) -> None:
        """どの遊びでも、すぐ止める(機器の声も止める)。遊んでいなくても、鳴りかけの声は止める。"""
        for h in self.hubs():
            h.stop(dev)
        for sess in list(self.hub.live()):
            if dev and getattr(sess, "dev_id", "") != dev:
                continue
            try:
                sess.send({"t": "hush"})
                sess.send({"t": "board", "clear": True})
                sess.send({"t": "face", "state": "neutral"})
            except Exception:  # noqa: BLE001
                pass

    def _wait_idle(self, dev: str, limit: float = STOP_WAIT_S) -> bool:
        t0 = self.clock()
        while self.busy(dev):
            if self.clock() - t0 > limit:
                return False
            self.sleep(0.05)
        return True

    # ---- 開始
    def start(self, kind: str, dev_ids: "list | None" = None) -> "tuple[bool, str]":
        """選んだ遊びを、dev_ids の機器(省略なら接続中の全機器)で始める。動いている遊びは、先に止める。"""
        if kind not in LABEL:
            return False, "知らない遊びです"
        ids = list(dev_ids) if dev_ids else [getattr(s, "dev_id", "") for s in self.hub.live()]
        ids = [i for i in ids if i]
        if not ids:
            return False, "接続中の機器がありません"
        started, err = [], ""
        for i in ids:
            if self.busy(i):
                self.stop(i)
                if not self._wait_idle(i):
                    err = "前の遊びを止めています。もう一度押してください"
                    continue
            ok, e = self._start_one(kind, i)
            if ok:
                started.append(i)
            else:
                err = e
        return (True, "") if started else (False, err or "始められませんでした")

    def _start_one(self, kind: str, dev: str) -> "tuple[bool, str]":
        hub = self.hub
        if kind == "shiritori":
            if getattr(hub, "game", None) is None:
                return False, "しりとりは使えません"
            return hub.game.start("robot", "easy", [dev])
        if kind == "janken":
            if getattr(hub, "janken", None) is None:
                return False, "じゃんけんは使えません"
            return hub.janken.start("robot", "easy", [dev])
        if getattr(hub, "quiz", None) is None:
            return False, "この遊びは使えません"
        return hub.quiz.start(kind, "", [dev])

    # ---- 状態(管理画面)
    def snapshot(self, devices: list) -> list:
        """devices: [{"id","name","online",...}]。機器ごとに、いまの遊びの状態を 1 つにまとめる。"""
        snaps: dict = {}
        for name, kind in (("game", "shiritori"), ("janken", "janken"), ("quiz", "")):
            h = getattr(self.hub, name, None)
            if h is None:
                continue
            try:
                for g in h.snapshot().get("games", []):
                    if g.get("dev_id"):
                        g = dict(g)
                        g["kind"] = g.get("kind") or kind
                        snaps.setdefault(g["dev_id"], []).append(g)
            except Exception:  # noqa: BLE001
                pass
        out = []
        for d in devices:
            dev = d.get("id", "")
            gl = snaps.get(dev, [])
            on = next((g for g in gl if g.get("on")), None)
            last = on or next((g for g in gl if g.get("result")), gl[0] if gl else None)
            board = None
            if on:                                               # いまの遊びが、機器の画面に出している絵(手・向き)。管理画面にも同じ絵を出す
                for h in self.hubs():
                    bs = getattr(h.games.get(dev), "board_state", None) if getattr(h, "games", None) else None
                    if bs and (bs.get("ai") or bs.get("bi") or bs.get("aa") or bs.get("ba")):
                        board = dict(bs)
            out.append({"id": dev, "board": board, "name": d.get("name", ""), "online": bool(d.get("online")),
                        "persona": (on or last or {}).get("persona", ""),
                        "kind": (on or {}).get("kind", ""), "label": LABEL.get((on or {}).get("kind", ""), ""),
                        "on": bool(on), "detail": on or last or {}})
        return out

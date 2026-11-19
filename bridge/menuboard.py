"""教材モードで、遊んでいない機器の画面に「言える遊びの一覧」を出し続ける(声は出さない)。

ロボットは遊びが選ばれるまで話さないので、子どもに「何を言えばいいか」を画面で見せる。
遊びが始まると、遊びのほうの表示が上書きする。一覧は短い時間で消える設定を、数秒おきに出し直すので、
遊びの間は出し直さず、遊びが終われば自然に一覧に戻る。
"""
from __future__ import annotations

import threading
import time

import board

SHORT = {"shiritori": "しりとり", "janken": "じゃんけん", "riddle": "なぞなぞ", "omikuji": "おみくじ", "mimic": "ものまね", "chari": "チャリ"}
ORDER = ["shiritori", "janken", "riddle", "omikuji", "mimic", "chari"]
EVERY_S = 5.0
SHOW_MS = 9000


def playing_devs(hub) -> set:
    """いま遊びをしている機器の ID(しりとり・じゃんけん・なぞなぞなど)。"""
    out = set()
    for name in ("game", "janken", "quiz"):
        h = getattr(hub, name, None)
        if h is None:
            continue
        try:
            for g in h.snapshot().get("games", []):
                if g.get("on") and g.get("dev_id"):
                    out.add(g["dev_id"])
        except Exception:  # noqa: BLE001
            pass
    return out


def items(hub) -> list:
    acts = list(getattr(hub, "menu_acts", []) or [])
    return [SHORT[a] for a in ORDER if a in acts]


def tick(hub) -> int:
    """1 回ぶん。一覧を出した機器の数を返す。"""
    if not getattr(hub, "edu_on", False) or getattr(hub, "teacher_only", True):
        return 0                                             # 先生のボタンだけで始めるときは、子どもに「言える遊び」を出さない(何も出さない)
    names = items(hub)
    if not names:
        return 0
    busy = playing_devs(hub)
    n = 0
    for sess in list(hub.live()):
        if getattr(sess, "dev_id", "") in busy:
            continue
        try:
            sess.send({"t": "board", "title": "こえで えらんでね！", "ls": names, "foot": "あそびの なまえを いってね", "ms": SHOW_MS})
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def run(hub) -> None:
    while True:
        try:
            tick(hub)
        except Exception:  # noqa: BLE001 - 表示の不具合で落とさない
            pass
        time.sleep(EVERY_S)


def start(hub) -> None:
    threading.Thread(target=run, args=(hub,), daemon=True).start()

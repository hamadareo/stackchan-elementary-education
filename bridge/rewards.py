"""ごほうび: 機器ごとの「しょうごう(称号)」と「きょうの記録」。

子どもの名前は取れないので、記録は機器ごと(ロボットごと)。遊びが出来事(じゃんけんで勝った・なぞなぞを当てた・おみくじで大吉…)を record() で渡し、
条件を満たすと称号を 1 つずつ「もらった」ことにする。ロボットが祝うのは、1 ラウンドに 1 回まで(take_pending で 1 つだけ取り出す)。
称号と通算の数は ~/.sokutsu_game.json の "rewards" に、機器ごとに保存する(ほかの教材の記録と同じファイルを共有。自分の分だけ書き換える)。
「きょうの記録」は日付が変わると数え直す(きょう何回あそんだか)。
"""
from __future__ import annotations

import json
import os
import threading
import time

from shiritori import FILE_LOCK

# 出来事 → (通算の数え方) add=足す / max=最大を残す
MODES = {"plays": "add", "janken_win": "add", "janken_streak": "max", "riddle_ok": "add", "riddle_perfect": "add", "omikuji": "add", "daikichi": "add",
         "hoi_win": "add", "mimic": "add", "shiritori_chain": "max"}

# (キー, 称号, 出来事, しきい値, ひとこと)
TITLES = [
    ("jk5", "じゃんけん じょうず", "janken_win", 5, "じゃんけんで 5かい かったよ！"),
    ("jk20", "じゃんけん めいじん", "janken_win", 20, "じゃんけんで 20かい かったよ！"),
    ("jk3s", "じゃんけん 3れんしょう", "janken_streak", 3, "3かい れんぞくで かったよ！"),
    ("rd10", "なぞなぞ くん", "riddle_ok", 10, "なぞなぞを 10もん とけたよ！"),
    ("rd25", "なぞなぞ はかせ", "riddle_ok", 25, "なぞなぞを 25もん とけたよ！"),
    ("rdp", "なぞなぞ パーフェクト", "riddle_perfect", 1, "5もん ぜんぶ せいかい！"),
    ("om5", "おみくじ マスター", "omikuji", 5, "おみくじを 5かい ひいたよ！"),
    ("omd", "おみくじ だいきち", "daikichi", 1, "だいきちを ひいたよ！"),
    ("hoi1", "ホイ しょうり", "hoi_win", 1, "あっちむいてホイで かったよ！"),
    ("hoi5", "ホイ めいじん", "hoi_win", 5, "あっちむいてホイで 5かい かったよ！"),
    ("mm10", "ものまね じょうず", "mimic", 10, "ものまねを 10かい したよ！"),
    ("mm30", "ものまね めいじん", "mimic", 30, "ものまねを 30かい したよ！"),
    ("sh5", "しりとり がんばりや", "shiritori_chain", 5, "しりとりを 5こ つなげたよ！"),
    ("sh10", "しりとり めいじん", "shiritori_chain", 10, "しりとりを 10こ つなげたよ！"),
    ("pl10", "あそびの なかま", "plays", 10, "10かい あそんだよ！"),
    ("pl50", "あそびの たつじん", "plays", 50, "50かい あそんだよ！"),
]
TITLE = {t[0]: t for t in TITLES}
EVENTS = {t[2] for t in TITLES}


# ロボットが言う台詞(キャラごとの口調)。{t}=称号 {n}=きょうの回数
TITLE_LINE = {"ずんだ": "「{t}」の しょうごうを ゲットしたのだ！すごいのだ！", "めたん": "「{t}」の しょうごうを ゲットですね。おめでとうございます。",
              "つむぎ": "「{t}」の しょうごうを ゲットしたよ！おめでとう！"}
DAILY = {"ずんだ": ["きょうは {n}かい あそんだのだ！たのしかったのだ！", "きょうは {n}かいも あそんだのだ！"],
         "めたん": ["きょうは {n}かい あそびましたね。", "きょうは {n}かいも あそびましたね。たのしかったです。"],
         "つむぎ": ["きょうは {n}かい あそんだね！", "きょうは {n}かいも あそんだね！たのしかったね！"]}
DAILY_MIN = 2                                  # きょう 2 回目から言う(1 回目では言わない)
DAILY_MAX_WARM = 12


def title_line(name: str, label: str) -> str:
    return TITLE_LINE.get(name, TITLE_LINE["つむぎ"]).replace("{t}", label)


def daily_line(name: str, n: int, k: int = 0) -> str:
    opts = DAILY.get(name, DAILY["つむぎ"])
    return opts[(0 if n < 5 else 1) if k < 0 else min(k, len(opts) - 1)].replace("{n}", str(n))


_SHARED: dict = {}


def shared(path: "str | None" = None) -> "Rewards":
    """同じ記録ファイルには、同じ Rewards を使う(教材ごとに別々に持つと、書き込みが食い違う)。"""
    key = path or os.environ.get("SC_GAME", os.path.expanduser("~/.sokutsu_game.json"))
    if key not in _SHARED:
        _SHARED[key] = Rewards(key)
    return _SHARED[key]


def today_str(now: "float | None" = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


class Rewards:
    def __init__(self, path: "str | None" = None, clock=time.time):
        self.path = path or os.environ.get("SC_GAME", os.path.expanduser("~/.sokutsu_game.json"))
        self.clock = clock
        self.lock = threading.Lock()
        self.dev: dict = {}
        try:
            d = json.load(open(self.path)).get("rewards", {})
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, dict):
                        self.dev[str(k)] = {"n": {a: int(b) for a, b in (v.get("n") or {}).items() if isinstance(b, (int, float))},
                                            "titles": [t for t in (v.get("titles") or []) if t in TITLE], "pending": [t for t in (v.get("pending") or []) if t in TITLE],
                                            "diff": int(v.get("diff", 1)) if str(v.get("diff", 1)).lstrip("-").isdigit() else 1, "day": str(v.get("day", "")), "today": {a: int(b) for a, b in (v.get("today") or {}).items() if isinstance(b, (int, float))}}
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    # ---- 保存
    def _save(self) -> None:
        try:
            with FILE_LOCK:
                try:
                    allv = json.load(open(self.path))
                    if not isinstance(allv, dict):
                        allv = {}
                except (OSError, ValueError):
                    allv = {}
                allv["rewards"] = self.dev
                tmp = f"{self.path}.tmp{os.getpid()}r"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(allv, f)
                os.replace(tmp, self.path)
        except OSError:
            pass

    def _d(self, dev: str) -> dict:
        d = self.dev.setdefault(dev, {"n": {}, "titles": [], "pending": [], "diff": 1, "day": "", "today": {}})
        day = today_str(self.clock())
        if d.get("day") != day:                                          # 日が変わったら、きょうの記録を数え直す
            d["day"], d["today"] = day, {}
        return d

    # ---- 出来事
    def record(self, dev: str, event: str, v: int = 1) -> "list":
        """出来事を記録し、新しくもらった称号(キーのリスト)を返す。もらった称号は pending に入る(祝うのは take_pending)。"""
        if not dev or event not in MODES:
            return []
        with self.lock:
            d = self._d(dev)
            n = d["n"]
            if MODES[event] == "max":
                n[event] = max(n.get(event, 0), int(v))
            else:
                n[event] = n.get(event, 0) + int(v)
            d["today"][event] = d["today"].get(event, 0) + (int(v) if MODES[event] == "add" else 0)
            new = []
            for key, _label, ev, th, _msg in TITLES:
                if ev == event and key not in d["titles"] and n.get(event, 0) >= th:
                    d["titles"].append(key)
                    d["pending"].append(key)
                    new.append(key)
            self._save()
            return new

    def take_pending(self, dev: str):
        """祝う称号を 1 つ取り出す(なければ None)。1 ラウンドに 1 回まで、のために 1 つだけ。"""
        with self.lock:
            d = self._d(dev)
            if not d["pending"]:
                return None
            key = d["pending"].pop(0)
            self._save()
            return TITLE[key]

    def get_diff(self, dev: str) -> int:
        with self.lock:
            return max(0, min(2, int(self._d(dev).get("diff", 1))))

    def set_diff(self, dev: str, v: int) -> None:
        with self.lock:
            self._d(dev)["diff"] = max(0, min(2, int(v)))
            self._save()

    def today_plays(self, dev: str) -> int:
        with self.lock:
            return int(self._d(dev)["today"].get("plays", 0))

    def titles(self, dev: str) -> list:
        with self.lock:
            return [TITLE[k][1] for k in self._d(dev)["titles"] if k in TITLE]

    def snapshot(self, dev: str) -> dict:
        with self.lock:
            d = self._d(dev)
            return {"titles": [TITLE[k][1] for k in d["titles"] if k in TITLE], "today_plays": int(d["today"].get("plays", 0)), "pending": len(d["pending"])}

"""じゃんけん(子供たちの教材)。各機器が独立に、自分の前の子どもたちとじゃんけんをする。

しりとり(shiritori.py)と同じ作りで、声の出し方・聞き取りの守り(ロボットの声の回り込みを捨てる)・先生の入力・
機器ごとの独立・ほかの機器が話している間は聞かない仕組みをそのまま使う。判定はすべてコード側で行う(AI には任せない)。
"""
from __future__ import annotations

import queue
import re
import threading
import time

import shiritori
from shiritori import GameHub, Shiritori, to_hira

HANDS = ("gu", "choki", "pa")
NAME = {"gu": "ぐー", "choki": "ちょき", "pa": "ぱー"}
BEATS = {"gu": "choki", "choki": "pa", "pa": "gu"}      # 左が右に勝つ

# 言い換え・聞き取りの揺れ(ひらがなにしてから、伸ばす棒を取った形で見る)
WORDS = {
    "gu": ("ぐ", "ぐう", "ぐー", "ぐぅ", "いし", "石", "岩", "いわ", "ぐーぐー", "ぐ―"),
    "choki": ("ちょき", "ちょ", "ちょっき", "ちょきー", "はさみ", "鋏", "ちょき!", "ちよき", "ちょーき"),
    "pa": ("ぱ", "ぱあ", "ぱー", "ぱぁ", "かみ", "紙", "ぱーぱー", "ぱーー"),
}
QUIT = re.compile(r"やめ|おわり|終わり|おしまい|もういい|もう終|あそばない|やだ")


def _norm(text: str) -> str:
    t = to_hira(text or "")
    t = re.sub(r"[\s、。,.!?！？・「」『』~〜…]+", "", t)
    return t


def parse_hand(text: str) -> "str | None":
    """子どもの言葉 → 手(gu / choki / pa)。聞き取れない・どの手か決められないときは None。"""
    t = _norm(text)
    if not t:
        return None
    flat = t.replace("ー", "").replace("―", "")
    found = set()
    for h, words in WORDS.items():
        for w in words:
            w2 = to_hira(w).replace("ー", "").replace("―", "").replace("!", "")
            if not w2:
                continue
            if t == to_hira(w) or flat == w2 or (len(w2) >= 3 and w2 in flat):
                found.add(h)
    for h, alias in (("gu", "ぐー"), ("choki", "ちょき"), ("pa", "ぱー")):          # 「ぐーだよ」のように文の中にあるとき
        if alias in t:
            found.add(h)
    if len(found) == 1:
        return next(iter(found))
    if not found and len(flat) <= 4:                      # STT の揺れ: 先頭の音で決める(ぐ・ち・ぱ のどれかだけ)
        head = {"ぐ": "gu", "ち": "choki", "ぱ": "pa", "ば": "pa", "く": "gu"}.get(flat[:1])
        if head:
            return head
    return None


def judge(kid: str, robot: str) -> str:
    """kid_win / robot_win / draw"""
    if kid == robot:
        return "draw"
    return "kid_win" if BEATS[kid] == robot else "robot_win"


def pick_robot_hand(rnd, history: list) -> str:
    """ロボットの手。乱数だが、同じ手が 3 回続かないようにする。"""
    hands = list(HANDS)
    if len(history) >= 2 and history[-1] == history[-2]:
        hands = [h for h in hands if h != history[-1]]
    return rnd.choice(hands)


class Janken(Shiritori):
    LINES = {   # キャラごとの口調。{r}=ロボットの手
        "ずんだ": {"kid_win": ["ぼくは{r}なのだ。きみの勝ちなのだ！", "{r}を出したのだ。くやしいのだ！きみの勝ち！"],
                 "robot_win": ["ぼくは{r}なのだ。ぼくの勝ちなのだ！", "{r}なのだ！やったのだ、ぼくの勝ち！"],
                 "draw": ["ぼくも{r}なのだ！あいこなのだ！"]},
        "めたん": {"kid_win": ["わたしは{r}です。あなたの勝ちですね。", "{r}でした。くやしいですが、あなたの勝ちです。"],
                 "robot_win": ["わたしは{r}です。わたしの勝ちですね。", "{r}です。ふふ、わたしの勝ちです。"],
                 "draw": ["わたしも{r}です。あいこですね。"]},
        "つむぎ": {"kid_win": ["わたしは{r}だよ。あなたの勝ち！すごいね！", "{r}だったよ。まけちゃった！"],
                 "robot_win": ["わたしは{r}だよ。わたしの勝ちだね！", "{r}でした。やったあ、わたしの勝ち！"],
                 "draw": ["わたしも{r}だよ。あいこだね！"]},
    }
    EMO = {"kid_win": "sad", "robot_win": "excited", "draw": "surprise"}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        for key in ("wins", "losses", "draws", "streak_best", "plays"):
            self.stats.setdefault(key, 0)
        self.wins = self.losses = self.draws = self.streak = 0
        self.hands: list = []                  # [{"kid","robot","res"}]
        self.misses = 0

    # ---- 開始
    def start(self, first: str = "robot", level: str = "easy", dev_id: "str | None" = None) -> "tuple[bool, str]":
        if self.on:
            return False, "すでに遊んでいます(やめてから始めてください)"
        if self.discuss is not None and self.discuss.on:
            return False, "いまロボットが会話中です(止めてから始めてください)"
        eng = self.eng_getter()
        if eng is None:
            return False, "エンジンの準備中です"
        pk = self.discuss.pick() if self.discuss is not None else []
        if dev_id:
            pk = [(r, s) for r, s in pk if r["id"] == dev_id]
        if not pk:
            return False, "接続中の機器がありません"
        from discuss import PERSONAS
        self.robots = [{"id": r["id"], "name": PERSONAS[self.discuss.persona_of(r["id"])]["name"], "dev_name": r["name"],
                        "voice": PERSONAS[self.discuss.persona_of(r["id"])]["voice"]} for r, _s in pk][:1]
        self.chain, self.need, self.turn, self.msg, self.result, self.score = [], "", "", "", "", 0
        self.wins = self.losses = self.draws = self.streak = self.misses = 0
        self.hands = []
        self.speaking, self.quiet_until = False, 0.0
        while not self.q.empty():
            self.q.get_nowait()
        self._stop.clear()
        self.on = True
        self.stats["plays"] += 1
        self._save()
        self.hub.event("じゃんけんを始めます")
        threading.Thread(target=self._run, args=(eng,), daemon=True).start()
        return True, ""

    # ---- 進行
    def _line(self, res: str, hand: str) -> str:
        name = self.robots[0]["name"]
        ls = self.LINES.get(name, self.LINES["ずんだ"])[res]
        return ls[len(self.hands) % len(ls)].format(r=NAME[hand])

    def _run(self, eng) -> None:
        try:
            self.turn = ""
            self._speak(eng, 0, "じゃんけんで、あそぼう！ぐー、ちょき、ぱーのどれかを、声で出してね。", "excited")
            call = "さいしょはぐー、じゃんけん…ぽん！"
            while not self._stop.is_set():
                if call:
                    self.msg = call
                    self._speak(eng, 0, call, "excited")
                self.turn = "kid"
                self._face("listening")
                robot = pick_robot_hand(self.rnd, [h["robot"] for h in self.hands])
                try:
                    text = self.q.get(timeout=15)
                except queue.Empty:
                    text = ""
                if self._stop.is_set() or text is None:
                    break
                if text and QUIT.search(text) and parse_hand(text) is None:
                    self.msg = "またあそぼうね！"
                    self._say_robot(eng, "たのしかったよ！またあそぼうね！ばいばーい！", "joy")
                    break
                hand = parse_hand(text) if text else None
                if hand is None:                                     # 聞き取れない
                    self.misses += 1
                    if self.misses >= 5:
                        self.msg = "またあそぼうね！"
                        self._say_robot(eng, "またあそぼうね！ばいばーい！", "joy")
                        break
                    self.turn = ""
                    self._say_robot(eng, "もういっかい！ぐー、ちょき、ぱー！", "doubt")
                    call = "じゃんけん…ぽん！"
                    continue
                self.misses = 0
                self.turn = self.robots[0]["name"]
                res = judge(hand, robot)
                self.hands.append({"kid": hand, "robot": robot, "res": res})
                del self.hands[:-30]
                if res == "kid_win":
                    self.wins += 1
                    self.streak += 1
                    self.stats["wins"] += 1
                    self.stats["streak_best"] = max(self.stats["streak_best"], self.streak)
                elif res == "robot_win":
                    self.losses += 1
                    self.streak = 0
                    self.stats["losses"] += 1
                else:
                    self.draws += 1
                    self.stats["draws"] = self.stats.get("draws", 0) + 1
                self._save()
                self.score = self.wins
                self.msg = f'きみ: {NAME[hand]} / ロボット: {NAME[robot]}'
                self._speak(eng, 0, self._line(res, robot), self.EMO[res])
                if self._stop.is_set():
                    break
                call = "あいこでしょ！" if res == "draw" else "つぎいくよ！さいしょはぐー、じゃんけん…ぽん！"
        except Exception as e:  # noqa: BLE001
            self.msg = f"エラー: {type(e).__name__}"
        finally:
            if not self.result:
                self.result = "stopped"
            self.turn = ""
            self._face("neutral")
            self.on = False
            self.hub.event(f"じゃんけん終了(勝ち {self.wins}・負け {self.losses}・あいこ {self.draws})")

    def snapshot(self) -> dict:
        r = self.robots[0] if self.robots else {}
        return {"dev_id": r.get("id", ""), "dev_name": r.get("dev_name", ""), "persona": r.get("name", ""),
                "on": self.on, "turn": self.turn, "msg": self.msg, "result": self.result,
                "wins": self.wins, "losses": self.losses, "draws": self.draws, "streak": self.streak,
                "best": self.stats.get("streak_best", 0), "plays": self.stats.get("plays", 0),
                "hands": [{"kid": NAME[h["kid"]], "robot": NAME[h["robot"]], "res": h["res"]} for h in self.hands[-8:]]}


class JankenHub(GameHub):
    CLS = Janken
    SKEY = "janken"
    KEYS = ("plays", "wins", "losses", "draws", "streak_best")
    LABEL = "じゃんけん"

    def snapshot(self) -> dict:
        gs = [g.snapshot() for g in self.games.values() if g.robots]
        return {"on": self.own_on, "games": gs, "best": self.stats.get("streak_best", 0), "plays": self.stats.get("plays", 0),
                "wins": self.stats.get("wins", 0), "losses": self.stats.get("losses", 0)}

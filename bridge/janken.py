"""じゃんけん(子供たちの教材)。各機器が独立に、自分の前の子どもたちとじゃんけんをする。

しりとり(shiritori.py)と同じ作りで、声の出し方・聞き取りの守り(ロボットの声の回り込みを捨てる)・先生の入力・
機器ごとの独立・ほかの機器が話している間は聞かない仕組みをそのまま使う。判定はすべてコード側で行う(AI には任せない)。
"""
from __future__ import annotations

import queue
import re
import threading
import time

import camguide
import hand as hand_mod
import shiritori
from playfx import Picker, tone
from shiritori import GameHub, Shiritori, to_hira

HANDS = ("gu", "choki", "pa")
NAME = {"gu": "ぐー", "choki": "ちょき", "pa": "ぱー"}
BEATS = {"gu": "choki", "choki": "pa", "pa": "gu"}      # 左が右に勝つ

# 言い換え・聞き取りの揺れ(ひらがなにしてから、伸ばす棒を取った形で見る)
WORDS = {
    "gu": ("ぐ", "ぐう", "ぐー", "ぐぅ", "ぐっ", "いし", "石", "岩", "いわ", "ぐーぐー", "ぐ―", "ぐうー", "ぐーう", "ぐぅー", "グ"),
    "choki": ("ちょき", "ちょ", "ちょっき", "ちょきー", "はさみ", "鋏", "ちょき!", "ちよき", "ちょーき", "ちょきちょき", "ちょぎ", "ちゃき", "ちょっきー"),
    "pa": ("ぱ", "ぱあ", "ぱー", "ぱぁ", "かみ", "紙", "ぱーぱー", "ぱーー", "ぱっ", "はー", "はあ", "ぱーう", "ぱうー", "ぱ―"),
}
CAM_WINDOW = 5.0            # 「ぽん！」のあと、カメラで手を見る秒数(機器から届く画像は、実測で毎秒1枚前後)
CAM_MAX_FRAMES = 14
QUIT = re.compile(r"やめ|おわり|終わり|おしまい|もういい|もう終|あそばない|やだ|ばいばい|バイバイ|つまんない|つまらない")


def del_old(frames: list) -> None:
    del frames[:-CAM_MAX_FRAMES]


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
    if not found and 1 <= len(flat) <= 2 and not QUIT.search(t):   # STT の揺れ: 1〜2 文字の短い聞き取りは、先頭の音で決める(ぐ・ち・ぱ。「ばいばい」「くま」のような言葉は決めない)
        head = {"ぐ": "gu", "ち": "choki", "ぱ": "pa"}.get(flat[:1])
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


def lose_run(history: list) -> int:
    """子どもが負け(ロボットが勝ち)つづけている回数。あいこは数えず、いちど子どもが勝つと 0。"""
    n = 0
    for h in reversed(history):
        r = h.get("res")
        if r == "robot_win":
            n += 1
        elif r == "draw":
            continue
        else:
            break
    return n


def mercy_p(run: int) -> float:
    """負けが続いた子に、勝ち・あいこを出す確率。2 連敗までは 0、そのあと すこしずつ上がる(上限 0.5)。"""
    return 0.0 if run < 2 else min(0.5, 0.2 + 0.15 * (run - 2))


def mercy_hand(rnd, kid: str, robot: str, history: list, run: int) -> str:
    """子どもが負け続けていたら、確率で、子どもが勝つ(3 : あいこ 1)手に かえる。同じ手が 3 回続かないきまりは、かならず守る。"""
    if rnd.random() >= mercy_p(run):
        return robot
    lose_to_kid = next(h for h in HANDS if BEATS[kid] == h)          # 子どもの手が かつ手(=ロボットが負ける手)
    want = lose_to_kid if rnd.random() < 0.75 else kid
    last2 = [h.get("robot") for h in history[-2:]]
    if len(last2) == 2 and last2[0] == last2[1] == want:
        return robot
    return want


JK = {
    "intro": ["じゃんけんで、あそぼう！ぐー、ちょき、ぱーを、こえでいうか、カメラに手を見せてね。", "じゃんけん{d}！ぐー、ちょき、ぱー、どれを だすかな？こえでも、カメラでも いいよ！",
              "じゃんけん しよう！てを カメラに みせても、こえで いっても だいじょうぶ{d}！", "さあ、じゃんけん{d}！{me}に かてるかな？"],
    "intro_voice": ["じゃんけんで、あそぼう！ぐー、ちょき、ぱーのどれかを、こえで出してね。", "じゃんけん{d}！ぐー、ちょき、ぱーを、こえで いってね！", "じゃんけん しよう！ぐー、ちょき、ぱー、こえで どうぞ！"],
    "first": ["さいしょはぐー、じゃんけん…ぽん！", "いくよー！さいしょはぐー、じゃんけん…ぽん！", "さいしょは ぐー！じゃんけん…ぽん！", "じゃーんけーん…ぽん！"],
    "next": ["つぎいくよ！さいしょはぐー、じゃんけん…ぽん！", "もういっかい！じゃんけん…ぽん！", "つぎは まけないよ！じゃんけん…ぽん！", "どんどん いくよ！じゃんけん…ぽん！", "さあ、つぎ！じゃんけん…ぽん！"],
    "aiko": ["あいこでしょ！", "あいこで…しょ！", "おっと あいこ！あいこで…しょ！", "もういっかい あいこで…しょ！"],
    "miss": ["もういっかい！ぐー、ちょき、ぱー！手をカメラに見せてもいいよ！", "んー、わからなかった！もういっかい！", "ぐー、ちょき、ぱーで おしえてね！"],
    "miss2": ["ぐー、ちょき、ぱーの どれかな？", "ゆっくり おしえてね！", "てを カメラに みせても いいよ！"],
    "miss_voice2": ["ぐー、ちょき、ぱーの どれかな？", "ゆっくり おおきな こえで どうぞ！", "ひとつずつ、ゆっくり いってね！"],
    "miss_voice": ["もういっかい！ぐー、ちょき、ぱー！", "ぐー、ちょき、ぱーで いってね！", "きこえなかった！もういっかい！"],
    "streak": ["{s}かい つづけて きみの かち{d}！すごい！", "また かった！{s}れんしょう{d}！", "{s}れんしょう！きみ さいきょう{d}！", "すごーい、{s}かい つづけて かった{v}！"],
    "lose_streak": ["{me}が {s}かい つづけて かったよ！つぎは かてるかな？", "{me}の {s}れんしょう！まけないで！"],
    "record": ["きみの いちばん すごいのは {b}れんしょう{d}！", "これまでで いちばんは {b}かい つづけて かった こと{d}！"],
    "bye": ["たのしかったよ！またあそぼうね！ばいばーい！", "いっぱい あそべた{v}！またね！", "ありがとう！またじゃんけん しようね！", "たのしかった{n}！つぎも まけないよ！"],
}


class Janken(Shiritori):
    LINES = {   # キャラごとの口調。{r}=ロボットの手
        "ずんだ": {"kid_win": ["ぼくは{r}なのだ。きみの かちなのだ！", "{r}を だしたのだ。くやしいのだ！きみの かち！"],
                 "robot_win": ["ぼくは{r}なのだ。ぼくの かちなのだ！", "{r}なのだ！やったのだ、ぼくの かち！"],
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
        self.cam_hand = ""                     # カメラで最後に見分けた手(表示用)
        self.cam_note = ""
        self.pick = Picker(self.rnd)
        self.lstreak = 0                       # ロボットの連勝
        self.cam_state = ""                    # カメラでの子どもの見え方(camguide.STATES。まだ確認していなければ空)
        self.cam_label = ""
        self._coach_obj = camguide.Coach(self.clock)
        self._cam_need = True                  # 次の「ぽん」の前に、子どもの位置を確認する
        self._face_need = True                 # (あっち向いてホイ)次の「ホイ」の前に、顔の向きを読める位置か確認する

    def cam_enabled(self) -> bool:
        return bool(getattr(self.hub, "janken_cam", True)) and not self._coach_obj.gave_up       # うまく見えないと判断したあとは、声だけで進める

    # ---- カメラの見え方の案内(近すぎ・遠すぎ・見えない・暗い…のとき、声と画面で子どもに知らせる)
    def _cam_reset(self) -> None:
        self._coach_obj = camguide.Coach(self.clock)
        self._cam_need = self._face_need = True
        self.cam_state = self.cam_label = ""

    def _nap(self, sec: float) -> None:
        """待つ(中止がかかったらすぐ抜ける。演出の早送り(FAST)の影響は受けない)。"""
        t0 = time.time()
        while time.time() - t0 < sec and not self._stop.is_set():
            time.sleep(0.05)

    def _cam_grab(self, secs: float = camguide.GRAB_S, n: int = camguide.GRAB_N) -> list:
        """その機器のカメラ画像(JPEG)を、n 枚か secs 秒ぶん集める。画像の受け口は、集め終わったらもとに戻す。"""
        sess = self.hub.by_id(self.robots[0]["id"]) if hasattr(self.hub, "by_id") and self.robots else None
        if sess is None:
            return []
        frames: list = []
        prev = getattr(sess, "hand_cb", None)

        def cb(j):
            frames.append(j)
            if prev is not None:
                try:
                    prev(j)
                except Exception:  # noqa: BLE001
                    pass

        sess.hand_cb = cb
        t0 = time.time()
        try:
            while time.time() - t0 < secs and not self._stop.is_set():
                if len(frames) >= n:
                    break
                time.sleep(0.05)
        finally:
            sess.hand_cb = prev
        return list(frames)

    def _cam_set(self, state: str) -> None:
        self.cam_state = "" if state == "unknown" else state
        self.cam_label = camguide.LABEL.get(state, "")

    def _cam_say(self, eng, state: str) -> None:
        """指示を、画面(大きな字)と声で出す。"""
        text = camguide.line(self.robots[0]["name"], state, self.pick)
        big, foot = camguide.board_text(state)
        self.turn = ""
        self.msg = text
        self._board(title=camguide.TITLE, a=("", big), foot=foot, ms=int((camguide.FIX_WAIT_S + 5) * 1000))
        self._speak(eng, 0, text, "joy")

    def _cam_giveup(self, eng, kind: str) -> None:
        """うまく見えないので、声だけで進める(以後、この遊びでは カメラを使わない)。"""
        text = camguide.giveup_line(self.robots[0]["name"], kind, self.pick)
        big, foot = camguide.giveup_board(kind)
        self.turn = ""
        self.msg = text
        self.cam_note = "うまく見えないので、声だけで進めます"
        self._board(title=camguide.TITLE, a=("", big), foot=foot, ms=6000)
        self._speak(eng, 0, text, "joy")

    def _cam_precheck(self, eng, kind: str = "jan") -> None:
        """始める前に、子どもがカメラに ちょうどよく見えているか確かめる。だめなら指示して、直るのを待つ(上限あり)。
        kind: jan=手のため(顔が見えればよい) / face=顔の向きを見るため(目の位置が読める大きさ・向きが要る)。"""
        coach = self._coach_obj
        face = getattr(eng, "face", None) if eng is not None else None
        need = self._face_need if kind == "face" else self._cam_need
        if not need or coach.skip or not self.cam_enabled() or not camguide.can_see(face):
            return
        if kind == "face":
            self._face_need = False
        else:
            self._cam_need = False
        t_end = time.time() + camguide.PRE_MAX_S
        shown = False
        while not self._stop.is_set() and time.time() < t_end:
            r = camguide.analyze(face, self._cam_grab(), need_eyes=(kind == "face"))
            st = r["state"]
            self._cam_set(st)
            act = coach.next(st)
            if act in ("ok", "muted"):
                break
            if act == "giveup":
                self._cam_giveup(eng, kind)
                shown = False
                break
            if act == "say":
                self._cam_say(eng, st)
                shown = True
                self._nap(camguide.FIX_WAIT_S)
            else:
                self._nap(camguide.RECHECK_S)
        if shown and not self._stop.is_set():
            self._board_clear()
        self.turn = ""

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
        self.wins = self.losses = self.draws = self.streak = self.misses = self.lstreak = 0
        self.hands = []
        self.cam_hand = self.cam_note = ""
        self.speaking, self.quiet_until = False, 0.0
        while not self.q.empty():
            self.q.get_nowait()
        self._stop.clear()
        self.on = True
        self.stats["plays"] += 1
        self._ev("plays")
        self._daily_done, self._cel_key, self._filler_t = False, None, -1e9
        self._save()
        self.hub.event("じゃんけんを始めます")
        threading.Thread(target=self._run, args=(eng,), daemon=True).start()
        return True, ""

    # ---- 進行
    def _jk(self, scene: str, **kw) -> str:
        s = tone(self.robots[0]["name"], self.pick(f"jk:{scene}", JK[scene]), self.pick)
        for k, v in kw.items():
            s = s.replace("{" + k + "}", str(v))
        return s

    def _line(self, res: str, hand: str) -> str:
        name = self.robots[0]["name"]
        ls = self.LINES.get(name, self.LINES["ずんだ"])[res]
        return self.pick(f"jk:res:{res}", [x.format(r=NAME[hand]) for x in ls])

    def _wait_hand(self, eng=None) -> "tuple[str | None, str | None]":
        """子どもの手を待つ。(聞き取った言葉, カメラで見分けた手)。声が優先で、声で手が決まらなければカメラ。

        カメラが使えるとき: 「ぽん！」のあと CAM_WINDOW 秒、その機器の画像を集める(この間、顔追従の首の動きは止まる)。
        声の聞き取りが先に手(またはやめる)になればそれを使い、窓が閉じるまで声が無ければ、集めた画像の多数決で手を決める。
        手が決まらなければ、残りの時間(15秒まで)は、従来どおり声だけを待つ。
        eng を渡すと、窓が閉じても手が見つからないとき、原因(近すぎ・遠すぎ・暗い・手が見えない…)を調べて、声と画面で子どもに指示し、もう一度見る
        (同じ指示は 2 回まで。それでも直らなければ、声だけで進める)。
        """
        sess = self.hub.by_id(self.robots[0]["id"]) if hasattr(self.hub, "by_id") and self.robots else None
        if not (self.cam_enabled() and sess is not None and hand_mod.HandRecognizer.available()):
            try:
                return self.q.get(timeout=15), None
            except queue.Empty:
                return "", None
        frames: list = []
        start = self.clock()
        collect = lambda j: (frames.append(j), del_old(frames))      # noqa: E731
        sess.hand_cb = collect
        self.msg = "カメラで、手を見ているよ…"
        voice_miss = ""
        cam_done = False
        budget = camguide.HAND_BUDGET if eng is not None else 0
        face = getattr(eng, "face", None) if eng is not None else None
        try:
            while self.clock() - start < 15.0:
                try:
                    text = self.q.get(timeout=0.25)
                except queue.Empty:
                    text = ""
                if self._stop.is_set() or text is None:
                    return None, None
                if text:
                    if parse_hand(text) is not None or QUIT.search(text):
                        return text, None                          # 声で決まった(声が優先)
                    voice_miss = text                              # 手に聞こえない言葉: カメラに任せる
                if not cam_done and self.clock() - start >= CAM_WINDOW:
                    cam_done = True
                    sess.hand_cb = None
                    h, per = hand_mod.recognize_jpegs(list(frames))
                    self._dump(frames)
                    self.cam_note = f"{len(frames)}枚中 {sum(1 for x in per if x)}枚で手を検出"
                    if h:
                        self.cam_hand = NAME[h]
                        return "", h
                    self.cam_hand = ""
                    self._cam_need = True                          # 次の「ぽん」の前に、位置を確かめなおす
                    if budget > 0 and frames and camguide.can_see(face):          # 原因を調べて、指示する
                        why = camguide.hand_reason(camguide.analyze(face, frames)["state"])
                        act = self._coach_obj.next(why) if why else "ok"
                        if why:
                            self._cam_set(why)
                        if act in ("say", "giveup"):
                            if act == "say":
                                self._cam_say(eng, why)
                                budget -= 1
                                frames.clear()
                                cam_done = False
                                sess.hand_cb = collect
                            else:
                                self._cam_giveup(eng, "jan")           # 以後は声だけ
                            self.turn = "kid"
                            self._face("listening")
                            self.msg = "カメラで、手を見ているよ…" if act == "say" else "こえで おしえてね"
                            start = self.clock()
                            voice_miss = ""
                            continue
            return voice_miss, None
        finally:
            sess.hand_cb = None

    def _dump(self, frames: list) -> None:
        """診断用: 直近の窓で見た画像を /tmp/sc_hand_dbg に置く(次のじゃんけんで上書き)。"""
        try:
            import os
            d = "/tmp/sc_hand_dbg"
            os.makedirs(d, exist_ok=True)
            for i, j in enumerate(frames[:8]):
                open(os.path.join(d, f"{getattr(self, 'dev_name', 'dev')}_{i}.jpg"), "wb").write(j if isinstance(j, (bytes, bytearray)) else b"")
        except Exception:  # noqa: BLE001
            pass

    def _run(self, eng) -> None:
        try:
            self._cam_reset()
            self.turn = ""
            self._speak(eng, 0, self._jk("intro" if self.cam_enabled() and hand_mod.HandRecognizer.available() else "intro_voice"), "excited")
            call = self._jk("first")
            while not self._stop.is_set():
                self._cam_precheck(eng, "jan")                      # 近すぎ・遠すぎ・見えないときは、「ぽん」の前に指示する
                if self._stop.is_set():
                    break
                if call:
                    self.msg = call
                    self._speak(eng, 0, call, "excited")
                self.turn = "kid"
                self._face("listening")
                robot = pick_robot_hand(self.rnd, [h["robot"] for h in self.hands])
                text, cam = self._wait_hand(eng)
                if self._stop.is_set() or text is None:
                    break
                if text and QUIT.search(text) and parse_hand(text) is None:
                    self.msg = "またあそぼうね！"
                    self._daily(eng)
                    self._say_robot(eng, self._jk("bye"), "joy")
                    break
                hand = parse_hand(text) if text else None
                via = "voice"
                if hand is None and cam:                             # 声で決まらなかったので、カメラで見た手
                    hand, via = cam, "camera"
                if hand is None:                                     # 聞き取れない・見えない
                    self.misses += 1
                    if self.misses >= 5:
                        self.msg = "またあそぼうね！"
                        self._daily(eng)
                        self._say_robot(eng, self._jk("bye"), "joy")
                        break
                    self.turn = ""
                    scene = ("miss" if self.cam_enabled() else "miss_voice") + ("2" if self.misses >= 2 else "")      # 「もういっかい」は 1 回だけ。つづくときは言い方をかえる
                    self._say_robot(eng, self._jk(scene), "doubt")
                    call = "じゃんけん…ぽん！"
                    continue
                self.misses = 0
                self.turn = self.robots[0]["name"]
                robot = mercy_hand(self.rnd, hand, robot, self.hands, lose_run(self.hands))     # 負けが続いたら、すこし勝たせる
                res = judge(hand, robot)
                self.hands.append({"kid": hand, "robot": robot, "res": res, "via": via})
                del self.hands[:-30]
                if res == "kid_win":
                    self.wins += 1
                    self.streak += 1
                    self._ev("janken_win")
                    self._ev("janken_streak", self.streak)
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
                self.msg = f'きみ: {NAME[hand]}{"(カメラ)" if via == "camera" else ""} / ロボット: {NAME[robot]}'
                foot = "あいこ！" if res == "draw" else ("きみの かち！" if res == "kid_win" else f'{self.robots[0]["name"]}の かち！')
                self._board(title="じゃんけん", a=(self.robots[0]["name"], NAME[robot]), b=("きみ", NAME[hand]), foot=foot, ms=6000, a_icon=robot, b_icon=hand, fx="win" if res == "kid_win" else "")
                self._speak(eng, 0, self._line(res, robot), self.EMO[res])
                if self._stop.is_set():
                    break
                self.lstreak = self.lstreak + 1 if res == "robot_win" else 0
                if res == "kid_win" and self.streak >= 2:                       # れんしょうを ほめる
                    extra = self._jk("streak", s=self.streak)
                    if self.streak >= 3 and self.streak >= self.stats.get("streak_best", 0):
                        extra += " " + self._jk("record", b=self.stats["streak_best"])
                    self._nod(2)
                    self._speak(eng, 0, extra, "excited")
                elif res == "robot_win" and self.lstreak >= 3:
                    self._speak(eng, 0, self._jk("lose_streak", s=self.lstreak), "joy")
                self._celebrate(eng, len(self.hands) + self.stats.get("plays", 0) * 1000)
                call = self._jk("aiko") if res == "draw" else self._jk("next")
        except Exception as e:  # noqa: BLE001
            self.msg = f"エラー: {type(e).__name__}"
        finally:
            if not self.result:
                self.result = "stopped"
            self.turn = ""
            self._board_clear()
            self._face("neutral")
            self.on = False
            self.hub.event(f"じゃんけん終了(勝ち {self.wins}・負け {self.losses}・あいこ {self.draws})")

    def snapshot(self) -> dict:
        r = self.robots[0] if self.robots else {}
        return {"dev_id": r.get("id", ""), "dev_name": r.get("dev_name", ""), "persona": r.get("name", ""),
                "on": self.on, "turn": self.turn, "msg": self.msg, "result": self.result,
                "wins": self.wins, "losses": self.losses, "draws": self.draws, "streak": self.streak,
                "best": self.stats.get("streak_best", 0), "plays": self.stats.get("plays", 0),
                "cam_on": self.cam_enabled(), "cam_ok": hand_mod.HandRecognizer.available(), "cam_hand": self.cam_hand, "cam_note": self.cam_note,
                "cam_state": self.cam_state, "cam_label": self.cam_label, "cam_muted": self._coach_obj.gave_up,
                "hands": [{"kid": NAME[h["kid"]], "robot": NAME[h["robot"]], "res": h["res"], "via": h.get("via", "voice")} for h in self.hands[-8:]]}


class JankenHub(GameHub):
    CLS = Janken
    SKEY = "janken"
    KEYS = ("plays", "wins", "losses", "draws", "streak_best")
    LABEL = "じゃんけん"

    def snapshot(self) -> dict:
        gs = [g.snapshot() for g in self.games.values() if g.robots]
        return {"on": self.own_on, "games": gs, "best": self.stats.get("streak_best", 0), "plays": self.stats.get("plays", 0),
                "wins": self.stats.get("wins", 0), "losses": self.stats.get("losses", 0)}

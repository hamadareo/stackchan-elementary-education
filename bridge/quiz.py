"""子ども向けの遊び: なぞなぞ・あっち向いてホイ・おみくじ・ものまねボイス(この4つの土台と、なぞなぞ)。

しりとり(shiritori.py)・じゃんけん(janken.py)と同じ作り: 機器ごとに独立(3台は連携しない)、声の出し方・聞き取りの守り
(ロボットの声の回り込みを捨てる)・先生の入力・「ほかの機器が話している間は聞かない」をそのまま使う。
問題・台詞・おみくじ・声の種類はすべて内蔵データで、判定もコードで行う(AI・s109 には頼らない=オフラインでも動く)。
あっち向いてホイ・おみくじ・ものまねは fun.py。
"""
from __future__ import annotations

import queue
import re
import threading
import time
import unicodedata

import quizdata as qd
from playfx import COMMON, QUIT, YES, NO, Picker, tone
from shiritori import GameHub, Shiritori, to_hira

ROUND = 5                       # なぞなぞ 1 ラウンドの問題数
IDLE_WAIT = 20.0                # 答えを待つ秒数(超えたらやさしく声をかける)
IDLE_MAX = 3                    # 声をかけても答えがない回数(超えたら終わる)
UNCLEAR_MAX = 4                 # 聞き取れない言葉が続く回数
AGAIN_WAIT = 15.0               # 「もういっかい?」の返事を待つ秒数
DONT = re.compile(r"わからない|わかんない|わかりません|わからん|しらない|知らない|ヒント|ひんと|おしえて|教えて|パス|ぱす|ギブ|ぎぶ|こたえは?(なに|なあに)")

KINDS = {"riddle": "なぞなぞ", "hoi": "あっちむいてホイ", "omikuji": "おみくじ", "mimic": "ものまねボイス"}
STAT_KEYS = ("plays", "rounds", "correct", "answered", "perfect", "hoi_rounds", "hoi_wins", "hoi_points", "hoi_streak_best",
             "omikuji_n", "omikuji_big", "mimic_n", "mimic_rounds")


# ---------------------------------------------------------------- 言葉の整え
def fold(text: str) -> str:
    """聞き取りのゆれをそろえる: 全角→半角、英字は小文字、かたかな→ひらがな、記号・空白・のばす棒を消す。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = to_hira(t)
    return re.sub(r"[\s、。,.!?！？・「」『』~〜…'\"ー_\-]+", "", t)


PRE = re.compile(r"^(えっと|えーと|うーん|うんと|うん|あのね|あの|そうだなあ|そうだな|たぶん|きっと|こたえは|答えは|わかった|それは|やっぱり|ぼくは|わたしは|あたしは|おれは)+")
TAIL = re.compile(r"(だとおもいます|だとおもうよ|だとおもう|だと|だよね|だよ|だね|でしょうか|でしょ|ですよ|です|じゃない|かな|かも|だぞ|のだ|よ|ね|だ|か|っ)+$")


def core(text: str) -> str:
    """「えっと、トマトだよ」→「とまと」(前置きと語尾を落とした、答えの中身)。"""
    f = fold(text)
    f = PRE.sub("", f)
    return TAIL.sub("", f)


def match(f: str, variants: list) -> bool:
    """子どもの言葉(fold 済み)が、答えのどれかを含むか。1 文字の答え(め・き)は、全体がそれのときだけ。"""
    c = core(f)
    for v in variants:
        a = fold(v)
        if not a:
            continue
        if len(a) == 1:
            if c == a or f == a:
                return True
        elif a in f:
            return True
    return False


class Item:
    """なぞなぞ 1 問ぶん。"""

    def __init__(self, prompt, say, show="", answers=None, hint="", tries=2, reprompt=""):
        self.prompt, self.say, self.show = prompt, say, show or prompt
        self.answers, self.hint, self.tries, self.reprompt = answers or [], hint, tries, reprompt

    def judge(self, text: str) -> str:
        """ok(せいかい) / ng(ちがう) / hint(ヒント・わからない) / unclear(聞き取れない)"""
        f = fold(text)
        if not f:
            return "unclear"
        if match(f, self.answers):
            return "ok"
        return "hint" if DONT.search(f) else "ng"

    def answer_text(self) -> str:
        return self.say


# ---------------------------------------------------------------- 遊びの土台
class Play(Shiritori):
    """なぞなぞ・あっち向いてホイ・おみくじ・ものまね の共通の土台(開始・聞き取り・首と顔の演出・管理画面の状態)。"""
    KIND = "riddle"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        for key in STAT_KEYS:
            self.stats.setdefault(key, 0)
        self.kind = self.KIND
        self.qno = 0
        self.score = 0
        self.level = 1
        self.cur = None
        self.history: list = []
        self.stat = ""                    # 管理画面のカードに出す 1 行(得点・連勝など)
        self.big = ""                     # 管理画面の大きな表示
        self.qtext = ""                   # 管理画面に出す、いまの問題・出し物
        self.atext = ""                   # 先生用のこたえ・ヒント
        self.pick = Picker(self.rnd)
        self._frames: list = []

    # ---- 開始
    def start(self, first: str = "", level: str = "", dev_id: "str | None" = None) -> "tuple[bool, str]":
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
        self.kind = self.KIND
        self.chain, self.need, self.turn, self.msg, self.result = [], "", "", "", ""
        self.qno = self.score = 0
        self.level = 1
        self.cur = None
        self.history = []
        self.stat = self.big = self.qtext = self.atext = ""
        self._dp = self._dt = 0
        self._frames = []
        self.speaking, self.quiet_until = False, 0.0
        while not self.q.empty():
            self.q.get_nowait()
        self._stop.clear()
        self.on = True
        self.stats["plays"] += 1
        self._save()
        self.hub.event(f"{KINDS[self.kind]}を始めます")
        threading.Thread(target=self._guarded_run, args=(eng,), daemon=True).start()
        return True, ""

    def _guarded_run(self, eng) -> None:
        try:
            self._freeze(True)
            self._run(eng)
        except Exception as e:  # noqa: BLE001
            self.msg = f"エラー: {type(e).__name__}"
        finally:
            self._finish()

    def _finish(self) -> None:
        self._freeze(False)
        self._front()
        self._board_clear()
        if not self.result:
            self.result = "stopped"
        self.turn = ""
        self.cur = None
        self._face("neutral")
        self.on = False
        self.hub.event(f"{KINDS[self.kind]}終了" + (f"({self.stat})" if self.stat else ""))

    def _run(self, eng) -> None:               # サブクラスが作る
        raise NotImplementedError

    def hear(self, text: str, lang: str = "ja") -> bool:
        """遊び中は、翻訳に回さないよう常に True。"""
        if not self.on:
            return False
        if lang not in ("ja", "", None):
            return True
        if self.speaking or self.clock() < self.quiet_until or self.turn != "kid" or (self.guard and self.guard()):
            return True
        self.q.put(text)
        return True

    # ---- 台詞
    @property
    def name(self) -> str:
        return self.robots[0]["name"] if self.robots else "ずんだ"

    def L(self, scene: str, options=None, **kw) -> str:
        """場面 scene の台詞を、直近と重ならないように選び、キャラの口調にして返す。{k} などは kw で置きかえる。"""
        opts = options if options is not None else COMMON[scene]
        s = self.pick(f"{self.kind}:{scene}", opts)
        cnt = kw.pop("cnt", None)
        if cnt is not None:
            s = s.replace("{n}もん", f"{cnt}もん")          # 「{n}もん」の {n} は数(口調の記号ではない)
        s = tone(self.name, s, self.pick)
        for k, v in kw.items():
            s = s.replace("{" + k + "}", str(v))
        return s

    def _say(self, eng, text: str, emo: str = "joy", caption: "str | None" = None, face: "str | None" = None, raw: "dict | None" = None) -> None:
        self.turn = ""
        self.msg = caption or text
        self._speak(eng, 0, text, emo, caption=caption, raw=raw, face=face)

    def _wait(self, timeout: float):
        """子どもの言葉を待つ。(None=止められた / ""=時間切れ / 言葉)"""
        self.turn = "kid"
        self._face("listening")
        try:
            text = self.q.get(timeout=timeout)
        except queue.Empty:
            return ""
        if self._stop.is_set() or text is None:
            return None
        self.turn = ""
        return (text or "").strip()

    def _again(self, eng) -> bool:
        """「もういっかい？」を聞く。続けるなら True。"""
        self._say(eng, self.L("again_q"), "joy")
        text = self._wait(AGAIN_WAIT)
        if not text or not YES.search(text) or (NO.search(text) and not re.search(r"もういっかい|やる", text)):
            return False
        return True

    # ---- 首と顔の演出(_servo/_front/_nod/_wobble/_board は playfx.Stage)
    def _freeze(self, on: bool) -> None:
        """遊び中は、顔追従の首の動きを止める(演出の首の動きとケンカしないように)。画像は貯めておく(ホイの顔の向きの判定用)。"""
        s = self._sess(0) if self.robots else None
        if s is None:
            return
        try:
            s.hand_cb = self._cam_cb if on else None
        except Exception:  # noqa: BLE001
            pass

    def _cam_cb(self, jpeg) -> None:
        self._frames.append((self.clock(), jpeg))
        del self._frames[:-10]

    # ---- 管理画面
    def snapshot(self) -> dict:
        r = self.robots[0] if self.robots else {}
        return {"dev_id": r.get("id", ""), "dev_name": r.get("dev_name", ""), "persona": r.get("name", ""),
                "on": self.on, "kind": self.kind, "kind_label": KINDS.get(self.kind, ""), "turn": self.turn, "msg": self.msg, "result": self.result,
                "qno": self.qno, "rounds": ROUND, "score": self.score, "level": 0, "question": self.qtext, "answer": self.atext,
                "stat": self.stat, "big": self.big, "history": self.history[-10:],
                "plays": self.stats.get("plays", 0), "correct": self.stats.get("correct", 0), "answered": self.stats.get("answered", 0)}


# ---------------------------------------------------------------- なぞなぞ
LINES = {
    "intro": ["なぞなぞを しよう！ぜんぶで5もん。わからないときは、ヒントって言ってね。", "なぞなぞの じかん{d}！5もん だすよ。ヒントも あるから あんしんしてね。",
              "さあ、なぞなぞ{d}！あたまを やわらかくして きいてね。ヒントって言うと、ヒントが でるよ。", "なぞなぞ しよう！5もん あるよ。わからなくても だいじょうぶ、ヒントを あげる{n}。",
              "なぞなぞ たいかい、はじまるよ！5もん、がんばろうね！"],
    "head": ["{k}もんめ！", "つぎは、{k}もんめ！", "さあ、{k}もんめ いくよ！", "{k}もんめ、いくよー！", "では、{k}もんめ！", "{k}もんめ、いってみよう！"],
    "ok": ["せいかい{d}！{cheer}", "あたり{d}！{cheer}", "そのとおり{d}！{cheer}", "ピンポーン！せいかい{d}！", "すごい、よく わかった{n}！", "{cheer} だいせいかい{d}！",
           "ばっちり{d}！{cheer}", "ぴったり せいかい！{cheer}", "さすが{d}！せいかい！"],
    "streak": ["{s}もん れんぞく せいかい{d}！{cheer}", "すごい！{s}もん れんぞく{d}！とまらない{n}！", "{s}れんぞく！{cheer}", "また せいかい！{s}もん つづけて{d}！{cheer}"],
    "retry": ["{oops} もういっかい かんがえてみて！", "ちがうよー。もういちど、ちょうせん{d}！", "おしい！もうすこし{d}！", "うーん、ちょっと ちがうかな。もういっかい！",
              "あとすこし！かんがえてみて！", "いいところまで きてる{n}！もういっかい！"],
    "reveal": ["こたえは「{a}」{d}！つぎは がんばろう！", "ざんねん！こたえは「{a}」{d}。", "おしえるね。こたえは「{a}」{d}！", "うーん、こたえは「{a}」だった{n}！",
               "こたえは「{a}」{d}。むずかしかったね！", "「{a}」が こたえ{d}！つぎは いけるよ！"],
    "hint": ["ヒントを あげる{n}。{h}", "じゃあ ヒント！{h}", "ヒント{d}！{h}", "こんな ヒントは どうかな？{h}", "ヒント いくよ！{h}"],
    "all": ["ぜんぶ せいかい{d}！{cheer}", "5もん ぜんぶ あたり！{cheer} てんさい{d}！", "ぜんぶ わかったね！{cheer}", "かんぺき！ぜんぶ せいかい{d}！{cheer}"],
    "good": ["{n}もん せいかい{d}！よくできました！", "{n}もん あたり！すごいね！", "{n}もん せいかい！なかなか やるね！"],
    "try": ["{n}もん せいかい{d}！がんばったね！", "{n}もん あたり！つぎは もっと とれるよ！", "{n}もん せいかい！いい ちょうし{d}！"],
}


class Quiz(Play):
    KIND = "riddle"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.used: dict = {}
        self.streak = 0

    def _line(self, scene: str, **kw) -> str:
        return self.L(scene, LINES[scene] if scene in LINES else None, **kw)

    def _pick(self, key: str, n: int) -> int:
        used = self.used.setdefault(key, [])
        left = [i for i in range(n) if i not in used] or list(range(n))
        if len(left) == n:
            used.clear()
        i = self.rnd.choice(left)
        used.append(i)
        return i

    def _make(self) -> Item:
        q, ans, hint = qd.RIDDLES[self._pick("riddle", len(qd.RIDDLES))]
        return Item(q, ans[0], answers=ans, hint=hint, tries=2)

    def _ask(self, eng, it: Item, n: int) -> str:
        """1 問。戻り値: correct / wrong / quit / idle"""
        self.cur = it
        self.qtext = it.show
        self.atext = it.answer_text()
        self.big = f"{n + 1}もんめ"
        self._say(eng, f'{self._line("head", k=n + 1)}{it.prompt}', "neutral", face="neutral")
        tries = idle = unclear = 0
        hinted = False
        while not self._stop.is_set():
            text = self._wait(IDLE_WAIT)
            if text is None:
                return "quit"
            if not text:
                idle += 1
                if idle >= IDLE_MAX:
                    return "idle"
                self._say(eng, self.L("idle"), "neutral")
                continue
            v = it.judge(text)
            if v != "ok" and QUIT.search(text):
                return "quit"
            if v == "ok":
                self.score += 1
                self.streak += 1
                self.stats["correct"] += 1
                self.stats["answered"] += 1
                self.history.append({"q": it.show, "ok": True})
                self._save()
                line = self._line("streak", s=self.streak) if self.streak >= 2 else self._line("ok")
                self.big = "せいかい！"
                self._nod(2)
                self._say(eng, line, "excited", face="giggle")
                return "correct"
            if v == "hint":
                if it.hint and not hinted:
                    hinted = True
                    self._say(eng, self._line("hint", h=it.hint), "joy", face="thinking")
                    continue
                return self._reveal(eng, it)
            if v == "ng":
                tries += 1
                if tries >= it.tries:
                    return self._reveal(eng, it)
                extra = " ヒントも あるよ。" if it.hint and not hinted else ""
                self._wobble()
                self._say(eng, self._line("retry") + extra, "doubt", face="doubt")
                continue
            unclear += 1
            if unclear >= UNCLEAR_MAX:
                return "idle"
            self._say(eng, self.L("unclear"), "doubt")
        return "quit"

    def _reveal(self, eng, it: Item) -> str:
        self.stats["answered"] += 1
        self.streak = 0
        self.history.append({"q": it.show, "ok": False})
        self._save()
        self.big = "ざんねん…"
        self._wobble()
        self._say(eng, self._line("reveal", a=it.answer_text()), "doubt", face="sad")
        return "wrong"

    def _bye(self, eng, res: str) -> None:
        self.msg = "またあそぼうね！"
        self._say(eng, self.L("bye" if res == "idle" else "quit"), "joy")

    def _run(self, eng) -> None:
        self._say(eng, self._line("intro"), "excited", face="happy")
        while not self._stop.is_set():
            self.score = 0
            self.streak = 0
            for n in range(ROUND):
                if self._stop.is_set():
                    break
                it = self._make()
                self.qno = n + 1
                self.stat = f"せいかい {self.score} / {ROUND}"
                res = self._ask(eng, it, n)
                self.stat = f"せいかい {self.score} / {ROUND}"
                if res in ("quit", "idle"):
                    self._bye(eng, res)
                    return
            self.stats["rounds"] += 1
            if self.score == ROUND:
                self.stats["perfect"] += 1
            self._save()
            key = "all" if self.score == ROUND else "good" if self.score >= 3 else "try"
            self.big = f"{self.score} / {ROUND} もん せいかい"
            self._say(eng, self._line(key, cnt=self.score), "excited" if self.score >= 3 else "joy", face="giggle" if self.score >= 3 else "happy")
            if self._stop.is_set():
                return
            if not self._again(eng):
                self._say(eng, self.L("bye"), "joy")
                return
            self.hub.event("なぞなぞ: もういっかい")


class QuizHub(GameHub):
    """なぞなぞ・あっち向いてホイ・おみくじ・ものまねの管理役(機器ごとに独立)。"""
    CLS = Quiz
    SKEY = "quiz"
    KEYS = STAT_KEYS
    LABEL = "あそび"

    @staticmethod
    def classes() -> dict:
        import fun
        return {"riddle": Quiz, "hoi": fun.Hoi, "omikuji": fun.Omikuji, "mimic": fun.Mimic}

    def start(self, kind: str = "riddle", level: str = "", dev_ids: "list | None" = None) -> "tuple[bool, str]":
        """dev_ids の機器それぞれで、独立に始める(省略なら、接続中の全機器)。kind: riddle / hoi / omikuji / mimic"""
        if self.discuss is not None and self.discuss.on:
            return False, "いまロボットが会話中です(止めてから始めてください)"
        cls = self.classes().get(kind)
        if cls is None:
            return False, "知らない遊びです"
        pk = self.discuss.pick() if self.discuss is not None else []
        ids = [r["id"] for r, _s in pk] if not dev_ids else [i for i in dev_ids if any(r["id"] == i for r, _s in pk)]
        if not ids:
            return False, "接続中の機器がありません"
        started, err = [], ""
        for i in ids:
            busy = next((p for p in self.peers if p.dev_busy(i)), None)
            if busy is not None:
                err = f"この機器は、いま{busy.LABEL}で遊んでいます"
                continue
            g = self.games.get(i)
            if g is not None and g.on:
                err = "すでに遊んでいます(やめてから始めてください)"
                continue
            if g is None or type(g) is not cls:                    # 違う遊びを始めるときは、その遊びの入れ物に替える
                g = self.games[i] = cls(self.hub, self.reg, self.discuss, self.eng_getter, self.path, self.rnd, stats=self.stats, save_cb=self.save)
                g.guard = self._guard_for(g)
            ok, e = g.start(kind, level, dev_id=i)
            if ok:
                started.append(i)
            else:
                err = e
        return (True, "") if started else (False, err)

    def snapshot(self) -> dict:
        gs = [g.snapshot() for g in self.games.values() if g.robots]
        s = self.stats
        return {"on": self.own_on, "games": gs, "kinds": KINDS, "plays": s.get("plays", 0), "correct": s.get("correct", 0),
                "answered": s.get("answered", 0), "rounds": s.get("rounds", 0), "perfect": s.get("perfect", 0),
                "hoi_rounds": s.get("hoi_rounds", 0), "hoi_wins": s.get("hoi_wins", 0), "hoi_streak_best": s.get("hoi_streak_best", 0),
                "omikuji_n": s.get("omikuji_n", 0), "omikuji_big": s.get("omikuji_big", 0), "mimic_n": s.get("mimic_n", 0)}

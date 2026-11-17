"""子ども向けの遊び: なぞなぞ・クイズ(○× と 3たく)・算数・かんじ・えいご。そして「きょうは何日?」などの単発の返事。

しりとり(shiritori.py)・じゃんけん(janken.py)と同じ作り: 機器ごとに独立(3台は連携しない)、声の出し方・聞き取りの守り
(ロボットの声の回り込みを捨てる)・先生の入力・「ほかの機器が話している間は聞かない」をそのまま使う。
問題は quizdata.py の固定データか、コードで作る(算数)。判定もすべてコードで行う(AI・s109 には頼らない=オフラインでも動く)。
"""
from __future__ import annotations

import queue
import re
import threading
import time
import unicodedata

import quizdata as qd
from shiritori import GameHub, Shiritori, to_hira

ROUND = 5                       # 1 ラウンドの問題数
IDLE_WAIT = 20.0                # 答えを待つ秒数(超えたらやさしく声をかける)
IDLE_MAX = 3                    # 声をかけても答えがない回数(超えたら終わる)
UNCLEAR_MAX = 4                 # 聞き取れない・答えの形でない言葉が続く回数
AGAIN_WAIT = 15.0               # 「もういっかい?」の返事を待つ秒数
QUIT = re.compile(r"やめ|おわり|終わり|おしまい|もういい|もう終|あそばない|ばいばい|バイバイ")
YES = re.compile(r"はい|うん|やる|やろ|もういっかい|もう一回|もういちど|いいよ|おねがい|おっけ|ok|する|やりたい|もっと")
NO = re.compile(r"いいえ|やめ|おわり|終わり|やらない|ううん|もういい|いやだ|いや|ばいばい|バイバイ|おしまい")
DONT = re.compile(r"わからない|わかんない|わかりません|わからん|しらない|知らない|ヒント|ひんと|おしえて|教えて|パス|ぱす|ギブ|ぎぶ|こたえは?(なに|なあに)")

KINDS = {"riddle": "なぞなぞ", "quiz": "クイズ", "math": "さんすう", "add": "たしざん", "sub": "ひきざん", "kuku": "かけざん(九九)", "english": "えいご", "kanji": "かんじ"}
MATH_KINDS = ("math", "add", "sub", "kuku")
LEVEL_KINDS = MATH_KINDS + ("kanji",)            # つづけて せいかいすると、むずかしくなる遊び
MATH_LEVELS = {"math": (1, 4), "add": (1, 2), "sub": (3, 3), "kuku": (4, 4), "kanji": (1, 3)}      # 種類ごとの、むずかしさ(レベル)の範囲


# ---------------------------------------------------------------- 言葉の整え・数詞
def fold(text: str) -> str:
    """聞き取りのゆれをそろえる: 全角→半角、英字は小文字、かたかな→ひらがな、記号・空白・のばす棒を消す。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = to_hira(t)
    return re.sub(r"[\s、。,.!?！？・「」『』~〜…'\"ー_\-]+", "", t)


PRE = re.compile(r"^(えっと|えーと|うーん|うんと|うん|あのね|あの|そうだなあ|そうだな|たぶん|きっと|こたえは|答えは|わかった|それは|やっぱり|ぼくは|わたしは|あたしは|おれは)+")
TAIL = re.compile(r"(だとおもいます|だとおもうよ|だとおもう|だと|だよね|だよ|だね|でしょうか|でしょ|ですよ|です|じゃない|かな|かも|だぞ|のだ|よ|ね|だ|か|っ)+$")


def core(text: str) -> str:
    """「えっと、にじゅうさんだよ」→「にじゅうさん」(前置きと語尾を落とした、答えの中身)。"""
    f = fold(text)
    f = PRE.sub("", f)
    return TAIL.sub("", f)


_DIGIT = {"いち": 1, "に": 2, "さん": 3, "よん": 4, "し": 4, "ご": 5, "ろく": 6, "ろっ": 6, "なな": 7, "しち": 7, "はち": 8, "はっ": 8, "きゅう": 9, "きゅ": 9, "く": 9,
          "ぜろ": 0, "れい": 0}
_UNIT = {"じゅう": 10, "じゅ": 10, "じゅっ": 10, "ひゃく": 100, "ぴゃく": 100, "びゃく": 100}
_TOK = sorted(list(_DIGIT) + list(_UNIT), key=len, reverse=True)
_KANJI = {"〇": "ぜろ", "零": "ぜろ", "一": "いち", "二": "に", "三": "さん", "四": "よん", "五": "ご", "六": "ろく", "七": "なな", "八": "はち", "九": "きゅう", "十": "じゅう", "百": "ひゃく"}


def to_number(text: str) -> "int | None":
    """子どもの答え → 数。「23」「にじゅうさん」「二十三」「にじゅうさんだよ」「えっと、さんじゅう」など。数でなければ None。"""
    f = core("".join(_KANJI.get(c, c) for c in unicodedata.normalize("NFKC", text or "")))
    if not f:
        return None
    if re.fullmatch(r"\d{1,4}", f):
        return int(f)
    total, cur, i = 0, None, 0
    while i < len(f):
        for tk in _TOK:
            if f.startswith(tk, i):
                break
        else:
            return None                                        # 数でない言葉が混じっている
        i += len(tk)
        if tk in _UNIT:
            total += (1 if cur is None else cur) * _UNIT[tk]
            cur = None
        else:
            cur = _DIGIT[tk] if cur is None else cur * 10 + _DIGIT[tk]
    return total + (cur or 0)


# ---------------------------------------------------------------- 答えの読み取り
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


_NO = ("ばつ", "×", "✕", "✖", "いいえ", "ううん", "ちがう", "ちがい", "うそ", "まちがい", "そうじゃ", "罰", "ノー", "のー")
_YES = ("まる", "○", "〇", "⭕", "丸", "はい", "うん", "そう", "ほんと", "ほんとう", "せいかい", "あってる", "あってます", "ただしい", "正しい", "イエス", "いえす")


def parse_tf(text: str) -> "bool | None":
    """○×の答え。まる→True、ばつ→False、決められないとき None。"""
    f = fold(re.sub(r"うーん|うぅん|うんと|うーむ|うーんと", "", text or ""))        # 考えているときの「うーん」は「うん(はい)」ではない
    no = any(w in f for w in _NO)
    yes = any(w in f.replace("ううん", "") for w in _YES)                      # 「ううん(いいえ)」の中の「うん」は はい ではない
    if no and not yes:
        return False
    if yes and not no:
        return True
    if no and yes:                                    # 「そうじゃない」のように、両方の語が入るときは、ばつ側を優先
        return False if ("じゃ" in f or "ちがう" in f) else None
    return None


_NUMWORD = {"いち": 1, "いっ": 1, "ひとつ": 1, "ひとつめ": 1, "1": 1, "に": 2, "ふたつ": 2, "ふたつめ": 2, "2": 2, "さん": 3, "みっつ": 3, "みっつめ": 3, "3": 3,
            "ひと": 1, "ふた": 2, "みっ": 3}


def parse_choice(text: str, opts: list) -> "int | None":
    """3たくの答え(1〜3)。「いち」「1ばん」「2」「さんばん」、または せんたくしの言葉そのもの。"""
    f = fold(text)
    hits = []
    for i, o in enumerate(opts):
        if any(len(fold(v)) >= 2 and fold(v) in f for v in o.split("|")):
            hits.append(i + 1)
    if len(hits) == 1:
        return hits[0]
    c = core(text)
    c = re.sub(r"(ばんめ|ばん|番目|番|こめ|つめ|です)$", "", c)
    return _NUMWORD.get(c)


# ---------------------------------------------------------------- 問題
class Item:
    """1 問ぶん。kind: riddle / tf / choice / math / jp_en / en_jp"""

    def __init__(self, kind, prompt, say, show="", answers=None, hint="", tries=1, truth=None, opts=None, idx=0, value=None, expl="", reprompt=""):
        self.kind, self.prompt, self.say, self.show = kind, prompt, say, show or prompt
        self.answers, self.hint, self.tries = answers or [], hint, tries
        self.truth, self.opts, self.idx, self.value, self.expl = truth, opts or [], idx, value, expl
        self.reprompt = reprompt

    def judge(self, text: str) -> str:
        """ok(せいかい) / ng(ちがう) / hint(ヒント・わからない) / unclear(答えの形でない・聞き取れない)"""
        f = fold(text)
        if not f:
            return "unclear"
        if self.kind in ("riddle", "jp_en", "en_jp", "kanji"):
            if match(f, self.answers):
                return "ok"
            return "hint" if DONT.search(f) else "ng"
        if self.kind == "tf":
            v = parse_tf(text)
            if v is None:
                return "hint" if DONT.search(f) else "unclear"
            return "ok" if v == self.truth else "ng"
        if self.kind == "choice":
            v = parse_choice(text, self.opts)
            if v is None:
                return "hint" if DONT.search(f) else "unclear"
            return "ok" if v == self.idx else "ng"
        if self.kind == "math":
            n = to_number(text)
            if n is None:
                return "hint" if DONT.search(f) else "unclear"
            return "ok" if n == self.value else "ng"
        return "unclear"

    def answer_text(self) -> str:
        """答えの読み上げ(ごほうびの一言の前に言う)。"""
        if self.kind == "tf":
            return "まる" if self.truth else "ばつ"
        if self.kind == "choice":
            return f'{self.idx}ばん、{self.opts[self.idx - 1].split("|")[0]}'
        if self.kind == "math":
            return str(self.value)
        return self.say


def gen_math(level: int, rnd) -> Item:
    """レベル 1: 1けた+1けた / 2: 2けた+(1〜2けた) / 3: ひきざん / 4: かけざん(九九)"""
    if level <= 1:
        a, b = rnd.randint(1, 9), rnd.randint(1, 9)
        q, v = f"{a}たす{b}は、いくつ？", a + b
    elif level == 2:
        a, b = rnd.randint(10, 60), rnd.randint(2, 39)
        q, v = f"{a}たす{b}は、いくつ？", a + b
    elif level == 3:
        a = rnd.randint(5, 20)
        b = rnd.randint(1, a - 1)
        q, v = f"{a}ひく{b}は、いくつ？", a - b
    else:
        a, b = rnd.randint(1, 9), rnd.randint(1, 9)
        q, v = f"{a}かける{b}は、いくつ？", a * b
    return Item("math", q, str(v), show=q, value=v, tries=2, reprompt="すうじで、こたえてね。")


# ---------------------------------------------------------------- 今日のこと(単発の返事)
_WEEK = "月火水木金土日"
TODAY = re.compile(r"(今日|きょう|キョウ).{0,4}(何日|なんにち|なんようび|何曜日|なんがつ|何月|日付|ひづけ)|何曜日|なんようび|"
                   r"(今|いま).{0,4}(何時|なんじ)|(何時|なんじ)(ですか|かな|だっけ|\?|？)?$|日付(は|を)?(教|おし)|なんにち(です|かな|だっけ)")
_END = {"ずんだ": "なのだ", "めたん": "です", "つむぎ": "だよ"}


def today_reply(text: str, name: str = "ずんだ", now: "time.struct_time | None" = None) -> "str | None":
    """「きょうは何日？」「何曜日？」「いま何時？」への決まった返事(時計はシステム時刻)。当てはまらなければ None。"""
    t = unicodedata.normalize("NFKC", text or "")
    if not TODAY.search(t):
        return None
    now = now or time.localtime()
    e = _END.get(name, "だよ")
    wd = _WEEK[now.tm_wday]
    if re.search(r"何時|なんじ|いま|今", t) and not re.search(r"何日|なんにち|曜日|ようび|何月|なんがつ|日付", t):
        h12 = now.tm_hour % 12 or 12
        ap = "午前" if now.tm_hour < 12 else "午後"
        mm = "ちょうど" if now.tm_min == 0 else f"{now.tm_min}分"
        return f"いまは、{ap}{h12}時{mm}{e}。"
    if re.search(r"曜日|ようび", t) and not re.search(r"何日|なんにち|日付|ひづけ", t):
        return f"きょうは、{wd}曜日{e}。"
    return f"きょうは、{now.tm_mon}月{now.tm_mday}日、{wd}曜日{e}。"


# ---------------------------------------------------------------- 口調
LINES = {
    "ずんだ": {"ok": ["せいかいなのだ！すごいのだ！", "あたりなのだ！やったのだ！", "そのとおりなのだ！かしこいのだ！"],
             "retry": ["おしいのだ！もういっかい、かんがえてみるのだ！", "うーん、ちがうのだ。もういちど、ちょうせんなのだ！"],
             "reveal": "こたえは、{a}なのだ。", "hint": "ヒントなのだ。{h}", "idle": ["ゆっくり かんがえて いいのだ。", "だいじょうぶ、まってるのだ。"],
             "all": "ぜんぶ せいかいなのだ！てんさいなのだ！", "good": "{n}もん せいかいなのだ！よくできたのだ！", "try": "{n}もん せいかいなのだ！がんばったのだ！",
             "again": "もういっかい、やるのだ？", "bye": "たのしかったのだ！またあそぶのだ！ばいばーい！", "up": "れべるあっぷなのだ！すこし むずかしくするのだ！"},
    "めたん": {"ok": ["せいかいです！すばらしいです。", "あたりです！よくできました。", "そのとおりです。さすがですね！"],
             "retry": ["おしいですね。もういちど、かんがえてみましょう。", "ちがいますよ。もういちど、ちょうせんしてみて。"],
             "reveal": "こたえは、{a}です。", "hint": "ヒントです。{h}", "idle": ["ゆっくり かんがえて くださいね。", "だいじょうぶ、まっていますよ。"],
             "all": "ぜんぶ せいかいです！かんぺきですね！", "good": "{n}もん せいかいです！よくできました。", "try": "{n}もん せいかいです。よく がんばりましたね。",
             "again": "もういっかい、やりますか？", "bye": "たのしかったです。また あそびましょうね。ばいばい！", "up": "レベルアップです！すこし むずかしくしますね。"},
    "つむぎ": {"ok": ["せいかい！すごいね！", "あたり！やったね！", "そのとおり！よく わかったね！"],
             "retry": ["おしい！もういっかい、かんがえてみて！", "ううん、ちがうよ。もういちど、やってみよう！"],
             "reveal": "こたえは、{a}だよ。", "hint": "ヒントだよ。{h}", "idle": ["ゆっくり かんがえて いいよ。", "だいじょうぶ、まってるよ。"],
             "all": "ぜんぶ せいかい！かんぺきだね！", "good": "{n}もん せいかい！よくできました！", "try": "{n}もん せいかい！がんばったね！",
             "again": "もういっかい、やる？", "bye": "たのしかったよ！またあそぼうね！ばいばーい！", "up": "レベルアップ！すこし むずかしくするね！"},
}
INTRO = {"riddle": "なぞなぞを しよう！ぜんぶで5もん。わからないときは、ヒントって言ってね。",
         "quiz": "クイズを しよう！ぜんぶで5もん。まるか ばつか、1ばん、2ばん、3ばんで こたえてね。",
         "math": "さんすうを しよう！ぜんぶで5もん。こたえを、こえで 言ってね。",
         "add": "たしざんを しよう！ぜんぶで5もん。こたえを、こえで 言ってね。",
         "sub": "ひきざんを しよう！ぜんぶで5もん。こたえを、こえで 言ってね。",
         "kuku": "九九を しよう！ぜんぶで5もん。こたえを、こえで 言ってね。",
         "kanji": "かんじを しよう！ぜんぶで5もん。がめんの かんじを みて、よみかたを 言ってね。わからないときは、ヒントって言ってね。",
         "english": "えいごを しよう！ぜんぶで5もん。えいごや にほんごで、こたえてね。"}


class Quiz(Shiritori):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        for key in ("plays", "rounds", "correct", "answered", "perfect"):
            self.stats.setdefault(key, 0)
        self.kind = "riddle"
        self.qno = 0
        self.score = 0
        self.level = 1
        self.cur: "Item | None" = None
        self.used: dict = {}
        self.history: list = []
        self.run_correct = 0

    # ---- 開始(first に種類が入る)
    def start(self, first: str = "riddle", level: str = "", dev_id: "str | None" = None) -> "tuple[bool, str]":
        kind = first if first in KINDS else "riddle"
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
        self.kind = kind
        self.level = MATH_LEVELS.get(kind, (1, 1))[0]
        self.chain, self.need, self.turn, self.msg, self.result = [], "", "", "", ""
        self.qno = self.score = self.run_correct = 0
        self.cur = None
        self.history = []
        self.speaking, self.quiet_until = False, 0.0
        while not self.q.empty():
            self.q.get_nowait()
        self._stop.clear()
        self.on = True
        self.stats["plays"] += 1
        self._save()
        self.hub.event(f"{KINDS[kind]}を始めます")
        threading.Thread(target=self._run, args=(eng,), daemon=True).start()
        return True, ""

    def hear(self, text: str, lang: str = "ja") -> bool:
        """遊び中は、翻訳に回さないよう常に True。えいごの遊びでは、英語で答えた聞き取り(lang=en)も受け付ける。"""
        if not self.on:
            return False
        if lang not in ("ja", "", None) and not (lang == "en" and self.kind == "english"):
            return True
        if self.speaking or self.clock() < self.quiet_until or self.turn != "kid" or (self.guard and self.guard()):
            return True
        self.q.put(text)
        return True

    # ---- 問題を作る
    def _pick(self, key: str, n: int) -> int:
        used = self.used.setdefault(key, [])
        left = [i for i in range(n) if i not in used] or list(range(n))
        if len(left) == n:
            used.clear()
        i = self.rnd.choice(left)
        used.append(i)
        return i

    def _make(self) -> Item:
        k = self.kind
        if k == "riddle":
            q, ans, hint = qd.RIDDLES[self._pick("riddle", len(qd.RIDDLES))]
            return Item("riddle", q, ans[0], answers=ans, hint=hint, tries=2)
        if k == "quiz":
            n_tf, n_ch = len(qd.TF), len(qd.CHOICE)
            j = self._pick("quiz", n_tf + n_ch)
            if j < n_tf:
                q, truth, expl = qd.TF[j]
                return Item("tf", f"まる？ ばつ？ {q}", "", show=f"○か×か: {q}", truth=truth, expl=expl, tries=1, reprompt="まるか、ばつで、こたえてね。")
            q, opts, idx, expl = qd.CHOICE[j - n_tf]
            heads = "。".join(f'{i + 1}ばん、{o.split("|")[0]}' for i, o in enumerate(opts))
            return Item("choice", f"{q} {heads}。どれかな？", "", show=f"{q}  " + " / ".join(f'{i + 1}.{o.split("|")[0]}' for i, o in enumerate(opts)),
                        opts=opts, idx=idx, expl=expl, tries=1, reprompt="1ばん、2ばん、3ばんの どれかで こたえてね。")
        if k == "english":
            jp, hira, en, kata = qd.ENGLISH[self._pick("english", len(qd.ENGLISH))]
            if self.rnd.random() < 0.65:                       # にほんご → えいご
                return Item("jp_en", f"「{jp}」は、えいごで なんて 言うかな？", kata, show=f"「{jp}」を えいごで → {en}", answers=[en, kata, to_hira(kata)], tries=2,
                            hint=f"「{kata[0]}」から はじまるよ。", reprompt="えいごで 言ってみてね。")
            return Item("en_jp", f"「{kata}」は、にほんごで なにかな？", jp, show=f"「{en}」({kata}) を にほんごで → {jp}", answers=[jp, hira], tries=2,
                        hint=f"「{hira[0]}」から はじまるよ。", reprompt="にほんごで 言ってみてね。")
        if k == "kanji":
            lv = max(1, min(3, self.level))
            ch, rd = qd.KANJI[lv][self._pick(f"kanji{lv}", len(qd.KANJI[lv]))]
            return Item("kanji", "がめんの かんじを みてね。なんて よむかな？", rd[0], show=f"「{ch}」は、なんて よむ？", answers=rd, tries=2,
                        hint=f"「{rd[0][0]}」から はじまるよ。", reprompt="よみかたを、ひらがなで 言ってね。")
        lo, hi = MATH_LEVELS.get(k, (1, 4))
        return gen_math(max(lo, min(hi, self.level)), self.rnd)

    # ---- 進行
    def _L(self, key: str, i: int = 0):
        d = LINES.get(self.robots[0]["name"], LINES["ずんだ"])[key]
        return d[i % len(d)] if isinstance(d, list) else d

    def _say(self, eng, text: str, emo: str = "joy", caption: "str | None" = None) -> None:
        self.turn = ""
        self.msg = caption or text
        self._speak(eng, 0, text, emo, caption=caption)

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

    def _ask(self, eng, it: Item, n: int) -> str:
        """1 問。戻り値: correct / wrong / quit / idle"""
        self.cur = it
        self._say(eng, f"{n + 1}もんめ！{it.prompt}", "neutral", caption=(f"{n + 1}もんめ！{it.show}" if it.kind == "kanji" else None))     # かんじは、声では読まず(誤読するため)画面に出す
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
                self._say(eng, self._L("idle", idle), "neutral")
                continue
            v = it.judge(text)
            if v != "ok" and QUIT.search(text):
                return "quit"
            if v == "ok":
                self.score += 1
                self.stats["correct"] += 1
                self.stats["answered"] += 1
                self.history.append({"q": it.show, "ok": True})
                self._save()
                self._say(eng, self._L("ok", self.qno_i), "excited")
                return "correct"
            if v == "hint":
                if it.hint and not hinted:
                    hinted = True
                    self._say(eng, self._L("hint").format(h=it.hint), "joy")
                    continue
                return self._reveal(eng, it)
            if v == "ng":
                tries += 1
                if tries >= it.tries:
                    return self._reveal(eng, it)
                extra = " ヒントも あるよ。" if it.hint and not hinted and it.kind == "riddle" else ""
                self._say(eng, self._L("retry", tries) + extra, "doubt")
                continue
            unclear += 1                                          # 答えの形でない・聞き取れない
            if unclear >= UNCLEAR_MAX:
                return "idle"
            self._say(eng, it.reprompt or "もういっかい 言ってね。", "doubt")
        return "quit"

    @property
    def qno_i(self) -> int:
        return self.qno

    def _reveal(self, eng, it: Item) -> str:
        self.stats["answered"] += 1
        self.history.append({"q": it.show, "ok": False})
        self._save()
        line = self._L("reveal").format(a=it.answer_text())
        if it.expl:
            line += it.expl
        self._say(eng, line, "doubt")
        return "wrong"

    def _run(self, eng) -> None:
        try:
            self._say(eng, INTRO[self.kind], "excited")
            streak = 0
            while not self._stop.is_set():
                self.score = 0
                for n in range(ROUND):
                    if self._stop.is_set():
                        break
                    it = self._make()
                    self.qno = n + 1
                    res = self._ask(eng, it, n)
                    if res in ("quit", "idle"):
                        self.msg = "またあそぼうね！"
                        if res == "idle":
                            self._say(eng, "またあそぼうね！ばいばーい！", "joy")
                        else:
                            self._say(eng, self._L("bye"), "joy")
                        return
                    if self.kind in LEVEL_KINDS:                    # つづけて せいかいしたら、むずかしくする
                        lo, hi = MATH_LEVELS[self.kind]
                        streak = streak + 1 if res == "correct" else 0
                        if streak >= 2 and self.level < hi:
                            self.level += 1
                            streak = 0
                            self._say(eng, self._L("up"), "joy")
                self.stats["rounds"] += 1
                if self.score == ROUND:
                    self.stats["perfect"] += 1
                self._save()
                key = "all" if self.score == ROUND else "good" if self.score >= 3 else "try"
                self._say(eng, self._L(key).format(n=self.score), "excited" if self.score >= 3 else "joy")
                if self._stop.is_set():
                    return
                self._say(eng, self._L("again"), "joy")
                text = self._wait(AGAIN_WAIT)
                if not text or not YES.search(text) or (NO.search(text) and not re.search(r"もういっかい|やる", text)):
                    self._say(eng, self._L("bye"), "joy")
                    return
                self.hub.event(f"{KINDS[self.kind]}: もういっかい")
        except Exception as e:  # noqa: BLE001
            self.msg = f"エラー: {type(e).__name__}"
        finally:
            if not self.result:
                self.result = "stopped"
            self.turn = ""
            self.cur = None
            self._face("neutral")
            self.on = False
            self.hub.event(f"{KINDS[self.kind]}終了(せいかい {self.score}/{self.qno})")

    def snapshot(self) -> dict:
        r = self.robots[0] if self.robots else {}
        it = self.cur
        return {"dev_id": r.get("id", ""), "dev_name": r.get("dev_name", ""), "persona": r.get("name", ""),
                "on": self.on, "kind": self.kind, "kind_label": KINDS.get(self.kind, ""), "turn": self.turn, "msg": self.msg, "result": self.result,
                "qno": self.qno, "rounds": ROUND, "score": self.score, "level": self.level if self.kind in LEVEL_KINDS else 0,
                "question": it.show if it else "", "answer": (it.answer_text() if it else ""),
                "history": self.history[-10:], "plays": self.stats.get("plays", 0), "correct": self.stats.get("correct", 0), "answered": self.stats.get("answered", 0)}


class QuizHub(GameHub):
    CLS = Quiz
    SKEY = "quiz"
    KEYS = ("plays", "rounds", "correct", "answered", "perfect")
    LABEL = "クイズ"

    def start(self, kind: str = "riddle", level: str = "", dev_ids: "list | None" = None) -> "tuple[bool, str]":
        """dev_ids の機器それぞれで、独立に始める(省略なら、接続中の全機器)。kind: riddle/quiz/math/add/sub/kuku/english/kanji"""
        if self.discuss is not None and self.discuss.on:
            return False, "いまロボットが会話中です(止めてから始めてください)"
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
            if g is None:
                g = self.games[i] = Quiz(self.hub, self.reg, self.discuss, self.eng_getter, self.path, self.rnd, stats=self.stats, save_cb=self.save)
                g.guard = self._guard_for(g)
            ok, e = g.start(kind, level, dev_id=i)
            if ok:
                started.append(i)
            else:
                err = e
        return (True, "") if started else (False, err)

    def snapshot(self) -> dict:
        gs = [g.snapshot() for g in self.games.values() if g.robots]
        return {"on": self.own_on, "games": gs, "kinds": KINDS, "plays": self.stats.get("plays", 0), "correct": self.stats.get("correct", 0),
                "answered": self.stats.get("answered", 0), "rounds": self.stats.get("rounds", 0), "perfect": self.stats.get("perfect", 0)}

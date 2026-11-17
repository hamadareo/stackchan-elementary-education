"""しりとり: 子どもたちと、StackChan(最大 3 台)がしりとりであそぶ教材。

- ルールの判定はすべてプログラムで行う(AI には任せない): 「ん」で終わる / 同じ言葉 / 前の言葉の最後の文字から始まらない。
- ロボットが言う言葉は、子ども向けの辞書(下の WORDS)から選ぶ。辞書に無い言葉は言わないので、変な言葉でつまることがない。
- 子どもの声は、いつもの聞き取り(STT)の結果をそのまま受け取る。漢字まじりの言葉は、読みを AI に聞く(かな・辞書にある言葉は不要)。
- 先生が画面から文字で入力することもできる(聞き取りがうまくいかないとき・教室がうるさいとき)。
- ロボットは順番に(ずんだ→めたん→つむぎ)答える。「やさしい / ふつう / つよい」で、子どもが続けやすい文字で終わるか、続けにくい文字で終わるかが変わる。
"""
from __future__ import annotations

import json
import os
import queue
import random
import re
import threading
import time

# ロボットが言える言葉(よみ)。「ん」で終わる言葉は入れない(ロボットが自分で負けないように)。
WORDS = """
あり あひる あめ あさがお あじ あおむし あくび あいす 
いぬ いちご いか いるか いす いも いえ いけ いかだ
うさぎ うま うし うみ うめ うた うちわ うでどけい うきわ
えび えんぴつ えだ えさ えき えがお えのぐ
おに おかし おもち おばけ おにぎり おりがみ おたまじゃくし おちゃ おふろ おもちゃ
かに かめ かえる かさ かぶとむし からす かば かぼちゃ かもめ
きつね きのこ きいろ きもの きゅうり きしゃ きんぎょ
くま くるま くり くも くじら くつ くすり くらげ
けむし けしごむ けいと けーき
こあら こま こおり こい こねこ こたつ こうもり
さる さかな さくら さいふ さとう さめ さんた さいころ
しか しまうま しろ しお しゃぼんだま しりとり しいたけ
すいか すし すずめ すなば すべりだい すもも すいえい
せみ せんべい せなか せかい せいうち
そら そば そうじき そり
たこ たぬき たまご たいこ たけ たいよう たんぽぽ たなばた
ちょうちょう ちず ちくわ ちから ちーず ちりとり
つき つくえ つばめ つみき つな つめ つくし つばき
てぶくろ てんとうむし てれび てがみ てつぼう てるてるぼうず
とり とら とまと とけい とかげ とびら ともだち とうふ
なす なし なべ なみ なつ なまず なわとび なかま
にわとり にじ にく にもつ にほんご にんぎょう
ぬいぐるみ ぬりえ ぬの ぬま
ねこ ねずみ ねぎ ねっこ ねんど ねぼすけ
のり のこぎり のはら のみもの のりもの のびのび
はさみ はち はな はし はみがき はたけ はなび はんこ
ひこうき ひよこ ひまわり ひつじ ひかり ひも ひなまつり
ふくろ ふね ふで ふく ふくろう
へび へや へそ へちま へりこぷたー
ほし ほたる ほうき ほっぺ ほね ほたて
まくら まめ まど まつり まり まんが まくわうり
みみず みずうみ みどり みち みせ みかづき
むし むぎ むらさき むかし むぎちゃ
めがね めだか めだま めいろ
もも もぐら もち もり もみじ もうふ
やぎ やま やきゅう やさい やね やくそう
ゆき ゆびわ ゆめ ゆり ゆげ ゆうやけ
よる ようかい よつば よぞら
らっぱ らくだ らっこ らじお
りんご りす りく りゅう りょうり
るり
れいぞうこ れたす れっしゃ れい
ろうそく ろば ろけっと ろく
わに わし わた わかめ わらび わなげ わたあめ
がっこう がま がちょう がいこつ がらす
ぎょうざ ぎたー ぎゅうにゅう
ぐらす ぐみ ぐうぱー
げた げーむ
ごりら ごま ごみ ごぼう ごいし
ざりがに ざる ざっし
じしゃく じてんしゃ じどうしゃ じしょ じゃがいも
ずこう
ぜりー ぜんまい ぜっけい
ぞう ぞうり
だるま だんご だちょう
でんしゃ でんわ でぐち
どんぐり どうぶつ どろ どあ どくだみ
ばなな ばった ばけつ ばす ばら
びわ びじゅつ びーだま
ぶた ぶどう ぶらんこ ぶんぼうぐ
べんとう べっど べる
ぼうし ぼーる ぼくじょう
ぱんだ ぱせり ぱじゃま
ぴあの ぴざ ぴんく
ぷーる ぷらむ
ぺこぺこ ぺったんこ
ぽすと ぽけっと ぽっけ
るーる るびー るーれっと るーぺ
ずがこうさく ずんぐり ずっこけ ずぶぬれ
げっぷ げんきもの げらげら げきじょう
ぞろぞろ ぞうげ ぞうに
ぷりぷり ぷかぷか ぷろぺら
ぺらぺら ぺたぺた ぺーじ
ぎんが ぎゃく ぎざぎざ
ぐるぐる ぐあい ぐらうんど
ざぶざぶ ざいりょう
ぜんぶ ぜいたく
だんす だいどころ だっこ だいず
でこぼこ でんち でんき
ぱらそる ぱずる ぱれーど
びーる びっくり びんぼう
ぴかぴか ぴくにっく
べんきょう べらんだ べろ
ぼたもち ぼうえんきょう ぼやぼや
ぽかぽか ぽてと ぽりぽり
""".split()

# 子どもが漢字・かたかなで言いそうな言葉(STT の出力)→ よみ
KANJI = {"林檎": "りんご", "犬": "いぬ", "猫": "ねこ", "象": "ぞう", "兎": "うさぎ", "牛": "うし", "馬": "うま", "亀": "かめ", "蟹": "かに", "鳥": "とり",
         "虎": "とら", "熊": "くま", "猿": "さる", "鯨": "くじら", "蛙": "かえる", "蛇": "へび", "魚": "さかな", "桜": "さくら", "月": "つき", "星": "ほし",
         "空": "そら", "雨": "あめ", "雪": "ゆき", "虹": "にじ", "山": "やま", "海": "うみ", "川": "かわ", "花": "はな", "木": "き", "本": "ほん",
         "車": "くるま", "電車": "でんしゃ", "自転車": "じてんしゃ", "飛行機": "ひこうき", "学校": "がっこう", "先生": "せんせい", "友達": "ともだち",
         "時計": "とけい", "眼鏡": "めがね", "帽子": "ぼうし", "鞄": "かばん", "鉛筆": "えんぴつ", "消しゴム": "けしごむ", "机": "つくえ", "椅子": "いす",
         "苺": "いちご", "蜜柑": "みかん", "西瓜": "すいか", "卵": "たまご", "寿司": "すし", "餅": "もち", "飴": "あめ", "海苔": "のり", "茄子": "なす",
         "人参": "にんじん", "胡瓜": "きゅうり", "南瓜": "かぼちゃ", "豆": "まめ", "太陽": "たいよう", "雲": "くも", "風": "かぜ", "水": "みず", "火": "ひ",
         "鼠": "ねずみ", "狸": "たぬき", "狐": "きつね", "鰐": "わに", "鷲": "わし", "蝶々": "ちょうちょう", "兜虫": "かぶとむし", "蜻蛉": "とんぼ", "蜂": "はち"}

FILLERS = re.compile(r"^(えーと|えっと|えーっと|あのー?|うーん|んーと|えー|うん|はい|じゃあ|ええと)[、,\s]*")
SMALL = {"ゃ": "や", "ゅ": "ゆ", "ょ": "よ", "ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お", "ゎ": "わ"}
DAKU = {c: b for b, cs in {"か": "がぎぐげご", "さ": "ざじずぜぞ", "た": "だぢづでど", "は": "ばびぶべぼぱぴぷぺぽ"}.items() for c in cs}
DAKU.update({"が": "か", "ぎ": "き", "ぐ": "く", "げ": "け", "ご": "こ", "ざ": "さ", "じ": "し", "ず": "す", "ぜ": "せ", "ぞ": "そ", "だ": "た", "ぢ": "ち", "づ": "つ",
             "で": "て", "ど": "と", "ば": "は", "び": "ひ", "ぶ": "ふ", "べ": "へ", "ぼ": "ほ", "ぱ": "は", "ぴ": "ひ", "ぷ": "ふ", "ぺ": "へ", "ぽ": "ほ"})
DAKU = {k: v for k, v in DAKU.items() if len(k) == 1 and k in "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ"}

CHEER = ["すごい!", "いいね!", "さすが!", "やるね!", "おみごと!", "その調子!"]
NUDGE = ["ゆっくり考えてね。", "だいじょうぶ、まってるよ。", "ヒントがほしかったら言ってね。"]
RETRY_MAX = 3


def to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in (s or ""))


def kana_only(s: str) -> str:
    """ひらがな・かたかなと「ー」だけを残し、ひらがなにして返す。"""
    return re.sub(r"[^ぁ-ゖー]", "", to_hira(s or ""))


def last_char(reading: str) -> str:
    """しりとりでつなぐ最後の文字。長音(ー)は飛ばし、小さい「ゃゅょ」は大きい字にし、「っ」は飛ばす。"""
    r = reading
    while r and r[-1] in "ーっッ":
        r = r[:-1]
    if not r:
        return ""
    c = r[-1]
    return SMALL.get(c, c)


def first_char(reading: str) -> str:
    return reading[:1]


def loose_eq(a: str, b: str) -> bool:
    """やさしいルール: 濁点・半濁点の違いは同じ文字として数える(ご→こ でも、こ→ご でもつなげる)。"""
    return a == b or DAKU.get(a, a) == DAKU.get(b, b)


def clean_word(text: str) -> str:
    t = re.sub(r"[。、,.!?!?「」『』\s…~〜]", "", text or "")
    t = FILLERS.sub("", t)
    for tail in ("です", "だよ", "だ", "かな", "だと思う"):
        if t.endswith(tail) and len(t) > len(tail) + 1:
            t = t[:-len(tail)]
    return t


INDEX: dict = {}
for _w in WORDS:
    INDEX.setdefault(first_char(_w), []).append(_w)


def available(need: str, used: set) -> list:
    """need の文字から始まる、まだ使っていない辞書の言葉。ぴったりが無ければ、濁点違いまで広げる。"""
    exact = [w for w in INDEX.get(need, []) if w not in used and last_char(w) != "ん"]
    if exact:
        return exact
    return [w for c, ws in INDEX.items() if loose_eq(c, need) for w in ws if w not in used and last_char(w) != "ん"]


def choose_word(need: str, used: set, level: str, rnd) -> "str | None":
    """ロボットの言葉。level: easy=子どもが続けやすい文字で終わる / normal=ランダム / hard=続けにくい文字で終わる。"""
    cands = available(need, used) if need else [w for w in WORDS if w not in used and last_char(w) != "ん"]
    if not cands:
        return None
    # 次の人が使える言葉の数(多い=続けやすい)。行き止まりに近い文字で終わる言葉は、どのレベルでも避ける(ロボットが先に詰まらないように)
    score = {w: len(available(last_char(w), used | {w})) for w in cands}
    floor = 2 if level == "hard" else 3
    cands = [w for w in cands if score[w] >= floor] or cands
    if level == "normal":
        return rnd.choice(cands)
    ordered = sorted(cands, key=lambda w: score[w], reverse=(level == "easy"))
    top = ordered[:max(2, len(ordered) // 3)]
    return rnd.choice(top)


def judge(reading: str, need: str, used: set) -> "tuple[str, str]":
    """子どもの言葉の判定。(結果, 理由): ok / lose(んで終わった) / bad_start / dup / empty / long"""
    r = reading
    if len(r) < 2:
        return "empty", "もう少し長い言葉でね"
    if len(r) > 12:
        return "long", "ひとつの言葉だけ言ってね"
    if need and not loose_eq(first_char(r), need):
        return "bad_start", f"「{need}」からはじまる言葉だよ"
    if r in used:
        return "dup", "それはもう出たよ!ちがう言葉にしてね"
    if last_char(r) == "ん":
        return "lose", "「ん」がついちゃった!"
    return "ok", ""


class Shiritori:
    PERSONA = {"ずんだ": {"word": "{w}なのだ!", "emo": "excited"}, "めたん": {"word": "{w}、ですね。", "emo": "neutral"},
               "つむぎ": {"word": "{w}、かな?", "emo": "joy"}}

    def __init__(self, hub, reg, discuss, eng_getter, path: "str | None" = None, rnd=None, clock=time.time, stats: "dict | None" = None, save_cb=None, guard=None):
        self.hub, self.reg, self.discuss, self.eng_getter = hub, reg, discuss, eng_getter
        self.save_cb, self.guard = save_cb, guard          # 管理役(GameHub)から: 記録の共有と、「ほかの機器が話している間は聞かない」の判定
        self.path = path or os.environ.get("SC_GAME", os.path.expanduser("~/.sokutsu_game.json"))
        self.rnd = rnd or random.Random()
        self.clock = clock
        self.lock = threading.Lock()
        self.q: "queue.Queue" = queue.Queue()
        self.on = False
        self.level = "easy"
        self.chain: list = []          # [{"who","word","reading","kid"}]
        self.need = ""
        self.turn = ""                 # "kid" / ロボット名 / ""
        self.msg = ""
        self.result = ""               # kid_win / robot_win / stopped / ""
        self.score = 0                 # 子どもがつないだ数
        self.speaking = False
        self.quiet_until = 0.0
        self._stop = threading.Event()
        self.robots: list = []
        self.stats = stats if stats is not None else {"best": 0, "plays": 0}
        if stats is None:
            try:
                self.stats.update({k: int(v) for k, v in json.load(open(self.path)).items() if k in ("best", "plays")})
            except (OSError, ValueError):
                pass

    # ---- 保存
    def _save(self) -> None:
        if self.save_cb:
            self.save_cb()
            return
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.stats, f)
        except OSError:
            pass

    # ---- 開始・終了
    def start(self, first: str = "robot", level: str = "easy", dev_id: "str | None" = None) -> "tuple[bool, str]":
        if self.on:
            return False, "すでに遊んでいます(やめてから始めてください)"
        if self.discuss is not None and self.discuss.on:
            return False, "いまロボットが会話中です(止めてから始めてください)"
        eng = self.eng_getter()
        if eng is None:
            return False, "エンジンの準備中です"
        pk = self.discuss.pick() if self.discuss is not None else []
        if dev_id:                                          # この機器だけで遊ぶ(ほかの機器とは連携しない)
            pk = [(r, s) for r, s in pk if r["id"] == dev_id]
        if not pk:
            return False, "接続中の機器がありません"
        from discuss import PERSONAS
        self.robots = [{"id": r["id"], "name": PERSONAS[self.discuss.persona_of(r["id"])]["name"], "dev_name": r["name"],
                        "voice": PERSONAS[self.discuss.persona_of(r["id"])]["voice"]} for r, _s in pk]
        self.level = level if level in ("easy", "normal", "hard") else "easy"
        self.chain, self.need, self.turn, self.msg, self.result, self.score = [], "", "", "", "", 0
        self.speaking, self.quiet_until = False, 0.0
        while not self.q.empty():
            self.q.get_nowait()
        self._stop.clear()
        self.on = True
        self.stats["plays"] += 1
        self._save()
        self.hub.event(f"しりとりを始めます({len(self.robots)}台・{self.level})")
        threading.Thread(target=self._run, args=(eng, first == "robot"), daemon=True).start()
        return True, ""

    def stop(self) -> None:
        self._stop.set()
        self.q.put(None)

    # ---- 子どもの声(または先生の入力)
    def hear(self, text: str, lang: str = "ja") -> bool:
        """STT の結果を受け取る。しりとり中は、翻訳に回さないよう常に True を返す。"""
        if not self.on:
            return False
        if lang not in ("ja", "", None):
            return True
        if self.speaking or self.clock() < self.quiet_until or self.turn != "kid" or (self.guard and self.guard()):
            return True                                     # ロボットの声を自分のマイクが拾った分・順番外は捨てる
        self.q.put(text)
        return True

    def say(self, text: str) -> bool:
        """先生が画面から入力した言葉(聞き取りの待ち時間・ロボットの声の回り込みを無視して受け付ける)。"""
        if not self.on or self.turn != "kid":
            return False
        self.q.put(text)
        return True

    # ---- 部品
    def _sess(self, i: int):
        return self.hub.by_id(self.robots[i]["id"])

    def _face(self, state: str, only: "int | None" = None) -> None:
        for k in range(len(self.robots)):
            if only is not None and k != only:
                continue
            s = self._sess(k)
            if s is not None:
                try:
                    s.send({"t": "face", "state": state})
                except Exception:  # noqa: BLE001
                    pass

    def _speak(self, eng, i: int, text: str, emo: str = "joy") -> None:
        """ロボット i が声に出す(顔と字幕つき)。話している間と直後は、聞き取りを捨てる。"""
        from discuss import EMOTIONS
        s = self._sess(i)
        if s is None:
            return
        self.speaking = True
        try:
            pr = EMOTIONS.get(emo, EMOTIONS["neutral"])
            for k in range(len(self.robots)):
                sk = self._sess(k)
                if sk is not None:
                    sk.send({"t": "face", "state": pr[2] if k == i else "listening"})
                    if k != i:
                        sk.send({"t": "caption", "lang": "ja", "to": "ja", "orig": f'{self.robots[i]["name"]}: {text}', "trans": "", "final": True})
            try:
                pcm = eng.speech_pcm_as(text, self.robots[i]["voice"], emotion=pr[1])
            except TypeError:
                pcm = eng.speech_pcm_as(text, self.robots[i]["voice"])
            dur = s._send_pcm(pcm, caption=text, lang="ja")
            t0 = self.clock()
            while self.clock() - t0 < (dur or 0) + 0.3 and not self._stop.is_set():
                time.sleep(0.1)
        finally:
            self.speaking = False
            self.quiet_until = self.clock() + 1.4           # 自分たちの声の回り込みが、遅れて聞き取りに届くぶん待つ
            while not self.q.empty():
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break

    def _reading(self, eng, text: str) -> str:
        """子どもの言葉 → よみ(ひらがな)。かな・辞書にある言葉はそのまま。それ以外は AI に読みを聞く。"""
        t = clean_word(text)
        if not t:
            return ""
        if t in KANJI:
            return KANJI[t]
        k = kana_only(t)
        if k and k == to_hira(t):
            return k
        try:
            out, _l = eng.translate([{"role": "system", "content": "次の日本語の言葉の読みを、ひらがなだけで出力してください。説明・かっこ・記号は書かない。読めない言葉は「?」とだけ出力。"},
                                     {"role": "user", "content": t}])
            r = kana_only(re.sub(r"<think>.*?</think>", "", out or "", flags=re.S).strip().splitlines()[0] if (out or "").strip() else "")
            return r
        except Exception:  # noqa: BLE001
            return ""

    def _used(self) -> set:
        return {c["reading"] for c in self.chain}

    def _say_robot(self, eng, text: str, emo: str = "joy", who: "int | None" = None) -> None:
        i = self.rnd.randrange(len(self.robots)) if who is None else who
        self.msg = text
        self._speak(eng, i, text, emo)

    def _robot_turn(self, eng, r: int) -> bool:
        """ロボットが 1 語こたえる。言葉がなければ(まけ)False。"""
        i = r % len(self.robots)
        name = self.robots[i]["name"]
        self.turn = name
        w = choose_word(self.need, self._used(), self.level, self.rnd)
        if w is None:
            self.result = "kid_win"
            self._say_robot(eng, "うわー、思いつかない!まいったのだ。きみの勝ちだよ!" if name == "ずんだ" else "まいりました。あなたの勝ちです!", "surprise", i)
            return False
        self.chain.append({"who": name, "word": w, "reading": w, "kid": False})
        self.need = last_char(w)
        P = self.PERSONA.get(name, self.PERSONA["ずんだ"])
        self.msg = f"{name}: {w}"
        self._speak(eng, i, P["word"].format(w=w) + (f" つぎは『{self.need}』だよ。" if self.rnd.random() < 0.5 else ""), P["emo"])
        return True

    def _run(self, eng, robot_first: bool) -> None:
        try:
            self.turn = ""
            self._speak(eng, 0, "しりとりで、あそぼう!言葉の最後の文字から、つぎの言葉をつなげるよ。「ん」がついたら負けだよ!", "excited")
            if len(self.robots) > 1 and not self._stop.is_set():
                self._speak(eng, 1, "ことばは、ひとつだけ言ってね。わたしたちが、じゅんばんにこたえます。", "joy")
            r = 0
            if robot_first and not self._stop.is_set():
                if not self._robot_turn(eng, r):
                    return
                r += 1
            else:
                self.msg = "すきな言葉から、はじめてね!"
            idle = 0
            retries = 0
            while not self._stop.is_set():
                self.turn = "kid"
                self._face("listening")
                try:
                    text = self.q.get(timeout=15)
                except queue.Empty:
                    text = ""
                if self._stop.is_set() or text is None:
                    break
                if not text:                                 # 子どもの番で、しばらく何も言わなかった
                    idle += 1
                    if idle >= 5:
                        self.msg = "またあそぼうね!"
                        self._say_robot(eng, "またあそぼうね!ばいばーい!", "joy")
                        self.result = "stopped"
                        return
                    if idle == 2 and self.need:
                        hint = choose_word(self.need, self._used(), "easy", self.rnd)
                        self._say_robot(eng, f"ヒントだよ。『{self.need}』からだよ。たとえば『{hint[:1]}…』かな?" if hint else f"『{self.need}』からだよ。", "doubt")
                    else:
                        self._say_robot(eng, self.rnd.choice(NUDGE) + (f" 『{self.need}』からだよ。" if self.need else ""), "joy")
                    continue
                idle = 0
                reading = self._reading(eng, text)
                if not reading:
                    self._say_robot(eng, "ごめん、よく聞こえなかった。もう一回言ってね。", "doubt")
                    continue
                verdict, why = judge(reading, self.need, self._used())
                if verdict == "ok":
                    retries = 0
                    self.chain.append({"who": "あなた", "word": clean_word(text), "reading": reading, "kid": True})
                    self.score += 1
                    self.need = last_char(reading)
                    self.msg = f"あなた: {clean_word(text)}"
                    if self.score > self.stats["best"]:
                        self.stats["best"] = self.score
                        self._save()
                    self._face("happy")
                    if self.rnd.random() < 0.5:                 # ほめる(話者はランダム)
                        self._say_robot(eng, self.rnd.choice(CHEER), "joy")
                    if not self._robot_turn(eng, r):
                        self.score = max(self.score, 1)
                        return
                    r += 1
                elif verdict == "lose":
                    self.chain.append({"who": "あなた", "word": clean_word(text), "reading": reading, "kid": True})
                    self.result = "robot_win"
                    self.msg = f"あなた: {clean_word(text)}(ん)"
                    self._say_robot(eng, "あっ、「ん」がついちゃった!ざんねん、わたしたちの勝ち!でも、すっごく楽しかったよ!", "joy")
                    return
                else:                                          # やり直し(同じ番のまま)
                    retries += 1
                    extra = ""
                    if retries >= RETRY_MAX and self.need:
                        hint = choose_word(self.need, self._used(), "easy", self.rnd)
                        extra = f" ヒントは『{hint[:1]}…』だよ。" if hint else ""
                    self._say_robot(eng, why + extra, "doubt")
        except Exception as e:  # noqa: BLE001
            self.msg = f"エラー: {type(e).__name__}"
        finally:
            if not self.result:
                self.result = "stopped"
            self.turn = ""
            self._face("neutral")
            self.on = False
            self.hub.event(f"しりとり終了({self.result}・つないだ数 {self.score})")

    def snapshot(self) -> dict:
        return {"dev_id": self.robots[0]["id"] if self.robots else "", "dev_name": self.robots[0]["dev_name"] if self.robots else "", "persona": self.robots[0]["name"] if self.robots else "",
                "on": self.on, "level": self.level, "turn": self.turn, "need": self.need, "msg": self.msg, "result": self.result,
                "score": self.score, "best": self.stats["best"], "plays": self.stats["plays"],
                "robots": [{"name": r["name"], "dev_name": r["dev_name"]} for r in self.robots],
                "chain": [{"who": c["who"], "word": c["word"], "kid": c["kid"]} for c in self.chain[-30:]]}


class GameHub:
    CLS = None                                  # 1 台ぶんのゲームのクラス(省略時は Shiritori)。じゃんけんなどが差し替える
    SKEY = ""                                   # 記録ファイルの中の置き場所(空なら最上位の best/plays)
    KEYS = ("best", "plays")
    LABEL = "しりとり"
    """機器ごとに、独立したしりとりを持つ管理役。実際の現場は「ロボット対子どもたち」なので、3 台は連携せず、
    各機器が自分のマイク・スピーカーで、自分の前にいる子どもたちとだけ遊ぶ。ただし、ほかの機器が話している間は、
    その声の回り込みを聞き取らないよう、どの機器も聞き取りを止める。"""

    def __init__(self, hub, reg, discuss, eng_getter, path: "str | None" = None, rnd=None):
        self.hub, self.reg, self.discuss, self.eng_getter = hub, reg, discuss, eng_getter
        self.path = path or os.environ.get("SC_GAME", os.path.expanduser("~/.sokutsu_game.json"))
        self.rnd = rnd
        self.games: dict = {}
        self.lock = threading.Lock()
        self.peer = None                        # もう一つの教材(しりとり↔じゃんけん)。同じ機器で 2 つ同時に動かさないために見る
        self.stats = {k: 0 for k in self.KEYS}
        try:
            d = json.load(open(self.path))
            d = d.get(self.SKEY, {}) if self.SKEY else d
            self.stats.update({k: int(v) for k, v in d.items() if k in self.KEYS})
        except (OSError, ValueError, AttributeError):
            pass

    def save(self) -> None:
        """記録ファイルは、ほかの教材と共有する。自分の分だけ書き換える。"""
        try:
            try:
                allv = json.load(open(self.path))
                if not isinstance(allv, dict):
                    allv = {}
            except (OSError, ValueError):
                allv = {}
            if self.SKEY:
                allv[self.SKEY] = dict(self.stats)
            else:
                allv.update(self.stats)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(allv, f)
        except OSError:
            pass

    @property
    def own_on(self) -> bool:
        return any(g.on for g in self.games.values())

    @property
    def on(self) -> bool:
        """教材がどれか動いているか(会話・自律・子どもモードが、ぶつからないように見る)。"""
        return self.own_on or bool(self.peer is not None and self.peer.own_on)

    def dev_busy(self, dev_id: str) -> bool:
        g = self.games.get(dev_id)
        return bool(g and g.on)
    def _guard_for(self, me):
        return lambda: any(g is not me and (g.speaking or g.clock() < g.quiet_until) for g in self.games.values())

    def start(self, first: str = "robot", level: str = "easy", dev_ids: "list | None" = None) -> "tuple[bool, str]":
        """dev_ids の機器それぞれで、独立にしりとりを始める(省略なら、接続中の全機器)。"""
        if self.discuss is not None and self.discuss.on:
            return False, "いまロボットが会話中です(止めてから始めてください)"
        pk = self.discuss.pick() if self.discuss is not None else []
        ids = [r["id"] for r, _s in pk] if not dev_ids else [i for i in dev_ids if any(r["id"] == i for r, _s in pk)]
        if not ids:
            return False, "接続中の機器がありません"
        started, err = [], ""
        for i in ids:
            if self.peer is not None and self.peer.dev_busy(i):
                err = f"この機器は、いま{self.peer.LABEL}で遊んでいます"
                continue
            g = self.games.get(i)
            if g is None:
                g = self.games[i] = (self.CLS or Shiritori)(self.hub, self.reg, self.discuss, self.eng_getter, self.path, self.rnd, stats=self.stats, save_cb=self.save)
                g.guard = self._guard_for(g)
            ok, e = g.start(first, level, dev_id=i)
            if ok:
                started.append(i)
            else:
                err = e
        return (True, "") if started else (False, err)

    def stop(self, dev_id: "str | None" = None) -> None:
        for i, g in list(self.games.items()):
            if dev_id is None or i == dev_id:
                g.stop()

    def hear(self, text: str, lang: str = "ja", dev_id: str = "") -> bool:
        """機器 dev_id のマイクで聞き取った言葉を、その機器のしりとりへ。その機器が遊んでいなければ False(ふつうの翻訳へ)。"""
        g = self.games.get(dev_id)
        return bool(g and g.on and g.hear(text, lang))

    def say(self, text: str, dev_id: str = "") -> bool:
        """先生の入力。dev_id を省略すると、いま子どもの番の機器すべてへ。"""
        ok = False
        for i, g in self.games.items():
            if g.on and (not dev_id or i == dev_id):
                ok = g.say(text) or ok
        return ok

    def snapshot(self) -> dict:
        gs = [g.snapshot() for g in self.games.values() if g.robots]
        return {"on": self.own_on, "games": gs, "best": self.stats.get("best", 0), "plays": self.stats.get("plays", 0)}

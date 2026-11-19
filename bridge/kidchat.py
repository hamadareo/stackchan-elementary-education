"""子どもが機器に話しかけたとき、その機器のキャラが、やさしい短い返事をする(子どもモード)。

- 日本語で話しかけられたときだけ。しりとり中・3人会話中・ほかの機器が話している間は何もしない(割り込まない)。
- 返事は AI(eng.translate)に作らせるが、長さ・言葉づかいはコードで整える(1〜2文、短く、危ない話題は決まった返事)。
"""
from __future__ import annotations

import re
import threading
import time

from discuss import EMOTIONS, PERSONAS, emo_key, split_emotion, guess_emotion

# 子ども向けの口調(キャラごと)。恋愛・怖い話・むずかしい説明はしない
STYLE = [
    "語尾は「〜なのだ」「〜のだ」。げんきで、びっくりしたり、よろこんだりする。",
    "おだやかで、やさしいおねえさん。「〜よ」「〜ね」と話す。ていねいだけど、こわくない。",
    "おっとりしていて、あたたかい。「〜だよ」「〜だね」と、ゆっくり話す。",
]
# 子どもに話してはいけない・深入りしない話題 → 決まった返事
SENSITIVE = re.compile(r"死|殺|しね|ころす|自殺|血|幽霊|おばけ|お化け|こわい話|怖い話|恋|好きな人|彼氏|彼女|キス|えっち|エッチ|セックス|酒|タバコ|たばこ")
SAFE = ["それは、先生やおうちの人に、きいてみてね。ほかのおはなしをしよう！", "うーん、それはロボットにはむずかしいな。ほかのおはなしをしよう！"]
FILLER = re.compile(r"^[\s、。,.!！?？ーあえおうんはひ]*$")


# 聞き取りが雑音から作ってしまう定番の言葉(動画の決まり文句など)。子どもの声ではないので返事をしない
HALLU = re.compile(r"ご視聴|チャンネル登録|字幕|最後までご覧|ご清聴|次の動画|高評価")


def worth_answering(text: str) -> bool:
    """雑音・あいづちだけ・短すぎる・同じ音の繰り返し(いっいっいっ)・動画の決まり文句には、返事をしない。"""
    t = (text or "").strip()
    c = re.sub(r"[\s、。,.!！?？]", "", t)
    if len(c) < 2 or FILLER.match(t) or HALLU.search(c):
        return False
    return not re.fullmatch(r"(.{1,2})\1{2,}", c)


def kid_msgs(k: int, name: str, text: str, hist: list) -> list:
    p = PERSONAS[k % len(PERSONAS)]
    # 共通の指示を先頭に置き、キャラごとの違いは最後に置く(LLM が先頭からの同じ部分を使い回せて、返事が速くなる)
    sys = ("あなたは、小学校で子どもたちとお話しするロボットです。子どもが話しかけてきました。きちんと内容に答えて、やさしく返事をしてください。\n"
           "ルール: 1〜2文、全部で50文字ぐらいまで。むずかしい言葉は使わず、ひらがなを多めにする。説明や箇条書きはしない。"
           "まず子どもの言ったことに、気持ちを込めて答える。そのあと、できるだけ、話が続く簡単な質問を1つ、最後につける(例: 「どんなのが すき？」「どうして？」「それから どうなったの？」)。子どもの名前や好きなものを聞いたら、おぼえて、あとで話にだす。"
           "こわい話・恋愛・けんか・おとなの話はしない。わからないときは、正直に「わからないよ」と言って、子どもに質問し返す。\n"
           "出力の先頭に、気持ちを【喜び】【驚き】【照れ】【平常】【困惑】のどれか1つ、かっこつきで付ける。例: 【喜び】きょうは、いいてんきだね！\n"
           f"あなたの名前は「{name}」。{p['trait'].split('。')[0]}。{STYLE[k % len(STYLE)]}")
    msgs = [{"role": "system", "content": sys}]
    for who, line in hist[-8:]:
        msgs.append({"role": "user" if who == "kid" else "assistant", "content": line})
    msgs.append({"role": "user", "content": text.strip()})
    return msgs


def clean_reply(out: str, prev: str = "neutral") -> "tuple[str, str]":
    """AI の出力 → (感情, 返事)。思考・注釈・かっこを落とし、2文・60文字までに切る。"""
    out = re.sub(r"<think>.*?</think>", "", out or "", flags=re.S).strip()
    emo, body = split_emotion(out, prev)
    body = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", body)                  # 絵文字は読み上げ・画面で崩れる
    body = re.sub(r"[「」『』*#\-・]", "", body).strip().split("\n")[0].strip()
    body = re.sub(r"([。！？!?])\s*[ねよわ]。$", r"\1", body)                                  # 文のあとに語尾だけ残さない
    body = re.sub(r"^(ずんだもん|ずんだ|めたん|つむぎ|ロボット)\s*[、,:：]\s*", "", body)     # 自分の名前を先頭につけない
    body = re.sub(r"([？?])\s*のだ[。!！]*$", r"\1", body)                                   # 質問のあとに語尾だけ残さない
    parts = re.findall(r".+?[。！？!?]|.+$", body)
    body = "".join(parts[:2]).strip()
    if len(body) > 60:
        body = body[:60].rstrip("、,") + "。"
    return emo, body


ASOBO = re.compile(r"あそぼ|遊ぼ|あそびたい|遊びたい|なにしよう|何しよう|なにして遊|なにしてあそ")
ACT_LABEL = {"shiritori": "しりとり", "janken": "じゃんけん", "riddle": "なぞなぞ", "omikuji": "おみくじ", "mimic": "ものまね"}
ACT_DEFAULT = ["shiritori", "janken", "riddle", "omikuji", "mimic"]      # 子どもに出す遊びは、この5個だけ(ほかのことはしない)
MENU = "しりとり、じゃんけん、なぞなぞ、おみくじ、ものまねが、できるよ！やりたいのを、言ってね！"
GUIDE_GAP = 7.0         # 遊びを選ぶ案内を、同じ機器でくり返すまでの最短の秒数


def menu_text(acts: list, lead: str = "") -> str:
    """出せる遊びを読み上げる文。例: 「しりとり、じゃんけん、なぞなぞの、どれで あそぶ？ えらんでね！」"""
    names = [ACT_LABEL[a] for a in ACT_DEFAULT if a in acts]
    if not names:
        return lead or "こんにちは！"
    return lead + "、".join(names) + "の、どれで あそぶ？ えらんでね！"
JANKEN = re.compile(r"じゃんけん|ジャンケン|じゃん拳")
SHIRITORI = re.compile(r"しりとり|尻取り|しりとろ|しり取り")


# 自分から話しかける(子どもが黙ったとき)。こわくない・むずかしくない・答えやすい一言
NUDGES = ["ねえ、ほかにも おはなし しよう！", "ぼくは、もっと きいてみたいな。なにか おしえて！", "つぎは、なんの おはなしを する？", "しりとりか じゃんけんも できるよ！"]
NUDGE_S = 9.0           # 子どもの返事が止まってから、自分から続きを話すまでの秒数
ENGAGED_S = 120.0       # 子どもが最後に話してから、この間は「会話中」とみなす
NUDGE_MAX = 2           # 返事がないとき、続けて話しかける回数(超えたら静かに待つ)


def nudge_msgs(k: int, name: str, hist: list) -> list:
    """子どもが黙ってしまったとき、前の話を受けて、続きの一言(質問)を作らせる。"""
    p = PERSONAS[k % len(PERSONAS)]
    sys = ("あなたは、小学校で子どもたちとお話しするロボットです。さっきまで子どもと話していましたが、子どもが少し黙りました。\n"
           "前の話に続けて、やさしく、答えやすい質問か、ひとことを、1文(30文字ぐらいまで)で言ってください。むずかしい言葉・こわい話・恋愛は使わない。ひらがなを多めに。同じ質問は繰り返さない。\n"
           "出力の先頭に、気持ちを【喜び】【驚き】【照れ】【平常】【困惑】のどれか1つ、かっこつきで付ける。\n"
           f"あなたの名前は「{name}」。{p['trait'].split('。')[0]}。{STYLE[k % len(STYLE)]}")
    msgs = [{"role": "system", "content": sys}]
    for who, line in hist[-8:]:
        msgs.append({"role": "user" if who == "kid" else "assistant", "content": line})
    msgs.append({"role": "user", "content": "(子どもは黙っています。続きを話しかけてください)"})
    return msgs


# 「なぞなぞしよう」「おみくじ」…(短く言うか、しよう・やろう などの言葉つきのとき)。通訳の指示(「えいごにして」)とは区別する
PLAY = re.compile(r"しよう|しよ|やろう|やろ|あそぼ|遊ぼ|したい|やりたい|やって|出して|だして|おしえて|ちょうだい|しましょう|やりましょう|やる|して$|ひきたい|ひく|ひこう|ひいて|ひかせて|引き|ひきましょう")
NOT_PLAY = re.compile(r"にして|に翻訳|に訳|通訳|で言って|でいって|で話|でしゃべ|ほんやく")
KIND_PAT = [("omikuji", re.compile(r"おみくじ|オミクジ|お神籤|御神籤|おみくじ")),
            ("mimic", re.compile(r"ものまね|モノマネ|物まね|物真似|まねっこ|マネッコ|おうむがえし|オウム返し|まねして|マネして|真似して")),
            ("riddle", re.compile(r"なぞなぞ|ナゾナゾ|謎々|謎謎"))]


def quiz_kind(text: str) -> "str | None":
    """子どもの言葉から、始めたい遊び(quiz.py / fun.py の種類)を決める。なければ None。"""
    t = (text or "").strip()
    if NOT_PLAY.search(t):
        return None
    short = len(re.sub(r"[\s、。,.!！?？]", "", t)) <= 10
    for kind, pat in KIND_PAT:
        if pat.search(t) and (PLAY.search(t) or short):
            return kind
    return None


class KidChat:
    """機器ごとの短い会話の記憶と、返事の発話。全機器で同時には 1 人しか話さない。"""

    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        self.busy = threading.Lock()
        self.speaking = False
        self.quiet_until = 0.0
        self.hist: dict = {}
        self.n = 0
        self.last_kid: dict = {}          # 機器 → 子どもが最後に話した時刻
        self.last_end: dict = {}          # 機器 → ロボットが最後に話し終えた時刻
        self.nudges: dict = {}            # 機器 → 返事がないまま続けて話しかけた回数
        self.next_idle: dict = {}         # 機器 → 次に自分から話しかける時刻
        self.used: dict = {}              # 機器 → 使った話しかけ(重ならないように)
        self.rnd = __import__("random").Random()
        self._sw_lock = threading.Lock()
        self._switching: set = set()
        self.last_guide: dict = {}        # 機器 → 遊びを案内した時刻
        self.greet_req: dict = {}         # 機器 → 人を見つけた時刻(あいさつ待ち)
        self.last_greet: dict = {}        # 機器 → 最後にあいさつした時刻

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.hub, "kid_on", True))      # 設定は管理画面(Hub)が持ち、保存される

    GAME_HUBS = ("game", "janken", "quiz")              # しりとり・じゃんけん・なぞなぞ/ホイ/おみくじ/ものまね の管理役

    def playing(self, dev: str) -> str:
        """この機器で遊んでいる遊びの管理役の名前(なければ空)。ほかの機器の遊びは関係ない(3 台は独立)。"""
        for n in self.GAME_HUBS:
            h = getattr(self.hub, n, None)
            try:
                if h is not None and dev and h.dev_busy(dev):
                    return n
            except Exception:  # noqa: BLE001
                continue
        return ""

    def blocked(self, dev: "str | None" = None) -> str:
        """子どもモードが返事をしてはいけない理由(なければ空)。dev を渡すと、遊びはその機器のものだけを見る(省略なら、どの機器でも)。"""
        if not self.enabled:
            return "子どもモードがオフです"
        if getattr(self.hub, "discussing", None) and self.hub.discussing():
            return "3人会話中です"
        if dev is not None:
            if self.playing(dev):
                return "この機器は遊んでいます"
        elif any(getattr(getattr(self.hub, n, None), "on", False) for n in self.GAME_HUBS):
            return "遊んでいます"
        if self.speaking or self.clock() < self.quiet_until:
            return "ロボットが話している間です"
        return ""

    def hear(self, sess, text: str, lang: str) -> bool:
        """この機器で聞き取った言葉。返事をする(別スレッド)なら True。しないなら、ふつうの翻訳に任せる(False)。"""
        if lang != "ja" or not worth_answering(text) or self.blocked(getattr(sess, "dev_id", "")):
            return False
        guided = bool(getattr(self.hub, "edu_on", False))           # 教材モード: 決まった遊びだけ。自由なおしゃべりはしない
        if guided and getattr(self.hub, "teacher_only", True):       # 先生のボタンだけで遊びを始める: 遊びが選ばれていない間は、子どもの声に何も反応しない
            return True
        acts = self.acts()
        try:
            if getattr(self.hub, "quiz", None) is not None:
                kind = quiz_kind(text)
                if kind and (not guided or kind in acts) and self._start_quiz(sess, kind):           # 「なぞなぞしよう」など: その機器で始める
                    return True
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] 遊びの判定に失敗: {type(e).__name__}: {e}", flush=True)
        if ASOBO.search(text) and not (JANKEN.search(text) or SHIRITORI.search(text)):   # 「あそぼう」: できる遊びを教える
            self._menu(sess)
            return True
        if JANKEN.search(text) and (not guided or "janken" in acts) and self._start_janken(sess):            # 「じゃんけんしよう」: その機器でじゃんけんを始める
            return True
        if SHIRITORI.search(text) and (not guided or "shiritori" in acts) and self._start_shiritori(sess):      # 「しりとりしよう」: その機器でしりとりを始める
            return True
        if guided:                                           # 教材モード: 遊びを選んでいない声には、何も話さない(「あそぼう」だけは遊びを案内する)
            return True
        if not self.busy.acquire(blocking=False):
            return True                                      # 返事を考え中の子どもの声は、翻訳せず捨てる
        self.speaking = True
        try:
            sess.send({"t": "face", "state": "thinking"})    # すぐ「考え中」の顔にして、待ち時間を短く感じさせる
        except Exception:  # noqa: BLE001
            pass
        dev = getattr(sess, "dev_id", "")
        self.last_kid[dev], self.nudges[dev] = self.clock(), 0          # 子どもが話した: 会話中にして、話しかけの回数を数え直す
        threading.Thread(target=self._reply, args=(sess, text.strip()), daemon=True).start()
        return True

    def want(self, text: str) -> "str | None":
        """はっきり「〜しよう」と言われた遊び(shiritori / janken / 遊びの種類)。名前だけでは決めない(しりとりの言葉や答えと区別するため)。"""
        t = (text or "").strip()
        if not t or NOT_PLAY.search(t) or not PLAY.search(t) or len(t) > 24:
            return None
        if JANKEN.search(t) and not KIND_PAT[0][1].search(t):
            return "janken"
        if SHIRITORI.search(t):
            return "shiritori"
        for kind, pat in KIND_PAT:
            if pat.search(t):
                return kind
        return None

    def current(self, dev: str) -> str:
        """この機器で遊んでいる遊びの種類(shiritori / janken / riddle / hoi / omikuji / mimic)。"""
        n = self.playing(dev)
        if n == "game":
            return "shiritori"
        if n == "janken":
            return "janken"
        if n == "quiz":
            g = self.hub.quiz.games.get(dev)
            return getattr(g, "kind", "") or "riddle"
        return ""

    def switch(self, sess, text: str) -> bool:
        """遊びの途中で、べつの遊びを「〜しよう」と言われたら、いまの遊びを終えて、新しい遊びを始める。切り替えを始めたら True。"""
        dev = getattr(sess, "dev_id", "")
        if not dev or not self.enabled or (getattr(self.hub, "edu_on", False) and getattr(self.hub, "teacher_only", True)):
            return False                                     # 先生のボタンだけで切り替える
        cur = self.current(dev)
        want = self.want(text)
        if not cur or not want or want == cur:
            return False
        if getattr(self.hub, "edu_on", False) and want not in self.acts():
            return False
        with self._sw_lock:
            if dev in self._switching:
                return True
            self._switching.add(dev)

        def go():
            try:
                for n in self.GAME_HUBS:
                    h = getattr(self.hub, n, None)
                    if h is not None:
                        h.stop(dev)
                t0 = time.time()
                while self.playing(dev) and time.time() - t0 < 8.0:
                    time.sleep(0.1)
                if self.playing(dev):
                    return
                if want == "janken":
                    self._start_janken(sess)
                elif want == "shiritori":
                    self._start_shiritori(sess)
                else:
                    self._start_quiz(sess, want)
            finally:
                with self._sw_lock:
                    self._switching.discard(dev)
        threading.Thread(target=go, daemon=True).start()
        return True

    def acts(self) -> list:
        """子どもに出す遊び(管理画面で選べる)。"""
        a = getattr(self.hub, "menu_acts", None)
        return list(a) if a is not None else list(ACT_DEFAULT)

    def _start_quiz(self, sess, kind: str) -> bool:
        h = self.hub
        q = getattr(h, "quiz", None)
        dev = getattr(sess, "dev_id", "")
        if q is None or not dev:
            return False
        try:
            if getattr(h, "auto", None):
                h.auto.set(enabled=False)
            h.listen = True
            ok, _err = q.start(kind, "", [dev])
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] 遊びの開始失敗: {type(e).__name__}: {e}", flush=True)
            return False
        if ok:
            print(f"[bridge] 子どもの声で{kind}を始めました", flush=True)
        return bool(ok)

    def _start_janken(self, sess) -> bool:
        h = self.hub
        j = getattr(h, "janken", None)
        dev = getattr(sess, "dev_id", "")
        if j is None or not dev:
            return False
        try:
            if getattr(h, "auto", None):
                h.auto.set(enabled=False)
            h.listen = True
            ok, _err = j.start("robot", "easy", [dev])
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] じゃんけん開始失敗: {type(e).__name__}: {e}", flush=True)
            return False
        if ok:
            print("[bridge] 子どもの声でじゃんけんを始めました", flush=True)
        return bool(ok)

    def _menu(self, sess) -> None:
        """「あそぼう」と言われたら、遊びの選び方を、その機器の声で話す(返事中は何もしない)。"""
        if not self.busy.acquire(blocking=False):
            return
        self.speaking = True

        def go():
            try:
                eng = sess.eng
                voice = getattr(sess, "voice", None) or PERSONAS[0]["voice"]
                sess.send({"t": "face", "state": "happy"})
                mt = menu_text(self.acts())
                pcm = eng.speech_pcm_as(mt, voice)
                dur = sess._send_pcm(pcm, caption=mt, lang="ja")
                time.sleep((dur or 0) + 0.3)
            except Exception as e:  # noqa: BLE001
                print(f"[bridge] 遊びの案内に失敗: {type(e).__name__}: {e}", flush=True)
            finally:
                self.speaking = False
                self.quiet_until = self.clock() + 1.4
                self.busy.release()
        threading.Thread(target=go, daemon=True).start()

    def _start_shiritori(self, sess) -> bool:
        """子どもの「しりとりしよう」で、この機器だけしりとりを始める(自律モードは止める)。始められなければ False。"""
        h = self.hub
        g = getattr(h, "game", None)
        dev = getattr(sess, "dev_id", "")
        if g is None or not dev:
            return False
        try:
            if getattr(h, "auto", None):
                h.auto.set(enabled=False)
            h.listen = True
            ok, _err = g.start("robot", "easy", [dev])
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] しりとり開始失敗: {type(e).__name__}: {e}", flush=True)
            return False
        if ok:
            print("[bridge] 子どもの声でしりとりを始めました", flush=True)
        return bool(ok)

    def _reply(self, sess, text: str) -> None:
        try:
            eng = sess.eng
            dev = getattr(sess, "dev_id", "")
            k = self.hub.discuss.persona_of(dev) if getattr(self.hub, "discuss", None) else 0
            name = PERSONAS[k % len(PERSONAS)]["name"]
            if SENSITIVE.search(text):
                emo, line = "doubt", SAFE[self.n % len(SAFE)]
            else:
                hist = self.hist.setdefault(dev, [])
                out, _l = eng.translate(kid_msgs(k, name, text, hist))
                emo, line = clean_reply(out)
                if not has_tag(out):
                    emo = guess_emotion(line, "neutral")
            if not line:
                line, emo = "うん、きこえたよ！", "joy"
            self.n += 1
            h = self.hist.setdefault(dev, [])
            h += [("kid", text), ("robot", line)]
            del h[:-12]
            pr = EMOTIONS.get(emo, EMOTIONS["neutral"])
            sess.send({"t": "face", "state": pr[2]})
            voice = getattr(sess, "voice", None) or PERSONAS[k % len(PERSONAS)]["voice"]
            try:
                pcm = eng.speech_pcm_as(line, voice, emotion=pr[1])
            except TypeError:
                pcm = eng.speech_pcm_as(line, voice)
            print(f"[bridge] 子どもモード({name}): 「{text}」→「{line}」", flush=True)
            dur = sess._send_pcm(pcm, caption=line, lang="ja")
            time.sleep((dur or 0) + 0.3)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] 子どもモード失敗: {type(e).__name__}: {e}", flush=True)
        finally:
            self.speaking = False
            self.quiet_until = self.clock() + 1.4            # 自分の声の回り込みが、遅れて届くぶん待つ
            self.last_end[getattr(sess, "dev_id", "")] = self.clock()
            self.busy.release()

    # ---- 自分から話しかける(自律) ----
    @property
    def auto_on(self) -> bool:
        return bool(getattr(self.hub, "kid_auto", True))

    def pending(self, sess, now: float) -> str:
        """この機器が、いま自分から話しかけるべきか。"nudge"(子どもが黙った) / ""。"""
        dev = getattr(sess, "dev_id", "")
        if not dev or not self.auto_on or self.blocked(dev) or getattr(sess, "talking", False):
            return ""
        if getattr(self.hub, "edu_on", False):
            return ""                                           # 教材モード: 遊びが選ばれるまで、ロボットから話さない
        eng = getattr(sess, "eng", None)
        if now - getattr(eng, "last_voice", 0.0) < 4.0:            # 誰かが話している最中は、話しかけない
            return ""
        kid_t, end_t = self.last_kid.get(dev, 0.0), self.last_end.get(dev, 0.0)
        if kid_t and now - kid_t < ENGAGED_S and self.nudges.get(dev, 0) < NUDGE_MAX:
            return "nudge" if now - max(kid_t, end_t) >= NUDGE_S else ""
        if kid_t and now - kid_t < ENGAGED_S:
            return ""                                           # 話しかけても返事がない: 静かに待つ
        return ""

    def tick(self, now: "float | None" = None) -> bool:
        """見張りの 1 回ぶん。話しかけを始めたら True(1 度に 1 台だけ)。"""
        now = self.clock() if now is None else now
        live = list(getattr(self.hub, "live", lambda: [])())
        self.rnd.shuffle(live)
        for sess in live:
            kind = self.pending(sess, now)
            if kind and self.busy.acquire(blocking=False):
                self.speaking = True
                dev = getattr(sess, "dev_id", "")
                if kind == "nudge":
                    self.nudges[dev] = self.nudges.get(dev, 0) + 1
                threading.Thread(target=self._initiate, args=(sess, kind), daemon=True).start()
                return True
        return False

    def _initiate(self, sess, kind: str) -> None:
        try:
            eng = sess.eng
            dev = getattr(sess, "dev_id", "")
            k = self.hub.discuss.persona_of(dev) if getattr(self.hub, "discuss", None) else 0
            name = PERSONAS[k % len(PERSONAS)]["name"]
            hist = self.hist.setdefault(dev, [])
            emo, line = "joy", ""
            if kind == "nudge" and hist:
                try:
                    out, _l = eng.translate(nudge_msgs(k, name, hist))
                    emo, line = clean_reply(out)
                    if not has_tag(out):
                        emo = guess_emotion(line, "neutral")
                except Exception:  # noqa: BLE001
                    line = ""
                recent = [x for who, x in hist[-8:] if who == "robot"]
                if line and (SENSITIVE.search(line) or any(line == r or (len(line) > 6 and line[:8] == r[:8]) for r in recent)):
                    line = ""                                       # 危ない話題・さっきと同じ話しかけは使わず、決まった質問にする
            if not line:
                pool = NUDGES
                used = self.used.setdefault(dev, [])
                left = [t for t in pool if t not in used] or pool
                line = self.rnd.choice(left)
                used.append(line)
                del used[:-8]
                emo = "joy"
            hist += [("robot", line)]
            del hist[:-12]
            pr = EMOTIONS.get(emo, EMOTIONS["neutral"])
            sess.send({"t": "face", "state": pr[2]})
            voice = getattr(sess, "voice", None) or PERSONAS[k % len(PERSONAS)]["voice"]
            try:
                pcm = eng.speech_pcm_as(line, voice, emotion=pr[1])
            except TypeError:
                pcm = eng.speech_pcm_as(line, voice)
            print(f"[bridge] 子どもモード自発({name}/{kind}): 「{line}」", flush=True)
            dur = sess._send_pcm(pcm, caption=line, lang="ja")
            time.sleep((dur or 0) + 0.3)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] 子どもモードの話しかけに失敗: {type(e).__name__}: {e}", flush=True)
        finally:
            self.speaking = False
            self.quiet_until = self.clock() + 1.4
            self.last_end[getattr(sess, "dev_id", "")] = self.clock()
            self.busy.release()

    def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 見張りは落とさない
                pass
            time.sleep(2.0)


def has_tag(out: str) -> bool:
    return bool(re.search(r"[【\[][^】\]]{1,6}[】\]]", re.sub(r"<think>.*?</think>", "", out or "", flags=re.S)))

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


def worth_answering(text: str) -> bool:
    """雑音・あいづちだけ・短すぎる聞き取りには、返事をしない。"""
    t = (text or "").strip()
    return len(re.sub(r"[\s、。,.!！?？]", "", t)) >= 2 and not FILLER.match(t)


def kid_msgs(k: int, name: str, text: str, hist: list) -> list:
    p = PERSONAS[k % len(PERSONAS)]
    sys = (f"あなたは、小学校で子どもたちとお話しするロボット「{name}」です。{p['trait'].split('。')[0]}。{STYLE[k % len(STYLE)]}\n"
           "子どもが話しかけてきました。きちんと内容に答えて、やさしく返事をしてください。\n"
           "ルール: 1〜2文、全部で40文字ぐらいまで。むずかしい言葉は使わず、ひらがなを多めにする。説明や箇条書きはしない。"
           "こわい話・恋愛・けんか・おとなの話はしない。わからないときは、正直に「わからないよ」と言って、子どもに質問し返す。\n"
           "出力の先頭に、気持ちを【喜び】【驚き】【照れ】【平常】【困惑】のどれか1つ、かっこつきで付ける。例: 【喜び】きょうは、いいてんきだね！")
    msgs = [{"role": "system", "content": sys}]
    for who, line in hist[-4:]:
        msgs.append({"role": "user" if who == "kid" else "assistant", "content": line})
    msgs.append({"role": "user", "content": text.strip()})
    return msgs


def clean_reply(out: str, prev: str = "neutral") -> "tuple[str, str]":
    """AI の出力 → (感情, 返事)。思考・注釈・かっこを落とし、2文・60文字までに切る。"""
    out = re.sub(r"<think>.*?</think>", "", out or "", flags=re.S).strip()
    emo, body = split_emotion(out, prev)
    body = re.sub(r"[「」『』*#\-・]", "", body).strip().split("\n")[0].strip()
    parts = re.findall(r".+?[。！？!?]|.+$", body)
    body = "".join(parts[:2]).strip()
    if len(body) > 60:
        body = body[:60].rstrip("、,") + "。"
    return emo, body


class KidChat:
    """機器ごとの短い会話の記憶と、返事の発話。全機器で同時には 1 人しか話さない。"""

    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        self.busy = threading.Lock()
        self.speaking = False
        self.quiet_until = 0.0
        self.hist: dict = {}
        self.n = 0

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.hub, "kid_on", True))      # 設定は管理画面(Hub)が持ち、保存される

    def blocked(self) -> str:
        """子どもモードが返事をしてはいけない理由(なければ空)。"""
        if not self.enabled:
            return "子どもモードがオフです"
        if getattr(self.hub, "discussing", None) and self.hub.discussing():
            return "3人会話中です"
        if getattr(getattr(self.hub, "game", None), "on", False):
            return "しりとり中です"
        if self.speaking or self.clock() < self.quiet_until:
            return "ロボットが話している間です"
        return ""

    def hear(self, sess, text: str, lang: str) -> bool:
        """この機器で聞き取った言葉。返事をする(別スレッド)なら True。しないなら、ふつうの翻訳に任せる(False)。"""
        if lang != "ja" or not worth_answering(text) or self.blocked():
            return False
        if not self.busy.acquire(blocking=False):
            return True                                      # 返事を考え中の子どもの声は、翻訳せず捨てる
        self.speaking = True
        try:
            sess.send({"t": "face", "state": "thinking"})    # すぐ「考え中」の顔にして、待ち時間を短く感じさせる
        except Exception:  # noqa: BLE001
            pass
        threading.Thread(target=self._reply, args=(sess, text.strip()), daemon=True).start()
        return True

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
            del h[:-8]
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
            self.busy.release()


def has_tag(out: str) -> bool:
    return bool(re.search(r"[【\[][^】\]]{1,6}[】\]]", re.sub(r"<think>.*?</think>", "", out or "", flags=re.S)))

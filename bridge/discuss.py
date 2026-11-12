"""3 台で議題について話し合う「会話モード」。

議題を入力すると、接続中の StackChan(最大 3 台)がそれぞれ別のキャラクターと声で順番に話す。
発言は LLM(いまの翻訳と同じモデル)が、これまでの会話を踏まえて 1 人ぶんずつ作る。
話している間は全機器のマイクを止める(お互いの声を拾って翻訳しないように)。途中で「私」の発言も差し込める。
"""
from __future__ import annotations

import re
import threading
import time

PERSONAS = [
    {"name": "ずんだ", "voice": "voicevox:3", "trait": "元気で好奇心いっぱい。素直に驚き、思いついたことをすぐ言う。語尾は「〜なのだ」「〜のだ」。"},
    {"name": "めたん", "voice": "voicevox:2", "trait": "知的で少し皮肉屋。丁寧な言葉づかいで、論点を整理したり、鋭い指摘や反論をする。"},
    {"name": "つむぎ", "voice": "voicevox:8", "trait": "おっとりして現実的。身近な具体例や体験談を出して、話をやわらかくまとめる。"},
]
MAX_CHARS = 70
HISTORY = 14                      # LLM に渡す直近の発言数


def clean_line(text: str, name: str = "") -> str:
    """LLM の出力から、名前の前置き・括弧・思考の残りを取り除いて 1〜2 文にする。"""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^\s*[「『(（\[]?\s*(?:%s|ずんだ|めたん|つむぎ|ずんだもん)\s*[」』)）\]]?\s*[:：]\s*" % re.escape(name or "x"), "", t)
    t = t.strip().strip("「」『』\"'")
    t = t.splitlines()[0].strip() if t else ""
    if len(t) > MAX_CHARS:
        cut = max(t.rfind("。", 0, MAX_CHARS), t.rfind("！", 0, MAX_CHARS), t.rfind("？", 0, MAX_CHARS))
        t = t[:cut + 1] if cut >= 15 else t[:MAX_CHARS]
    return t


def build_msgs(topic: str, people: list, idx: int, log: list, last: bool = False) -> list:
    me = people[idx]
    names = "・".join(p["name"] for p in people)
    sysmsg = (f"あなたは「{me['name']}」。{me['trait']}\n"
              f"いま {names}(と、ときどき参加する「私」)の{len(people)}人で、議題「{topic}」について雑談のように話し合っています。\n"
              f"ルール: {me['name']}として次の一言だけを、日本語の話し言葉で、1〜2文・{MAX_CHARS - 10}文字以内で話す。"
              "名前・かっこ・説明・絵文字は書かない。直前の発言に反応し、同じことを繰り返さず、"
              "自分の意見や具体例を言い切るのを基本にし、質問で終わるのは 3 回に 1 回までにする(「どう?」で毎回終えない)。ときどき反論や別の視点も出して話を進める。" + ("今回は話をきれいに締めくくる。" if last else ""))
    hist = "\n".join(f"{who}: {text}" for who, text in log[-HISTORY:]) or "(まだ誰も話していません。口火を切ってください)"
    return [{"role": "system", "content": sysmsg},
            {"role": "user", "content": f"これまでの会話:\n{hist}\n\n{me['name']}の番です。{me['name']}の発言だけを出力してください。"}]


class Discussion:
    def __init__(self, hub, reg, eng_getter):
        self.hub, self.reg, self.eng_getter = hub, reg, eng_getter
        self.on = False
        self.topic = ""
        self.turn = 0
        self.max_turns = 12
        self.people: list = []         # [{"name","voice","trait","dev"(機器の ID),"dev_name"}]
        self.log: list = []            # [(話者名, 発言)]
        self._stop = threading.Event()
        self._inject: list = []
        self.lock = threading.Lock()
        self.note = ""

    # --- 参加者: 接続中の機器を、登録順に最大 3 台
    def pick(self) -> list:
        out = []
        for rec in self.reg.all():
            s = self.hub.by_id(rec["id"])
            if s is not None:
                out.append((rec, s))
        return out[:3]

    def start(self, topic: str, turns: int = 12) -> "tuple[bool, str]":
        topic = (topic or "").strip()[:80]
        if not topic:
            return False, "議題が空です"
        if self.on:
            return False, "すでに会話中です(止めてから始めてください)"
        eng = self.eng_getter()
        if eng is None:
            return False, "エンジンの準備中です"
        pk = self.pick()
        if len(pk) < 2:
            return False, "接続中の機器が 2 台以上必要です"
        self.people = [{**PERSONAS[i], "dev": r["id"], "dev_name": r["name"]} for i, (r, _s) in enumerate(pk)]
        self.topic, self.turn, self.max_turns, self.log, self._inject, self.note = topic, 0, max(2, min(40, int(turns))), [], [], ""
        self._stop.clear()
        self.on = True
        self.hub.event(f"会話モード開始: {topic}({len(self.people)}人)")
        threading.Thread(target=self._run, args=(eng,), daemon=True).start()
        return True, ""

    def stop(self) -> None:
        self._stop.set()

    def say(self, text: str) -> None:
        """「私」の発言を、次の話者が受けるように差し込む。"""
        t = (text or "").strip()[:100]
        if t and self.on:
            with self.lock:
                self._inject.append(t)

    def _sess(self, i: int):
        return self.hub.by_id(self.people[i]["dev"])

    def _gen(self, eng, idx: int, last: bool) -> str:
        with self.lock:
            while self._inject:
                self.log.append(("私", self._inject.pop(0)))
        out, _label = eng.translate(build_msgs(self.topic, self.people, idx, self.log, last))
        return clean_line(out, self.people[idx]["name"])

    def _run(self, eng) -> None:
        try:
            nxt, nxt_pcm, i = None, None, 0
            while self.turn < self.max_turns and not self._stop.is_set():
                s = self._sess(i)
                if s is None or any(self._sess(k) is None for k in range(len(self.people))):
                    self.note = "機器が切れたので会話を終えました"
                    break
                last = self.turn == self.max_turns - 1
                if nxt is None:
                    nxt = self._gen(eng, i, last)
                    if not nxt:
                        self.note = "発言を作れませんでした(LLM)。会話を終えます"
                        break
                text = nxt
                pcm = nxt_pcm if nxt_pcm is not None else eng.speech_pcm_as(text, self.people[i]["voice"])
                nxt, nxt_pcm = None, None
                with self.lock:
                    self.log.append((self.people[i]["name"], text))
                self.turn += 1
                # 話者は声と字幕、ほかの機器は「聞いている」顔と字幕
                for k in range(len(self.people)):
                    sk = self._sess(k)
                    if sk is None:
                        continue
                    if k == i:
                        sk.send({"t": "face", "state": "happy"})
                    else:
                        sk.send({"t": "face", "state": "listening"})
                        sk.send({"t": "caption", "lang": "ja", "to": "ja", "orig": f"{self.people[i]['name']}: {text}", "trans": "", "final": True})
                t0 = time.time()
                dur = s._send_pcm(pcm, caption=text, lang="ja")
                # 再生している間に、次の人の発言を作って声にしておく
                j = (i + 1) % len(self.people)
                if self.turn < self.max_turns and not self._stop.is_set():
                    try:
                        nxt = self._gen(eng, j, self.turn == self.max_turns - 1)
                        if nxt:
                            nxt_pcm = eng.speech_pcm_as(nxt, self.people[j]["voice"])
                    except Exception:  # noqa: BLE001
                        nxt, nxt_pcm = None, None
                rest = t0 + dur + 0.5 - time.time()
                while rest > 0 and not self._stop.is_set():
                    time.sleep(min(0.2, rest))
                    rest = t0 + dur + 0.5 - time.time()
                s.send({"t": "face", "state": "neutral"})
                i = j
        except Exception as e:  # noqa: BLE001
            self.note = f"エラー: {type(e).__name__}"
        finally:
            for k in range(len(self.people)):
                sk = self._sess(k)
                if sk is not None:
                    sk.send({"t": "face", "state": "neutral"})
            self.on = False
            self.hub.event("会話モード終了" + (f"({self.note})" if self.note else ""))

    def snapshot(self) -> dict:
        with self.lock:
            return {"on": self.on, "topic": self.topic, "turn": self.turn, "max": self.max_turns, "note": self.note,
                    "people": [{"name": p["name"], "dev_name": p["dev_name"], "trait": p["trait"]} for p in self.people],
                    "log": [{"who": w, "text": t} for w, t in self.log[-30:]]}

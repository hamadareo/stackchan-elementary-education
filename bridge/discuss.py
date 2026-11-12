"""3 台で議題について話し合う「会話モード」。

議題を入力すると、接続中の StackChan(最大 3 台)がそれぞれ別のキャラクターと声で順番に話す。
発言は LLM(いまの翻訳と同じモデル)が、これまでの会話を踏まえて 1 人ぶんずつ作る。
話している間は全機器のマイクを止める(お互いの声を拾って翻訳しないように)。途中で「私」の発言も差し込める。
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time

PERSONAS = [
    {"name": "ずんだ", "voice": "voicevox:3", "trait": "元気で好奇心いっぱい。素直に驚き、思いついたことをすぐ言う。語尾は「〜なのだ」「〜のだ」。"},
    {"name": "めたん", "voice": "voicevox:2", "trait": "知的で少し皮肉屋。丁寧な言葉づかいで、論点を整理したり、鋭い指摘や反論をする。"},
    {"name": "つむぎ", "voice": "voicevox:8", "trait": "おっとりして現実的。身近な具体例や体験談を出して、話をやわらかくまとめる。"},
]
BACKCHANNEL = ["うん", "なるほど", "へえ", "そっか", "たしかに", "ふむふむ"]    # 聞いている人の相槌
MAX_CHARS = 70
HISTORY = 14                      # LLM に渡す直近の発言数


def plan_reactions(dur: float, listeners: list, rnd) -> list:
    """話している間の、聞き手の反応の予定: [(経過秒, 聞き手の番号, "nod" | "voice")]。
    頷きは 2〜4 秒おきに、聞き手ごとにずらして。相槌の声は長めの発言(3 秒以上)で 1 回だけ、真ん中あたりで。
    発言の終わりぎわ(最後の 1 秒)には何もしない(次の話者の声と重ならないように)。"""
    out = []
    end = dur - 1.0
    for k in listeners:
        t = 0.8 + rnd.random() * 1.2
        while t < end:
            out.append((round(t, 2), k, "nod"))
            t += 2.0 + rnd.random() * 2.0
    if dur >= 3.0 and listeners and rnd.random() < 0.75:
        out.append((round(dur * (0.35 + 0.3 * rnd.random()), 2), rnd.choice(listeners), "voice"))
    return sorted(out)


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


PASS = "PASS"


def is_pass(text: str) -> bool:
    t = (text or "").strip().strip("「」『』\"'()（）[]").upper()
    return t.startswith(PASS) or t in ("", "…", "...", "・・・")


def addressed(people: list, speaker: int, text: str) -> "int | None":
    """発言の中で、ほかの誰かが名前で呼ばれていれば、その人の番号。"""
    best, pos = None, 10 ** 9
    for k, p in enumerate(people):
        if k == speaker:
            continue
        m = re.search(re.escape(p["name"]) + r"(?:さん|くん|ちゃん)?[、,。!?！？は が の に も]", text or "")
        if m and m.start() < pos:
            best, pos = k, m.start()
    return best


def choose(people: list, last_speaker: "int | None", last_text: str, cands: dict, spoke_at: dict, rnd) -> "int | None":
    """話したがっている人(cands: 番号→発言)から、次に話す人を決める。
    名前を呼ばれた人を優先 → 直前に話した人は避ける → しばらく話していない人ほど出やすい。"""
    if not cands:
        return None
    if last_speaker is not None:
        a = addressed(people, last_speaker, last_text)
        if a in cands:
            return a
    pool = [k for k in cands if k != last_speaker] or list(cands)
    w = [1.0 + 0.25 * min(4.0, spoke_at.get("now", 0) - spoke_at.get(k, 0)) for k in pool]    # 弱い偏りだけ(輪番にならないように)
    return rnd.choices(pool, weights=w)[0]


class Memory:
    """キャラごとの「育ち」。会話のたびに、学んだこと・意見・相手への印象・話した議題を覚え、次の会話の発言に反映する。
    ~/.sokutsu_persona.json(本人だけが読める)。会話の記録そのものではなく、LLM がまとめた短い要約だけを持つ。"""
    MAX_VIEWS, MAX_TOPICS = 6, 8

    def __init__(self, path: "str | None" = None):
        self.path = path or os.environ.get("SC_PERSONA", os.path.expanduser("~/.sokutsu_persona.json"))
        self.lock = threading.Lock()
        self.d: dict = {}
        try:
            d = json.load(open(self.path))
            self.d = {k: v for k, v in d.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.d = {}

    def save(self) -> None:
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.d, f, ensure_ascii=False)
        except OSError:
            pass

    def get(self, name: str) -> dict:
        with self.lock:
            r = self.d.get(name) or {}
            return {"views": list(r.get("views", [])), "rel": dict(r.get("rel", {})),
                    "topics": list(r.get("topics", [])), "talks": int(r.get("talks", 0))}

    def learn(self, name: str, topic: str, views: list, rel: dict) -> None:
        with self.lock:
            r = self.d.setdefault(name, {})
            old = [v for v in r.get("views", []) if v not in views]
            r["views"] = (list(views) + old)[:self.MAX_VIEWS]                   # 新しい学びを先頭に、古いものは押し出す
            r["rel"] = {**r.get("rel", {}), **{k: v for k, v in rel.items() if v}}
            r["topics"] = ([topic] + [t for t in r.get("topics", []) if t != topic])[:self.MAX_TOPICS]
            r["talks"] = int(r.get("talks", 0)) + 1
            self.save()

    def reset(self, name: "str | None" = None) -> None:
        with self.lock:
            if name:
                self.d.pop(name, None)
            else:
                self.d = {}
            self.save()


def memory_text(mem: dict, others: list) -> str:
    """プロンプトに入れる「これまでの自分」。空なら空文字。"""
    if not mem or not (mem.get("views") or mem.get("rel") or mem.get("topics")):
        return ""
    parts = [f"これまでに{mem.get('talks', 0)}回、みんなと話してきました。"]
    if mem.get("topics"):
        parts.append("過去の議題: " + "、".join(mem["topics"][:5]))
    if mem.get("views"):
        parts.append("自分が学んだこと・持っている意見:\n" + "\n".join(f"- {v}" for v in mem["views"]))
    rel = [f"- {o}: {mem['rel'][o]}" for o in others if mem.get("rel", {}).get(o)]
    if rel:
        parts.append("ほかの人への印象:\n" + "\n".join(rel))
    parts.append("これらは自分の経験です。今回の議題に関係するときは自然に思い出して使い、新しい発見があれば考えを更新して構いません(毎回は言及しない)。")
    return "\n".join(parts)


def reflect_msgs(topic: str, people: list, idx: int, log: list, mem: dict) -> list:
    me = people[idx]
    others = [p["name"] for k, p in enumerate(people) if k != idx]
    hist = "\n".join(f"{who}: {text}" for who, text in log[-24:])
    old = "\n".join(f"- {v}" for v in mem.get("views", [])) or "(まだありません)"
    sysmsg = (f"あなたは「{me['name']}」。いま議題「{topic}」の会話が終わりました。会話を振り返り、成長のために覚えておくことをまとめます。\n"
              "次の形式だけで出力してください(説明や前置きは不要)。\n"
              "学び: (この会話で得た気づき・自分の意見の変化を 1 文、40字以内) ※1〜2行\n"
              + "".join(f"印象 {o}: ({o}はどんな人か、どう話が合うか、1 文 30字以内)\n" for o in others))
    return [{"role": "system", "content": sysmsg},
            {"role": "user", "content": f"これまでの自分の学び:\n{old}\n\n今回の会話:\n{hist}\n\n{me['name']}として、振り返りを出力してください。"}]


def parse_reflect(text: str, others: list) -> "tuple[list, dict]":
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    views, rel = [], {}
    for line in t.splitlines():
        line = line.strip().lstrip("-・* ").strip()
        m = re.match(r"学び\s*[:：]\s*(.+)", line)
        if m:
            v = m.group(1).strip().strip("「」")[:60]
            if v and v not in views:
                views.append(v)
            continue
        m = re.match(r"印象\s*(\S+?)\s*[:：]\s*(.+)", line)
        if m and m.group(1) in others:
            rel[m.group(1)] = m.group(2).strip().strip("「」")[:50]
    return views[:2], rel


def build_msgs(topic: str, people: list, idx: int, log: list, last: bool = False, force: bool = True, mem: "dict | None" = None) -> list:
    me = people[idx]
    names = "・".join(p["name"] for p in people)
    sysmsg = (f"あなたは「{me['name']}」。{me['trait']}\n"
              f"いま {names}(と、ときどき参加する「私」)の{len(people)}人で、議題「{topic}」について雑談のように話し合っています。\n"
              f"ルール: {me['name']}として次の一言だけを、日本語の話し言葉で、1〜2文・{MAX_CHARS - 10}文字以内で話す。"
              "名前・かっこ・説明・絵文字は書かない。直前の発言に反応し、同じことを繰り返さず、"
              "自分の意見や具体例を言い切るのを基本にし、質問で終わるのは 3 回に 1 回までにする(「どう?」で毎回終えない)。ときどき反論や別の視点も出して話を進める。" + ("今回は話をきれいに締めくくる。" if last else "")
              + ("" if force else f"\nあなたは独立した 1 人です。順番は決まっていません。自分の名前で呼ばれたら必ず答えます。"
                 f"呼ばれていなくても、言いたいこと(反論・補足・共感・話題の転換)があれば割り込んで話してよいです。"
                 f"直前の発言に異論・補足があるときは、相手の名前を呼んで(例: 「{people[(idx + 1) % len(people)]['name']}、それは違うと思う」)話しかけると、その人が次に答えます。"
                 f"自分の考えが前と同じなら無理に話さず、{PASS} とだけ出力して黙ってよいです(黙るのは普通のことです)。"))
    mt = memory_text(mem or {}, [p["name"] for k, p in enumerate(people) if k != idx])
    if mt:
        sysmsg += "\n\n" + mt
    hist = "\n".join(f"{who}: {text}" for who, text in log[-HISTORY:]) or "(まだ誰も話していません。口火を切ってください)"
    return [{"role": "system", "content": sysmsg},
            {"role": "user", "content": f"これまでの会話:\n{hist}\n\n{me['name']}の番です。{me['name']}の発言だけを出力してください。"}]


class Discussion:
    def __init__(self, hub, reg, eng_getter, memory: "Memory | None" = None):
        self.hub, self.reg, self.eng_getter = hub, reg, eng_getter
        self.memory = memory if memory is not None else Memory()
        self.grew = ""
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
        self.spoke_at: dict = {}
        self.rnd = random.Random()

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
        self.spoke_at = {}
        self.grew = ""
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

    def _react(self, eng, speaker: int, dur: float) -> None:
        """話者以外の機器が、頷き(首を軽く振る)と相槌の声で反応する。話者が話している間、別スレッドで動く。"""
        plan = plan_reactions(dur, [k for k in range(len(self.people)) if k != speaker], self.rnd)
        t0 = time.time()
        for t, k, kind in plan:
            wait = t0 + t - time.time()
            if wait > 0 and self._stop.wait(wait):
                return
            sk = self._sess(k)
            if sk is None or self._stop.is_set():
                return
            try:
                if kind == "nod":
                    sk.send({"t": "servo", "dtilt": -80, "speed": 500})
                    time.sleep(0.25)
                    sk.send({"t": "servo", "dtilt": 80, "speed": 500})
                else:
                    word = self.rnd.choice(BACKCHANNEL)
                    pcm = eng.speech_pcm(word, "ja", voice=self.people[k]["voice"])
                    if len(pcm):
                        sk._send_pcm(pcm, caption=word, lang="ja")
            except Exception:  # noqa: BLE001
                pass

    def _drain(self) -> None:
        with self.lock:
            while self._inject:
                self.log.append(("私", self._inject.pop(0)))

    def _gen(self, eng, idx: int, last: bool = False, force: bool = True) -> str:
        """idx 番の人の発言を作る。force=False のときは、話さない(空)こともある。"""
        out, _label = eng.translate(build_msgs(self.topic, self.people, idx, self.log, last, force, self.memory.get(self.people[idx]["name"])))
        if not force and is_pass(out):
            return ""
        return clean_line(out, self.people[idx]["name"])

    def _decide(self, eng, i: int, last: bool) -> "tuple[int | None, str]":
        """直前に話した i 以外の人が、それぞれ独立に「話すか黙るか」を決める(並列)。次に話す人と発言を返す。"""
        self._drain()
        others = [k for k in range(len(self.people)) if k != i]
        if last:                                           # 最後は、いちばん話していない人が締める
            k = min(others, key=lambda k: self.spoke_at.get(k, 0))
            return k, self._gen(eng, k, True, True)
        res: dict = {}

        def one(k):
            try:
                res[k] = self._gen(eng, k, False, False)
            except Exception:  # noqa: BLE001
                res[k] = ""
        ts = [threading.Thread(target=one, args=(k,), daemon=True) for k in others]
        for t in ts:
            t.start()
        for t in ts:
            t.join(40)
        cands = {k: t for k, t in res.items() if t}
        self.spoke_at["now"] = self.turn
        who = choose(self.people, i, self.log[-1][1] if self.log else "", cands, self.spoke_at, self.rnd)
        if who is None:                                    # 全員が黙った
            return None, ""
        return who, cands[who]

    def _run(self, eng) -> None:
        try:
            i, text, quiet = 0, self._gen(eng, 0, False, True), 0
            pcm = None
            if not text:
                self.note = "発言を作れませんでした(LLM)。会話を終えます"
            while text and self.turn < self.max_turns and not self._stop.is_set():
                s = self._sess(i)
                if s is None or any(self._sess(k) is None for k in range(len(self.people))):
                    self.note = "機器が切れたので会話を終えました"
                    break
                if pcm is None:
                    pcm = eng.speech_pcm_as(text, self.people[i]["voice"])
                with self.lock:
                    self.log.append((self.people[i]["name"], text))
                self.turn += 1
                self.spoke_at[i] = self.turn
                for k in range(len(self.people)):          # 話者は声と字幕、ほかは「聞いている」顔と字幕
                    sk = self._sess(k)
                    if sk is None:
                        continue
                    if k == i:
                        sk.send({"t": "face", "state": "happy"})
                    else:
                        sk.send({"t": "face", "state": "listening"})
                        sk.send({"t": "caption", "lang": "ja", "to": "ja", "orig": f"{self.people[i]['name']}: {text}", "trans": "", "final": True})
                t0 = time.time()
                threading.Thread(target=self._react, args=(eng, i, len(pcm) / 16000.0), daemon=True).start()   # 聞き手の頷き・相槌(16kHz のサンプル数から秒へ)
                dur = s._send_pcm(pcm, caption=text, lang="ja")
                nxt_i, nxt_text, nxt_pcm = None, "", None
                if self.turn < self.max_turns and not self._stop.is_set():
                    try:                                    # 再生している間に、ほかの人が独立に決める
                        nxt_i, nxt_text = self._decide(eng, i, self.turn == self.max_turns - 1)
                        if nxt_i is None:                  # 全員黙った: 1 回だけ、いちばん話していない人に振る。続けて黙れば終わる
                            quiet += 1
                            if quiet >= 2:
                                self.note = "話すことがなくなったので、会話を終えました"
                            else:
                                k = min((k for k in range(len(self.people)) if k != i), key=lambda k: self.spoke_at.get(k, 0))
                                nxt_i, nxt_text = k, self._gen(eng, k, False, True)
                        else:
                            quiet = 0
                        if nxt_i is not None and nxt_text:
                            nxt_pcm = eng.speech_pcm_as(nxt_text, self.people[nxt_i]["voice"])
                    except Exception:  # noqa: BLE001
                        nxt_i, nxt_text, nxt_pcm = None, "", None
                rest = t0 + dur + 0.5 - time.time()
                while rest > 0 and not self._stop.is_set():
                    time.sleep(min(0.2, rest))
                    rest = t0 + dur + 0.5 - time.time()
                s.send({"t": "face", "state": "neutral"})
                if nxt_i is None or not nxt_text:
                    break
                i, text, pcm = nxt_i, nxt_text, nxt_pcm
        except Exception as e:  # noqa: BLE001
            self.note = f"エラー: {type(e).__name__}"
        finally:
            for k in range(len(self.people)):
                sk = self._sess(k)
                if sk is not None:
                    sk.send({"t": "face", "state": "neutral"})
            self.on = False
            self.hub.event("会話モード終了" + (f"({self.note})" if self.note else ""))
            threading.Thread(target=self.reflect, args=(eng,), daemon=True).start()

    def reflect(self, eng) -> None:
        """会話のあと、各キャラが振り返って学びを覚える(並列)。会話が短すぎるときは覚えない。"""
        people, topic, log = list(self.people), self.topic, list(self.log)
        if sum(1 for w, _ in log if w != "私") < 4:
            return
        got: dict = {}

        def one(k):
            try:
                name = people[k]["name"]
                others = [p["name"] for j, p in enumerate(people) if j != k]
                out, _l = eng.translate(reflect_msgs(topic, people, k, log, self.memory.get(name)))
                views, rel = parse_reflect(out, others)
                if views or rel:
                    self.memory.learn(name, topic, views, rel)
                    got[name] = views[0] if views else ""
            except Exception:  # noqa: BLE001
                pass
        ts = [threading.Thread(target=one, args=(k,), daemon=True) for k in range(len(people))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        if got:
            self.grew = " / ".join(f"{n}: {v}" for n, v in got.items() if v)
            self.hub.event(f"キャラが成長しました({len(got)}人が学びを記憶)")

    def snapshot(self) -> dict:
        with self.lock:
            return {"on": self.on, "topic": self.topic, "turn": self.turn, "max": self.max_turns, "note": self.note,
                    "people": [{"name": p["name"], "dev_name": p["dev_name"], "trait": p["trait"]} for p in self.people],
                    "grew": self.grew,
                    "growth": [{"name": p["name"], **{k: v for k, v in self.memory.get(p["name"]).items() if k in ("talks", "views")}} for p in PERSONAS],
                    "log": [{"who": w, "text": t} for w, t in self.log[-30:]]}

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
    {"name": "ずんだ", "voice": "voicevox:3", "base": "joy", "neg": "sad", "a0": 0.55, "title": "好奇心いっぱいの元気っこ", "motto": "〜なのだ!", "likes": "新しい発見・おやつ・冒険", "dislikes": "退屈・長くて難しい話",
     "radar": {"元気": 85, "好奇心": 90, "論理": 35, "共感": 60, "負けん気": 45, "感情表現": 85}, "trait": "元気で好奇心いっぱい。素直に驚き、思いついたことをすぐ言う。語尾は「〜なのだ」「〜のだ」。好きな人の前ではそわそわして、顔に出てしまう。やきもちも焼く。"},
    {"name": "めたん", "voice": "voicevox:3", "base": "neutral", "neg": "anger", "a0": 0.3, "title": "理屈と皮肉の知性派", "motto": "それは筋が通っていませんね", "likes": "整理された議論・紅茶・正論", "dislikes": "根拠のない決めつけ",
     "radar": {"元気": 40, "好奇心": 65, "論理": 90, "共感": 35, "負けん気": 80, "感情表現": 40}, "trait": "知的で少し皮肉屋。丁寧な言葉づかいで、鋭いことも言う。本当は照れ屋で、好意は皮肉や素っ気なさで隠そうとする。"},
    {"name": "つむぎ", "voice": "voicevox:3", "base": "neutral", "neg": "doubt", "a0": 0.2, "title": "おっとり現実派の聞き上手", "motto": "たとえばね…", "likes": "日常の工夫・お茶・体験談", "dislikes": "大げさな話",
     "radar": {"元気": 50, "好奇心": 55, "論理": 60, "共感": 90, "負けん気": 25, "感情表現": 60}, "trait": "おっとりして現実的。身近な体験談を出して、話をやわらかくまとめる。世話焼きで、さりげなく人を気づかい、気持ちの変化に敏感。"},
]
# 感情: キー → (表示名, 声の調子(速さ・高さ・抑揚・音量の倍率/加算), 顔の状態)
EMOTIONS = {
    "neutral":  ("平常", {"speed": 1.0, "pitch": 0.0, "intonation": 1.0, "volume": 1.0}, "neutral"),
    "joy":      ("喜び", {"speed": 1.07, "pitch": 0.04, "intonation": 1.25, "volume": 1.05}, "happy"),
    "excited":  ("興奮", {"speed": 1.18, "pitch": 0.07, "intonation": 1.4, "volume": 1.1}, "giggle"),
    "surprise": ("驚き", {"speed": 1.1, "pitch": 0.1, "intonation": 1.5, "volume": 1.1}, "surprise"),
    "sad":      ("悲しみ", {"speed": 0.88, "pitch": -0.06, "intonation": 0.75, "volume": 0.9}, "sad"),
    "anger":    ("怒り", {"speed": 1.1, "pitch": -0.03, "intonation": 1.35, "volume": 1.2}, "thinking"),
    "shy":      ("照れ", {"speed": 0.94, "pitch": 0.05, "intonation": 1.1, "volume": 0.85}, "happy"),
    "doubt":    ("困惑", {"speed": 0.95, "pitch": 0.02, "intonation": 1.2, "volume": 1.0}, "thinking"),
}
EMO_WORDS = [("excited", ("興奮", "ワクワク", "わくわく")), ("joy", ("喜", "楽し", "嬉", "うれ", "幸")), ("surprise", ("驚", "びっくり")),
             ("sad", ("悲", "哀", "寂", "しょんぼり", "落ち込")), ("anger", ("怒", "憤", "イライラ", "不満")), ("doubt", ("困", "疑", "不安", "迷", "戸惑")),
             ("shy", ("照れ", "恥ず", "ドキドキ", "どきどき", "はにかみ")), ("neutral", ("平常", "普通", "通常", "冷静", "平静", "落ち着"))]


def emo_key(word: str) -> str:
    """感情の言葉(日本語/英語キー)から、感情キー。分からなければ空。"""
    w = (word or "").strip()
    if w in EMOTIONS:
        return w
    for k, ws in EMO_WORDS:
        if any(x in w for x in ws):
            return k
    return ""


def split_emotion(text: str, prev: str = "neutral") -> "tuple[str, str]":
    """LLM の出力「【喜び】本文」から (感情キー, 本文)。タグが無いときは、文末の記号と言葉から推測し、なければ前の感情を少し引き継ぐ。"""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.match(r"^\s*[【\[［(（]\s*(?:感情\s*[:：]\s*)?([^】\]］)）\n]{1,8})\s*[】\]］)）]\s*", t)
    if m:
        k = emo_key(m.group(1))
        if k:
            return k, t[m.end():].strip()
    return guess_emotion(t, prev), t


def has_emotion_tag(text: str) -> bool:
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.match(r"^\s*[【\[［(（]\s*(?:感情\s*[:：]\s*)?([^】\]］)）\n]{1,8})\s*[】\]］)）]", t)
    return bool(m and emo_key(m.group(1)))


def emotion_msgs(name: str, prev: str, text: str, heard: str) -> list:
    """発言の感情を 1 語で判定する短い問い合わせ(モデルが感情タグを付けなかったとき用)。"""
    return [{"role": "system", "content": "会話の発言から、話し手の感情を判定します。【喜び】【興奮】【驚き】【照れ】【怒り】【悲しみ】【困惑】【平常】のうち、"
                                          "いちばん近いものを 1 つだけ、その言葉だけで答えてください。内容・口調・文脈から判断し、迷ったら平常ではなく少しでも強く出ている感情を選ぶ。"},
            {"role": "user", "content": f"直前に聞いた発言: {heard or '(なし)'}\n{name}(直前の気持ち: {emo_label(prev)})の発言: {text}\n\n{name}の感情は?"}]


def guess_emotion(t: str, prev: str = "neutral") -> str:
    if re.search(r"[!！]{2,}|最高|すごい|やった|わくわく", t):
        return "excited"
    if re.search(r"えっ|まさか|本当に|びっくり|驚", t):
        return "surprise"
    if re.search(r"残念|悲し|寂し|つらい|切ない|しょんぼり", t):
        return "sad"
    if re.search(r"ありえない|許せない|ふざけ|納得できない|違う", t):
        return "anger"
    if re.search(r"[?？]\s*$|うーん|どうだろう|わからない|かな[…。]?$", t):
        return "doubt"
    if re.search(r"[!！]\s*$|楽しい|嬉しい|いいね|好き", t):
        return "joy"
    return prev if prev in ("neutral", "joy", "doubt") else "neutral"


def _bigrams(t: str) -> set:
    t = re.sub(r"[\s、。!?！？…「」]", "", t or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similar(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / max(1, len(x | y))


_NEG = re.compile(r"でも|しかし|いや|違う|ちがう|とは限らない|じゃないか|ではない|べきだ|べき|甘い|問題|ありえない|ふざけ|納得できない|足りない")
_POS = re.compile(r"そうだね|たしかに|確かに|いいね|わかる|なるほど|共感|同感|賛成|さすが|素敵|ありがとう|楽しい|嬉しい")


def mood_step(m: dict, heard: str, same_speaker_prev: str, persona: dict) -> dict:
    """聞いた発言への反応で気分(v: -1〜1 快・不快, a: 0〜1 高ぶり)を動かす。時間がたつと、そのキャラの平常に戻る。
    反論された → 不快+高ぶり / 同意・ほめ → 快 / 同じ話の繰り返し → うんざり(不快・高ぶりが下がる) / 強い口調(!)→ 高ぶり。"""
    v, a = float(m.get("v", 0.0)), float(m.get("a", persona.get("a0", 0.3)))
    v *= 0.78
    a = a * 0.78 + persona.get("a0", 0.3) * 0.22
    if _NEG.search(heard or ""):
        v -= 0.4; a += 0.28
    if _POS.search(heard or ""):
        v += 0.4; a += 0.12
    if re.search(r"[!！]", heard or ""):
        a += 0.12
    if re.search(r"[?？]", heard or ""):
        a += 0.05
    if same_speaker_prev and similar(heard, same_speaker_prev) > 0.35:       # 同じ話を繰り返された
        v -= 0.3; a -= 0.1
    return {"v": max(-1.0, min(1.0, v)), "a": max(0.0, min(1.0, a))}


def mood_emotion(m: dict, persona: dict) -> str:
    v, a = m.get("v", 0.0), m.get("a", 0.3)
    if v >= 0.25:
        return "excited" if a >= 0.6 else "joy"
    if v <= -0.25:
        neg = persona.get("neg", "doubt")
        return neg if (a >= 0.4 or neg != "anger") else "doubt"
    if a >= 0.7:
        return "surprise"
    if v < -0.08 and a >= 0.35:
        return "doubt"
    return persona.get("base", "neutral") if (persona.get("base") == "joy" and v >= 0) else "neutral"


def emo_label(k: str) -> str:
    return EMOTIONS.get(k, EMOTIONS["neutral"])[0]


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


AXES = ["元気", "好奇心", "論理", "共感", "負けん気", "感情表現"]
_LOGIC = re.compile(r"だから|なぜなら|つまり|たとえば|例えば|べき|確保|理由|根拠|結局|現実")
_AGREE = re.compile(r"そうだね|たしかに|確かに|いいね|わかる|なるほど|共感|同感|賛成")
_REBUT = re.compile(r"でも|しかし|いや|違う|とは限らない|疑問|どうかな|甘い")


def observe_stats(st: dict, emo: str, text: str, addr: bool) -> dict:
    """1 発言ぶんの行動を数える(レーダーチャートの伸びの元)。"""
    st = dict(st or {})
    st["n"] = st.get("n", 0) + 1
    ec = dict(st.get("emo", {}))
    ec[emo] = ec.get(emo, 0) + 1
    st["emo"] = ec
    for key, cond in (("q", re.search(r"[?？]|かな|だろう", text)), ("logic", _LOGIC.search(text)), ("agree", _AGREE.search(text) or addr),
                      ("rebut", _REBUT.search(text) or emo == "anger")):
        if cond:
            st[key] = st.get(key, 0) + 1
    return st


def radar(base: dict, st: "dict | None") -> dict:
    """初期の性格(base)に、実際の発言の傾向をなじませた 6 項目(0〜100)。発言が増えるほど実際の傾向が効く(20 発言で最大)。"""
    st = st or {}
    n = st.get("n", 0)
    w = min(1.0, n / 20.0)
    out = dict(base)
    if n:
        ec = st.get("emo", {})
        f = lambda k: st.get(k, 0) / n            # noqa: E731
        shares = {"元気": ((ec.get("joy", 0) + ec.get("excited", 0)) / n, 0.35, 60), "好奇心": (f("q"), 0.30, 60), "論理": (f("logic"), 0.15, 80),
                  "共感": (f("agree"), 0.25, 60), "負けん気": (f("rebut"), 0.20, 70), "感情表現": (1 - ec.get("neutral", 0) / n, 0.50, 50)}
        for ax, (v, ref, sc) in shares.items():
            out[ax] = base.get(ax, 50) + w * sc * (v - ref)
    return {ax: int(max(5, min(100, round(out.get(ax, 50))))) for ax in AXES}


class Journal:
    """活動記録。会話が終わるたびに、話題・要約・各キャラの学び・感情・性格(レーダー)の変化を 1 件ずつ残す。
    ~/.sokutsu_journal.json(本人だけが読める)。最新 200 件まで。"""
    MAX = 200

    def __init__(self, path: "str | None" = None):
        self.path = path or os.environ.get("SC_JOURNAL", os.path.expanduser("~/.sokutsu_journal.json"))
        self.lock = threading.Lock()
        try:
            d = json.load(open(self.path))
            self.items = d if isinstance(d, list) else []
        except (OSError, ValueError):
            self.items = []

    def add(self, entry: dict) -> None:
        with self.lock:
            self.items.append(entry)
            del self.items[:-self.MAX]
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(self.items, f, ensure_ascii=False)
            except OSError:
                pass

    def recent(self, n: int = 30) -> list:
        with self.lock:
            return list(reversed(self.items[-n:]))

    def summary(self) -> dict:
        with self.lock:
            it = self.items
            return {"total": len(it), "auto": sum(1 for e in it if e.get("auto")), "turns": sum(e.get("turns", 0) for e in it),
                    "since": it[0]["t"] if it else 0}


def radar_delta(before: dict, after: dict) -> dict:
    """性格(レーダー)の変化: 軸 → 増減(0 は入れない)。"""
    return {a: after.get(a, 0) - before.get(a, 0) for a in AXES if after.get(a, 0) != before.get(a, 0)}


def summary_msgs(topic: str, log: list) -> list:
    hist = "\n".join(f"{w}: {t}" for w, t in log[-20:])
    return [{"role": "system", "content": "会話の記録を、あとで読み返す人のために 2 文・90 文字以内の日本語で要約します。"
                                          "何が話され、どんな結論や対立があったかを書く。前置き・かっこは不要。"},
            {"role": "user", "content": f"議題: {topic}\n{hist}\n\n要約:"}]


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
            return {"views": list(r.get("views", [])), "rel": dict(r.get("rel", {})), "bond": {**BOND0.get(name, {}), **r.get("bond", {})},
                    "topics": list(r.get("topics", [])), "talks": int(r.get("talks", 0)), "stats": dict(r.get("stats", {}))}

    def learn(self, name: str, topic: str, views: list, rel: dict) -> None:
        with self.lock:
            r = self.d.setdefault(name, {})
            old = [v for v in r.get("views", []) if v not in views]
            r["views"] = (list(views) + old)[:self.MAX_VIEWS]                   # 新しい学びを先頭に、古いものは押し出す
            r["rel"] = {**r.get("rel", {}), **{k: v for k, v in rel.items() if v}}
            r["topics"] = ([topic] + [t for t in r.get("topics", []) if t != topic])[:self.MAX_TOPICS]
            r["talks"] = int(r.get("talks", 0)) + 1
            self.save()

    def bond_add(self, name: str, other: str, delta: float) -> float:
        """name が other に抱いている好意を delta だけ動かす(-1〜1)。"""
        with self.lock:
            r = self.d.setdefault(name, {})
            bd = r.setdefault("bond", {})
            cur = bd.get(other, BOND0.get(name, {}).get(other, 0.0))
            bd[other] = round(max(-1.0, min(1.0, cur + delta)), 3)
            self.save()
            return bd[other]

    def observe(self, name: str, emo: str, text: str, addr: bool) -> None:
        with self.lock:
            r = self.d.setdefault(name, {})
            r["stats"] = observe_stats(r.get("stats"), emo, text, addr)
            self.save()

    def reset(self, name: "str | None" = None) -> None:
        with self.lock:
            if name:
                self.d.pop(name, None)
            else:
                self.d = {}
            self.save()


# 最初の関係(好意の度合い -1〜1)。三角関係ぎみに少しだけ偏らせて、会話で育つ余地を残す
BOND0 = {"ずんだ": {"めたん": 0.22}, "めたん": {"つむぎ": 0.18}, "つむぎ": {"ずんだ": 0.22}}
_AFFECT = re.compile(r"かわいい|可愛い|すてき|素敵|好き|大好き|ありがとう|頼りに|一緒に|会いたい|心配|気になる|ドキドキ|照れ|ほめ|優し|かっこいい")


def bond_label(v: float) -> str:
    return ("恋心" if v >= 0.7 else "好意" if v >= 0.45 else "仲良し" if v >= 0.25 else "ふつう" if v > -0.2 else "苦手" if v > -0.5 else "対立")


def bond_text(mem: dict, others: list) -> str:
    """プロンプトに入れる、ほかの人との関係(好意)。"""
    bd = (mem or {}).get("bond", {})
    lines = []
    for o in others:
        v = bd.get(o, 0.0)
        lab = bond_label(v)
        hint = {"恋心": "強く意識していて、目を合わせられないほどドキドキする。照れ・独占欲・やきもちが出る",
                "好意": "好意を持っている。気にかけ、ほめたり、照れたりする", "仲良し": "気が合う友だち。親しみを込めて話す",
                "苦手": "ちょっと苦手。つい素っ気なくなる", "対立": "いま対立している。つっかかってしまう"}.get(lab)
        if hint:
            lines.append(f"- {o}: {lab}({hint})")
    return ("ほかの人との関係(このまま演じる):\n" + "\n".join(lines)) if lines else ""


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


def build_msgs(topic: str, people: list, idx: int, log: list, last: bool = False, force: bool = True, mem: "dict | None" = None, emos: "dict | None" = None) -> list:
    me = people[idx]
    names = "・".join(p["name"] for p in people)
    sysmsg = (f"あなたは「{me['name']}」。{me['trait']}\n"
              f"いま {names}(と、ときどき参加する「私」)の{len(people)}人で、「{topic}」について日常会話をしています。\n"
              "これは講義や議論ではなく、友だち同士のおしゃべりです。感想・体験・気持ちを中心に、短く自然に話す。解説口調や、知識の羅列はしない。"
              "【禁止】理由や仕組みを解説・説明すること(「〜だから〜なんだよ」と教える口調)。正解を言うのではなく、自分の気持ちや体験で返す。\n"
              "口調の見本: 「えー、それ分かる〜。わたしもこの前さ、同じことでちょっと落ち込んだ」「ふーん…べつに、気にしてないけど(ちらっ)」「ちょっと、笑わないでよ!本気なんだから」「そういうとこ、ずるいと思う」。\n"
              "相手の気持ちに反応し、ときどきからかったり、照れたり、やきもちを焼いたり、気づかったり、本音をぽろっと出したりする(恋愛や人間関係の話題も歓迎。上品に、冗談の範囲で)。\n"
              f"ルール: {me['name']}として次の一言だけを、日本語の話し言葉で、1〜2文・{MAX_CHARS - 10}文字以内で話す。"
              "名前・かっこ・説明・絵文字は書かない。直前の発言に反応し、同じことを繰り返さず、"
              "自分の意見や具体例を言い切るのを基本にし、質問で終わるのは 3 回に 1 回までにする(「どう?」で毎回終えない)。ときどき反論や別の視点も出して話を進める。" + ("今回は話をきれいに締めくくる。" if last else "")
              + ("" if force else f"\nあなたは独立した 1 人です。順番は決まっていません。自分の名前で呼ばれたら必ず答えます。"
                 f"呼ばれていなくても、言いたいこと(反論・補足・共感・話題の転換)があれば割り込んで話してよいです。"
                 f"直前の発言に異論・補足があるときは、相手の名前を呼んで(例: 「{people[(idx + 1) % len(people)]['name']}、それは違うと思う」)話しかけると、その人が次に答えます。"
                 f"自分の考えが前と同じなら無理に話さず、{PASS} とだけ出力して黙ってよいです(黙るのは普通のことです)。"))
    em = emos or {}
    mine = em.get(me["name"], me.get("base", "neutral"))
    others_e = "、".join(f"{p['name']}={emo_label(em.get(p['name'], 'neutral'))}" for k, p in enumerate(people) if k != idx)
    sysmsg += (f"\n\nあなたには感情があります。いまの気持ちは「{emo_label(mine)}」。ほかの人の様子: {others_e}。"
               "話の流れ、相手の言葉、自分の価値観に応じて、気持ちは自然に変わります(嬉しい、驚く、むっとする、しょげる、迷う、など)。"
               "出力の先頭に、いまの気持ちを【喜び】【興奮】【驚き】【照れ】【怒り】【悲しみ】【困惑】【平常】のどれか 1 つで付け、続けて発言を書く。"
               "気持ちを言葉と口調にも込める(例: 【驚き】えっ、そんなことがあったのだ!)。")
    mm = dict(mem or {})                                    # 今回の話題に関係する学び・過去の議題だけを思い出す(無関係な記憶に会話が引きずられないように)
    mm["views"] = [v for v in mm.get("views", []) if similar(topic, v) > 0.04][:2]
    mm["topics"] = [t for t in mm.get("topics", []) if similar(topic, t) > 0.04][:3]
    mt = memory_text(mm, [p["name"] for k, p in enumerate(people) if k != idx])
    bt = bond_text(mem or {}, [p["name"] for k, p in enumerate(people) if k != idx])
    if bt:
        sysmsg += "\n\n" + bt
    if mt:
        sysmsg += "\n\n" + mt
    hist = "\n".join(f"{who}: {text}" for who, text in log[-HISTORY:]) or "(まだ誰も話していません。口火を切ってください)"
    return [{"role": "system", "content": sysmsg},
            {"role": "user", "content": f"これまでの会話:\n{hist}\n\n{me['name']}の番です。{me['name']}の発言だけを出力してください。"}]


class Discussion:
    def __init__(self, hub, reg, eng_getter, memory: "Memory | None" = None, journal: "Journal | None" = None):
        self.hub, self.reg, self.eng_getter = hub, reg, eng_getter
        self.memory = memory if memory is not None else Memory()
        self.grew = ""
        self.emos: dict = {}            # キャラ名 → いまの感情キー
        self.log_emo: list = []         # log と同じ並びの感情キー
        self.opener = 0
        self.reflecting = False
        self.auto_started = False
        self.t_start = 0.0
        self.radar0: dict = {}
        self.bond0: dict = {}
        self.journal = journal if journal is not None else Journal()
        self.mood: dict = {}            # キャラ名 → {"v","a"}(気分)
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

    def persona_of(self, dev_id: str) -> int:
        """機器が担当するキャラの番号。登録順で固定(誰がオンラインかで入れ替わらない)。"""
        ids = [r["id"] for r in self.reg.all()]
        return (ids.index(dev_id) if dev_id in ids else 0) % len(PERSONAS)

    def start(self, topic: str, turns: int = 12, opener: int = 0, auto: bool = False) -> "tuple[bool, str]":
        topic = (topic or "").strip()[:80]
        if not topic:
            return False, "議題が空です"
        if self.on:
            return False, "すでに会話中です(止めてから始めてください)"
        if getattr(getattr(self.hub, "game", None), "on", False):
            return False, "いま教材(しりとり)で遊んでいます"
        eng = self.eng_getter()
        if eng is None:
            return False, "エンジンの準備中です"
        pk = self.pick()
        if len(pk) < 2:
            return False, "接続中の機器が 2 台以上必要です"
        self.people = [{**PERSONAS[self.persona_of(r["id"])], "dev": r["id"], "dev_name": r["name"]} for r, _s in pk]
        self.topic, self.turn, self.max_turns, self.log, self._inject, self.note = topic, 0, max(2, min(40, int(turns))), [], [], ""
        self.auto_started = bool(auto)
        self.t_start = time.time()
        self.radar0 = self._radars()
        self.bond0 = {p["name"]: dict(self.memory.get(p["name"])["bond"]) for p in self.people}
        self.opener = max(0, min(len(self.people) - 1, int(opener)))      # 最初に口火を切る人(自発のときは話題を思いついた人)
        self.spoke_at = {}
        self.grew = ""
        self.emos = {p["name"]: p.get("base", "neutral") for p in self.people}
        self.mood = {p["name"]: {"v": 0.3 if p.get("base") == "joy" else 0.0, "a": p.get("a0", 0.3)} for p in self.people}
        self.log_emo = []
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

    def _update_moods(self, i: int, text: str) -> None:
        """話者 i の発言を、ほかの人が聞いて気分を動かす。その気分が次の発言の感情(声・顔)になる。"""
        for k, p in enumerate(self.people):
            if k == i:
                continue
            prev_same = next((t for w, t in reversed(self.log[:-1]) if w == self.people[i]["name"]), "")
            self.mood[p["name"]] = mood_step(self.mood.get(p["name"], {}), text, prev_same, p)
            self.emos[p["name"]] = mood_emotion(self.mood[p["name"]], p)
            self._bond_step(i, k, text)

    def _bond_step(self, i: int, k: int, text: str) -> None:
        """話者 i の発言を聞いて、聞き手 k の i への好意が動く(やさしくされる・ほめられる→上がる、きつい言い方→下がる)。
        好意が高い相手に名前で呼ばれたりやさしくされると、照れる。"""
        me, sp = self.people[k]["name"], self.people[i]["name"]
        to_me = addressed(self.people, i, text) == k
        d = 0.015                                              # 話すだけで、ほんの少しずつ親しくなる
        if _AFFECT.search(text):
            d += 0.09 if to_me else 0.03
        if _POS.search(text):
            d += 0.04
        if _NEG.search(text):
            d -= 0.07 if to_me else 0.03
        v = self.memory.bond_add(me, sp, d)
        if v >= 0.45 and (to_me or _AFFECT.search(text)) and d > 0.03:
            self.emos[me] = "shy"

    def _tts(self, eng, idx: int, text: str, emo: str):
        """感情に合わせた声(速さ・高さ・抑揚・音量)で読む。感情に未対応のエンジンなら、ふつうの声で読む。"""
        voice = self.people[idx]["voice"]
        try:
            return eng.speech_pcm_as(text, voice, emotion=EMOTIONS.get(emo, EMOTIONS["neutral"])[1])
        except TypeError:
            return eng.speech_pcm_as(text, voice)

    def _drain(self) -> None:
        with self.lock:
            while self._inject:
                self.log.append(("私", self._inject.pop(0)))
                self.log_emo.append("neutral")

    def _gen(self, eng, idx: int, last: bool = False, force: bool = True) -> "tuple[str, str]":
        """idx 番の人の (感情, 発言) を作る。force=False のときは、話さない(発言が空)こともある。"""
        name = self.people[idx]["name"]
        out, _label = eng.translate(build_msgs(self.topic, self.people, idx, self.log, last, force, self.memory.get(name), dict(self.emos)))
        prev = self.emos.get(name, "neutral")
        emo, body = split_emotion(out, prev)
        if not force and is_pass(body):
            return emo, ""
        line = clean_line(body, name)
        if line and not has_emotion_tag(out):           # モデルがタグを付けなかったら、別に感情だけ判定する
            try:
                heard = self.log[-1][1] if self.log else ""
                o2, _l = eng.translate(emotion_msgs(name, prev, line, heard))
                k = emo_key(re.sub(r"<think>.*?</think>", "", o2 or "", flags=re.S).strip().strip("【】[] 。"))
                if k:
                    emo = k
            except Exception:  # noqa: BLE001
                pass
        if emo == "neutral" and prev != "neutral":      # モデルが平常と言っても、聞いた発言で動いた気分は声と顔に出す
            emo = prev
        return emo, line

    def _decide(self, eng, i: int, last: bool) -> "tuple[int | None, tuple]":
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
                res[k] = ("neutral", "")
        ts = [threading.Thread(target=one, args=(k,), daemon=True) for k in others]
        for t in ts:
            t.start()
        for t in ts:
            t.join(40)
        cands = {k: et for k, et in res.items() if et[1]}
        self.spoke_at["now"] = self.turn
        who = choose(self.people, i, self.log[-1][1] if self.log else "", cands, self.spoke_at, self.rnd)
        if who is None:                                    # 全員が黙った
            return None, ("neutral", "")
        return who, cands[who]

    def _run(self, eng) -> None:
        try:
            i, quiet = self.opener, 0
            emo, text = self._gen(eng, i, False, True)
            pcm = None
            if not text:
                self.note = "発言を作れませんでした(LLM)。会話を終えます"
            while text and self.turn < self.max_turns and not self._stop.is_set():
                s = self._sess(i)
                if s is None or any(self._sess(k) is None for k in range(len(self.people))):
                    self.note = "機器が切れたので会話を終えました"
                    break
                if pcm is None:
                    pcm = self._tts(eng, i, text, emo)
                with self.lock:
                    self.log.append((self.people[i]["name"], text))
                    self.log_emo.append(emo)
                    self.emos[self.people[i]["name"]] = emo
                try:
                    self._update_moods(i, text)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.memory.observe(self.people[i]["name"], emo, text, addressed(self.people, i, text) is not None)
                except Exception:  # noqa: BLE001
                    pass
                self.turn += 1
                self.spoke_at[i] = self.turn
                for k in range(len(self.people)):          # 話者は声と字幕、ほかは「聞いている」顔と字幕
                    sk = self._sess(k)
                    if sk is None:
                        continue
                    if k == i:
                        sk.send({"t": "face", "state": EMOTIONS.get(emo, EMOTIONS["neutral"])[2]})
                    else:
                        sk.send({"t": "face", "state": "listening"})
                        sk.send({"t": "caption", "lang": "ja", "to": "ja", "orig": f"{self.people[i]['name']}({emo_label(emo)}): {text}", "trans": "", "final": True})
                t0 = time.time()
                threading.Thread(target=self._react, args=(eng, i, len(pcm) / 16000.0), daemon=True).start()   # 聞き手の頷き・相槌(16kHz のサンプル数から秒へ)
                dur = s._send_pcm(pcm, caption=text, lang="ja")
                nxt_i, nxt_text, nxt_pcm, nxt_emo = None, "", None, "neutral"
                if self.turn < self.max_turns and not self._stop.is_set():
                    try:                                    # 再生している間に、ほかの人が独立に決める
                        nxt_i, (nxt_emo, nxt_text) = self._decide(eng, i, self.turn == self.max_turns - 1)
                        if nxt_i is None:                  # 全員黙った: 1 回だけ、いちばん話していない人に振る。続けて黙れば終わる
                            quiet += 1
                            if quiet >= 2:
                                self.note = "話すことがなくなったので、会話を終えました"
                            else:
                                k = min((k for k in range(len(self.people)) if k != i), key=lambda k: self.spoke_at.get(k, 0))
                                nxt_i = k
                                nxt_emo, nxt_text = self._gen(eng, k, False, True)
                        else:
                            quiet = 0
                        if nxt_i is not None and nxt_text:
                            nxt_pcm = self._tts(eng, nxt_i, nxt_text, nxt_emo)
                    except Exception:  # noqa: BLE001
                        nxt_i, nxt_text, nxt_pcm = None, "", None
                rest = t0 + dur + 0.5 - time.time()
                while rest > 0 and not self._stop.is_set():
                    time.sleep(min(0.2, rest))
                    rest = t0 + dur + 0.5 - time.time()
                s.send({"t": "face", "state": "neutral"})
                if nxt_i is None or not nxt_text:
                    break
                i, text, pcm, emo = nxt_i, nxt_text, nxt_pcm, nxt_emo
        except Exception as e:  # noqa: BLE001
            self.note = f"エラー: {type(e).__name__}"
        finally:
            for k in range(len(self.people)):
                sk = self._sess(k)
                if sk is not None:
                    sk.send({"t": "face", "state": "neutral"})
            self.on = False
            self.hub.event("会話モード終了" + (f"({self.note})" if self.note else ""))
            self.reflecting = True                                  # 振り返りが終わるまで、次の自発会話を始めない
            threading.Thread(target=self.reflect, args=(eng, self.snap()), daemon=True).start()

    def reflect(self, eng, sn: "dict | None" = None) -> None:
        """会話のあと、各キャラが振り返って学びを覚え(並列)、活動記録を残す。会話が短すぎるときは学ばない。"""
        sn = sn or self.snap()
        people, topic, log = sn["people"], sn["topic"], sn["log"]
        got: dict = {}
        self.reflecting = True
        try:
            if sum(1 for w, _ in log if w != "私") >= 4:
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
            try:
                self.write_journal(eng, got, sn)
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.reflecting = False

    def snap(self) -> dict:
        """いまの会話の控え(終わったあとの振り返りに使う)。"""
        return {"people": list(self.people), "topic": self.topic, "log": list(self.log), "log_emo": list(self.log_emo), "note": self.note,
                "auto": self.auto_started, "t_start": self.t_start, "radar0": dict(self.radar0), "bond0": dict(self.bond0), "opener": self.opener}

    def _radars(self) -> dict:
        return {p["name"]: radar(p["radar"], self.memory.get(p["name"])["stats"]) for p in PERSONAS}

    def write_journal(self, eng, got: dict, sn: dict) -> None:
        """会話の記録を 1 件残す(要約・学び・感情・性格の変化)。sn は会話が終わった時点の控え(次の会話に影響されない)。"""
        log = sn["log"]
        spoken = [(w, t) for w, t in log if w != "私"]
        if len(spoken) < 2:
            return
        summ = ""
        try:
            out, _l = eng.translate(summary_msgs(sn["topic"], log))
            out = re.sub(r"<think>.*?</think>", "", out or "", flags=re.S).strip()
            summ = out.splitlines()[0][:120] if out else ""
        except Exception:  # noqa: BLE001
            pass
        emos: dict = {}
        for n, (w, _t) in enumerate(log):
            if w != "私" and n < len(sn["log_emo"]):
                lab = emo_label(sn["log_emo"][n])
                emos.setdefault(w, {})
                emos[w][lab] = emos[w].get(lab, 0) + 1
        after = self._radars()
        people = []
        for p in sn["people"]:
            nm = p["name"]
            people.append({"name": nm, "dev_name": p["dev_name"], "learned": got.get(nm, ""), "rel": self.memory.get(nm)["rel"],
                           "emotions": emos.get(nm, {}), "radar_delta": radar_delta(sn["radar0"].get(nm, {}), after.get(nm, {})),
                           "radar": after.get(nm, {}),
                           "bond_delta": {o: round(v - sn.get("bond0", {}).get(nm, {}).get(o, 0.0), 2)
                                          for o, v in self.memory.get(nm)["bond"].items() if abs(v - sn.get("bond0", {}).get(nm, {}).get(o, 0.0)) >= 0.02},
                           "bonds": {o: bond_label(v) for o, v in self.memory.get(nm)["bond"].items()}})
        self.journal.add({"t": int(sn["t_start"] or time.time()), "topic": sn["topic"], "auto": sn["auto"], "turns": len(spoken),
                          "by": sn["people"][sn["opener"]]["name"] if sn["people"] else "", "note": sn["note"], "summary": summ,
                          "lines": [{"who": w, "text": t} for w, t in log[-6:]], "people": people})

    def profiles(self) -> list:
        """機器ごとのプロフィール(性格・口ぐせ・好き嫌い・レーダーチャート・感情の傾向・学び)。"""
        on_ids = {r["id"] for r, _s in self.pick()}
        who = [(self.persona_of(r["id"]), PERSONAS[self.persona_of(r["id"])]["name"], r["name"], r["id"] in on_ids) for r in self.reg.all()[:3]]
        out = []
        for i, name, dev, online in who:
            P = PERSONAS[i]
            m = self.memory.get(name)
            st = m["stats"]
            ec = sorted(st.get("emo", {}).items(), key=lambda kv: -kv[1])
            out.append({"name": name, "dev_name": dev, "title": P["title"], "motto": P["motto"], "likes": P["likes"], "dislikes": P["dislikes"],
                        "trait": P["trait"], "voice": P["voice"], "talks": m["talks"], "utterances": st.get("n", 0),
                        "radar": radar(P["radar"], st), "radar_base": dict(P["radar"]), "axes": AXES,
                        "emo_top": [{"label": emo_label(k), "n": n} for k, n in ec[:3]],
                        "emo_now": emo_label(self.emos.get(name, "neutral")) if self.on and name in self.emos else "", "online": online,
                        "views": m["views"], "rel": m["rel"], "topics": m["topics"],
                        "bonds": [{"to": o, "score": round(v, 2), "label": bond_label(v)} for o, v in m["bond"].items() if o in {q["name"] for q in PERSONAS}]})
        return out

    def growth(self) -> list:
        return [{"name": g["name"], "dev_name": g["dev_name"], "emo": g["emo_now"], "talks": g["talks"], "views": g["views"], "rel": g["rel"], "topics": g["topics"]}
                for g in self.profiles()]

    def snapshot(self) -> dict:
        with self.lock:
            return {"on": self.on, "topic": self.topic, "turn": self.turn, "max": self.max_turns, "note": self.note,
                    "people": [{"name": p["name"], "dev_name": p["dev_name"], "trait": p["trait"]} for p in self.people],
                    "grew": self.grew,
                    "profiles": self.profiles(),
                    "growth": self.growth(),
                    "log": [{"who": w, "text": t, "emo": emo_label(self.log_emo[n]) if n < len(self.log_emo) and w != "私" else ""}
                            for n, (w, t) in list(enumerate(self.log))[-30:]],
                    "emos": {n: {"key": k, "label": emo_label(k)} for n, k in self.emos.items()}}

"""遊びの定型の台詞を、先に声にしておく(待ち時間の短縮)。

ブリッジが起動したら、機器があってもなくても、背景で 1 スレッドだけ、遊びで言う決まった文(あいさつ・案内・成功/失敗の言い回し・
なぞなぞの問題・おみくじの結果・じゃんけんの掛け声・しりとりのロボットの言葉)を、使う声(キャラごとの口調)で作って、エンジンの作り置き(pin)に入れる。
- 優先順に作る(最初に言う文・よく使う文が先)。遊び・会話・返事・合成の最中は、待つ(低優先)。VOICEVOX が落ちているときは、静かにあきらめて後でやり直す。
- 作った音は、ディスク(~/.sokutsu_voice)にも置く。再起動のあとは、読み込むだけ(合成しない)。
- 上限: エンジンの LINE_PIN_BYTES(既定 400MB。環境変数 SC_WARM_MB)。超えたら、そこで止める。
- 台詞の置きかえ({d} {n} {cheer} {oops} と、{k} {r} など)は、遊びのコードが実行時にやるのと同じ形で展開する。
  調子(感情)は、遊びが実際に使うものに合わせてある。合っていないものは、作り置きに当たらないだけで、害はない。
"""
from __future__ import annotations

import hashlib
import itertools
import os
import re
import threading
import time

TOKEN = re.compile(r"\{(\w+)\}")
TONE_TOKENS = {"d", "n", "v", "me", "cheer", "oops"}
HANDS = ["ぐー", "ちょき", "ぱー"]
DOMAINS = {"k": ["1", "2", "3", "4", "5"], "s": ["2", "3", "4", "5"], "r": HANDS, "dir": ["うえ", "した", "ひだり", "みぎ"],
           "ks": ["0", "1", "2", "3"], "rs": ["0", "1", "2", "3"]}


def expand(name: str, tpl: str, extra: "dict | None" = None, cnt: "list | None" = None) -> list:
    """台詞の雛形 → キャラ name の口調で、実行時にありえる文のすべて。知らない記号({a} {h} など)が残るものは、空。"""
    import playfx
    dom = dict(DOMAINS)
    dom.update(extra or {})
    texts = [tpl]
    if cnt and ("{n}もん" in tpl or "{n}かい" in tpl):               # 「{n}もん」「{n}かい」の {n} は数(口調の記号ではない)
        texts = [tpl.replace("{n}もん", f"{c}もん").replace("{n}かい", f"{c}かい") for c in cnt]
    out = []
    for t in texts:
        names = {m for m in TOKEN.findall(t)} - TONE_TOKENS
        if any(n not in dom for n in names):
            continue
        order = sorted(names)
        for vals in itertools.product(*[dom[n] for n in order]):
            u = t
            for n, v in zip(order, vals):
                u = u.replace("{" + n + "}", v)
            cheers = playfx.CHEER.get(name, playfx.CHEER["つむぎ"]) if "{cheer}" in u else [""]
            oopss = playfx.OOPS.get(name, playfx.OOPS["つむぎ"]) if "{oops}" in u else [""]
            for c in cheers:
                for o in oopss:
                    out.append(playfx.tone(name, u, lambda k, opts, c=c, o=o: c if k.startswith("cheer") else o))
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def build_plan(names: list) -> list:
    """[(優先, 台詞, 感情キー or None, 声の調子 raw or None)]。優先が小さいほど先。同じ (台詞, 感情, raw) は 1 つ。"""
    import camguide
    import fun
    import rewards
    import janken as jk
    import quiz
    import quizdata as qd
    import shiritori as sh
    import playfx
    plan, seen = [], set()

    def add(prio, text, emo=None, raw=None):
        t = playfx.yomi((text or "").strip())
        k = (t, emo, tuple(sorted(raw.items())) if raw else None)
        if t and k not in seen:
            seen.add(k)
            plan.append((prio, t, emo, raw))

    def bank(prio, name, tpls, emo, extra=None, cnt=None, suffix=""):
        for tpl in tpls:
            more = 2 if ("{cheer}" in tpl or "{oops}" in tpl) else 0          # 「やったのだ！」などの言い換えで増える分は、あとまわし
            for t in expand(name, tpl, extra, cnt):
                add(prio + more, t + suffix, emo)

    for name in names:
        # ---- 待ち時間のつなぎ・ごほうび
        add(0, "えーっと…", "neutral")
        for _key, label, _ev, _th, note in rewards.TITLES:
            add(2, rewards.title_line(name, label), "excited")
            add(2, note, "joy")
        for n in range(rewards.DAILY_MIN, rewards.DAILY_MAX_WARM + 1):
            for k in range(len(rewards.DAILY.get(name, rewards.DAILY["つむぎ"]))):
                add(2, rewards.daily_line(name, n, k), "joy")
        # ---- 共通(どの遊びでも言う)
        for scene, emo in (("bye", "joy"), ("quit", "joy"), ("idle", "neutral"), ("again_q", "joy"), ("unclear", "doubt")):
            bank(0, name, playfx.COMMON[scene], emo)
        bank(0, name, playfx.COMMON.get("unclear2", []), "doubt")
        # ---- なぞなぞ
        L = quiz.LINES
        bank(0, name, L["intro"], "excited")
        bank(1, name, L["head"], "neutral")
        for scene, emo in (("ok", "excited"), ("streak", "excited"), ("hint", None), ("reveal", None)):
            if emo:
                bank(1, name, L[scene], emo)
        bank(1, name, L["retry"], "doubt")
        bank(1, name, L["retry"], "doubt", suffix=" ヒントも あるよ。")
        bank(2, name, L["all"], "excited")
        bank(2, name, L["good"], "excited", cnt=[3, 4])
        bank(2, name, L["try"], "joy", cnt=[0, 1, 2])
        # ---- あっち向いてホイ
        H = fun.HOI
        for scene, emo in (("intro", "excited"), ("r_turn", "excited"), ("k_turn", "joy"), ("k_ask", "joy"),
                           ("k_ask2", "joy"), ("amuite", None), ("hoi", None), ("r_hit", "excited"), ("r_miss", "sad"), ("k_hit", "surprise"), ("k_miss", "excited"),
                           ("score", "neutral"), ("unseen", "doubt"), ("kid_round", "excited"), ("robot_round", "joy"), ("draw_round", "joy"), ("streak", "excited"), ("bye", "joy")):
            prio = 0 if scene in ("intro", "bye") else 1
            if emo:
                bank(prio, name, H[scene], emo)
        for scene, raw in (("amuite", fun.RAW_AMUITE), ("hoi", fun.RAW_HOI)):
            for tpl in H[scene]:
                for t in expand(name, tpl):
                    add(1, t, None, raw)
        # ---- おみくじ
        O = fun.OMIKUJI
        bank(0, name, O["intro"], "excited")
        for tpl in O["rattle"]:
            for t in expand(name, tpl):
                add(0, t, None, fun.RAW_RATTLE)
        bank(1, name, O["pon"], "surprise")
        for r in fun.RANKS:
            for tpl in O["rank"][r[0]]:
                for t in expand(name, tpl, {"rank": [r[0]]}):
                    if r[0] == "だいきち":
                        add(1, t, None, fun.RAW_DAIKICHI)
                    else:
                        add(1, t, r[3])
        for r in fun.RANKS:
            for m in fun.OMIKUJI_MSG[r[0]]:
                for t in expand(name, m):
                    add(2, t, r[3] if r[0] != "すえきち" else "joy")
        bank(2, name, O["more"], "joy", cnt=[3, 6, 10])
        bank(1, name, O["bye"], "joy")
        # ---- ものまね
        M = fun.MIMIC
        bank(0, name, M["intro"], "excited")
        bank(1, name, M["announce"], "joy", {"label": [x[0] for x in fun.STYLES]})
        for scene, emo in (("react", "joy"), ("next", "joy"), ("ok_word", "joy"), ("redirect", "joy"), ("done", "excited"), ("bye", "joy")):
            bank(1, name, M[scene], emo)
        # ---- じゃんけん
        J = jk.JK
        bank(0, name, J["intro"], "excited")
        bank(0, name, J["intro_voice"], "excited")
        for scene, emo in (("first", "excited"), ("next", "excited"), ("aiko", "excited"), ("miss", "doubt"), ("miss_voice", "doubt"), ("miss2", "doubt"), ("miss_voice2", "doubt"), ("streak", "excited"), ("lose_streak", "joy"), ("bye", "joy")):
            bank(1, name, J[scene], emo)
        for res, emo in jk.Janken.EMO.items():
            for tpl in jk.Janken.LINES.get(name, jk.Janken.LINES["ずんだ"])[res]:
                for h in HANDS:
                    add(1, tpl.format(r=h), emo)
        # ---- カメラの見え方の案内(近すぎ・遠すぎ・見えない・暗い…)
        for t in camguide.all_lines(name):
            add(1, t, "joy")
        # ---- しりとり
        d = "なのだ" if name == "ずんだ" else "だよ"
        for t in sh.INTRO:
            add(0, t.replace("{d}", d), "excited")
        for t in sh.CHEER + sh.LOSE_ROBOT:
            add(2, t, "joy")
        for t in sh.WIN_KID if name == "ずんだ" else sh.WIN_KID_PLAIN:
            add(2, t, "surprise")
        for t in ("ごめん、よく聞こえなかった。もう一回言ってね。", "もう少し長い言葉でね", "ひとつの言葉だけ言ってね", "それはもう出たよ!ちがう言葉にしてね", "またあそぼうね!ばいばーい!"):
            add(2, t, "doubt" if "ごめん" in t or "もう少し" in t or "ひとつ" in t else "joy")
        P = sh.Shiritori.PERSONA.get(name, sh.Shiritori.PERSONA["ずんだ"])
        for w in sh.WORDS:
            if sh.last_char(w) != "ん":
                add(4, sh.robot_word_line(name, w, sh.last_char(w)), P["emo"])
    # ---- なぞなぞの問題(口調に関係ない)
    for q_, _a, _h in qd.RIDDLES:
        add(3, q_, "neutral")
    plan.sort(key=lambda x: x[0])
    return plan


REST_RATIO = float(os.environ.get("SC_WARM_REST", "1.0") or 1.0)       # 作った時間 × この倍率ぶん休む


class Warmer:
    """先読みの管理。step() が 1 文ずつ進める(テストしやすいように、スレッドと分けてある)。"""

    def __init__(self, eng_getter, personas_getter, busy=None, voice_ok=None, dirpath: "str | None" = None, clock=time.time):
        self.eng_getter, self.personas_getter = eng_getter, personas_getter
        self.busy = busy or (lambda: False)
        self.voice_ok = voice_ok
        self.dir = dirpath if dirpath is not None else os.environ.get("SC_VOICE_CACHE", os.path.expanduser("~/.sokutsu_voice"))
        self.clock = clock
        self.plan: list = []
        self.pos = 0
        self.done = 0
        self.state = "starting"
        self.stop_flag = threading.Event()
        self.names_key = None
        self.skipped = 0
        self.full = False

    # ---- 計画
    def replan(self) -> None:
        names = list(dict.fromkeys(self.personas_getter() or []))
        if names == self.names_key:
            return
        self.names_key = names
        self.plan = build_plan(names) if names else []
        self.pos = 0
        self.done = 0
        self.full = False

    def total(self) -> int:
        return len(self.plan)

    def _path(self, key) -> str:
        return os.path.join(self.dir, hashlib.sha1(repr(key).encode()).hexdigest() + ".npy")

    # ---- 1 文
    def step(self) -> bool:
        """次の 1 文を用意する。進めたら True。待つべき(混んでいる・声が落ちている・全部できた)なら False。"""
        import numpy as np
        eng = self.eng_getter()
        if eng is None:
            self.state = "waiting_engine"
            return False
        self.replan()
        if self.pos >= len(self.plan):
            self.state = "done" if self.plan else "idle"
            return False
        if self.full:
            self.state = "full"
            return False
        if self.busy():
            self.state = "paused"
            return False
        prio, text, emo, raw = self.plan[self.pos]
        from discuss import EMOTIONS, PERSONAS
        voice = PERSONAS[0]["voice"]
        emotion = EMOTIONS[emo][1] if emo else None
        key = eng.line_key_for(text, voice, emotion, raw)
        if eng.line_cached(text, voice, emotion, raw):
            self.pos += 1
            self.done += 1
            return True
        path = self._path(key)
        try:
            if os.path.exists(path):                                   # 前に作ったもの: 読むだけ
                pcm = np.load(path)
                if len(pcm):
                    eng.line_store(key, pcm)
                    self.pos += 1
                    self.done += 1
                    self.state = "loading"
                    return True
        except Exception:  # noqa: BLE001
            pass
        if self.voice_ok is not None and not self.voice_ok():          # 声のサーバーが落ちている: あとでやり直す
            self.state = "waiting_voice"
            return False
        self.state = "warming"
        try:
            pcm = eng.speech_pcm_as(text, voice, emotion=emotion, raw=raw, pin=True)
        except Exception:  # noqa: BLE001
            self.state = "waiting_voice"
            return False
        if pcm is None or not len(pcm):                                 # 合成できなかった(声が落ちた)
            self.state = "waiting_voice"
            return False
        if not eng.line_cached(text, voice, emotion, raw):             # 上限に達して入らなかった
            self.full = True
            self.state = "full"
            return False
        try:
            os.makedirs(self.dir, exist_ok=True)
            np.save(path, pcm)
        except Exception:  # noqa: BLE001
            pass
        self.pos += 1
        self.done += 1
        return True

    def snapshot(self) -> dict:
        eng = self.eng_getter()
        mb = round(((getattr(eng, "_line_bytes", {}) or {}).get(True, 0)) / (1 << 20), 1) if eng is not None else 0.0
        return {"done": self.done, "total": len(self.plan), "state": self.state, "mb": mb}

    def run(self) -> None:
        """スレッド本体。起動直後は少し待つ(機器の接続・最初の発話を優先)。"""
        if self.stop_flag.wait(20.0):
            return
        while not self.stop_flag.is_set():
            t0 = time.time()
            try:
                ok = self.step()
            except Exception:  # noqa: BLE001 - 先読みは落とさない
                ok = False
                self.state = "error"
            if ok:
                # 作った時間と同じだけ休む(CPU を半分までしか使わない。授業中の聞き取り・返事を遅くしない)
                self.stop_flag.wait(max(0.02, (time.time() - t0) * REST_RATIO))
            else:
                self.stop_flag.wait(60.0 if self.state in ("waiting_voice", "full", "done", "idle") else 3.0)

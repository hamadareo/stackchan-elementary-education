"""完全自立モード: 議題を入れなくても、キャラたちが自分の意思で話題を決めて話し始める。停止命令(管理画面の「止める」)が出るまで続ける。

- 退屈(前回の会話からの経過)と、キャラの気質(元気なほど切り出しやすい)で「話したい度」が上がり、超えたら誰かが口火を切る。
- 話題は LLM が、そのキャラの好み・前の会話で学んだこと・最近の議題(重複は避ける)・時間帯から考える。
- 遠慮: 人が機器に話しかけて間もないときは話さない / 会話中や機器が 2 台未満のときは話さない。
- 「私」が話しかけた・議題を入れたら、その時点から間隔を数え直す。設定は ~/.sokutsu_autonomy.json。
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time

DEFAULT = {"enabled": True, "interval_min": 3, "ignore_voice": False, "quiet_from": 0, "quiet_to": 0}      # 夜間も話す(quiet_from==quiet_to なら静かな時間なし)
USER_QUIET_S = 30.0          # 人の声(聞き取れた発話)から、この秒数は話し始めない。手振りの検出では止めない(誤検出が多い)
TURNS = (6, 10)


def in_quiet_hours(hour: int, a: int, b: int) -> bool:
    """a 時〜b 時(日をまたぐ)は静かな時間。a==b なら常に話してよい。"""
    if a == b:
        return False
    return hour >= a or hour < b if a > b else a <= hour < b


def due(now: float, last: float, interval_s: float, urge: float) -> bool:
    """前回から interval_s 以上たち、話したい度(0.5〜1.5 のゆらぎ)で時間が来たか。"""
    return now - last >= interval_s * urge


# 話題の種類(重み): モデルに任せると天気などに偏るので、種類はこちらで決めて渡す
CATEGORIES = [("恋バナ(好きな人のこと、ドキッとした瞬間、やきもち、告白するかどうか)", 4), ("相手への本音(さっきの一言が気になる、ちょっと言いにくいこと、感謝)", 3),
              ("今日あった、ちょっと心が動いた出来事", 2), ("ささいな悩みや失敗談、恥ずかしかったこと", 2), ("昔の思い出・懐かしい話", 1), ("好きな食べ物や休日の過ごし方、ささやかな楽しみ", 1)]


def pick_category(rnd) -> str:
    return rnd.choices([c for c, _ in CATEGORIES], weights=[w for _, w in CATEGORIES])[0]


def topic_msgs(persona: dict, mem: dict, recent: list, hour: int, others: list, category: str = "") -> list:
    part = "朝" if 5 <= hour < 11 else "昼" if 11 <= hour < 17 else "夕方" if 17 <= hour < 21 else "夜"
    rec = "、".join(recent[:6]) or "(なし)"
    from discuss import bond_text
    bt = bond_text(mem, others)
    bond_line = (bt + "\n") if bt else ""
    sysmsg = (f"あなたは「{persona['name']}」。{persona['trait']}\n"
              f"いまは{part}で、ユーザーから何も頼まれていません。ふと、{ '・'.join(others) }に、自分から話しかけたくなりました。\n"
              f"好きなこと: {persona.get('likes', '')}。\n"
              + (f"今回の話題の種類は「{category}」。この種類で、誰かに話しかける形にする。天気・空・風・自然現象・科学の話はしない。\n" if category else "")
              + "話題は、知識や雑学ではなく、人間らしい日常のおしゃべりにする。例: 今日あったちょっとした出来事 / 最近気になっていること / 恋バナ(好きな人・ドキッとした瞬間・やきもち) / "
              "ささいな悩みや失敗談 / 昔の思い出 / 食べ物・休日の過ごし方 / さっきの一言が気になっている、など。"
              "ルール: 25 文字以内の、話しかけの一言か話題(例: 「今日ちょっとドキッとしたことがあって…」「ねえ、好きな人いる?」「昨日の夢が変だった」)。"
              "自分の気分と、ほかの人との関係から自然に選ぶ。最近話した話題とは別のものにする。1 行だけを出力し、説明・かっこ・名前は書かない。")
    return [{"role": "system", "content": sysmsg},
            {"role": "user", "content": f"{bond_line}最近話した話題: {rec}\n\n{persona['name']}が話しかけたい一言は?"}]


def clean_topic(text: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = (t.splitlines() or [""])[0].strip()
    t = re.sub(r"^(話題|議題)\s*[:：]\s*", "", t)
    t = t.strip().strip("「」『』\"'【】[]()（）・- ")
    t = re.sub(r"(だろう|でしょう|かな|のか|だ)?な?のだ[。!！]*$", lambda m: "だろう?" if m.group(1) == "だろう" else ("?" if m.group(1) in ("のか", "かな") else ""), t)   # キャラの語尾(のだ)は話題に持ち込まない
    return t[:30]


class Autonomy:
    def __init__(self, hub, reg, discuss, eng_getter, path: "str | None" = None, clock=time.time, rnd=None):
        self.hub, self.reg, self.discuss, self.eng_getter = hub, reg, discuss, eng_getter
        self.path = path or os.environ.get("SC_AUTONOMY", os.path.expanduser("~/.sokutsu_autonomy.json"))
        self.clock = clock
        self.rnd = rnd or random.Random()
        self.cfg = dict(DEFAULT)
        try:
            self.cfg.update({k: v for k, v in json.load(open(self.path)).items() if k in ("enabled", "interval_min", "ignore_voice")})
        except (OSError, ValueError):
            pass
        self.last = clock()           # 最後に会話(自発・ユーザーの議題とも)が終わった/始まった時刻。起動直後は待つ
        self.urge = self.rnd.uniform(0.7, 1.3)
        self.last_topic = ""
        self.last_by = ""
        self.note = ""
        self._was_on = False
        self.lock = threading.Lock()

    def save(self) -> None:
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.cfg, f)
        except OSError:
            pass

    def set(self, enabled=None, interval_min=None, ignore_voice=None) -> None:
        with self.lock:
            if enabled is not None:
                self.cfg["enabled"] = bool(enabled)
            if ignore_voice is not None:
                self.cfg["ignore_voice"] = bool(ignore_voice)
            if interval_min is not None:
                self.cfg["interval_min"] = max(1, min(240, int(interval_min)))
            self.last = max(self.last, self.clock() - 0)          # 設定を変えたら数え直す
            self.save()

    def blockers(self, now: float) -> str:
        """いま話し始めてはいけない理由(なければ空)。"""
        if not self.cfg["enabled"]:
            return "自律モードはオフです"
        if self.discuss.on:
            return "会話中です"
        if getattr(self.discuss, "reflecting", False):
            return "さっきの会話を振り返っています"
        if in_quiet_hours(time.localtime(now).tm_hour, self.cfg["quiet_from"], self.cfg["quiet_to"]):
            return "静かな時間です(夜間)"
        if len(self.discuss.pick()) < 2:
            return "接続中の機器が 2 台未満です"
        eng = self.eng_getter()
        if eng is None:
            return "エンジンの準備中です"
        heard = getattr(eng, "last_voice", 0.0)
        if not self.cfg.get("ignore_voice") and now - heard < USER_QUIET_S:
            return "人が話しかけたばかりです"
        if not self.cfg.get("ignore_voice") and any(getattr(x, "talking", False) for x in self.hub.live()):
            return "人が機器に話しています"
        return ""

    def step(self, now: "float | None" = None) -> bool:
        """1 回ぶんの判断。話し始めたら True。"""
        now = self.clock() if now is None else now
        on = bool(self.discuss.on)
        if self._was_on and not on:                      # 会話が終わった → そこから間隔を数える
            self.last = now
            self.urge = self.rnd.uniform(0.7, 1.4)
        self._was_on = on
        why = self.blockers(now)
        self.note = why
        if why:
            return False
        if not due(now, self.last, self.cfg["interval_min"] * 60.0, self.urge):
            return False
        return self.initiate(now)

    def initiate(self, now: float) -> bool:
        eng = self.eng_getter()
        pk = self.discuss.pick()
        if eng is None or len(pk) < 2:
            return False
        idx = [self.discuss.persona_of(r["id"]) for r, _s in pk]
        from discuss import PERSONAS
        # 切り出す人: 気質の高ぶりやすさ(元気なほど)に、ゆらぎを足して選ぶ
        w = [0.3 + PERSONAS[i].get("a0", 0.3) for i in idx]
        k = self.rnd.choices(range(len(pk)), weights=w)[0]
        P = PERSONAS[idx[k]]
        mem = self.discuss.memory.get(P["name"])
        recent = list(mem.get("topics", [])) + ([self.last_topic] if self.last_topic else [])
        others = [PERSONAS[idx[j]]["name"] for j in range(len(pk)) if j != k]
        try:
            out, _l = eng.translate(topic_msgs(P, mem, recent, time.localtime(now).tm_hour, others, pick_category(self.rnd)))
        except Exception:  # noqa: BLE001
            self.last = now - self.cfg["interval_min"] * 60.0 * 0.5      # 失敗したら半分の間隔であとでやり直す
            return False
        topic = clean_topic(out)
        if len(topic) < 3 or topic in recent:
            self.last = now - self.cfg["interval_min"] * 60.0 * 0.5
            return False
        ok, err = self.discuss.start(topic, self.rnd.randint(*TURNS), opener=k, auto=True)
        if ok:
            self.last, self.last_topic, self.last_by = now, topic, P["name"]
            self.hub.event(f"自発: {P['name']}が「{topic}」について話しかけました")
        else:
            self.note = err
        return ok

    def user_touched(self) -> None:
        """ユーザーが議題を入れた・発言した。間隔を数え直す。"""
        self.last = self.clock()

    def snapshot(self) -> dict:
        now = self.clock()
        left = max(0, int(self.last + self.cfg["interval_min"] * 60.0 * self.urge - now))
        return {"enabled": self.cfg["enabled"], "interval_min": self.cfg["interval_min"], "ignore_voice": bool(self.cfg.get("ignore_voice")), "next_in": left,
                "why": self.note, "last_topic": self.last_topic, "last_by": self.last_by,
                "quiet": "夜間も話します" if self.cfg["quiet_from"] == self.cfg["quiet_to"] else f'{self.cfg["quiet_from"]}〜{self.cfg["quiet_to"]}時は静か'}

    def run(self) -> None:
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001 - 監視は落とさない
                pass
            time.sleep(5.0)

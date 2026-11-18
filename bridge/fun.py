"""子ども向けの「楽しい遊び」3つ: あっち向いてホイ・おみくじ・ものまねボイス。

quiz.py の Play(開始・聞き取り・首と顔の演出・管理画面の状態)の上に作る。機器ごとに独立で、LLM・s109 に頼らない(オフラインでも動く)。
首の動きは小さめ・ゆっくりで、終わったら必ず正面へ戻す。台詞は場面ごとに複数持ち、同じ言い方が続かない(playfx.Picker)。
機器の画面には、遊びの結果(出した手・指さした向き・運勢・言葉)を board.show で大きく出す(古いファームウェアでは何も起きない)。
"""
from __future__ import annotations

import re
import statistics
import threading

import board
from janken import NAME, QUIT as J_QUIT, Janken, judge, parse_hand, pick_robot_hand
from playfx import AGAIN, QUIT, Stage, fold
from quiz import IDLE_MAX, IDLE_WAIT, Play

# ---------------------------------------------------------------- あっち向いてホイ
DIRS = ("up", "down", "left", "right")
DIR_NAME = {"up": "うえ", "down": "した", "left": "ひだり", "right": "みぎ"}      # 子どもから見た向き(子どもの右 = みぎ)
DIR_WORDS = {"up": ("うえ", "上", "うえむき", "あっぷ", "up"), "down": ("した", "下", "したむき", "だうん", "down"),
             "left": ("ひだり", "左", "ひだりむき", "れふと", "left"), "right": ("みぎ", "右", "みぎむき", "らいと", "right")}
WIN_POINTS = 3                 # 先に取ると勝ちの点
MAX_ATTACKS = 7                # これだけ「あっちむいて」をやっても決まらなければ、点の多い方の勝ち
FOOL_RATE = 0.3                # 子どもが指さしたとき、ロボットが つい同じ向きを向いてしまう確率
HOI_WINDOW = 3.4               # ロボットが「ホイ」で向いてから、子どもの顔の向きを見る秒数
HOI_HOLD = 1.5                 # ロボットが向いたままでいる秒数
ROBOT_PAN, ROBOT_TILT = 220, 120      # ロボットが向く大きさ(0.1度。ゆっくり・小さめ)
MIRROR = False                 # カメラの映像が左右反転しているとき True(子どもの みぎ/ひだり が逆になる)
TX, TY = 0.26, 0.20            # 顔の向きの判定のしきい値(目の間隔に対する、鼻の動き)


def parse_dir(text: str) -> "str | None":
    """子どもの言葉 → 向き(up/down/left/right)。「うえ」「上」「ひだり」「左むき」など。決められないとき None。"""
    t = re.sub(r"(ゆびさ|さ)した|さしました|しました|ました|でした", "", fold(text))       # 「ゆびをさした」「むきました」の「した」は、向きの「した」ではない
    found = {d for d, ws in DIR_WORDS.items() for w in ws if fold(w) in t}
    return next(iter(found)) if len(found) == 1 else None


def face_metrics(row) -> "tuple[float, float] | None":
    """YuNet の顔の行(右目・左目・鼻の位置)から、顔の向きの手がかり(鼻の横のずれ, 鼻の縦の位置)を、目の間隔を 1 として返す。"""
    try:
        rex, rey, lex, ley, nx, ny = (float(row[i]) for i in (4, 5, 6, 7, 8, 9))
    except Exception:  # noqa: BLE001
        return None
    d = abs(lex - rex)
    if d < 8.0:
        return None
    return ((nx - (rex + lex) / 2.0) / d, (ny - (rey + ley) / 2.0) / d)


def classify_dir(m: "tuple[float, float]", base: "tuple[float, float]") -> str:
    """顔の向き(子どもから見た up/down/left/right、または center)。基準 base(正面を向いていたときの値)との差で決める。
    カメラは子どもの正面から見るので、子どもの みぎ は 画像の ひだり にあたる(鼻が画像の左へ動く)。"""
    dx, dy = m[0] - base[0], m[1] - base[1]
    if MIRROR:
        dx = -dx
    ax, ay = abs(dx) / TX, abs(dy) / TY
    if max(ax, ay) < 1.0:
        return "center"
    if ax >= ay:
        return "right" if dx < 0 else "left"
    return "up" if dy < 0 else "down"


def vote_dir(labels: list) -> "str | None":
    """画像ごとの向きの多数決。向いている(center 以外)画像が 2 枚以上あり、center より多く、1 つの向きに決まるときだけ返す。"""
    moved = [x for x in labels if x != "center"]
    if len(moved) < 2 or len(moved) <= labels.count("center"):
        return None
    top = max(set(moved), key=moved.count)
    if moved.count(top) * 2 <= len(moved) or moved.count(top) < 2:
        return None
    return top


def baseline(metrics: list) -> "tuple[float, float]":
    """正面を向いているときの手がかり(中央値)。取れていなければ、ふつうの値。"""
    if not metrics:
        return (0.0, 0.85)
    return (statistics.median(m[0] for m in metrics), statistics.median(m[1] for m in metrics))


HOI = {
    "intro": ["あっちむいてホイを しよう！まず じゃんけん。かった ほうが ゆびで さして、あっちむいてホイ！さきに 3てん とった ほうの かち{d}！",
              "あっちむいてホイ{d}！じゃんけんに かったら ゆびを さすよ。さきに 3てん とったら かち！ぐー、ちょき、ぱーを 声で言うか、カメラに 手を みせてね。",
              "あっちむいてホイ、はじめるよ！じゃんけんで かって、ゆびを さした ほうに むかなければ かち{d}！3てん せんしゅ{d}！",
              "さあ、あっちむいてホイ{d}！ぼくと しょうぶ！さきに 3てん とったら かち{d}！じゃんけん、いくよー！"],
    "jan_call": ["さいしょはぐー、じゃんけん…ぽん！", "じゃんけん…ぽん！", "いくよー！じゃんけん…ぽん！", "じゃーんけーん…ぽん！", "つぎ いくよ！じゃんけん…ぽん！", "しょうぶ{d}！じゃんけん…ぽん！"],
    "aiko": ["{r}！あいこで…しょ！", "おなじ{d}！あいこで…しょ！", "あいこ{d}！あいこで…しょ！", "{r}で あいこ！あいこで…しょ！", "あらら あいこ！もういっかい、あいこで…しょ！"],
    "r_win": ["{r}！ぼくの かち{d}！{cheer} こんどは ぼくが ゆびを さす{n}！", "やったー！{r}で ぼくの かち{d}！", "ぼくは{r}！かった{n}！あっちむいてホイ、いくよ！",
              "{cheer} ぼくの かち！あっちむいてホイ、じゅんびは いい？", "{r}を だしたら かった！ぼくの ばん{d}！"],
    "k_win": ["{r}！{oops} きみの かち{d}！ゆびを さして！", "ぼくは{r}。まけちゃった！きみの かち{d}！あっちむいてホイ、どうぞ！", "{oops} きみの かち{d}！さあ、あっちむいてホイ！",
              "{r}を だしたけど まけた{n}！きみの ばん{d}！"],
    "k_ask": ["「あっちむいてホイ！」って 言いながら、ゆびを さしてね。ゆびの ほうこうは、うえ、した、ひだり、みぎで おしえてね！", "ゆびを さす ほうこうを 言ってね。うえ、した、ひだり、みぎ！",
              "どっちを さす？うえ、した、ひだり、みぎ、どれかな？"],
    "k_ask2": ["どっちを さしたの？うえ、した、ひだり、みぎ で おしえてね！", "もういっかい おしえて！うえ、した、ひだり、みぎ！"],
    "amuite": ["あっちむいて〜…", "あっちむいて〜〜…", "あっち〜、むいて〜…", "いくよ、あっちむいて〜…"],
    "hoi": ["ホイ！", "ホーイ！", "ホイッ！"],
    "r_hit": ["やったー！おなじ むき{d}！ぼくの 1てん！", "ひっかかった{n}！ぼくの 1てん{d}！{cheer}", "{dir}を むいたね！ぼくの かち{d}！", "せいこう！{cheer} ぼくの 1てん！"],
    "r_miss": ["あー、よけられた！やるね！", "{oops} よけられちゃった！", "うまい！こっちを むかなかった{n}！", "セーフ{d}！やるね！", "ざんねん、つぎは ねらう{n}！"],
    "k_hit": ["あっ、{dir}を むいちゃった！きみの 1てん{d}！{cheer}", "やられた！{dir}を むいちゃった{n}！きみの 1てん！", "{oops} ひっかかった！きみの かち{d}！", "あー！つられちゃった！きみの 1てん{d}！"],
    "k_miss": ["セーフ{d}！ぼくは {dir}を むいた{n}！", "よけたよ！ぼくは {dir}！ざんねん！", "ざんねん、ぼくは {dir}を むいた{n}！つぎ いこう！", "ふふふ、{dir}を むいたよ！つられない{n}！"],
    "score": ["いま、きみ {ks}、ぼく {rs}！", "きみが {ks}てん、ぼくが {rs}てん{d}！", "{ks} たい {rs}！", "ここまで、きみ {ks}、ぼく {rs}！"],
    "unseen": ["ごめん、みえなかった！もういっかい やろう！", "あれ、どっちか わからなかった！もういっかい！", "うーん、みえなかった。もういっかい じゃんけん！"],
    "kid_round": ["きみの かち{d}！{cheer} {ks}たい{rs}！", "ぼくの まけ{d}！きみ、つよいね！{ks}たい{rs}！", "きみが ゆうしょう{d}！{cheer} {ks}たい{rs}！", "まいった！きみの かち{d}！{ks}たい{rs}！"],
    "robot_round": ["ぼくの かち{d}！でも いい しょうぶ{d}！{rs}たい{ks}！", "ぼくが かった{n}！きみも つよかった{n}よ！{rs}たい{ks}！", "{rs}たい{ks}で ぼくの かち！もういっかい やれば かてる{n}よ！"],
    "draw_round": ["ひきわけ{d}！いい しょうぶ{d}！{ks}たい{rs}！", "どっちも おなじ！ひきわけ{d}！{ks}たい{rs}！"],
    "streak": ["{s}かい れんぞくで きみの かち{d}！{cheer}", "すごい！{s}しょう れんぞく{d}！", "{s}れんしょう！きみ むてき{d}！"],
    "bye": ["たのしかった{n}！また あっちむいてホイ しようね！", "いい しょうぶ だった{n}！またね！", "ありがとう！また ぼくと しょうぶ しようね！"],
}


class Hoi(Play, Janken):
    KIND = "hoi"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.kp = self.rp = 0
        self.round_streak = 0
        self.attacks = 0
        self.fails = 0
        self.phase = ""                # いまの場面(jan=じゃんけんの手を待つ / ask_dir=子どもの指さしを待つ / watch=子どもの顔の向きを見る)

    def _h(self, scene: str, **kw) -> str:
        return self.L(scene, HOI[scene] if scene in HOI else None, **kw)

    def _board(self, title: str = "あっちむいてホイ", a=("", ""), b=("", ""), foot="", ms=6000, a_arrow="", b_arrow="") -> None:
        Stage._board(self, title, a, b, foot, ms, a_arrow, b_arrow)

    def _set_stat(self) -> None:
        self.stat = f"きみ {self.kp}てん ・ ロボット {self.rp}てん ・ れんしょう {self.round_streak}"
        self.score = self.kp

    # ---- じゃんけん(勝ち負けが決まるまで、あいこは くりかえす)
    def _jan(self, eng, call: str) -> "tuple":
        """戻り値: ("kid_win"|"robot_win", 子どもの手, ロボットの手) / ("quit",) / ("idle",)"""
        misses = 0
        while not self._stop.is_set():
            self.big = "じゃんけん…ぽん！"
            self.phase = "jan"
            self.qtext = call
            self._board(title="じゃんけん…", a=("", "ぽん！"), ms=3000)
            self._say(eng, call, "excited", face="happy")
            self.turn = "kid"
            self._face("listening")
            robot = pick_robot_hand(self.rnd, [h["robot"] for h in self.hands])
            text, cam = self._wait_hand()
            self._freeze(True)                                   # _wait_hand が手の認識用に取り替えた画像の受け口を、もとに戻す
            if self._stop.is_set() or text is None:
                return ("quit",)
            if text and J_QUIT.search(text) and parse_hand(text) is None:
                return ("quit",)
            hand = parse_hand(text) if text else None
            if hand is None and cam:
                hand = cam
            if hand is None:
                misses += 1
                if misses >= 4:
                    return ("idle",)
                self._say(eng, self.L("jan_miss", ["もういっかい！ぐー、ちょき、ぱー！", "ぐー、ちょき、ぱーで おしえてね！", "てを カメラに みせても いいよ！もういっかい！"]), "doubt", face="doubt")
                call = "じゃんけん…ぽん！"
                continue
            misses = 0
            res = judge(hand, robot)
            self.hands.append({"kid": hand, "robot": robot, "res": res, "via": "voice"})
            del self.hands[:-30]
            self.qtext = f"じゃんけん: きみ {NAME[hand]} / ロボット {NAME[robot]}"
            foot = "あいこ！" if res == "draw" else ("きみの かち！" if res == "kid_win" else f"{self.name}の かち！")
            self._board(title="じゃんけん", a=(self.name, NAME[robot]), b=("きみ", NAME[hand]), foot=foot, ms=5000)
            if res == "draw":
                call = self._h("aiko", r=NAME[robot])
                continue
            return (res, hand, robot)
        return ("quit",)

    # ---- ロボットが向く(首は小さく・ゆっくり)
    def _turn_head(self, d: str, speed: int = 320) -> None:
        # 子どもの みぎ = ロボットから見て ひだり側。パンの + 向きが、子どもの みぎ。
        dpan = {"right": ROBOT_PAN, "left": -ROBOT_PAN}.get(d, 0)
        dtilt = {"up": ROBOT_TILT, "down": -ROBOT_TILT}.get(d, 0)
        self._servo(dpan=dpan, dtilt=dtilt, speed=speed)

    def _kid_metrics(self, eng, frames: list) -> list:
        face = getattr(eng, "face", None)
        out = []
        if face is None or getattr(face, "cv2", None) is None:
            return out
        for _t, j in frames:
            try:
                img = face.decode(j)
                f = face.find(img)
                if f is not None and f[4] is not None:
                    m = face_metrics(f[4])
                    if m is not None:
                        out.append(m)
            except Exception:  # noqa: BLE001
                continue
        return out

    def _watch_kid(self, eng, t_hoi: float, base: "tuple[float, float]") -> "tuple[str | None, str]":
        """ホイのあと、子どもがどちらを向いたかを待つ。声(先生の入力も)が先に決まればそれ、なければ、集めた画像の多数決。
        戻り値: (向き or None, 判定のしかた)"""
        self.turn = "kid"
        self.phase = "watch"
        end = self.clock() + HOI_WINDOW
        back = False
        while self.clock() < end and not self._stop.is_set():
            if not back and self.clock() - t_hoi >= HOI_HOLD:
                self._front()                                    # 一定時間 向いたら、正面に もどる
                back = True
            try:
                text = self.q.get(timeout=0.2)
            except Exception:  # noqa: BLE001
                continue
            if text is None:
                return None, "stop"
            d = parse_dir(text)
            if d:
                self.turn = ""
                return d, "voice"
            if text and QUIT.search(text):                       # 判定の待ち時間でも「やめよう」を取りこぼさない
                self._front()
                return None, "stop"
        if not back:
            self._front()
        self.turn = ""
        frames = [x for x in list(self._frames) if x[0] >= t_hoi + 0.35]
        labels = [classify_dir(m, base) for m in self._kid_metrics(eng, frames)]
        d = vote_dir(labels)
        return (d, "camera") if d else (None, f"camera:{len(labels)}")

    def _robot_points(self, eng) -> str:
        """ロボットが かった: ロボットが向きを決めて「ホイ」。子どもが同じ向きを向いたら、ロボットの 1 点。戻り値: robot / kid / skip / quit"""
        d = self.rnd.choice(DIRS)
        self.atext = f"先生用: ロボットは「{DIR_NAME[d]}」を向きます。子どもが向いた向きを言葉で入れられます(うえ/した/ひだり/みぎ)"
        self._say(eng, self._h("r_win", r=NAME[self.hands[-1]["robot"]]), "excited", face="giggle")
        base = baseline(self._kid_metrics(eng, [x for x in list(self._frames) if x[0] >= self.clock() - 6.0]))
        self._say(eng, self._h("amuite"), "surprise", face="surprise", raw={"speed": 0.75, "pitch": 0.04, "intonation": 1.5, "volume": 1.1})
        self._sleep(0.35)                                       # ちょっと ためる
        t_hoi = self.clock()
        self._turn_head(d)
        self._board(a=(self.name, "ホイ！"), foot=f"ぼくは {DIR_NAME[d]}を ゆびさした！", ms=5000, a_arrow=d)
        self._say(eng, self._h("hoi"), "excited", face="surprise", raw={"speed": 1.3, "pitch": 0.08, "intonation": 1.8, "volume": 1.3})
        kd, how = self._watch_kid(eng, t_hoi, base)
        self._front()
        if self._stop.is_set() or how == "stop":
            return "quit"
        if kd is None:
            self.big = "みえなかった…"
            self._board(a=(self.name, "?"), foot="どっちを むいたか わからなかった", ms=3500)
            self._say(eng, self._h("unseen"), "doubt", face="doubt")
            self.fails += 1
            return "skip"
        self.fails = 0
        hit = kd == d
        self.history.append({"q": f"ロボットが {DIR_NAME[d]}を さした・きみは {DIR_NAME[kd]}({'カメラ' if how == 'camera' else '声'})", "ok": not hit})
        self._board(a=(self.name, ""), b=("きみ", ""), foot="ぼくの 1てん！" if hit else "セーフ！よけた！", ms=5500, a_arrow=d, b_arrow=kd)
        if hit:
            self.rp += 1
            self.big = "ロボットの 1てん！"
            self._say(eng, self._h("r_hit", dir=DIR_NAME[d]), "excited", face="giggle")
            return "robot"
        self.big = "よけた！セーフ"
        self._wobble()
        self._say(eng, self._h("r_miss"), "sad", face="sad")
        return "none"

    def _kid_points(self, eng) -> str:
        """子どもが かった: 子どもが ゆびさす向きを言い、ロボットは(ときどき つられて)向く。同じ向きを向いたら、子どもの 1 点。戻り値: kid / none / skip / quit"""
        self.atext = "先生用: 子どもが ゆびさした向きを、うえ/した/ひだり/みぎ で入力してください"
        self._say(eng, self._h("k_win", r=NAME[self.hands[-1]["robot"]]), "sad", face="sad")
        self._board(b=("きみ", "ゆびさし"), foot="うえ・した・ひだり・みぎ", ms=8000)
        kd = None
        for scene in ("k_ask", "k_ask2"):
            self._say(eng, self._h(scene), "joy", face="happy")
            self.phase = "ask_dir"
            text = self._wait(12.0)
            if text is None:
                return "quit"
            if text and QUIT.search(text) and parse_dir(text) is None:
                return "quit"
            kd = parse_dir(text) if text else None
            if kd:
                break
        if kd is None:
            self.fails += 1
            self._say(eng, self._h("unseen"), "doubt", face="doubt")
            return "skip"
        self.fails = 0
        self.big = f"きみ: {DIR_NAME[kd]}"
        self._board(b=("きみ", ""), foot=f"きみは {DIR_NAME[kd]}を ゆびさした", ms=4000, b_arrow=kd)
        self._say(eng, self._h("amuite"), "surprise", face="surprise", raw={"speed": 0.75, "pitch": 0.04, "intonation": 1.5, "volume": 1.1})
        self._sleep(0.35)
        fooled = self.rnd.random() < FOOL_RATE
        rd = kd if fooled else self.rnd.choice([x for x in DIRS if x != kd])
        self._turn_head(rd)
        self._board(a=(self.name, ""), b=("きみ", ""), foot="", ms=5500, a_arrow=rd, b_arrow=kd)
        self._say(eng, self._h("hoi"), "excited", face="surprise", raw={"speed": 1.3, "pitch": 0.08, "intonation": 1.8, "volume": 1.3})
        self._sleep(HOI_HOLD - 0.6)
        self._front()
        hit = rd == kd
        self.history.append({"q": f"きみが {DIR_NAME[kd]}を さした・ロボットは {DIR_NAME[rd]}を むいた", "ok": hit})
        self._board(a=(self.name, ""), b=("きみ", ""), foot="きみの 1てん！" if hit else "セーフ！", ms=5500, a_arrow=rd, b_arrow=kd)
        if hit:
            self.kp += 1
            self.big = "きみの 1てん！"
            self._nod(1)
            self._say(eng, self._h("k_hit", dir=DIR_NAME[rd]), "surprise", face="surprise")
            return "kid"
        self.big = "セーフ！"
        self._say(eng, self._h("k_miss", dir=DIR_NAME[rd]), "excited", face="giggle")
        return "none"

    def _run(self, eng) -> None:
        self.hands = []
        self.round_streak = 0
        self._say(eng, self._h("intro"), "excited", face="happy")
        call = self._h("jan_call")
        while not self._stop.is_set():
            self.kp = self.rp = self.attacks = self.fails = 0
            self._set_stat()
            while self.kp < WIN_POINTS and self.rp < WIN_POINTS and self.attacks < MAX_ATTACKS and not self._stop.is_set():
                r = self._jan(eng, call)
                call = self._h("jan_call")
                if r[0] in ("quit", "idle"):
                    self._bye(eng, r[0])
                    return
                out = self._robot_points(eng) if r[0] == "robot_win" else self._kid_points(eng)
                if out == "quit":
                    self._bye(eng, "quit")
                    return
                if out == "skip":
                    if self.fails >= 3:
                        self._bye(eng, "idle")
                        return
                    continue
                self.attacks += 1
                self._set_stat()
                if self.kp < WIN_POINTS and self.rp < WIN_POINTS:
                    self._say(eng, self._h("score", ks=self.kp, rs=self.rp), "neutral", face="neutral")
            self.stats["hoi_rounds"] += 1
            self.stats["hoi_points"] += self.kp
            if self.kp > self.rp:
                self.round_streak += 1
                self.stats["hoi_wins"] += 1
                self.stats["hoi_streak_best"] = max(self.stats["hoi_streak_best"], self.round_streak)
                key, emo, face = "kid_round", "excited", "giggle"
            elif self.rp > self.kp:
                self.round_streak = 0
                key, emo, face = "robot_round", "joy", "happy"
            else:
                key, emo, face = "draw_round", "joy", "happy"
            self._save()
            self._set_stat()
            self.big = {"kid_round": "きみの かち！", "robot_round": "ロボットの かち", "draw_round": "ひきわけ"}[key]
            self._board(a=(self.name, str(self.rp)), b=("きみ", str(self.kp)), foot=self.big, ms=7000, title="けっか")
            if key == "kid_round":
                self._nod(3)
            self._say(eng, self._h(key, ks=self.kp, rs=self.rp), emo, face=face)
            if key == "kid_round" and self.round_streak >= 2 and not self._stop.is_set():
                self._say(eng, self._h("streak", s=self.round_streak), "excited", face="giggle")
            if self._stop.is_set():
                return
            if not self._again(eng):
                self._say(eng, self._h("bye"), "joy")
                return
            self.hub.event("あっちむいてホイ: もういっかい")
            call = self._h("jan_call")

    def _bye(self, eng, res: str) -> None:
        self.msg = "またあそぼうね！"
        self._say(eng, self.L("bye" if res == "idle" else "quit"), "joy")

    def snapshot(self) -> dict:
        d = Play.snapshot(self)
        d.update({"phase": self.phase, "kid_points": self.kp, "robot_points": self.rp, "round_streak": self.round_streak, "best": self.stats.get("hoi_streak_best", 0)})
        return d


# ---------------------------------------------------------------- おみくじ
# (読み, 表示, 重み, 声の調子, 顔, うなずく回数)
RANKS = [("だいきち", "大吉", 9, "excited", "giggle", 3), ("ちゅうきち", "中吉", 20, "joy", "happy", 2), ("しょうきち", "小吉", 24, "joy", "happy", 1),
         ("きち", "吉", 26, "joy", "happy", 1), ("すえきち", "末吉", 21, "shy", "happy", 0)]
COLORS = ["あか", "あお", "きいろ", "みどり", "ピンク", "オレンジ", "むらさき", "みずいろ", "しろ", "きんいろ", "ぎんいろ", "ちゃいろ", "ももいろ", "きみどり", "こんいろ", "くろ"]
ITEMS = ["えんぴつ", "おにぎり", "くつした", "ハンカチ", "どんぐり", "ボール", "えほん", "おべんとう", "かさ", "ぼうし", "リボン", "ふでばこ", "けしごむ", "とけい", "かがみ",
         "てぶくろ", "マフラー", "ぬいぐるみ", "ブロック", "しゃぼんだま", "ふうせん", "ビーだま", "おりがみ", "クレヨン", "たいこ", "ふえ", "スプーン", "ノート", "じてんしゃ",
         "ぼうえんきょう", "ひまわり", "たんぽぽ", "クッキー", "みかん", "いちご", "ほうき", "シール", "わゴム"]
OMIKUJI_MSG = {
    "だいきち": ["きょうは さいこうの いちにち{d}！なんでも うまく いくよ！", "とっても いい ことが ありそう{d}！", "ともだちと もっと なかよく なれるよ！", "ちょうせんしたら、きっと うまく いくよ！",
               "きらきら ひかる いちにちに なるよ！", "びっくりする いい ことが あるかも！", "おおきな こえで あいさつ すると、もっと いい ことが あるよ！", "きょうの きみは むてき{d}！"],
    "ちゅうきち": ["いい ことが ありそうな よかん{d}！", "ともだちに やさしく すると、もっと いいよ！", "ちょっと がんばると、いい けっかが でるよ！", "えがおで すごすと、しあわせが ふえるよ！",
                "おもしろい ことが おきるかも！", "きょうは、あたらしい ことを ためすと いいよ！", "やさしい きもちが、いい ことを よぶよ！"],
    "しょうきち": ["ちいさな しあわせが みつかるよ！", "ゆっくり いけば、うまく いくよ！", "いつもより ちょっと だけ いい ことが あるよ！", "まわりを よく みると、いい ものが みつかるよ！",
                "おともだちと わらうと、もっと いいよ！", "きょうは ていねいに やると いいよ！", "ありがとうを 言うと、いい ことが あるよ！"],
    "きち": ["ふつうに いい いちにち{d}！", "いつもどおり がんばれば だいじょうぶ！", "おひるごはんが おいしく なりそう{d}！", "きょうは のんびり すごすと いいよ！",
           "ちょっと いい ことが あるかも！", "げんきに すごせる いちにち{d}！", "なかよく あそぶと、たのしい{d}！"],
    "すえきち": ["あとから どんどん よく なるよ！", "ゆっくり でも、だいじょうぶ！", "すこしずつ いい ことが ふえていくよ！", "おわりの ほうに いい ことが あるよ！",
               "あせらず いこうね！きっと うまく いくよ！", "あしたは もっと いい ひに なるよ！", "ゆうがたに、うれしい ことが あるかも！"],
}
OMIKUJI = {
    "intro": ["おみくじを ひくよ！「もういっかい」で なんどでも ひけるよ。いくよ、ガラガラ〜！", "おみくじ{d}！きょうの うんせいを みてあげる{n}。ガラガラ、まわすよ！",
              "きょうの うんせいを うらなうよ！ドキドキ するね。ガラガラ〜！", "おみくじの じかん{d}！なにが でるかな？いくよー！"],
    "rattle": ["ガラガラ、ガラガラ…", "ガラガラガラ…", "ガラ、ガラ、ガラガラ〜…", "ガラガラ〜、ガラガラ〜…"],
    "pon": ["ぽん！でたよ！", "コロン！でた！", "ぽとん！でました！", "でた でた！"],
    "rank": {"だいきち": ["{rank}！！おおよろこび{d}！{cheer}", "なんと {rank}！やったね！{cheer}", "{rank}{d}！すごい！いちばん いい やつ{d}！"],
             "ちゅうきち": ["{rank}{d}！いいね！", "{rank}！{cheer}", "おお、{rank}！いい うんせい{d}！"],
             "しょうきち": ["{rank}{d}！", "{rank}！いい ちょうし{d}！", "{rank}が でた{n}！"],
             "きち": ["{rank}{d}！", "{rank}！ばっちり{d}！", "{rank}が でたよ！"],
             "すえきち": ["{rank}{d}！", "{rank}！あとから よく なるよ！", "{rank}が でた{n}！"]},
    "lucky": ["ラッキーカラーは「{color}」、ラッキーな ものは「{item}」{d}！", "きょうの ラッキーカラーは「{color}」！ラッキーな ものは「{item}」{d}！",
              "「{color}」いろの ものを みつけてね。ラッキーな ものは「{item}」{d}！", "ラッキーな ものは「{item}」！ラッキーカラーは「{color}」{d}！"],
    "more": ["おみくじ、{n}かい ひいたね！", "{n}かい めだね！たのしい？", "どんどん ひいちゃおう！{n}かい め{d}！"],
    "bye": ["いい うんせい だった{n}！また ひこうね！", "たのしかった{n}！またね、ばいばい！", "また ひきたく なったら、よんでね！", "ありがとう！きょうも いい いちにちに なるよ！"],
}


def omikuji_draw(rnd, last_rank: str, recent_colors: list, recent_items: list) -> dict:
    """おみくじ 1 回ぶん。同じ運勢が 2 回続かないよう避け、直近の色・ものも避ける。"""
    ranks = [r for r in RANKS if r[0] != last_rank] or RANKS
    r = rnd.choices(ranks, weights=[x[2] for x in ranks])[0]
    colors = [c for c in COLORS if c not in recent_colors] or COLORS
    items = [i for i in ITEMS if i not in recent_items] or ITEMS
    return {"yomi": r[0], "rank": r[1], "emo": r[3], "face": r[4], "nod": r[5], "color": rnd.choice(colors), "item": rnd.choice(items),
            "msg": rnd.choice(OMIKUJI_MSG[r[0]])}


class Omikuji(Play):
    KIND = "omikuji"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.draws = 0
        self.big_n = 0
        self.last_rank = ""
        self.rc: list = []
        self.ri: list = []

    def _o(self, scene: str, **kw) -> str:
        return self.L(scene, OMIKUJI[scene], **kw)

    def _rattle(self, eng, text: str) -> None:
        """「ガラガラ…」と言いながら、首を ちいさく ふる。"""
        stop = threading.Event()

        def go():
            sign = 1
            while not stop.is_set():
                self._servo(dpan=sign * 130, speed=600)
                sign = -sign
                stop.wait(0.33)
        t = threading.Thread(target=go, daemon=True)
        t.start()
        try:
            self._say(eng, text, "excited", face="surprise", raw={"speed": 1.15, "pitch": 0.05, "intonation": 1.6, "volume": 1.2})
        finally:
            stop.set()
            t.join(1.0)
            self._front()

    def _draw_once(self, eng) -> None:
        d = omikuji_draw(self.rnd, self.last_rank, self.rc, self.ri)
        self.last_rank = d["yomi"]
        self.rc = (self.rc + [d["color"]])[-4:]
        self.ri = (self.ri + [d["item"]])[-6:]
        self.draws += 1
        self.stats["omikuji_n"] += 1
        if d["yomi"] == "だいきち":
            self.big_n += 1
            self.stats["omikuji_big"] += 1
        self._save()
        self.big = "ガラガラ…"
        self.qtext = ""
        self._board(title="おみくじ", a=("きょうの うんせい", "…"), foot="ガラガラ…", ms=4000)
        self._rattle(eng, self._o("rattle"))
        self._sleep(0.25)
        self._say(eng, self._o("pon"), "surprise", face="surprise")
        self.big = f"{d['rank']}！"
        self.qtext = f"{d['rank']}  ラッキーカラー: {d['color']}  ラッキーなもの: {d['item']}"
        self.atext = d["msg"].replace("{d}", "だよ")
        self._board(title="おみくじ", a=("きょうの うんせい", d["rank"]), foot=f"{d['color']} / {d['item']}", ms=9000)
        if d["nod"]:
            self._nod(d["nod"])
        self._say(eng, self.L("rank", OMIKUJI["rank"][d["yomi"]], rank=d["yomi"]), d["emo"], face=d["face"], raw={"speed": 1.1, "pitch": 0.08, "intonation": 1.8, "volume": 1.3} if d["yomi"] == "だいきち" else None)
        self._say(eng, tone_text(self, d["msg"]), d["emo"] if d["yomi"] != "すえきち" else "joy", face="happy")
        self._say(eng, self._o("lucky", color=d["color"], item=d["item"]), "joy", face="happy")
        self.history.append({"q": f"{d['rank']}  {d['color']} / {d['item']}", "ok": d["yomi"] in ("だいきち", "ちゅうきち")})
        self.stat = f"ひいた かず {self.draws} ・ 大吉 {self.big_n}"

    def _run(self, eng) -> None:
        self._say(eng, self._o("intro"), "excited", face="happy")
        idle = 0
        while not self._stop.is_set():
            self._draw_once(eng)
            if self._stop.is_set():
                return
            if self.draws in (3, 6, 10) and not self._stop.is_set():
                self._say(eng, self._o("more", n=self.draws), "joy", face="happy")
            if not self._again(eng):
                self._say(eng, self._o("bye"), "joy", face="happy")
                return


def tone_text(game, text: str) -> str:
    """台詞の口調の記号を、遊び中のキャラの言い方にして返す(おみくじの ひとこと用)。"""
    from playfx import tone
    return tone(game.name, text, game.pick)


# ---------------------------------------------------------------- ものまねボイス
# (名前, 短い名前, 声の調子, 顔, 感情)。VOICEVOX の速さ・高さ・抑揚・音量だけで作る(声は、いつものキャラのまま)
STYLES = [
    ("はやい こえ", "はやい", {"speed": 1.55, "pitch": 0.02, "intonation": 1.15, "volume": 1.1}, "giggle"),
    ("ゆっくりの こえ", "ゆっくり", {"speed": 0.6, "pitch": -0.03, "intonation": 0.85, "volume": 1.0}, "thinking"),
    ("たかい こえ", "たかい", {"speed": 1.12, "pitch": 0.15, "intonation": 1.4, "volume": 1.1}, "happy"),
    ("ひくい こえ", "ひくい", {"speed": 0.82, "pitch": -0.15, "intonation": 0.8, "volume": 1.2}, "neutral"),
    ("ロボットの こえ", "ロボット", {"speed": 0.92, "pitch": -0.02, "intonation": 0.0, "volume": 1.1}, "thinking"),
    ("あかちゃんの こえ", "あかちゃん", {"speed": 0.78, "pitch": 0.15, "intonation": 1.7, "volume": 1.0}, "happy"),
    ("おおげさな こえ", "おおげさ", {"speed": 1.0, "pitch": 0.06, "intonation": 2.0, "volume": 1.3}, "surprise"),
    ("ひそひそ こえ", "ひそひそ", {"speed": 0.9, "pitch": 0.0, "intonation": 0.7, "volume": 0.45}, "thinking"),
    ("おじいさんの こえ", "おじいさん", {"speed": 0.68, "pitch": -0.15, "intonation": 0.9, "volume": 1.15}, "neutral"),
    ("げんきな こえ", "げんき", {"speed": 1.25, "pitch": 0.09, "intonation": 1.7, "volume": 1.25}, "giggle"),
    ("ねむい こえ", "ねむい", {"speed": 0.55, "pitch": -0.08, "intonation": 0.6, "volume": 0.8}, "sad"),
    ("うたうような こえ", "うた", {"speed": 0.8, "pitch": 0.1, "intonation": 2.0, "volume": 1.15}, "happy"),
]
MIMIC_MAX = 5
# まねしない言葉(悪口・おとなの言葉)。「うんち」などの子どもが好きなおかしな言葉は、そのまま まねする
INSULT = re.compile(r"ばか|バカ|馬鹿|あほ|アホ|きもい|キモい|うざい|ウザい|くそ|クソ|ちんちん|おっぱい|まんこ|ちんこ|うるさい|だまれ|しね|死ね|ぶす|ブス|でぶ|デブ|はげ|ハゲ")
MIMIC = {
    "intro": ["ものまねボイスを しよう！なにか ことばを 言ってね。いろんな こえで まねっこ するよ！", "まねっこ あそび{d}！ぼくに ことばを おしえてね。へんな こえで まねするよ！",
              "ものまね たいかい{d}！すきな ことばを 言ってみて。ぼくが まねするよ！", "おうむがえし あそび{d}！なんでも いいよ、ことばを 言ってね。"],
    "announce": ["{label}で いくよ！", "{label}で まねっこ！", "つぎは {label}！", "こんどは {label}で どうぞ！", "{label}に へんしん！"],
    "react": ["どう？にてた？", "おもしろい でしょ？", "ふふふ、たのしい！", "どうかな？", "へんな こえ{d}！", "にてるかな？"],
    "next": ["つぎの ことばを 言ってね！「もういっかい」で べつの こえで まねするよ！", "ほかの ことばも 言ってみて！", "つぎは なんて 言う？", "もっと まねっこ するよ！ことばを どうぞ！",
             "「もういっかい」って 言うと、べつの こえで まねするよ！"],
    "ok_word": ["いいね！", "おもしろい ことば{d}！", "よーし、まねするよ！", "それ いいね！", "まかせて！"],
    "redirect": ["それは ちょっと むずかしいな。ほかの ことばで あそぼう！", "うーん、ほかの ことばを 言ってみて！すきな たべものとか、どうぶつとか！", "ことばあそびを しよう。すきな ことばを 言ってね！"],
    "done": ["5つ まねしたね！たくさん あそんだ{n}！", "いっぱい まねしたね！おもしろかった{n}！", "5つ できたね！ものまね めいじん{d}！"],
    "bye": ["たのしかった{n}！また ものまね しようね！", "いろんな こえで あそべて うれしかった{n}！またね！", "ありがとう！また まねっこ しようね！"],
}


def clean_echo(text: str) -> str:
    """まねする言葉を整える(前後の記号・空白を落とし、長すぎるときは切る)。"""
    t = re.sub(r"[\s]+", " ", (text or "").strip())
    t = t.strip("、。,.!！?？「」『』 ")
    return t[:24]


def short_text(t: str, n: int = 4) -> str:
    return t if len(t) <= n else t[:n] + "…"


class Mimic(Play):
    KIND = "mimic"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.words = 0
        self.echoes = 0
        self.order: list = []
        self.last = ""

    def _m(self, scene: str, **kw) -> str:
        return self.L(scene, MIMIC[scene], **kw)

    def _next_style(self):
        if not self.order:
            self.order = list(range(len(STYLES)))
            self.rnd.shuffle(self.order)
        return STYLES[self.order.pop()]

    def _echo(self, eng, text: str) -> None:
        label, short, raw, face = self._next_style()
        self.echoes += 1
        self.stats["mimic_n"] += 1
        self._save()
        self.big = f"{short}の こえ"
        self.qtext = f"「{text}」を「{label}」で"
        self.atext = f"声: {label}"
        s = self._sess(0) if self.robots else None
        if s is not None:
            board.show(s, "ものまね", a=("きみ", short_text(text)), b=(short, "♪"), foot=f"{label}で まねっこ", ms=7000)
        self._say(eng, self._m("announce", label=label), "joy", face="listening")
        if face in ("happy", "giggle", "surprise"):
            self._nod(1)
        self._say(eng, text, "joy", face=face, raw=raw)
        self.history.append({"q": f"「{text}」→ {label}", "ok": True})
        self.stat = f"まねした ことば {self.words} / {MIMIC_MAX} ・ まねっこ {self.echoes}かい"

    def _run(self, eng) -> None:
        from kidchat import SENSITIVE, worth_answering
        self._say(eng, self._m("intro"), "excited", face="happy")
        idle = unclear = 0
        while not self._stop.is_set():
            text = self._wait(IDLE_WAIT)
            if text is None:
                return
            if not text:
                idle += 1
                if idle >= IDLE_MAX:
                    self.msg = "またあそぼうね！"
                    self._say(eng, self._m("bye"), "joy")
                    return
                self._say(eng, self.L("idle"), "neutral")
                continue
            idle = 0
            if QUIT.search(text) and not AGAIN.search(text):
                self.msg = "またあそぼうね！"
                self._say(eng, self.L("quit"), "joy")
                return
            if AGAIN.search(text) and self.last:                    # 「もういっかい」: 同じ ことばを、べつの こえで
                self._echo(eng, self.last)
                continue
            word = clean_echo(text)
            if SENSITIVE.search(word) or INSULT.search(re.sub(r"[ー\s、。]", "", word)):          # 子どもに まねさせない言葉は、まねせず、ことば遊びに もどす
                self._say(eng, self._m("redirect"), "joy", face="happy")
                continue
            if not worth_answering(word):                           # 雑音・あいづち・動画の決まり文句
                unclear += 1
                if unclear >= 3:
                    unclear = 0
                    self._say(eng, self.L("unclear"), "doubt", face="doubt")
                continue
            unclear = 0
            self.last = word
            self.words += 1
            self.score = self.words
            self._echo(eng, word)
            if self.words >= MIMIC_MAX:
                self.stats["mimic_rounds"] += 1
                self._save()
                self.big = "おしまい！"
                self._say(eng, self._m("done"), "excited", face="giggle")
                self._say(eng, self._m("bye"), "joy", face="happy")
                self.msg = "またあそぼうね！"
                return
            if self.rnd.random() < 0.7:
                self._say(eng, self._m("react"), "joy", face="happy")
            if self.words in (1, 2, 4) or self.rnd.random() < 0.3:
                self._say(eng, self._m("next"), "joy", face="listening")

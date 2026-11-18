"""カメラを使う遊び(じゃんけん・あっち向いてホイ)で、検知が難しいときに、子どもへ「声と機器の画面」で指示する部品。

画像から「いまの見え方」を決める(純関数。テストしやすいよう、顔検出は外から渡す):
  ok(だいじょうぶ) / near(近すぎ) / far(遠すぎ) / none(顔が見えない) / dark(暗い) / side(端に寄りすぎ・横向き)
  手が見つからないときは、原因を hand(顔は見えるが手がない) / hidden(手が近すぎて顔まで隠れた) などで言い分ける。
  顔検出が使えない・画像が来ない(unknown)ときは、指示しない(いままでどおり)。
指示のしかた(Coach): 同じ指示を連発しない(クールダウン)・同じ状態は 2 回まで・それでも直らなければ「声でおしえてね」にして声だけで進める。
"""
from __future__ import annotations

import time

import numpy as np

# ---- しきい値(画像の横幅に対する、顔の横幅の比。カメラは約 55° の画角。顔まで 25cm で約 0.5、1.2m で約 0.1)
NEAR_RATIO = 0.50           # これより大きい=近すぎ
FAR_RATIO = 0.10            # これより小さい=遠すぎ
SIDE_CX = 0.60              # 顔の中心が、画面の中心から(-1..1)この値より外=端に寄りすぎ
CUT_FRAC = 0.20             # 顔の枠が、画像の左右からはみ出している割合がこれより大きい=端に寄りすぎ
DARK_MEAN = 40.0            # 画像の平均の明るさ(0..255)がこれより暗い=暗い
MIN_EYE_PX = 8.0            # 目の間隔(ピクセル)がこれより小さい=横向き・小さすぎて、顔の向きを見分けられない
FACE_MAJORITY = 0.5         # 顔が見えた画像の割合がこれ以上なら、顔あり

# ---- 指示のしかた
COOLDOWN_S = 10.0           # 同じ指示のあと、次の指示までの最短秒数
MAX_SAME = 2                # 同じ状態の指示は 2 回まで(それでも直らなければ、声だけで進める)
GRAB_S = 2.0                # 確認のために画像を集める最長の秒数
GRAB_N = 3                  # これだけ集まったら、すぐ判定する
FIX_WAIT_S = 3.0            # 指示のあと、直すのを待つ秒数
RECHECK_S = 2.0             # クールダウン中に、見直す間隔
PRE_MAX_S = 26.0            # 事前の確認に使う最長の秒数(超えたら、そのまま始める)
HAND_BUDGET = 2             # 1 回の手待ちの中で、指示してやり直す最大回数

STATES = ("ok", "near", "far", "none", "dark", "side")
LABEL = {"ok": "ちょうどいい", "near": "近すぎ", "far": "遠すぎ", "none": "顔が見えない", "dark": "暗い", "side": "端によっている",
         "hand": "手が見えない", "hidden": "手が近すぎ", "unknown": ""}

# 状態 → (声の台詞(記号は playfx.tone)、機器の画面の大きな字、画面の一言)
GUIDE = {
    "near": (["ちかすぎるよ！すこし うしろに さがってね。", "カメラに ちかいみたい{d}！ちょっと はなれてね。", "おっと、ちかづきすぎ{d}！すこし さがって！"],
             "さがってね", "カメラから すこし はなれてね"),
    "far": (["ちょっと とおいよ！もうすこし ちかくに きてね。", "とおくて みえにくいよ！カメラに ちかづいてね。", "もうちょっと こっちに きてね{v}！"],
            "ちかづいてね", "カメラに もうすこし ちかづいてね"),
    "none": (["あれ、みえないよ！カメラの まえに きてね。", "かおが みえないよ！こっちを むいてね。", "どこかな？カメラの まえに きてね！"],
             "まえに きてね", "カメラの まえで こっちを みてね"),
    "dark": (["ちょっと くらいよ！あかるい ほうを むいてね。", "くらくて みえにくいよ！あかるい ところに きてね。", "あかるい ほうに むいてね{v}！"],
             "あかるく", "あかるい ほうを むいてね"),
    "side": (["カメラの まんなかに きてね！", "すこし よこに いるよ！まんなかで こっちを みてね。", "まんなかで、こっちを みてね{v}！"],
             "まんなかへ", "カメラの まんなかで こっちを みてね"),
    "hand": (["てが みえないよ！てを カメラの まえに だしてね。", "てを もうすこし まえに だして、カメラに みせてね！", "ぐー、ちょき、ぱーを カメラに むけてね{v}！"],
             "てを みせて", "てを カメラの まえに だしてね"),
    "hidden": (["てが ちかすぎるかも！すこし はなして みせてね。", "てを すこし とおざけて、かおも みせてね{v}！", "カメラから てを すこし はなしてね！"],
               "てを はなして", "てを すこし はなしてね"),
}
GIVEUP = {
    "jan": (["うまく みえないから、こえで おしえてね。ぐー、ちょき、ぱー！", "みえにくいから、こえで 言ってね{v}。ぐー、ちょき、ぱー！"], "こえで おしえてね", "ぐー・ちょき・ぱー を こえで 言ってね"),
    "face": (["うまく みえないから、むいた ほうを こえで おしえてね。うえ、した、ひだり、みぎ！", "みえにくいから、あっちむいた むきを こえで 言ってね{v}！"], "こえで おしえてね", "むいた むきを こえで 言ってね"),
}
TITLE = "カメラの ちょうせい"


# ---------------------------------------------------------------- 画像 → 状態(純関数)
def face_reading(f, shape, bright: float) -> dict:
    """1 枚の画像の読み取り。f は Face.find の結果 (x, y, w, h, row) か None。shape は画像の (高さ, 幅, ...)。"""
    h, w = int(shape[0]), int(shape[1])
    out = {"face": False, "bright": float(bright), "ratio": 0.0, "cx": 0.0, "cut": 0.0, "eye": None}
    if f is None or w <= 0:
        return out
    x, _y, fw, _fh = (float(v) for v in f[:4])
    if fw <= 0:
        return out
    out.update({"face": True, "ratio": fw / w, "cx": (x + fw / 2.0) / w * 2.0 - 1.0, "cut": (max(0.0, -x) + max(0.0, x + fw - w)) / fw})
    row = f[4] if len(f) > 4 else None
    try:
        if row is not None:
            out["eye"] = abs(float(row[6]) - float(row[4]))
    except (IndexError, TypeError, ValueError):
        pass
    return out


def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def decide(readings: list, need_eyes: bool = False) -> str:
    """複数の画像の読み取り → 状態(ok/near/far/none/dark/side、画像が無ければ unknown)。多数決と中央値で、1 枚のぶれに引きずられない。"""
    n = len(readings)
    if n == 0:
        return "unknown"
    faces = [r for r in readings if r["face"]]
    bright = sum(r["bright"] for r in readings) / n
    if len(faces) < n * FACE_MAJORITY or not faces:
        return "dark" if bright < DARK_MEAN else "none"
    ratio = _median([r["ratio"] for r in faces])
    cx = _median([r["cx"] for r in faces])
    cut = _median([r["cut"] for r in faces])
    if ratio > NEAR_RATIO:
        return "near"
    if ratio < FAR_RATIO:
        return "far"
    if abs(cx) > SIDE_CX or cut > CUT_FRAC:
        return "side"
    if need_eyes:
        eyes = [r["eye"] for r in faces if r["eye"] is not None]
        if eyes and _median(eyes) < MIN_EYE_PX:
            return "side"
    return "ok"


def can_see(face) -> bool:
    """顔検出が使えるか(cv2 とモデルがあるか)。使えなければ、指示はしない。"""
    return face is not None and getattr(face, "cv2", None) is not None


def analyze(face, jpegs: list, need_eyes: bool = False, last: int = 6) -> dict:
    """JPEG の列(新しいほうから last 枚)→ {state, label, n, n_face, ratio, bright}。face は Face(decode/find)。使えない・画像が無ければ unknown。"""
    if not can_see(face):
        return {"state": "unknown", "label": "", "n": 0, "n_face": 0, "ratio": 0.0, "bright": 0.0}
    rs = []
    for j in list(jpegs)[-last:]:
        try:
            img = face.decode(j)
            if img is None:
                continue
            bright = float(np.mean(img[::4, ::4]))
            rs.append(face_reading(face.find(img), img.shape, bright))
        except Exception:  # noqa: BLE001 - 1 枚の失敗で止めない
            continue
    st = decide(rs, need_eyes)
    fs = [r for r in rs if r["face"]]
    return {"state": st, "label": LABEL.get(st, ""), "n": len(rs), "n_face": len(fs),
            "ratio": round(_median([r["ratio"] for r in fs]), 3) if fs else 0.0, "bright": round(sum(r["bright"] for r in rs) / len(rs), 1) if rs else 0.0}


def hand_reason(face_state: str) -> "str | None":
    """手が見つからなかったときの原因(声かけの種類)。顔が見えて位置もよい=手が見えない(hand)。顔が見えない=手が近すぎて隠れた(hidden)。
    顔が使えない(unknown)ときは None(指示しない)。"""
    if face_state == "unknown":
        return None
    return {"ok": "hand", "side": "hand", "none": "hidden"}.get(face_state, face_state)


# ---------------------------------------------------------------- 台詞・画面
def line(name: str, state: str, pick=None) -> str:
    """状態 state の指示の台詞(キャラの口調つき)。"""
    from playfx import tone
    opts = GUIDE[state][0]
    s = (pick or (lambda _k, o: o[0]))(f"cam:{state}", opts)
    return tone(name, s, pick)


def giveup_line(name: str, kind: str, pick=None) -> str:
    from playfx import tone
    opts = GIVEUP.get(kind, GIVEUP["jan"])[0]
    return tone(name, (pick or (lambda _k, o: o[0]))(f"cam:giveup:{kind}", opts), pick)


def board_text(state: str) -> "tuple[str, str]":
    """(画面の大きな字, 一言)。"""
    g = GUIDE[state]
    return g[1], g[2]


def giveup_board(kind: str) -> "tuple[str, str]":
    g = GIVEUP.get(kind, GIVEUP["jan"])
    return g[1], g[2]


def all_lines(name: str) -> list:
    """先読み(warm.py)用: 指示の台詞をすべて(キャラ name の口調で)。"""
    from playfx import tone
    out = []
    for st, (opts, _b, _f) in GUIDE.items():
        out += [tone(name, o) for o in opts]
    for _k, (opts, _b, _f) in GIVEUP.items():
        out += [tone(name, o) for o in opts]
    return out


# ---------------------------------------------------------------- 指示の出し方
class Coach:
    """指示を出してよいかを決める。クールダウン(連発しない)・同じ状態は MAX_SAME 回まで・それでも直らなければ giveup(声だけにする)。"""

    def __init__(self, clock=time.time, cooldown: "float | None" = None, max_same: "int | None" = None):
        self.clock = clock
        self.cooldown = COOLDOWN_S if cooldown is None else cooldown        # 定数は作るときに読む(試験で差し替えられる)
        self.max_same = MAX_SAME if max_same is None else max_same
        self.count: dict = {}
        self.last = -1e9
        self.gave_up = False
        self.blind = 0              # 画像・顔検出が使えなかった回数(続くなら、確認を省く)
        self._okrun = 0

    @property
    def skip(self) -> bool:
        """もう確認しなくてよい(声だけにした / 見る手段がない)。"""
        return self.gave_up or self.blind >= 2

    def next(self, state: str) -> str:
        """戻り値: ok(指示不要) / say(指示する) / wait(クールダウン中) / giveup(声だけにする) / muted(すでに声だけ)"""
        if self.gave_up:
            return "muted"
        if state == "unknown":
            self.blind += 1
            return "ok"
        self.blind = 0
        if state == "ok":
            self._okrun += 1
            if self._okrun >= 2:                    # 続けて 2 回よく見えたら、直ったとして数え直す
                self.count.clear()
            return "ok"
        self._okrun = 0
        now = self.clock()
        if now - self.last < self.cooldown:
            return "wait"
        if self.count.get(state, 0) >= self.max_same:
            self.gave_up = True
            return "giveup"
        self.count[state] = self.count.get(state, 0) + 1
        self.last = now
        return "say"

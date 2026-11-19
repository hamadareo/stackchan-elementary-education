"""子ども向けの遊びの共通部品: 台詞の選び方(直近と重ならない)、キャラの口調、聞き取りのゆれの吸収。

台詞は「場面ごとに複数」持ち、同じ言い方が続かないよう、直近に使ったものを避けて選ぶ。
キャラ(ずんだ/めたん/つむぎ)の口調は、台詞の中の記号で切り替える:
  {d} … 名詞のあと(だよ / なのだ / です)      例: 「いい しょうぶ{d}！」
  {n} … 形容詞のあと(よ / のだ / です)         例: 「たのしかった{n}！」
  {v} … 動詞(ふつうの言い方)のあと(よ / のだ / ね)  例: 「いっぱい あそんだ{v}！」
  {me} … 自分(ぼく / わたし)                    例: 「{me}の かち{d}！」
  {cheer} … そのキャラの「やったね」系の一言   {oops} … 「ざんねん・くやしい」系の一言
"""
from __future__ import annotations

import re
import unicodedata

TONE = {"ずんだ": {"d": "なのだ", "n": "のだ", "v": "のだ", "me": "ぼく"}, "めたん": {"d": "です", "n": "です", "v": "ね", "me": "わたし"}, "つむぎ": {"d": "だよ", "n": "よ", "v": "よ", "me": "わたし"}}
CHEER = {"ずんだ": ["やったのだ！", "すごいのだ！", "さいこうなのだ！", "うれしいのだ！", "びっくりなのだ！", "かんぺきなのだ！", "てんさいなのだ！"],
         "めたん": ["すてきです！", "すばらしいです！", "おみごとです！", "うれしいです！", "さすがですね！", "かんぺきです！", "おみごとですね！"],
         "つむぎ": ["やったね！", "すごーい！", "さいこう！", "うれしいな！", "わあ、すごいね！", "かんぺきだね！", "ばっちりだね！"]}
OOPS = {"ずんだ": ["くやしいのだ！", "ざんねんなのだ！", "おしいのだ！", "あちゃーなのだ！", "むむむ、なのだ！", "うーん、まいったのだ！"],
        "めたん": ["くやしいです。", "ざんねんです。", "おしいですね。", "あらら、ですね。", "むむ、やりますね。", "まいりました。"],
        "つむぎ": ["くやしい！", "ざんねん！", "おしいなあ！", "あれれ！", "むー、やるね！", "まいったなあ！"]}


# 声に出すときの読み: VOICEVOX が読みまちがえやすい漢字(声・言・手・勝ち負けなど)を、ひらがなにする。画面の字幕は そのまま。
_YOMI = [("言葉", "ことば"), ("言って", "いって"), ("言う", "いう"), ("言い", "いい"), ("言わ", "いわ"), ("言え", "いえ"), ("声", "こえ"), ("お手あげ", "おてあげ"),
         ("勝ち", "かち"), ("勝", "かち"), ("負け", "まけ"), ("見せて", "みせて"), ("見せ", "みせ"), ("見事", "みごと"), ("見えな", "みえな"), ("出して", "だして"), ("出した", "だした"), ("出したら", "だしたら"),
         ("出す", "だす"), ("出て", "でて"), ("出た", "でた"), ("出ない", "でない"), ("最後", "さいご"), ("文字", "もじ"), ("楽しかった", "たのしかった"), ("楽しい", "たのしい"),
         ("思いつかない", "おもいつかない"), ("聞こえ", "きこえ"), ("もう一回", "もういっかい"), ("一回", "いっかい"), ("少し", "すこし"), ("長い", "ながい"), ("全部", "ぜんぶ"), ("調子", "ちょうし")]


def yomi(text: str) -> str:
    t = text
    for a, b in _YOMI:
        t = t.replace(a, b)
    return re.sub(r"(?<![一-鿿])手(?![一-鿿])", "て", t)


CHUNK_MIN = 40                    # これより短い台詞は、分けない
CHUNK_PART_MIN = 8                # これより短い断片は、前の文にくっつける


def split_chunks(text: str) -> list:
    """長い台詞を、文の切れ目(。！？)で分ける。最初の文だけ先に作って・送って鳴らし始めれば、待ち時間が短くなる。
    先読み(warm.py)も同じ分け方で作り置きするので、ここを変えたら先読みも作り直しになる。"""
    t = (text or "").strip()
    if len(t) < CHUNK_MIN:
        return [t] if t else []
    parts = [x for x in re.findall(r"[^。！？!?]+[。！？!?]*", t) if x.strip()]
    out: list = []
    for x in parts:
        if out and (len(x) < CHUNK_PART_MIN or len(out[-1]) < CHUNK_PART_MIN):
            out[-1] += x
        else:
            out.append(x)
    return out or [t]


def tone(name: str, text: str, pick=None) -> str:
    """台詞の記号({d} {n} {cheer} {oops})を、キャラの言い方に置きかえる。"""
    t = TONE.get(name, TONE["つむぎ"])
    out = text.replace("{d}", t["d"]).replace("{n}", t["n"]).replace("{v}", t["v"]).replace("{me}", t["me"])
    if "{cheer}" in out:
        out = out.replace("{cheer}", (pick or (lambda k, o: o[0]))("cheer:" + name, CHEER.get(name, CHEER["つむぎ"])))
    if "{oops}" in out:
        out = out.replace("{oops}", (pick or (lambda k, o: o[0]))("oops:" + name, OOPS.get(name, OOPS["つむぎ"])))
    return out


class Picker:
    """候補から 1 つ選ぶ。同じ場面(key)で直近に使ったものは避ける(候補が少ないときは、すぐ前のものだけ避ける)。"""

    def __init__(self, rnd, keep: int = 3):
        self.rnd, self.keep = rnd, keep
        self.recent: dict = {}

    def __call__(self, key: str, options) -> str:
        opts = list(options)
        if len(opts) == 1:
            return opts[0]
        rec = self.recent.setdefault(key, [])
        keep = max(1, min(self.keep, len(opts) - 1))
        pool = [o for o in opts if o not in rec] or opts
        o = self.rnd.choice(pool)
        rec.append(o)
        del rec[:-keep]
        return o


# ---------------------------------------------------------------- 場面ごとの共通の台詞
COMMON = {
    "bye": ["たのしかった{n}！また あそんでね！ばいばーい！", "いっぱい あそべて うれしかった{n}！またね！", "ありがとう！とっても たのしかった{n}！ばいばーい！",
            "また いつでも あそぼう！まってる{v}！", "たのしい じかん だった{v}！またね、ばいばい！", "あそんでくれて ありがとう！またね！",
            "もう おしまいだね。また あそぼうね！", "たのしかった！つぎも あそぼうね！ばいばーい！"],
    "quit": ["うん、わかった{v}！また あそぼうね！", "いいよ！また こんど あそぼう{v}！", "はーい！いつでも また あそぼうね！", "わかった{v}！たのしかったよ、ありがとう！",
             "うん！またね！いつでも よんでね！", "おっけー！また あそぼうね！"],
    "idle": ["あれ？きこえなかったかな。", "ゆっくりで いい{v}。まってる{v}！", "だいじょうぶ、まってる{v}。", "おーい、いるかな？", "どうしたの？ゆっくりで いいよ。"],
    "again_q": ["もういっかい、やる？", "もういっかい あそぶ？「やる」か「やめる」って 言ってね！", "つづき、やる？", "もっと あそぶ？", "もういっかい いってみる？"],
    "unclear2": ["ゆっくり おおきな こえで おしえてね！", "ちかくで おはなし してみてね！", "すきな ことばで いいよ。おしえてね！", "ひとつずつ、ゆっくり どうぞ！"],
    "unclear": ["ごめんね、もういっかい 言って！", "んー、きこえなかった！もういっかい！", "もうすこし おおきな こえで 言ってね！", "もういっかい おねがい！"],
}

# ---------------------------------------------------------------- 聞き取りのゆれ
QUIT = re.compile(r"やめ|おわり|終わり|おしまい|もういい|もう終|あそばない|ばいばい|バイバイ|やだ|つまんない")
YES = re.compile(r"はい|うん|やる|やろ|もういっかい|もう一回|もういちど|もう1回|いいよ|おねがい|お願い|おっけ|オッケー|ok|する|やりたい|もっと|もう一度|いく|ひく|引く|つづき|続き")
NO = re.compile(r"いいえ|やめ|おわり|終わり|やらない|ううん|もういい|いやだ|いや|ばいばい|バイバイ|おしまい|いらない|もうやめ")
AGAIN = re.compile(r"もういっかい|もう一回|もういちど|もう1回|もう一度|もっかい|もう1ぺん|もう一遍|もっと|べつの|別の|ちがう こえ|ちがうこえ|つぎ|次")


def fold(text: str) -> str:
    """全角→半角・かたかな→ひらがな・記号と空白と のばす棒を落とす(聞き取りのゆれをそろえる)。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)
    return re.sub(r"[\s、。,.!?！？・「」『』~〜…'\"ー_\-]+", "", t)


class Stage:
    """遊びの演出の部品(首・画面の表示カード)。Shiritori / Janken / Play が使う。
    首の動きは小さめ・ゆっくりで、動かした分を覚えておき、終わったら必ず正面へ戻す(_front)。"""
    PAN_MAX, TILT_MAX = 350, 160          # 首の動きの積算の上限(0.1 度)
    _dp = 0
    _dt = 0

    def _servo(self, dpan: int = 0, dtilt: int = 0, speed: int = 260) -> None:
        dpan = max(-self.PAN_MAX - self._dp, min(self.PAN_MAX - self._dp, int(dpan)))
        dtilt = max(-self.TILT_MAX - self._dt, min(self.TILT_MAX - self._dt, int(dtilt)))
        if not dpan and not dtilt:
            return
        s = self._sess(0) if getattr(self, "robots", None) else None
        if s is None:
            return
        try:
            s.send({"t": "servo", "dpan": dpan, "dtilt": dtilt, "speed": int(speed)})
            self._dp += dpan
            self._dt += dtilt
        except Exception:  # noqa: BLE001
            pass

    def _front(self, speed: int = 240) -> None:
        """首を、動かす前の向き(正面)に戻す。"""
        if self._dp or self._dt:
            s = self._sess(0) if getattr(self, "robots", None) else None
            if s is not None:
                try:
                    s.send({"t": "servo", "dpan": -self._dp, "dtilt": -self._dt, "speed": int(speed)})
                except Exception:  # noqa: BLE001
                    pass
            self._dp = self._dt = 0

    FAST = False                           # テストでは True(演出の待ち時間を省く)

    def _sleep(self, sec: float) -> None:
        import time
        if self.FAST:
            return
        t0 = time.time()
        stop = getattr(self, "_stop", None)
        while time.time() - t0 < sec and not (stop is not None and stop.is_set()):
            time.sleep(0.05)

    def _nod(self, n: int = 2) -> None:
        """うれしいとき: こくこくと うなずく(小さく)。"""
        for _ in range(n):
            self._servo(dtilt=-70, speed=420)
            self._sleep(0.28)
            self._servo(dtilt=70, speed=420)
            self._sleep(0.28)

    def _wobble(self) -> None:
        """ざんねん・こまったとき: 首を ちょっと かしげる。"""
        self._servo(dpan=90, speed=300)
        self._sleep(0.45)
        self._servo(dpan=-90, speed=300)
        self._sleep(0.3)

    def _board(self, title: str = "", a=("", ""), b=("", ""), foot: str = "", ms: int = 6000, a_arrow: str = "", b_arrow: str = "",
               a_icon: str = "", b_icon: str = "", fx: str = "") -> None:
        """機器の画面に、遊びの表示カード(出した手・言葉・向き・運勢)を出す。古いファームウェアでは何も起きない。"""
        import board
        self.board_state = {"title": title, "aw": a[0], "ab": a[1], "bw": b[0], "bb": b[1], "ai": a_icon, "bi": b_icon, "aa": a_arrow, "ba": b_arrow, "foot": foot, "fx": fx}   # 管理画面にも、同じ絵を見せる
        s = self._sess(0) if getattr(self, "robots", None) else None
        if s is not None:
            board.show(s, title, a=a, b=b, foot=foot, ms=ms, a_arrow=a_arrow, b_arrow=b_arrow, a_icon=a_icon, b_icon=b_icon, fx=fx)

    def _board_clear(self) -> None:
        import board
        self.board_state = None
        s = self._sess(0) if getattr(self, "robots", None) else None
        if s is not None:
            board.clear(s)

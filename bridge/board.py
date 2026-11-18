"""機器の画面に出す「遊びの表示カード」(じゃんけんの手・おみくじの結果・今の単語など)を送る。

ファームウェアの {"t":"board"} を使う。見出し(title)・左(aw=名前, ab=大きな字, aa=矢印)・右(bw, bb, ba)・一言(foot)・表示ミリ秒(ms)。
矢印は up/down/left/right。手の絵(a_icon/b_icon)は gu/choki/pa(絵があるときは大きな字は出ない)。fx="win" で、きらきらの演出。左だけ指定すると、中央に大きく出す。古いファームウェアは無視する(何も起きない)。
"""
from __future__ import annotations


def show(sess, title: str = "", a=("", ""), b=("", ""), foot: str = "", ms: int = 6000, a_arrow: str = "", b_arrow: str = "",
         a_icon: str = "", b_icon: str = "", fx: str = "") -> None:
    """a / b は (名前, 大きな字)。例: show(sess, "じゃんけん", a=("ずんだ", "ぐー"), b=("きみ", "ぱー"), foot="きみの かち！")"""
    try:
        sess.send({"t": "board", "title": title, "aw": a[0], "ab": a[1], "aa": a_arrow, "bw": b[0], "bb": b[1], "ba": b_arrow, "ai": a_icon, "bi": b_icon, "fx": fx, "foot": foot, "ms": int(ms)})
    except Exception:  # noqa: BLE001 - 表示が出せなくても、遊びは続ける
        pass


def clear(sess) -> None:
    try:
        sess.send({"t": "board", "clear": True})
    except Exception:  # noqa: BLE001
        pass

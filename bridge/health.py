"""授業の前に確かめる「準備チェック」: 機器・電池・声・AI・聞き取り・教材モードを、一覧にする(管理画面に出す)。

ネットワークを見る項目は、画面が何度も問い合わせても重くならないよう、数秒ぶん覚えておく。
ok: True=だいじょうぶ / False=直したほうがよい / None=参考(どちらでもよい)
"""
from __future__ import annotations

import os
import time

EXPECTED = int(os.environ.get("SC_DEVICES", "3"))        # 使う機器の台数
CACHE_S = 6.0
_cache: dict = {"t": 0.0, "v": []}


def _get(url: str, timeout: float = 0.8) -> bool:
    try:
        import requests
        return bool(requests.get(url, timeout=timeout).ok)
    except Exception:  # noqa: BLE001
        return False


def checks(hub, eng) -> list:
    now = time.time()
    if now - _cache["t"] < CACHE_S and _cache["v"]:
        out = [dict(c) for c in _cache["v"]]
        _fast(hub, out)
        return out
    out = []
    live = list(hub.live()) if hub is not None else []
    usb = sum(1 for x in live if "USB" in str(getattr(x, "link", "") or getattr(x, "via", "")))
    out.append({"id": "devices", "label": "機器", "ok": len(live) >= EXPECTED,
                "note": f"{len(live)}台つながっています(使う台数 {EXPECTED})" + (f"・USB有線 {usb}台" if usb else "")
                        + ("" if len(live) >= EXPECTED else "。USB ケーブルとハブの電源を確かめてください")})
    batts = [b for b in (getattr(x, "batt", None) for x in live) if isinstance(b, (int, float))]
    low = [b for b in batts if b < 20]
    out.append({"id": "battery", "label": "電池", "ok": not low if batts else None,
                "note": ("電池が少ない機器があります(20%未満)。充電してください" if low else f"いちばん少ない機器で {int(min(batts))}%" if batts else "電池の情報がありません")})
    vurl = getattr(getattr(eng, "s", None), "voicevox_url", "") if eng is not None else ""
    vok = _get(vurl.rstrip("/") + "/version") if vurl else None
    out.append({"id": "voice", "label": "声(VOICEVOX)", "ok": vok, "note": "動いています" if vok else ("動いていません。hamada を確認してください" if vok is False else "確認できません")})
    llms = list(getattr(eng, "llm", []) or []) if eng is not None else []
    names = []
    for label, base, _m in llms:
        if _get(base.rstrip("/") + "/v1/models"):
            names.append(label.replace("このMac", "このサーバー"))
    out.append({"id": "llm", "label": "返事を作るAI", "ok": bool(names) if llms else None,
                "note": ("使える: " + "、".join(names)) if names else "どれにもつながりません(翻訳・おしゃべり以外の遊びは動きます)"})
    out.append({"id": "ear", "label": "聞き取り", "ok": True if eng is not None else None,
                "note": ("s109(高速)" if getattr(eng, "remote_ear", False) else "hamada のなか(インターネットなしで動作中)") if eng is not None else "準備中"})
    _cache["t"], _cache["v"] = now, [dict(c) for c in out]
    _fast(hub, out)
    return out


def _fast(hub, out: list) -> None:
    """毎回すぐ変わる項目(教材モード・出す遊び)は、覚えずに、いつも新しく入れる。"""
    out[:] = [c for c in out if c["id"] not in ("edu", "acts")]
    edu = bool(getattr(hub, "edu_on", False))
    acts = list(getattr(hub, "menu_acts", []) or [])
    out.append({"id": "edu", "label": "教材モード", "ok": edu, "note": "オン(遊びを選ぶまで、ロボットは話しません)" if edu else "オフです。子どもに遊ばせるときは、オンにしてください"})
    out.append({"id": "acts", "label": "出す遊び", "ok": len(acts) > 0, "note": f"{len(acts)}個" if acts else "ひとつも選ばれていません"})

"""StackChan の機器台帳。初めて見た機器は自動で登録(=自動セットアップ)し、名前・設定を覚える。

機器の ID は端末の MAC アドレス(hello の "id")。古いファームウェアは ID を送らないので、
その場合は IP アドレスから "ip-<IP>" を仮の ID にする(ファームウェアを更新したら、同じ IP の仮の台帳を引き継ぐ)。
台帳は ~/.sokutsu_devices.json(本人だけが読める)。
"""
from __future__ import annotations

import json
import os
import threading
import time


# 顔の種類(ファームウェアの zface::STYLE_NAME と同じ並び)。M5Stack-Avatar 同梱の顔(MIT ライセンス)
FACE_STYLES = ["標準", "まる目(OωO)", "ガーリー", "ガーリー(リボン)", "ピンクの小悪魔", "わんこ", "シンプル(o_o)", "ずんだもん(イラスト)"]
DEFAULT_FACE = [7, 7, 7]          # 3台ともずんだもんのイラスト(顔は管理画面の「機器」タブで機器ごとに変えられる)


class Registry:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("SC_DEVICES", os.path.expanduser("~/.sokutsu_devices.json"))
        self.devs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.load()

    def load(self) -> None:
        try:
            d = json.load(open(self.path))
            self.devs = {k: v for k, v in d.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.devs = {}

    def save(self) -> None:
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.devs, f, ensure_ascii=False)
        except OSError:
            pass

    def _next_name(self) -> str:
        used = {d.get("name") for d in self.devs.values()}
        n = 1
        while f"StackChan-{n}" in used:
            n += 1
        return f"StackChan-{n}"

    def touch(self, dev_id: str, ip: str = "", fw: str = "", now: float | None = None) -> "tuple[dict, bool]":
        """接続してきた機器を台帳に記録する。(記録, 初めての機器か) を返す。"""
        now = time.time() if now is None else now
        with self.lock:
            rec = self.devs.get(dev_id)
            new = rec is None
            if new:
                legacy = f"ip-{ip}" if ip and not dev_id.startswith("ip-") else ""
                if legacy and legacy in self.devs:           # ID なしの古い版で登録済みの機器 → 引き継ぐ
                    rec = self.devs.pop(legacy)
                    new = False
                else:
                    rec = {"first": now, "name": self._next_name(), "auto": True}
                rec["id"] = dev_id
                self.devs[dev_id] = rec
            rec["seen"], rec["ip"] = now, ip
            if fw:
                rec["fw"] = fw
            self.save()
            return dict(rec), new

    def get(self, dev_id: str) -> dict:
        return dict(self.devs.get(dev_id) or {})

    def set(self, dev_id: str, **kw) -> bool:
        with self.lock:
            rec = self.devs.get(dev_id)
            if rec is None:
                return False
            rec.update(kw)
            self.save()
            return True

    def rename(self, dev_id: str, name: str) -> bool:
        name = (name or "").strip()[:24]
        if not name or any(d.get("name") == name and k != dev_id for k, d in self.devs.items()):
            return False
        return self.set(dev_id, name=name)

    def forget(self, dev_id: str) -> bool:
        with self.lock:
            ok = self.devs.pop(dev_id, None) is not None
            if ok:
                self.save()
            return ok

    def all(self) -> list:
        return sorted((dict(v) for v in self.devs.values()), key=lambda r: r.get("first", 0))

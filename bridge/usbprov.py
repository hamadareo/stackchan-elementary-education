"""Ubuntu に USB で挿した StackChan を認識し、新品なら自動で書き込む(ブリッジの中で動く監視)。

- 認識: /sys/bus/usb/devices の Espressif(303a)から、機器の MAC(USB のシリアル番号)を読む。ポートを開く権限は要らない。
  MAC は機器の ID と同じなので、台帳にあれば「登録済み」、なければ「新しい機器」。
- 書き込み: 新しい機器で、USB ポートを開く権限(dialout)とイメージがあれば、esptool で書き込む。
  書き込んだ機器は再起動して Wi-Fi につながり、ブリッジに自動登録される(名前・音量などは自動設定)。
- 権限が無いときは書き込まず、管理画面に理由を出す(Mac の tools/provision.py でも書き込める)。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import threading
import time

ESP_VID = "303a"
SYSFS = os.environ.get("SC_SYSFS_USB", "/sys/bus/usb/devices")
IMAGE = os.environ.get("SC_FW_IMAGE", os.path.expanduser("~/stackchan/firmware/stackchan-factory.bin"))
RETRY_S = 180.0                    # 書き込みに失敗したら、この秒数あけてやり直す
MAX_TRIES = 3
GRACE_S = 25.0                    # 見つけてから、この秒数は書き込まない(USB で動いている機器の hello を待つ)


def scan() -> list:
    """USB につながっている Espressif の機器: [{"mac", "port", "product"}]。"""
    out = []
    for d in glob.glob(os.path.join(SYSFS, "*")):
        try:
            if open(os.path.join(d, "idVendor")).read().strip().lower() != ESP_VID:
                continue
            serial = open(os.path.join(d, "serial")).read().strip()
        except OSError:
            continue
        try:
            product = open(os.path.join(d, "product")).read().strip()
        except OSError:
            product = ""
        tty = sorted(glob.glob(os.path.join(d, "*", "tty", "tty*")))
        out.append({"mac": serial.replace(":", "").lower(), "port": "/dev/" + os.path.basename(tty[0]) if tty else "", "product": product})
    return out


def decide(dev: dict, registered: bool, access: bool, image: bool, attempts: int, last_try: float, now: float) -> "tuple[str, str]":
    """(状態, 説明)。状態: registered / waiting / no_access / no_image / ready(書き込む) / cooldown / gave_up"""
    if registered:
        return "registered", "登録済みの機器です"
    if not dev.get("port"):
        return "waiting", "USB のシリアルが現れるのを待っています"
    if not access:
        return "no_access", ("新しい機器ですが、USB ポートを開く権限がありません。管理者が一度だけ "
                             "`sudo usermod -aG dialout <ユーザー>` を実行し、再ログインしてください(Mac の tools/provision.py でも書き込めます)")
    if not image:
        return "no_image", "書き込み用のイメージがありません(Mac で deploy.sh を実行してください)"
    if attempts >= MAX_TRIES:
        return "gave_up", "書き込みに失敗しました。USB を挿し直して、RST ボタンを長押ししてください"
    if attempts and now - last_try < RETRY_S:
        return "cooldown", "書き込みに失敗しました。少し待ってやり直します"
    return "ready", "新しい機器を書き込みます"


class Watcher:
    def __init__(self, hub, reg):
        self.hub, self.reg = hub, reg
        self.state: dict = {}                       # mac → {"mac","port","status","msg","tries","t"}
        self.lock = threading.Lock()
        self._busy: set = set()
        self.release = None                          # 書き込む前に、その機器のシリアル接続を手放す(usblink が設定)
        self.resume = None

    def registered(self, mac: str) -> bool:
        return bool(self.reg.get(mac)) or self.hub.by_id(mac) is not None

    def step(self, now: "float | None" = None) -> None:
        now = time.time() if now is None else now
        devs = scan()
        seen = {d["mac"] for d in devs}
        with self.lock:
            for mac in [m for m in self.state if m not in seen]:
                del self.state[mac]                  # 抜かれた
        for d in devs:
            mac = d["mac"]
            with self.lock:
                st = self.state.get(mac)
                if st is None:
                    st = self.state[mac] = {"mac": mac, "port": d["port"], "status": "", "msg": "", "tries": 0, "t": 0.0, "seen": now}
                    known = self.registered(mac)
                    self.hub.event(f"USB に機器を検出: {mac[-6:]}({'登録済み' if known else '新しい機器'})")
                st["port"] = d["port"]
                if mac in self._busy:
                    continue
                access = bool(d["port"]) and os.access(d["port"], os.R_OK | os.W_OK)
                status, msg = decide(d, self.registered(mac), access, os.path.exists(IMAGE), st["tries"], st["t"], now)
                if st["status"] == "flashed" and status != "registered":
                    status, msg = "flashed", "書き込み完了。再起動して Wi-Fi につながると登録されます"
                if status == "ready" and now - st.get("seen", now) < GRACE_S:   # USB ですでに動いている機器が、自分で名乗る(hello)のを少し待つ(動いている機器を書き換えない)
                    status, msg = "waiting", "機器の応答を待っています"
                st["status"], st["msg"] = status, msg
                if status == "ready":
                    self._busy.add(mac)
                    st["tries"] += 1
                    st["t"] = now
                    st["status"], st["msg"] = "flashing", "書き込み中(約 1 分)…"
                    threading.Thread(target=self._flash, args=(mac, d["port"]), daemon=True).start()

    def reflash(self, mac: str = "") -> "tuple[bool, str]":
        """登録済みの機器を USB で書き換える(ファームウェアの更新)。mac が空なら、つながっている全機器を順に。
        書き込み中は、その機器のシリアル接続を一度手放し、終わると再接続する。"""
        if not os.path.exists(IMAGE):
            return False, "書き込み用のイメージがありません(Mac で deploy.sh を実行してください)"
        devs = [d for d in scan() if d["port"] and (not mac or d["mac"] == mac.replace(":", "").lower())]
        if not devs:
            return False, "USB でつながっている機器がありません" if not mac else "その機器は USB でつながっていません"
        if not os.access(devs[0]["port"], os.R_OK | os.W_OK):
            return False, "USB ポートを開く権限がありません(管理者が `sudo usermod -aG dialout <ユーザー>` を実行し、再ログインしてください)"
        todo = []
        with self.lock:
            for d in devs:
                if d["mac"] in self._busy:
                    continue
                self._busy.add(d["mac"])
                st = self.state.setdefault(d["mac"], {"mac": d["mac"], "port": d["port"], "status": "ready", "msg": "", "tries": 0, "t": 0.0})
                st["port"], st["status"], st["msg"], st["t"] = d["port"], "flashing", "ファームウェアを更新中(約 1 分)…", time.time()
                todo.append(d)
        if not todo:
            return False, "すでに書き込み中です"

        def go():
            for d in todo:                              # 1 台ずつ(同時に何台も書くと USB が不安定になる)
                self._flash(d["mac"], d["port"], update=True)
        threading.Thread(target=go, daemon=True).start()
        return True, f"{len(todo)} 台を更新します(約 1 分ずつ)。終わるまで USB を抜かないでください"

    def _flash(self, mac: str, port: str, update: bool = False) -> None:
        try:
            if self.release:
                self.release(mac)
        except Exception:  # noqa: BLE001
            pass
        self.hub.event(f"{'機器のファームウェアを更新します' if update else '新しい機器を書き込みます'}: {mac[-6:]}")
        ok, tail = False, ""
        try:
            r = subprocess.run([sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port, "--baud", "921600",
                                "write-flash", "0x0", IMAGE], capture_output=True, text=True, timeout=300)
            ok, tail = r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
            tail = tail[0][:120]
        except Exception as e:  # noqa: BLE001
            tail = f"{type(e).__name__}: {e}"[:120]
        with self.lock:
            st = self.state.get(mac)
            if st:
                st["status"] = "flashed" if ok else "failed"
                st["msg"] = ("更新完了。再起動してつながり直します" if update else "書き込み完了。再起動して Wi-Fi につながると登録されます") if ok else f"書き込みに失敗しました: {tail}"
                st["t"] = time.time()
        self.hub.event(f"{mac[-6:]}: " + ("書き込み完了" if ok else "書き込みに失敗"))
        self._busy.discard(mac)
        try:
            if self.resume:
                self.resume(mac)
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> list:
        with self.lock:
            out = []
            for st in self.state.values():
                r = self.reg.get(st["mac"])
                out.append({"mac": st["mac"], "port": st["port"], "status": st["status"], "msg": st["msg"],
                            "name": r.get("name", "") if r else ""})
            return out

    def run(self) -> None:
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001 - 監視は落とさない
                pass
            time.sleep(2.0)

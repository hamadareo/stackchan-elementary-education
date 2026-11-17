"""USB に挿した StackChan(何台でも)を自動で見つけ、機器ごとに独立した接続(Session)として扱う。

- 見つけ方: usbprov.scan()(/sys/bus/usb/devices の Espressif)。識別は MAC(USB のシリアル番号)。
  ポート名(/dev/ttyACM0〜)は挿し直しや再起動で入れ替わるので、MAC ごとに「いまのポート」を引き直す。
- 機器ごとに 1 本のスレッドが、そのポートを開いて serve_serial を動かす。抜かれたら(その MAC が見えなくなる、
  またはポート名が変わる)スレッドは終わり、挿し直されたら新しいスレッドが始まる。
- 新品の機器を usbprov が書き込む間は、そのポートを手放す(同じポートを同時に開かない)。
"""
from __future__ import annotations

import threading
import time

import usbprov


class UsbLinks:
    def __init__(self, serve_port, scan=usbprov.scan, watcher=None, interval: float = 2.0):
        self.serve_port, self.scan, self.watcher, self.interval = serve_port, scan, watcher, interval
        self.threads: dict = {}            # mac → (port, Thread)
        self.ports: dict = {}              # mac → いまのポート
        self.paused: set = set()           # 書き込み中などで手放している MAC
        self.lock = threading.Lock()

    def wanted(self, mac: str, port: str) -> bool:
        """この MAC のこのポートの接続を続けてよいか(serve_serial が定期的に聞く)。"""
        with self.lock:
            return mac not in self.paused and self.ports.get(mac) == port

    def step(self) -> None:
        devs = self.scan()
        with self.lock:
            self.ports = {d["mac"]: d["port"] for d in devs if d.get("port")}
            busy = set(getattr(self.watcher, "_busy", ()) or ())
            todo = []
            for mac, port in self.ports.items():
                cur = self.threads.get(mac)
                if cur and cur[1].is_alive() and cur[0] == port:
                    continue
                if cur and cur[1].is_alive():
                    continue                                    # ポート名が変わった: 古いスレッドが終わるのを待つ(次の周回で始める)
                if mac in self.paused or mac in busy:
                    continue
                todo.append((mac, port))
        for mac, port in todo:
            t = threading.Thread(target=self.serve_port, args=(port, lambda m=mac, p=port: self.wanted(m, p)), daemon=True, name=f"usb-{mac[-4:]}")
            with self.lock:
                self.threads[mac] = (port, t)
            t.start()
            print(f"[bridge] USB 機器を検出: {mac[-6:]} → {port}", flush=True)

    def release(self, mac: str, wait: float = 6.0) -> None:
        """書き込みの前: この機器の接続を終わらせ、ポートが空くのを待つ。"""
        with self.lock:
            self.paused.add(mac)
            cur = self.threads.get(mac)
        t0 = time.time()
        while cur and cur[1].is_alive() and time.time() - t0 < wait:
            time.sleep(0.2)

    def resume(self, mac: str) -> None:
        with self.lock:
            self.paused.discard(mac)

    def run(self) -> None:
        while True:
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - 監視は落とさない
                print(f"[bridge] USB 監視エラー: {type(e).__name__}: {e}", flush=True)
            time.sleep(self.interval)

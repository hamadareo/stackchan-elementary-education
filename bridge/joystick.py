"""ジョイスティック(Arduino UNO + アナログスティック)で、チャリはしりをジャンプさせる。

- Arduino は "J,x,y,b" の行を 50 回/秒で USB シリアルに流す(arduino/joystick/joystick.ino)。
- ブリッジは、Arduino らしい USB(StackChan の ESP32 以外)を見つけて読む。挿し直しにも自動で追従する。
- スティックを、どの向きでも「ぐいっ」と倒す / ボタンを押す = ジャンプ(倒しっぱなしで連続はしない。いったん戻す)。
  向きを問わないので、スティックの取り付け向きを気にしなくてよい。
- ジャンプは、いまチャリはしりを遊んでいる機器すべてに送る。
"""
from __future__ import annotations

import glob
import os
import threading
import time

ARDUINO_VIDS = ("2341", "2a03", "1a86", "0403", "10c4")      # Arduino 純正・互換(CH340)・FTDI・CP210x。StackChan(303a)は含めない
PUSH, RELEASE = 0.55, 0.35                                      # 倒した量(中心から端までを 1)。倒す / 戻す のしきい値(ヒステリシス)


def parse(line: str):
    """"J,512,498,0" → (512, 498, 0)。壊れた行は None。"""
    p = line.strip().split(",")
    if len(p) != 4 or p[0] != "J":
        return None
    try:
        x, y, b = int(p[1]), int(p[2]), int(p[3])
    except ValueError:
        return None
    if not (0 <= x <= 1023 and 0 <= y <= 1023):
        return None
    return x, y, 1 if b else 0


class Pad:
    """スティックの値から、「ジャンプ」の合図を作る(中心は、最初の十数個の中央値で自動合わせ)。"""

    def __init__(self, on_jump):
        self.on_jump = on_jump
        self.cal: list = []
        self.cx = self.cy = 512.0
        self.pushed = False
        self.btn = False
        self.x = self.y = 0.0                                   # 中心からの倒し量(-1〜1)
        self.jumps = 0

    def feed(self, x: int, y: int, b: int) -> None:
        if len(self.cal) < 15:                                  # 起動直後は、手を離している前提で中心を測る
            self.cal.append((x, y))
            if len(self.cal) == 15:
                xs, ys = sorted(c[0] for c in self.cal), sorted(c[1] for c in self.cal)
                self.cx, self.cy = float(xs[7]), float(ys[7])
            return
        self.x, self.y = (x - self.cx) / 512.0, (y - self.cy) / 512.0
        mag = max(abs(self.x), abs(self.y))
        fire = False
        if not self.pushed and mag >= PUSH:
            self.pushed, fire = True, True
        elif self.pushed and mag <= RELEASE:
            self.pushed = False
        if b and not self.btn:
            fire = True
        self.btn = bool(b)
        if fire:
            self.jumps += 1
            self.on_jump()


def find_ports() -> list:
    """Arduino らしい USB シリアルのポート(Linux: /sys から、ほかは pyserial)。"""
    out = []
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            vid = open(os.path.join(d, "idVendor")).read().strip().lower()
        except OSError:
            continue
        if vid in ARDUINO_VIDS:
            out += sorted(glob.glob(os.path.join(d, "*", "tty", "tty*")))
    ports = ["/dev/" + os.path.basename(p) for p in out]
    if not ports:
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports() if p.vid is not None and f"{p.vid:04x}" in ARDUINO_VIDS]
        except Exception:  # noqa: BLE001
            ports = []
    return ports


class Joystick:
    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        self.pad = Pad(self.jump)
        self.port = ""
        self.last_rx = 0.0
        self._stop = threading.Event()
        self._t = None

    def jump(self) -> int:
        """いま チャリはしりを遊んでいる機器すべてを、ジャンプさせる。ジャンプさせた機器の数を返す。"""
        q = getattr(self.hub, "quiz", None)
        n = 0
        for g in list(getattr(q, "games", {}).values()) if q is not None else []:
            if getattr(g, "on", False) and getattr(g, "kind", "") == "chari":
                g._dev("jump")
                n += 1
        return n

    def status(self) -> dict:
        ok = bool(self.port) and self.clock() - self.last_rx < 3.0
        return {"connected": ok, "x": round(self.pad.x, 2), "y": round(self.pad.y, 2), "jumps": self.pad.jumps}

    def start(self) -> None:
        self._t = threading.Thread(target=self._run, daemon=True, name="joystick")
        self._t.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            import serial
        except Exception:  # noqa: BLE001
            return
        while not self._stop.is_set():
            ports = find_ports()
            if not ports:
                self.port = ""
                self._stop.wait(3.0)
                continue
            try:
                with serial.Serial(ports[0], 115200, timeout=0.5) as s:
                    self.port = ports[0]
                    print("[bridge] ジョイスティック: 接続", flush=True)
                    self.pad = Pad(self.jump)
                    time.sleep(2.0)                              # UNO は、開くと再起動する
                    s.reset_input_buffer()
                    while not self._stop.is_set():
                        line = s.readline().decode("ascii", "ignore")
                        v = parse(line)
                        if v:
                            self.last_rx = self.clock()
                            self.pad.feed(*v)
                        elif not line:
                            if self.clock() - self.last_rx > 5.0:
                                break                            # 何も来ない: 開き直す
            except Exception as e:  # noqa: BLE001 - 抜かれたら、また探す
                print(f"[bridge] ジョイスティック: 切断({e})", flush=True)
            self.port = ""
            self._stop.wait(2.0)

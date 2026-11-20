"""ジョイスティック(Arduino UNO + アナログスティック)で、チャリはしりをジャンプさせる。

- Arduino は "J,x,y,b" の行を 50 回/秒で USB シリアルに流す(arduino/joystick/joystick.ino)。
- ブリッジは、Arduino らしい USB(StackChan の ESP32 以外)を見つけて読む。挿し直しにも自動で追従する。
- チャリはしり: スティックをどの向きでも「ぐいっ」と倒す / ボタンを押す = ジャンプ(倒しっぱなしで連続はしない。いったん戻す)。
- じゃんけん: 左=ぐー / 上=ちょき / 右=ぱー。おみくじ: ボタン=やる / 下=やめる。なぞなぞ: ボタン=ヒント / 下=わからない。
  (しりとり・ものまねは声だけ。)
- 待機中(遊んでいない機器): スティックを倒すと、StackChan の首がその向きに動く(顔追従は、動かしている間と、そのあと数秒は止める)。
- 操作は、その遊び(や待機)をしている機器すべてに送る。向きの符号は SC_JOY_PAN_SIGN / SC_JOY_TILT_SIGN(1 か -1)で反転できる。
"""
from __future__ import annotations

import glob
import os
import threading
import time

ARDUINO_VIDS = ("2341", "2a03", "1a86", "0403", "10c4")      # Arduino 純正・互換(CH340)・FTDI・CP210x。StackChan(303a)は含めない
PUSH, RELEASE = 0.35, 0.2                                       # 倒した量(中心から端までを 1)。倒す / 戻す のしきい値(ヒステリシス)
DEAD = 0.07                                                     # これより小さい倒しは、首を動かさない
PAN_RANGE, TILT_HOME, TILT_RANGE = 520, 250, 200                # 首の動かす範囲(0.1 度単位。機器の可動域の内側)
HOLD_S = 4.0                                                    # 首を動かしたあと、顔追従を止めておく秒数
MOVE_EVERY_S = 0.04                                             # 首へ送る間隔
SAY = {                                                         # (遊び, 操作) → 子どもの代わりに入れる言葉
    ("janken", "left"): "ぐー", ("janken", "up"): "ちょき", ("janken", "right"): "ぱー",
    ("omikuji", "button"): "やる", ("omikuji", "down"): "やめる",
    ("riddle", "button"): "ヒント", ("riddle", "down"): "わからない",
}


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

    def __init__(self, on_event, on_move=None):
        self.on_event = on_event                                 # on_event(kind): "push"(どの向きでも)/ "left|right|up|down" / "button"
        self.on_move = on_move                                   # on_move(x, y): 倒し量(-1〜1)。倒している間、約 0.04 秒ごと。戻したときに (0, 0) を 1 回
        self._move_t = 0.0
        self._moving = False
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
        if not self.pushed and mag >= PUSH:
            self.pushed = True
            self.jumps += 1
            d = ("right" if self.x > 0 else "left") if abs(self.x) >= abs(self.y) else ("down" if self.y > 0 else "up")
            self.on_event("push")
            self.on_event(d)
        elif self.pushed and mag <= RELEASE:
            self.pushed = False
        if b and not self.btn:
            self.jumps += 1
            self.on_event("push")
            self.on_event("button")
        self.btn = bool(b)
        if self.on_move is not None:
            if mag >= DEAD:
                import time as _t
                t = _t.time()
                if t - self._move_t >= MOVE_EVERY_S:
                    self._move_t = t
                    self._moving = True
                    s = min(1.0, (mag - DEAD) / (0.7 - DEAD))                  # 不感帯の外を 0〜1 に引きのばす(動き出しがなめらか)
                    self.on_move(self.x / mag * s, self.y / mag * s)
            elif self._moving:
                self._moving = False
                self.on_move(0.0, 0.0)


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
        self.pad = Pad(self.handle, self.move)
        self.pan_sign = -1 if os.environ.get("SC_JOY_PAN_SIGN", "1") == "-1" else 1
        self.tilt_sign = -1 if os.environ.get("SC_JOY_TILT_SIGN", "1") == "-1" else 1
        self.port = ""
        self.last_rx = 0.0
        self._stop = threading.Event()
        self._t = None

    def _devs(self) -> list:
        return [getattr(s, "dev_id", "") for s in list(self.hub.live()) if getattr(s, "dev_id", "")]

    def _kind(self, dev: str) -> str:
        les = getattr(self.hub, "lesson", None)
        try:
            return les.current(dev) if les is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def jump(self) -> int:
        """いま チャリはしりを遊んでいる機器すべてを、ジャンプさせる。ジャンプさせた機器の数を返す。"""
        q = getattr(self.hub, "quiz", None)
        n = 0
        for g in list(getattr(q, "games", {}).values()) if q is not None else []:
            if getattr(g, "on", False) and getattr(g, "kind", "") == "chari":
                g._dev("jump")
                n += 1
        return n

    def handle(self, kind: str) -> None:
        """スティックの操作(push / left / right / up / down / button)を、遊びごとの操作にする。"""
        if kind == "push":
            self.jump()                                          # チャリはしり(向きを問わない)
            return
        for dev in self._devs():
            word = SAY.get((self._kind(dev), kind))
            if not word:
                continue
            for h in (getattr(self.hub, "game", None), getattr(self.hub, "janken", None), getattr(self.hub, "quiz", None)):
                try:
                    if h is not None and h.say(word, dev):
                        break
                except Exception:  # noqa: BLE001
                    pass

    def move(self, x: float, y: float) -> None:
        """待機中の機器の首を、倒した量に比例した速さで動かす(遊んでいる機器は動かさない)。顔追従は、しばらく止める。
        機器は、0.3 秒信号が来なければ止まる。(0, 0) は、止める合図。"""
        vx, vy = max(-1.0, min(1.0, x)) * self.pan_sign, max(-1.0, min(1.0, y)) * self.tilt_sign
        now = self.clock()
        for s in list(self.hub.live()):
            dev = getattr(s, "dev_id", "")
            if not dev or self._kind(dev):
                continue
            s._manual_until = now + HOLD_S
            try:
                s.send({"t": "joy", "vx": round(vx, 3), "vy": round(vy, 3)})
            except Exception:  # noqa: BLE001
                pass

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
                    self.pad = Pad(self.handle, self.move)
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

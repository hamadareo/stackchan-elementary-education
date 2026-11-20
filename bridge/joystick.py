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
DEAD = 0.10                                                     # これより小さい倒しは、首を動かさない
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
                    s = 1.0                  # 不感帯を出たら、最初から最高速
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
        self._vec: dict = {}
        self._open: set = set()
        self._tried: set = set()
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

    def _move_from(self, port: str, x: float, y: float) -> None:
        """複数のスティックがあるとき、1 本が戻しても、もう 1 本が倒されていれば止めない。"""
        if self._vec.get(port, (0.0, 0.0)) == (0.0, 0.0) != (x, y):
            print(f"[bridge] 入力開始 {port} {x:.2f},{y:.2f} 機器={len(list(self.hub.live()))}台", flush=True)
        self._vec[port] = (x, y)
        if (x, y) == (0.0, 0.0) and any(v != (0.0, 0.0) for p, v in self._vec.items() if p != port):
            return
        self.move(x, y)

    def _run(self) -> None:
        """見つかったスティックを、1 本ずつ別のスレッドで読む(何本でも)。"""
        try:
            import serial  # noqa: F401
        except Exception:  # noqa: BLE001
            return
        while not self._stop.is_set():
            for port in find_ports():
                if port not in self._open:
                    self._open.add(port)
                    threading.Thread(target=self._read, args=(port,), daemon=True, name="joystick-" + port).start()
            self._tried &= set(find_ports()) | self._open          # 抜かれたポートは、次に挿したとき、また調べる
            self._pads()
            if not self._open:
                self.port = ""
            self._stop.wait(3.0)

    def _pads(self) -> None:
        """USB ゲームパッド(/dev/input/js*)を、Arduino のスティックと同じように使う。"""
        import glob
        for path in glob.glob("/dev/input/js*"):
            if path not in self._open:
                self._open.add(path)
                threading.Thread(target=self._read_pad, args=(path,), daemon=True, name="gamepad-" + path).start()

    def _read_pad(self, path: str) -> None:
        import select
        import struct
        try:
            with open(path, "rb", buffering=0) as f:
                print(f"[bridge] ゲームパッド: 接続 {path}", flush=True)
                g = float(os.environ.get("SC_PAD_GAIN", "0.4"))      # 十字キーは、倒すか倒さないかの 2 段階。最高速のままだと、すぐ端に当たるので、ゆっくりにする
                pad = Pad(self.handle, lambda x, y, _p=path, _g=g: self._move_from(_p, x * _g, y * _g))
                self.pad = pad
                ax: dict = {}
                held: set = set()
                while not self._stop.is_set():
                    r, _, _ = select.select([f], [], [], 0.02)
                    while r:
                        ev = f.read(8)
                        if not ev or len(ev) < 8:
                            raise OSError("切断")
                        _t, val, typ, num = struct.unpack("IhBB", ev)
                        if typ & 0x80:
                            continue                              # 起動時の初期状態は、ボタンとして扱わない
                        if typ == 1:
                            (held.add if val else held.discard)(num)
                        elif typ == 2:
                            ax[num] = val / 32767.0
                        r, _, _ = select.select([f], [], [], 0)
                    # 左スティック(軸 0,1)。倒していなければ、十字キー(軸 4,5 または 6,7)
                    x, y = ax.get(0, 0.0), ax.get(1, 0.0)
                    if (x * x + y * y) ** 0.5 < 0.2:
                        x = ax.get(4, ax.get(6, 0.0))
                        y = ax.get(5, ax.get(7, 0.0))
                    self.last_rx = self.clock()
                    self.port = path
                    pad.feed(int(512 + x * 511), int(512 + y * 511), 1 if held else 0)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] ゲームパッド: 切断({path}: {e})", flush=True)
        self._vec.pop(path, None)
        self._open.discard(path)

    def _provision(self, port: str) -> bool:
        """スケッチが入っていない新しい Arduino に、arduino-cli でスケッチを自動で書き込む。"""
        import shutil
        import subprocess
        cli = shutil.which("arduino-cli") or os.path.expanduser("~/bin/arduino-cli")
        sketch = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "arduino", "joystick")
        if not (os.path.exists(cli) and os.path.isdir(sketch)):
            print("[bridge] ジョイスティック: 自動セットアップに必要な arduino-cli またはスケッチがありません", flush=True)
            return False
        print(f"[bridge] ジョイスティック: 新しいスティックを自動セットアップ中 {port}", flush=True)
        try:
            r = subprocess.run([cli, "compile", "--upload", "-p", port, "--fqbn", "arduino:avr:uno", sketch],
                               capture_output=True, text=True, timeout=180)
            ok = r.returncode == 0
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] ジョイスティック: 自動セットアップ失敗({e})", flush=True)
            return False
        print("[bridge] ジョイスティック: 自動セットアップ" + ("完了" if ok else "失敗: " + (r.stderr or "")[-200:]), flush=True)
        return ok

    def _read(self, port: str) -> None:
        import serial
        try:
            if port not in self._tried:                      # 信号が来ない(スケッチ未書き込み)なら、1 度だけ自動で書き込む
                self._tried.add(port)
                with serial.Serial(port, 115200, timeout=0.5) as probe:
                    time.sleep(2.0)
                    end = time.time() + 2.0
                    seen = False
                    while time.time() < end and not seen:
                        seen = parse(probe.readline().decode("ascii", "ignore")) is not None
                if not seen:
                    self._provision(port)
                    time.sleep(1.0)
            with serial.Serial(port, 115200, timeout=0.5) as s:
                self.port = port
                print(f"[bridge] ジョイスティック: 接続 {port}", flush=True)
                pad = Pad(self.handle, lambda x, y, _p=port: self._move_from(_p, x, y))
                self.pad = pad
                time.sleep(2.0)                              # UNO は、開くと再起動する
                s.reset_input_buffer()
                last = self.clock()
                while not self._stop.is_set():
                    line = s.readline().decode("ascii", "ignore")
                    v = parse(line)
                    if v:
                        last = self.last_rx = self.clock()
                        pad.feed(*v)
                    elif not line and self.clock() - last > 5.0:
                        break                                # 何も来ない: 開き直す
        except Exception as e:  # noqa: BLE001 - 抜かれたら、また探す
            print(f"[bridge] ジョイスティック: 切断({port}: {e})", flush=True)
        self._vec.pop(port, None)
        self._open.discard(port)

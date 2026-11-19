"""StackChan 管理画面(ブラウザで開く)。ブリッジと同じプロセスで動く(標準ライブラリだけ)。

  http://<このサーバー>:8080/        … 画面
  GET  /api/state                     … 接続・電池・音量・言語・直近の通訳
  POST /api/volume   {"v":0..255} / {"delta":±40}
  POST /api/lang     {"code":"en|zh|ko"}
  POST /api/topic    {"text":"..."}  (空で解除)
  POST /api/say      {"text":"...","lang":"ja"}
  POST /api/bright   {"v":15..255}
  POST /api/shot                      … 端末の画面を撮る(GET /screen.jpg で取得)
  POST /api/recalibrate               … 顔追従の校正をやり直す

SC_ADMIN_TOKEN を設定すると、?token=… か Authorization: Bearer … が必要になる。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
import devices as _devices
REG = _devices.Registry()          # 機器台帳(初めて見た機器は自動で登録する)
TOKEN = os.environ.get("SC_ADMIN_TOKEN", "")
ACTS = ("shiritori", "janken", "riddle", "omikuji", "mimic", "chari")     # 子どもに出す遊び(教材モード)。これ以外は出さない


class Hub:
    """ブリッジから状態を受け取り、画面からの操作を現在の接続へ渡す。"""

    def __init__(self):
        self.discuss = None              # 3人会話モード(ブリッジが起動時に設定)
        self.auto = None                 # 完全自立モード(自分たちで話題を決めて話し始める)
        self.game = None                 # 子供たちの教材(しりとり)
        self.janken = None               # 子供たちの教材(じゃんけん)
        self.quiz = None                 # 子供たちの遊び(なぞなぞ・あっち向いてホイ・おみくじ・ものまね)
        self.kid = None                  # 子どもモード(子どもが話しかけたら、キャラが返事をする)
        self.kid_on = True
        self.menu_acts = list(ACTS)     # 教材モードで子どもに出す遊び(これ以外はしない)
        self.warm = None                 # 遊びの台詞の先読み(warm.Warmer)
        self.kid_auto = True             # 子どもモードで、ロボットが自分から話しかける
        self.hand_cache: dict = {}       # 機器ID → 手の確認の結果 {ts, src, jpeg, info}(管理画面が見ている機器だけ計算する)
        self.hand_lock = threading.Lock()
        self.janken_cam = True           # じゃんけんで、カメラの手の認識を使う
        self.lesson = None               # 授業の進行役(lesson.Lesson): 管理画面のボタンで、機器ごとに遊びを始める・中止する
        self.teacher_only = True         # 先生の操作だけで遊びを始める(オンの間は、子どもが「〇〇しよう」と言っても始まらない。遊びが選ばれていない間、ロボットは何もしない)
        self.edu_on = True               # 教材モード(既定はオン: 先生がボタンを押すまで、ロボットは何もしない。オンの間は、翻訳・3人会話・自律会話など教材と関係ないものを止める)
        self.usb = None                  # USB 監視(同上)
        self.sessions: list = []
        self.turns: list = []            # 直近の通訳(新しい順に最大 40)
        self.screen: bytes = b""
        self.screen_t = 0.0
        self.engine = None
        self.started = time.time()
        self.vol = None                  # 最後に分かった端末の音量(0..255)
        self.lock = threading.Lock()
        self.stages: dict = {}           # パイプラインの各段(mic/vad/stt/translate/tts/play)の最新の様子
        self.events: list = []           # 動きの履歴(新しい順)
        self.mic_rms = 0.0
        self.mic_t = 0.0
        self.vad_t = 0.0
        self.lat: list = []              # 直近の「最初の音まで」(秒)
        self._svc = ("", 0.0)
        self.mode = "auto"
        self.track = True                # 顔追従
        self.listen = True               # マイクを聞く
        self._voices = ([], 0.0)
        self.turn_count = 0
        self.gate_min = 0.006            # 雑音ゲートの下限
        self.gate_mult = 1.5             # 雑音の何倍より大きい音から「話し始め」とするか
        import people as _pp
        self.people = _pp.People()       # 登録した顔(特徴ベクトルだけ。画像は持たない)
        self.people_now = {"name": "", "score": 0.0, "t": 0.0, "face": False}
        self.reg = None                  # 登録の撮影中 {"name","need","got","t0","last"}
        self.reg_msg = ""
        self.wave_on = True              # 手を振ったら反応する
        self.wave_voice = True           # 声でも返事する
        self.wave_count = 0
        self.wave_t = 0.0
        self.target = "all"              # 操作の対象: "all" か機器 ID
        self.screens: dict = {}          # 機器ごとの画面
        self.raws: dict = {}             # 機器ごとの直近 15 秒のマイク音声
        self.mics: dict = {}             # 機器ごとの (rms, 時刻)
        self.vads: dict = {}             # 機器ごとの最後に声を検出した時刻
        self.nows: dict = {}             # 機器ごとの「いま見えている人」
        self._gs: dict = {}              # 機器ごとの雑音ゲートの測定
        self.halluc = 0                  # 幻聴として捨てた/削った回数
        self.phrases: list = [{"text": "こんにちは", "lang": "ja"}, {"text": "少し待ってください", "lang": "ja"},
                              {"text": "Nice to meet you.", "lang": "en"}]
        self.path = os.environ.get("SC_SETTINGS", os.path.expanduser("~/.sokutsu_admin.json"))

    # ---- 設定の保存と復元(再起動しても声・話速などを覚える) ----
    def save(self) -> None:
        e = self.engine
        d = {"wave_on": self.wave_on, "wave_voice": self.wave_voice, "gate": self.gate_mult, "mode": self.mode, "track": self.track, "listen": self.listen, "phrases": self.phrases, "kid_on": self.kid_on, "kid_auto": self.kid_auto, "menu_acts": self.menu_acts, "edu_on": self.edu_on, "janken_cam": self.janken_cam, "teacher_only": self.teacher_only}
        if e is not None:
            d["speed"] = getattr(e, "speed_scale", 1.0)
            d["voice"] = getattr(e, "voice_ja", None)
            po = getattr(e, "pair_override", None)
            d["lang"] = po[1] if po else None
            d["topic"] = getattr(e, "topic_override", "") or ""
            try:
                d["vad"] = float(e.vad_conf.silero_vad.threshold)
            except Exception:  # noqa: BLE001
                pass
        try:
            json.dump(d, open(self.path, "w"), ensure_ascii=False)
        except OSError:
            pass

    def load(self) -> None:
        try:
            d = json.load(open(self.path))
        except (OSError, ValueError):
            return
        self.gate_mult = max(1.0, min(4.0, float(d.get("gate", 1.5))))
        self.wave_on, self.wave_voice = bool(d.get("wave_on", True)), bool(d.get("wave_voice", True))
        self.track, self.listen = bool(d.get("track", True)), bool(d.get("listen", True))
        self.kid_on = bool(d.get("kid_on", True))
        self.kid_auto = bool(d.get("kid_auto", True))
        if isinstance(d.get("menu_acts"), list):
            old = {"quiz", "math", "kanji", "english"} & set(d["menu_acts"])           # 勉強の遊びを廃止する前の保存値: 新しい 6 個の既定に直す
            self.menu_acts = list(ACTS) if old else [a for a in d["menu_acts"] if a in ACTS]
        self.edu_on = bool(d.get("edu_on", True))
        self.janken_cam = bool(d.get("janken_cam", True))
        self.teacher_only = bool(d.get("teacher_only", True))
        if isinstance(d.get("phrases"), list):
            self.phrases = d["phrases"][:12]
        for name, body in (("speed", {"scale": d.get("speed")}), ("vad", {"threshold": d.get("vad")}),
                           ("engine", {"mode": d.get("mode")})):
            if all(v is not None for v in body.values()):
                try:
                    self.act(name, body, persist=False)
                except Exception:  # noqa: BLE001
                    pass
        if d.get("lang") in ("en", "zh", "ko") and self.engine is not None:
            self.engine.pair_override = ("ja", d["lang"])
            try:
                self.engine.stt.langs = self.engine.pair_override
            except Exception:  # noqa: BLE001
                pass
        if d.get("topic") and self.engine is not None:
            self.engine.topic_override = str(d["topic"])[:40]
        v = d.get("voice")
        if isinstance(v, str) and v.startswith("voicevox:"):
            try:
                self.act("voice", {"id": int(v.split(":")[1]), "default": True}, persist=False)
            except Exception:  # noqa: BLE001
                pass

    def prewarm(self) -> None:
        pw = getattr(self.engine, "prewarm", None)
        if pw:
            pw([(x["text"], x["lang"]) for x in self.phrases])

    def build(self) -> str:
        try:
            return str(int(os.path.getmtime(os.path.join(HERE, "admin.html"))))
        except OSError:
            return "0"

    def event(self, msg: str) -> None:
        with self.lock:
            self.events.insert(0, {"t": time.strftime("%H:%M:%S"), "stage": "people", "msg": msg})
            del self.events[40:]

    def reg_done(self) -> None:
        r, self.reg = self.reg, None
        if r:
            self.people.add(r["name"], r["got"])
            self.reg_msg = f"{r['name']}さんを登録しました({len(r['got'])}枚)"
            self.event(self.reg_msg)

    def reg_end(self, msg: str) -> None:
        self.reg, self.reg_msg = None, msg

    def gs(self, dev: str) -> dict:
        return self._gs.setdefault(dev, {"amb": 0.0, "speech": 0.0, "gated": 0})

    def set_now(self, dev: str, d: dict) -> None:
        self.nows[dev] = d
        self.people_now = d

    def keep_raw(self, b: bytes, dev: str = "") -> None:
        raw = self.raws.setdefault(dev, bytearray())
        raw += b
        if len(raw) > 16000 * 2 * 15:
            del raw[: len(raw) - 16000 * 2 * 15]

    def usb_live(self) -> bool:
        """USB でつながっている機器の接続があるか。"""
        with self.lock:
            return any(x.alive and getattr(x, "ip", "") == "usb" for x in self.sessions)

    def usb_session(self, dev: str, me=None):
        """この機器 ID で、生きている USB の接続(me 以外)。なければ None。"""
        if not dev:
            return None
        with self.lock:
            for x in self.sessions:
                if x is not me and x.alive and getattr(x, "dev_id", "") == dev and getattr(x, "ip", "") == "usb":
                    return x
        return None

    def replace_dups(self, sess) -> None:
        """同じ機器 ID の古い接続(切れたのに残っているもの)を閉じる。"""
        with self.lock:
            olds = [x for x in self.sessions if x is not sess and x.alive and getattr(x, "dev_id", "") == sess.dev_id]
        for x in olds:
            x.alive = False

    def _face_of(self, rec: dict) -> int:
        f = rec.get("face")
        if isinstance(f, int) and 0 <= f < len(_devices.FACE_STYLES):
            return f
        try:
            return _devices.DEFAULT_FACE[self.discuss.persona_of(rec["id"]) % len(_devices.DEFAULT_FACE)] if self.discuss else 0
        except Exception:  # noqa: BLE001
            return 0

    def discussing(self) -> bool:
        return bool(self.discuss and self.discuss.on)

    def by_id(self, dev: str):
        with self.lock:
            for x in self.sessions:
                if x.alive and getattr(x, "dev_id", "") == dev:
                    return x
        return None

    def view_session(self):
        """画面に状態を出す機器: 選んだ機器、なければ最初に見つかった接続中の機器。"""
        if self.target != "all":
            x = self.by_id(self.target)
            if x:
                return x
        live = sorted(self.live(), key=lambda x: (REG.get(getattr(x, "dev_id", "")).get("first", 0), getattr(x, "name", "")))
        return live[0] if live else None

    def targets(self, body: dict) -> list:
        """操作の対象になる接続中の機器。body["device"] があればそれ、なければ選択中(all なら全部)。"""
        dev = str(body.get("device") or self.target or "all")
        if dev == "all":
            return self.live()
        x = self.by_id(dev)
        return [x] if x else []

    def devices_state(self) -> list:
        now = time.time()
        out = []
        for rec in REG.all():
            x = self.by_id(rec["id"])
            d = {"id": rec["id"], "name": rec.get("name", ""), "ip": rec.get("ip", ""), "fw": rec.get("fw", ""),
                 "first": rec.get("first"), "voice": rec.get("voice", ""), "face": self._face_of(rec), "seen_age": int(now - rec["seen"]) if rec.get("seen") else None,
                 "auto": bool(rec.get("auto")), "setup": rec.get("setup"), "online": bool(x),
                 "lang": rec.get("lang"), "topic": rec.get("topic", ""), "via": ""}
            if x:
                d["via"] = "USB 有線" if getattr(x, "ip", "") == "usb" else "Wi-Fi"
                d.update({"pair": list(x.pair), "batt": getattr(x, "batt", None), "vol": getattr(x, "vol", None),
                          "rssi": (getattr(x, "dbg", None) or {}).get("rssi"),
                          "level": round(min(1.0, self.mics.get(rec["id"], (0.0, 0))[0] * 6), 2),
                          "talking": bool(getattr(x, "talking", False)), "who": (self.nows.get(rec["id"]) or {}).get("name", "")
                          if now - (self.nows.get(rec["id"]) or {}).get("t", 0) < 4 else ""})
            out.append(d)
        return out

    def mic_wav(self, dev: str = "") -> bytes:
        import struct
        if not dev:
            v = self.view_session()
            dev = getattr(v, "dev_id", "") if v else ""
        pcm = bytes(self.raws.get(dev, b""))
        h = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) \
            + b"data" + struct.pack("<I", len(pcm))
        return h + pcm

    def clip_pct(self, dev: str = "") -> float:
        import array
        a = array.array("h", bytes(self.raws.get(dev, b"")[: len(self.raws.get(dev, b"")) // 2 * 2]))
        return round(100.0 * sum(1 for v in a if abs(v) >= 32000) / max(1, len(a)), 2)

    def mic(self, rms: float, dev: str = "") -> None:
        old = self.mics.get(dev, (0.0, 0.0))[0]
        self.mics[dev] = (0.6 * old + 0.4 * rms, time.time())
        self.mic_rms = 0.6 * self.mic_rms + 0.4 * rms
        self.mic_t = time.time()

    def vad(self, dev: str = "") -> None:
        self.vads[dev] = time.time()
        self.vad_t = time.time()

    def stage(self, name: str, state: str = "done", **info) -> None:
        with self.lock:
            st = self.stages.setdefault(name, {})
            st.update(info)
            st["state"], st["t"] = state, time.time()
            if state == "done":
                msg = {"stt": "聞き取り", "translate": "翻訳", "tts": "声づくり"}.get(name)
                if msg:
                    self.events.insert(0, {"t": time.strftime("%H:%M:%S"), "stage": name,
                                           "msg": f"{msg} {info.get('ms', 0)}ms  {info.get('text', '')}"})
                    del self.events[40:]

    def add_latency(self, sec: float) -> None:
        with self.lock:
            self.lat.append(round(float(sec), 2))
            del self.lat[:-30]

    @staticmethod
    def _sys() -> dict:
        d: dict = {}
        try:
            d["load"] = [float(x) for x in open("/proc/loadavg").read().split()[:3]]
            m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if l.split()[0].rstrip(":") in ("MemTotal", "MemAvailable")}
            d["mem_used_pct"] = round(100 - m["MemAvailable"] * 100 / m["MemTotal"])
            d["cpus"] = os.cpu_count()
        except Exception:  # noqa: BLE001 - Linux 以外では出さない
            pass
        try:
            ts = [int(open(f"/sys/class/thermal/{z}/temp").read()) / 1000 for z in os.listdir("/sys/class/thermal") if z.startswith("thermal_zone")]
            if ts:
                d["temp_c"] = round(max(ts))
        except Exception:  # noqa: BLE001
            pass
        return d

    def services(self) -> dict:
        """systemd --user のサービス状態(10 秒キャッシュ)。無ければ空。"""
        txt, t = self._svc
        if time.time() - t > 10:
            names = ["sc-bridge", "sc-voicevox", "sc-llm", "sc-s109"]
            try:
                out = subprocess.run(["systemctl", "--user", "is-active", *names], capture_output=True, text=True, timeout=3).stdout.split()
                txt = json.dumps(dict(zip(names, out)))
            except Exception:  # noqa: BLE001
                txt = "{}"
            self._svc = (txt, time.time())
        return json.loads(txt)

    def set_mode(self, mode: str) -> bool:
        """翻訳の置き場所: auto(s109→このサーバー) / s109 / local(このサーバーのCPU)。"""
        e = self.engine
        allc = getattr(e, "llm_all", [])
        pick = {"auto": allc, "s109": [c for c in allc if "s109" in c[0].lower()],
                "local": [c for c in allc if "s109" not in c[0].lower()]}.get(mode)
        if not pick:
            return False
        e.llm = list(pick)
        self.mode = mode
        return True

    def add_session(self, s) -> None:
        with self.lock:
            self.sessions = [x for x in self.sessions if x.alive] + [s]

    HAND_EVERY = 0.6                     # 手の検出は、機器ごとに 0.6 秒に 1 回まで(重い処理)。画像はそのつど新しいものに、直前の枠・線を重ねる

    def hand_view(self, dev: str):
        """管理画面「手の確認」用: その機器の最新のカメラ画像に手の検出を重ねた JPEG と結果。画像が無ければ None。
        この経路が呼ばれた機器だけ計算する(画面を開いていなければ何もしない)。"""
        x = self.by_id(dev)
        src = getattr(x, "last_jpeg", None) if x is not None else None
        if not src:
            return None
        now = time.time()
        with self.hand_lock:
            c = self.hand_cache.get(dev)
            if c and c["src"] is src:
                return c
            import hand as _hand
            if c and now - c["ts"] < self.HAND_EVERY:        # 検出は間引き、新しい画像には直前の枠・線を重ねて返す(映像を滑らかに)
                jpg = _hand.overlay_jpeg(src, c["info"])
                c = {**c, "src": src, "jpeg": jpg or src, "frame_ts": getattr(x, "last_jpeg_ts", 0.0)}
                self.hand_cache[dev] = c
                return c
            jpg, info = _hand.analyze_jpeg(src)
            c = {"ts": time.time(), "src": src, "jpeg": jpg or src, "info": info,
                 "frame_ts": getattr(x, "last_jpeg_ts", 0.0)}
            self.hand_cache[dev] = c
            return c

    def hand_status(self) -> dict:
        now = time.time()
        return {d: {"hand": c["info"]["hand"], "n_palm": c["info"]["n_palm"], "text": c["info"]["text"], "age": round(now - c["ts"], 1)}
                for d, c in list(self.hand_cache.items()) if now - c["ts"] < 10}

    def live(self) -> list:
        with self.lock:
            return [x for x in self.sessions if x.alive]

    def current(self):
        with self.lock:
            live = [x for x in self.sessions if x.alive]
            return live[-1] if live else None

    def voices(self) -> list:
        """VOICEVOX の話者一覧 [{id,label}](5 分キャッシュ)。"""
        v, t = self._voices
        if (not v and time.time() - t > 20) or time.time() - t > 300:
            try:
                import urllib.request
                url = getattr(getattr(self.engine, "s", None), "voicevox_url", "http://127.0.0.1:50021")
                data = json.loads(urllib.request.urlopen(url.rstrip("/") + "/speakers", timeout=3).read())
                v = [{"id": st["id"], "label": f"{sp['name']}({st['name']})"} for sp in data for st in sp["styles"]]
            except Exception:  # noqa: BLE001
                pass
            self._voices = (v, time.time())
        return v

    def voice_label(self, vid) -> str:
        for x in self.voices():
            if str(x["id"]) == str(vid):
                return x["label"].split("(")[0]
        return ""

    def add_turn(self, rec: dict) -> None:
        self.turn_count += 1
        with self.lock:
            self.turns.insert(0, rec)
            del self.turns[60:]

    def state(self) -> dict:
        s = self.view_session()
        vdev = getattr(s, "dev_id", "") if s else ""
        tdevs = [vdev] if self.target != "all" else [x.dev_id for x in self.live()]
        e = self.engine
        st = {"uptime_s": int(time.time() - self.started), "connected": bool(s), "now": time.strftime("%H:%M:%S")}
        try:
            import health
            st["ready"] = health.checks(self, e)                # 授業前の準備チェック
        except Exception:  # noqa: BLE001 - チェックの不具合で状態を壊さない
            st["ready"] = []
        if e is not None:
            st["ear"] = getattr(e.stt, "last_where", "") or ("remote" if e.remote_ear else "local")
            st["llm"] = getattr(e, "last_llm", "") or (e.llm[0][0] if e.llm else "")
            st["topic"] = (s.topic if s else getattr(e, "topic_override", "")) or ""
            st["llm"] = st["llm"].replace("このMac", "このサーバー")
        if s:
            st.update({"pair": list(s.pair), "batt": getattr(s, "batt", None), "dbg": getattr(s, "dbg", None),
                       "vol": getattr(s, "vol", None) or self.vol, "link": getattr(s, "link", "")})
        st["build"] = self.build()
        st["halluc"] = self.halluc
        now_p = time.time()
        pn = dict(self.nows.get(vdev) or self.people_now)
        if now_p - pn.get("t", 0) > 4:                               # しばらく更新が無い = 顔が見えていない
            pn["face"] = False
            pn["name"] = ""
        st["people"] = {"known": self.people.names(), "now": pn, "waves": self.wave_count,
                        "wave_age": round(now_p - self.wave_t, 1) if self.wave_t else None,
                        "wave_on": self.wave_on, "wave_voice": self.wave_voice,
                        "reg": ({"name": self.reg["name"], "got": len(self.reg["got"]), "need": self.reg["need"]} if self.reg else None),
                        "reg_msg": self.reg_msg, "available": bool(getattr(getattr(self.engine, "face", None), "sface", None))}
        g = self.gs(vdev)
        st["gate"] = {"mult": self.gate_mult, "min": self.gate_min, "amb": round(g["amb"], 4), "speech": round(g["speech"], 4),
                      "gated": g["gated"], "clip": self.clip_pct(vdev), "level": round(max(self.gate_min, g["amb"] * self.gate_mult), 4)}
        st["devices"] = self.devices_state()
        st["discuss"] = self.discuss.snapshot() if self.discuss else None
        st["auto"] = self.auto.snapshot() if self.auto else None
        st["kid_on"] = self.kid_on
        st["kid_auto"] = self.kid_auto
        try:
            st["warm"] = self.warm.snapshot() if self.warm is not None else None
        except Exception:  # noqa: BLE001
            st["warm"] = None
        st["menu_acts"] = list(self.menu_acts)
        st["hand"] = self.hand_status()
        st["edu_on"] = self.edu_on
        st["janken_cam"] = self.janken_cam
        st["teacher_only"] = self.teacher_only
        try:
            st["lesson"] = self.lesson.snapshot([{"id": d["id"], "name": d["name"], "online": d.get("online")} for d in st["devices"]]) if self.lesson else []
        except Exception:  # noqa: BLE001
            st["lesson"] = []
        st["game"] = self.game.snapshot() if self.game else None
        st["janken"] = self.janken.snapshot() if self.janken else None
        st["quiz"] = self.quiz.snapshot() if self.quiz else None
        st["face_styles"] = list(_devices.FACE_STYLES)
        st["journal"] = {"summary": self.discuss.journal.summary(), "items": self.discuss.journal.recent(20)} if self.discuss else None
        st["usb"] = self.usb.snapshot() if getattr(self, "usb", None) else []
        st["target"] = self.target
        st["view_dev"] = vdev
        if self.lat:
            st["lat_avg"] = round(sum(self.lat) / len(self.lat), 2)
            st["lat_best"], st["lat_worst"] = min(self.lat), max(self.lat)
        st["phrases"] = self.phrases
        st["turns"] = self.turns[:40]
        st["track"], st["listen"], st["turn_count"] = self.track, self.listen, self.turn_count
        if e is not None:
            st["speed"] = round(getattr(e, "speed_scale", 1.0), 2)
            vs_ = self.view_session()
            vv = (REG.get(getattr(vs_, "dev_id", "")).get("voice") if self.target != "all" and vs_ else "") or getattr(e, "voice_ja", "") or ""
            st["voice"] = str(vv).split(":")[-1]
            st["voices"] = self.voices()
            try:
                st["vad_th"] = round(float(e.vad_conf.silero_vad.threshold), 2)
            except Exception:  # noqa: BLE001
                pass
        now = time.time()
        stg = {}
        for k, v in self.stages.items():
            v = dict(v)
            v["age"] = round(now - v.get("t", now), 1)
            if k == "play":
                v["state"] = "active" if v.get("until", 0) > now else "idle"
            elif v.get("state") == "active" and v["age"] > 40:
                v["state"] = "idle"
            stg[k] = v
        st["stages"] = stg
        ms = [self.mics.get(d, (0.0, 0.0)) for d in tdevs]
        st["mic"] = {"level": round(min(1.0, max((m[0] for m in ms), default=0.0) * 6), 2), "live": any(now - m[1] < 2.0 for m in ms)}
        st["vad"] = any(now - self.vads.get(d, 0.0) < 0.8 for d in tdevs)
        st["events"] = self.events[:14]
        if e is not None:
            st["models"] = {
                "stt": {"name": e.stt_name(), "where": "s109 GPU" if e.remote_ear else "このサーバーのCPU"},
                "llm": [{"label": c[0].replace("このMac", "このサーバーCPU"), "model": c[2], "short": __import__("sys").modules["__main__"].short_model(c[2])} for c in getattr(e, "llm_all", [])],
                "tts": {l: (e.voice_name(l) if not (l == "ko" and not __import__("shutil").which("say") and str((e.tts.voices or {}).get("ko", "")).startswith("say:")) else "なし(字幕のみ)") for l in ("ja", "en", "zh", "ko")},
                "face": "YuNet(顔検出)",
            }
        st["sys"] = self._sys()
        st["services"] = self.services()
        st["mode"] = self.mode
        st["lat"] = self.lat[-12:]
        st["lat_all"] = self.lat[-30:]
        if self.lat:
            xs = sorted(self.lat)
            st["lat_med"] = xs[len(xs) // 2]
        if e is not None:
            import sys; short_model = sys.modules["__main__"].short_model
            st["model"] = short_model(getattr(e, "last_model", "") or (e.llm[0][2] if e.llm else ""))
        st["screen_age"] = int(time.time() - self.screen_t) if self.screen_t else None
        return st

    def act(self, name: str, body: dict, persist: bool = True) -> dict:
        r = self._act(name, body)
        if persist and r.get("ok") and name in ("speed", "voice", "vad", "engine", "track", "listen", "phrases", "lang", "topic", "gate", "wave", "kid_set", "edu_set", "janken_cam_set", "teacher_only_set"):
            self.save()
        return r

    def _act(self, name: str, body: dict) -> dict:
        if name == "phrases":
            out = []
            for x in (body.get("list") or [])[:12]:
                t = str(x.get("text", "")).strip()[:100]
                lg = str(x.get("lang", "ja"))
                if t and lg in ("ja", "en", "zh", "ko"):
                    out.append({"text": t, "lang": lg})
            self.phrases = out
            self.prewarm()
            return {"ok": True}
        if name == "face_register":
            nm = str(body.get("name", "")).strip()[:20]
            if not nm:
                return {"ok": False, "error": "名前を入れてください"}
            if not getattr(getattr(self.engine, "face", None), "sface", None):
                return {"ok": False, "error": "顔の識別モデルがありません(bridge/fetch_models.sh)"}
            self.reg_msg = ""
            v = self.view_session()
            if v is None:
                return {"ok": False, "error": "端末が接続していません"}
            self.reg = {"name": nm, "need": 6, "got": [], "t0": time.time(), "last": 0.0, "dev": v.dev_id}
            return {"ok": True}
        if name == "face_cancel":
            self.reg_end("登録をやめました")
            return {"ok": True}
        if name == "face_delete":
            return {"ok": True} if self.people.remove(str(body.get("name", ""))) else {"ok": False, "error": "その人は登録されていません"}
        if name == "wave":
            if "on" in body:
                self.wave_on = bool(body["on"])
            if "voice" in body:
                self.wave_voice = bool(body["voice"])
            return {"ok": True}
        if name == "gate":
            self.gate_mult = max(1.0, min(4.0, float(body.get("mult", 1.5))))
            return {"ok": True}
        if name == "engine":
            m = str(body.get("mode", ""))
            return {"ok": True} if self.set_mode(m) else {"ok": False, "error": "その置き場所は使えません"}
        if name == "clear":
            with self.lock:
                self.turns.clear(); self.events.clear(); self.lat.clear()
            return {"ok": True}
        if name == "track":
            self.track = bool(body.get("on", True))
            return {"ok": True}
        if name == "listen":
            self.listen = bool(body.get("on", True))
            return {"ok": True}
        if name == "speed":
            self.engine.speed_scale = max(0.7, min(1.5, float(body.get("scale", 1.0))))
            return {"ok": True}
        if name == "voice":
            vid = str(int(body.get("id", 3)))
            spec = f"voicevox:{vid}"
            tts = getattr(self.engine, "tts", None)
            if tts is None:
                return {"ok": False, "error": "声エンジンがありません"}
            dev = str(body.get("device") or self.target or "all")
            if body.get("default") or dev == "all":               # 既定の声(全機器に適用)
                self.engine.voice_ja = spec
                tts.voices = {**dict(tts.voices or {}), "ja": spec}
                if not body.get("default"):                       # 画面で「全機器」のときは、各機器の個別設定もそろえる
                    for rec in REG.all():
                        REG.set(rec["id"], voice=spec)
                    for x in self.live():
                        x.voice = spec
                return {"ok": True}
            x = self.by_id(dev)
            if not REG.get(dev):
                return {"ok": False, "error": "その機器は登録されていません"}
            REG.set(dev, voice=spec)                              # この機器だけの声
            if x is not None:
                x.voice = spec
            return {"ok": True}
        if name == "vad":
            th = max(0.3, min(0.9, float(body.get("threshold", 0.5))))
            self.engine.vad_conf.silero_vad.threshold = th
            for x in self.live():                     # 動いている接続の検出器も作り直して、すぐ効かせる
                try:
                    x.vad = self.engine.sherpa.VoiceActivityDetector(self.engine.vad_conf, buffer_size_in_seconds=60)
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": True}
        if name == "restart":
            svc = str(body.get("service", ""))
            if svc not in ("sc-bridge", "sc-voicevox", "sc-llm", "sc-s109"):
                return {"ok": False, "error": "不明なサービス"}
            subprocess.Popen(["systemctl", "--user", "restart", svc])
            return {"ok": True}
        if name == "face_style":                          # 機器の顔の種類を変える(台帳に覚えて、機器にも送る)
            dev = str(body.get("id", ""))
            try:
                st = int(body.get("style", 0))
            except (TypeError, ValueError):
                return {"ok": False, "error": "顔の番号が不正です"}
            if not 0 <= st < len(_devices.FACE_STYLES) or not REG.get(dev):
                return {"ok": False, "error": "不明な顔または機器です"}
            REG.set(dev, face=st)
            x = self.by_id(dev)
            if x is not None:
                x.send({"t": "faceStyle", "id": st})
            return {"ok": True}
        if name == "game_start":                          # 教材を始める。会話中・自律モードは先に止める(教材とぶつからないように)
            if not self.game:
                return {"ok": False, "error": "未対応"}
            if self.auto:
                self.auto.set(enabled=False)
            if self.discuss and self.discuss.on:
                self.discuss.stop()
                t0 = time.time()
                while self.discuss.on and time.time() - t0 < 8:
                    time.sleep(0.2)
            self.listen = True                            # 子どもの声を聞き取る(マイクがオフなら入れる)
            ids = body.get("devices")
            ok, err = self.game.start(str(body.get("first", "robot")), str(body.get("level", "easy")), list(ids) if isinstance(ids, list) and ids else None)
            return {"ok": True} if ok else {"ok": False, "error": err}
        if name == "janken_start":                        # じゃんけんを始める(しりとりと同じ: 会話・自律は止める。機器ごとに独立)
            if not self.janken:
                return {"ok": False, "error": "未対応"}
            if self.auto:
                self.auto.set(enabled=False)
            if self.discuss and self.discuss.on:
                self.discuss.stop()
                t0 = time.time()
                while self.discuss.on and time.time() - t0 < 8:
                    time.sleep(0.2)
            self.listen = True
            ids = body.get("devices")
            ok, err = self.janken.start("robot", "easy", list(ids) if isinstance(ids, list) and ids else None)
            return {"ok": True} if ok else {"ok": False, "error": err}
        if name == "janken_stop":
            if self.janken:
                self.janken.stop(str(body.get("id", "")) or None)
            return {"ok": True}
        if name == "janken_say":                          # 先生が、子どもの代わりに手(ぐー/ちょき/ぱー)を入れる
            if not self.janken or not self.janken.say(str(body.get("text", "")).strip()[:40], str(body.get("id", ""))):
                return {"ok": False, "error": "いまは子どもの番ではありません"}
            return {"ok": True}
        if name == "game_stop":
            if self.game:
                self.game.stop(str(body.get("id", "")) or None)
            return {"ok": True}
        if name == "game_say":                            # 先生が、子どもの代わりに言葉を入力する
            if not self.game or not self.game.say(str(body.get("text", "")).strip()[:40], str(body.get("id", ""))):
                return {"ok": False, "error": "いまは子どもの番ではありません"}
            return {"ok": True}
        if name == "edu_set":                             # 教材モード: オンにすると、翻訳・3人会話・自律会話を止め、子どもモードとマイクを入れる
            self.edu_on = bool(body.get("on", False))
            if self.edu_on:
                if self.auto:
                    self.auto.set(enabled=False)
                if self.discuss and self.discuss.on:
                    self.discuss.stop()
                self.kid_on = True
                self.listen = True
            else:
                if self.game:
                    self.game.stop(None)
                if self.janken:
                    self.janken.stop(None)
                if self.quiz:
                    self.quiz.stop(None)
            self.event("教材モード: " + ("オン(翻訳・3人会話・自律会話はオフ)" if self.edu_on else "オフ(ふつうの通訳に戻ります)"))
            return {"ok": True}
        if name == "lesson_start":                        # 先生が、機器ごと(省略なら全機器)に、遊びを始める。動いている遊びは、すぐ止めて切り替える
            if not self.lesson:
                return {"ok": False, "error": "未対応"}
            if self.auto:
                self.auto.set(enabled=False)
            if self.discuss and self.discuss.on:
                self.discuss.stop()
                t0 = time.time()
                while self.discuss.on and time.time() - t0 < 8:
                    time.sleep(0.2)
            self.listen = True
            ids = body.get("devices")
            if not ids and body.get("id"):
                ids = [str(body.get("id"))]
            ok, err = self.lesson.start(str(body.get("kind", "")), list(ids) if isinstance(ids, list) and ids else None)
            return {"ok": True} if ok else {"ok": False, "error": err}
        if name == "lesson_stop":                         # 中止: どの遊びでも、その機器(省略なら全機器)をすぐ止める。声も止める
            if self.lesson:
                self.lesson.stop(str(body.get("id", "")) or None)
            self.event("中止しました" + (f"({body.get('id')})" if body.get("id") else "(全機器)"))
            return {"ok": True}
        if name == "lesson_say":                          # 先生が、子どもの代わりに言葉・手・向き・答えを入れる(どの遊びでも)
            i = str(body.get("id", ""))
            t = str(body.get("text", "")).strip()[:40]
            ok = False
            for h in (self.game, self.janken, self.quiz):
                if h is not None and t and h.say(t, i):
                    ok = True
            return {"ok": True} if ok else {"ok": False, "error": "いまは子どもの番ではありません"}
        if name == "teacher_only_set":                    # 先生の操作だけで遊びを始める(オフなら、子どもが「あそぼう」「〇〇しよう」と言って始められる)
            self.teacher_only = bool(body.get("on", True))
            self.event("遊びの開始: " + ("先生のボタンだけ" if self.teacher_only else "子どもの声でも始められる"))
            return {"ok": True}
        if name == "quiz_start":                          # なぞなぞ・あっち向いてホイ・おみくじ・ものまねを始める(機器ごとに独立。会話・自律は先に止める)
            if not self.quiz:
                return {"ok": False, "error": "未対応"}
            if self.auto:
                self.auto.set(enabled=False)
            if self.discuss and self.discuss.on:
                self.discuss.stop()
                t0 = time.time()
                while self.discuss.on and time.time() - t0 < 8:
                    time.sleep(0.2)
            self.listen = True
            ids = body.get("devices")
            if not ids and body.get("id"):
                ids = [str(body.get("id"))]
            ok, err = self.quiz.start(str(body.get("kind", "riddle")), "", list(ids) if isinstance(ids, list) and ids else None)
            return {"ok": True} if ok else {"ok": False, "error": err}
        if name == "quiz_stop":
            if self.quiz:
                self.quiz.stop(str(body.get("id", "")) or None)
            return {"ok": True}
        if name == "quiz_say":                            # 先生が、子どもの代わりに答え・向き・言葉を入れる
            if not self.quiz or not self.quiz.say(str(body.get("text", "")).strip()[:40], str(body.get("id", ""))):
                return {"ok": False, "error": "いまは子どもの番ではありません"}
            return {"ok": True}
        if name == "janken_cam_set":                      # じゃんけんのカメラ認識のオン・オフ
            self.janken_cam = bool(body.get("on", True))
            self.event("じゃんけんのカメラ認識: " + ("オン" if self.janken_cam else "オフ(声だけ)"))
            return {"ok": True}
        if name == "kid_set":                             # 子どもモードのオン・オフ
            self.kid_on = bool(body.get("on", True))
            self.event("子どもモード: " + ("オン(話しかけると返事をします)" if self.kid_on else "オフ"))
            return {"ok": True}
        if name == "kid_auto_set":                        # 子どもモード: ロボットが自分から話しかける
            self.kid_auto = bool(body.get("on", True))
            self.event("自分から話しかける: " + ("オン" if self.kid_auto else "オフ(話しかけられたときだけ返事)"))
            return {"ok": True}
        if name == "fw_usb":                              # USB でつながっている機器のファームウェアを更新(id が空なら全部)
            u = getattr(self, "usb", None)
            if u is None:
                return {"ok": False, "error": "USB の監視が動いていません"}
            ok, msg = u.reflash(str(body.get("id", "")))
            self.event("ファームウェア更新(USB): " + msg)
            return {"ok": ok, "error": "" if ok else msg, "msg": msg}
        if name == "menu_set":                            # 教材モードで子どもに出す遊びを選ぶ
            self.menu_acts = [a for a in body.get("acts", []) if a in ACTS]
            self.event("子どもに出す遊び: " + "、".join(self.menu_acts or ["なし"]))
            return {"ok": True}
        if name == "kid_say":                             # 先生が、子どもの代わりに話しかける(声が聞き取れないとき・動作確認)
            x = self.by_id(str(body.get("id", ""))) or (self.live()[0] if self.live() else None)
            if x is None or not self.kid:
                return {"ok": False, "error": "機器がありません"}
            if self.kid.hear(x, str(body.get("text", "")), "ja"):
                return {"ok": True}
            return {"ok": False, "error": "いまは返事できません(子どもモードがオフ・会話中・しりとり中・話し中)"}
        if name == "auto_set":
            if not self.auto:
                return {"ok": False, "error": "未対応"}
            self.auto.set(body.get("enabled"), body.get("interval_min"), body.get("ignore_voice"))
            self.event("自律モード: " + ("オン" if self.auto.cfg["enabled"] else "オフ") + f"({self.auto.cfg['interval_min']}分おき)")
            return {"ok": True}
        if name == "discuss_stop":                      # 停止命令: いまの会話を止め、自律も止める(再開は自律モードをオンに)
            if self.auto:
                self.auto.set(enabled=False)
                self.event("停止命令: 自律モードをオフにしました")
            if self.discuss:
                self.discuss.stop()
            return {"ok": True}
        if name == "discuss_forget":
            if not self.discuss:
                return {"ok": False, "error": "未対応"}
            if self.discuss.on:
                return {"ok": False, "error": "会話中はリセットできません"}
            who = str(body.get("name", "")).strip()
            self.discuss.memory.reset(who or None)
            self.discuss.grew = ""
            self.event("学びをリセット: " + (who or "全員"))
            return {"ok": True}
        # ---- 機器の管理(複数台)
        if name == "device_select":
            dev = str(body.get("id", "all"))
            if dev != "all" and not REG.get(dev):
                return {"ok": False, "error": "その機器は登録されていません"}
            self.target = dev
            return {"ok": True}
        if name == "device_rename":
            did = str(body.get("id", ""))
            if not REG.rename(did, str(body.get("name", ""))):
                return {"ok": False, "error": "その名前は使えません(空、または他の機器と同じ)"}
            ss = self.by_id(did)
            if ss is not None:
                ss.name = REG.get(did).get("name", ss.name)
                ss.send({"t": "name", "name": ss.name})
            return {"ok": True}
        if name == "device_forget":
            dev = str(body.get("id", ""))
            if self.by_id(dev):
                return {"ok": False, "error": "接続中の機器は消せません(電源を切ってから)"}
            if self.target == dev:
                self.target = "all"
            return {"ok": True} if REG.forget(dev) else {"ok": False, "error": "その機器は登録されていません"}
        ss = self.targets(body)
        if not ss:
            return {"ok": False, "error": "端末が接続していません"}
        s = ss[0]
        if name == "device_setup":                        # 初期設定をやり直す
            for x in ss:
                x.auto_setup(REG.get(x.dev_id))
            return {"ok": True}
        if name == "volume":
            for x in ss:
                if "v" in body:
                    x.send({"t": "volume", "v": max(20, min(255, int(body["v"])))})
                else:
                    x.send({"t": "volume", "delta": int(body.get("delta", 0))})
        elif name == "lang":
            code = str(body.get("code", ""))
            if code not in ("en", "zh", "ko"):
                return {"ok": False, "error": "言語は en / zh / ko"}
            for x in ss:
                x.set_pair_b(code)
        elif name == "topic":
            t = str(body.get("text", "")).strip()[:40]
            if self.target == "all" and not body.get("device"):
                self.engine.topic_override = t
            for x in ss:
                x.topic = t
                x.history = []
                REG.set(x.dev_id, topic=t)
        elif name == "discuss_start":
            if self.edu_on:
                return {"ok": False, "error": "教材モードの間は、3人会話は使えません(教材モードをオフにしてください)"}
            if self.auto:
                self.auto.user_touched()
            ok, err = self.discuss.start(str(body.get("topic", "")), int(body.get("turns", 12) or 12)) if self.discuss else (False, "未対応")
            if not ok:
                return {"ok": False, "error": err}
        elif name == "discuss_say":
            if self.auto:
                self.auto.user_touched()
            if self.discuss:
                self.discuss.say(str(body.get("text", "")))
        elif name == "say":
            text = str(body.get("text", "")).strip()[:300]
            if not text:
                return {"ok": False, "error": "文章が空です"}
            for x in ss:
                x.say(text, str(body.get("lang", "ja")))
        elif name == "caption":                         # 字幕だけ出す(表示・字体の確認用)
            t = str(body.get("text", ""))[:120]
            lg = str(body.get("lang", "ja"))
            for x in ss:
                x.send({"t": "caption", "lang": lg, "to": lg, "orig": t, "trans": t, "final": True})
        elif name == "face":
            f = str(body.get("state", "neutral"))
            if f not in ("neutral", "happy", "sad", "listening", "thinking"):
                return {"ok": False, "error": "不明な表情"}
            for x in ss:
                x.send({"t": "face", "state": f})
        elif name == "neck":
            d = str(body.get("dir", "center"))
            moves = {"left": {"dpan": -90}, "right": {"dpan": 90}, "up": {"dtilt": -50}, "down": {"dtilt": 50},
                     "center": {"pan": 0, "tilt": 200}}
            if d not in moves:
                return {"ok": False, "error": "不明な向き"}
            for x in ss:
                x.send({"t": "servo", "speed": 300, **moves[d]})
        elif name == "bright":
            for x in ss:
                x.send({"t": "bright", "v": max(15, min(255, int(body.get("v", 140))))})
        elif name == "shot":
            s.send({"t": "shot"})                            # 画面は 1 台ずつ(対象が複数なら最初の 1 台)
        elif name == "recalibrate":
            for x in ss:
                threading.Thread(target=x.calibrate, daemon=True).start()
        else:
            return {"ok": False, "error": "不明な操作"}
        return {"ok": True}


HUB = Hub()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静かに
        pass

    def _auth(self) -> bool:
        if not TOKEN:
            return True
        q = self.path.split("token=", 1)[1].split("&")[0] if "token=" in self.path else ""
        return q == TOKEN or self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _send(self, code: int, ctype: str, data: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if not self._auth():
            return self._send(401, "text/plain; charset=utf-8", "token が必要です".encode())
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", open(os.path.join(HERE, "admin.html"), "rb").read())
        if p == "/api/state":
            return self._send(200, "application/json", json.dumps(HUB.state(), ensure_ascii=False).encode())
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        dev = next((kv.split("=", 1)[1] for kv in q.split("&") if kv.startswith("dev=")), "")
        if p == "/api/devices":
            return self._send(200, "application/json", json.dumps(HUB.devices_state(), ensure_ascii=False).encode())
        if p == "/api/mic.wav":
            return self._send(200, "audio/wav", HUB.mic_wav(dev))
        if p == "/api/logs":
            d = os.path.join(HERE, "logs")
            names = sorted((x[:-6] for x in os.listdir(d) if x.endswith(".jsonl")), reverse=True) if os.path.isdir(d) else []
            return self._send(200, "application/json", json.dumps(names).encode())
        if p == "/api/log.jsonl":
            day = time.strftime("%Y-%m-%d")
            if "date=" in self.path:
                cand = self.path.split("date=", 1)[1].split("&")[0]
                if len(cand) == 10 and cand.replace("-", "").isdigit():      # パスの書き換えを防ぐ
                    day = cand
            f = os.path.join(HERE, "logs", day + ".jsonl")
            return self._send(200, "application/x-ndjson; charset=utf-8", open(f, "rb").read() if os.path.exists(f) else b"")
        if p == "/api/hand.jpg":                    # 手の確認: 重ね描き済みのカメラ画像(画像が無ければ 404)
            did = next((kv.split("=", 1)[1] for kv in q.split("&") if kv.startswith("id=")), "")
            c = HUB.hand_view(did)
            return self._send(200, "image/jpeg", c["jpeg"]) if c else self._send(404, "text/plain", b"no frame")
        if p == "/screen.jpg":
            v = HUB.view_session()
            img = HUB.screens.get(dev or getattr(v, "dev_id", ""), HUB.screen)
            return self._send(200, "image/jpeg", img) if img else self._send(404, "text/plain", b"no screen")
        self._send(404, "text/plain", b"not found")

    def do_POST(self):  # noqa: N802
        if not self._auth():
            return self._send(401, "text/plain; charset=utf-8", "token が必要です".encode())
        n = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        name = self.path.split("?")[0].removeprefix("/api/")
        try:
            r = HUB.act(name, body)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        self._send(200, "application/json", json.dumps(r, ensure_ascii=False).encode())


def start(engine, port: int = 8080, host: str = "0.0.0.0") -> None:
    HUB.engine = engine
    HUB.load()
    HUB.prewarm()
    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[bridge] 管理画面 http://{host}:{port}/", flush=True)

"""管理画面(bridge/admin.py)の試験。端末・サーバー不要。"""
import os, re, sys, tempfile, unittest

_TMP = tempfile.mkdtemp()
os.environ["SC_JOURNAL"] = os.path.join(_TMP, "journal.json")
os.environ["SC_AUTONOMY"] = os.path.join(_TMP, "auto.json")
os.environ["SC_DEVICES"] = os.path.join(_TMP, "devices.json")       # 実際の台帳・顔データ・設定に触れない
os.environ["SC_FACES"] = os.path.join(_TMP, "faces.json")
os.environ["SC_SETTINGS"] = os.path.join(_TMP, "settings.json")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "bridge"))
import admin  # noqa: E402


class FakeSess:
    alive = True
    pair = ("ja", "en")
    batt = {"pct": 90, "chg": False}
    dbg = {"rssi": -55}
    vol = 120

    n = 0

    def __init__(self):
        FakeSess.n += 1
        self.dev_id, self.name = f"dev{FakeSess.n}", f"StackChan-{FakeSess.n}"
        admin.REG.touch(self.dev_id, "10.0.0.%d" % FakeSess.n)
        self.sent, self.said, self.pair_b = [], [], None
        self.topic, self.history = "", ["x"]
        self.talking = False

    def send(self, m): self.sent.append(m)
    def say(self, text, lang): self.said.append((text, lang))
    def set_pair_b(self, c): self.pair_b = c
    def calibrate(self): pass


class FakeEng:
    class stt: last_where = "s109"
    remote_ear = True
    topic_override = ""
    last_llm = "s109 GPU"
    llm = [("s109 GPU", "http://a", "m1"), ("このMac", "http://b", "m2")]
    llm_all = list(llm)

    def stt_name(self): return "fake-stt"
    def voice_name(self, lang): return "fake-voice"


def fresh():
    h = admin.Hub()
    h.engine = FakeEng()
    h.engine.llm = list(h.engine.llm_all)
    s = FakeSess()
    h.add_session(s)
    return h, s


class HubTests(unittest.TestCase):
    def test_edu_mode(self):
        h = admin.Hub()
        self.assertFalse(h.edu_on)
        self.assertTrue(h.act("edu_set", {"on": True})["ok"])
        self.assertTrue(h.edu_on and h.kid_on)
        r = h.act("discuss_start", {"topic": "てすと"})
        self.assertFalse(r["ok"])
        self.assertTrue(h.act("edu_set", {"on": False})["ok"])
        self.assertFalse(h.edu_on)

    def test_face_style(self):
        import devices
        h = admin.Hub()
        rec, _ = admin.REG.touch("aabbccddeeff", "1.2.3.4", "x")
        self.assertEqual(h._face_of(admin.REG.get("aabbccddeeff")), 0)            # discuss が無ければ標準
        self.assertFalse(h.act("face_style", {"id": "aabbccddeeff", "style": 99})["ok"])
        self.assertFalse(h.act("face_style", {"id": "nope", "style": 1})["ok"])
        self.assertTrue(h.act("face_style", {"id": "aabbccddeeff", "style": 4})["ok"])
        self.assertEqual(admin.REG.get("aabbccddeeff")["face"], 4)
        self.assertEqual(h._face_of(admin.REG.get("aabbccddeeff")), 4)
        self.assertEqual(len(devices.FACE_STYLES), 8)
        self.assertTrue(all(0 <= i < len(devices.FACE_STYLES) for i in devices.DEFAULT_FACE))

    def test_stop_command_stops_autonomy(self):
        import discuss, autonomy
        h = admin.Hub()
        h.discuss = discuss.Discussion(h, admin.REG, lambda: None, discuss.Memory(os.path.join(tempfile.mkdtemp(), "m.json")))
        h.auto = autonomy.Autonomy(h, admin.REG, h.discuss, lambda: None, os.path.join(tempfile.mkdtemp(), "a.json"))
        h.auto.set(enabled=True)
        self.assertTrue(h.act("discuss_stop", {})["ok"])
        self.assertFalse(h.auto.cfg["enabled"])                          # 停止命令で自律も止まる
        self.assertTrue(h.act("auto_set", {"enabled": True, "interval_min": 5})["ok"])
        self.assertTrue(h.auto.cfg["enabled"])
        st = h.state() if hasattr(h, "state") else {}
        self.assertIn("journal", st)

    def test_discuss_forget(self):
        import discuss
        h = admin.Hub()
        h.discuss = discuss.Discussion(h, admin.REG, lambda: None, discuss.Memory(os.path.join(tempfile.mkdtemp(), "m.json")))
        h.discuss.memory.learn("ずんだ", "旅", ["a"], {})
        h.discuss.memory.learn("めたん", "旅", ["b"], {})
        self.assertTrue(h.act("discuss_forget", {"name": "ずんだ"})["ok"])
        self.assertEqual((h.discuss.memory.get("ずんだ")["talks"], h.discuss.memory.get("めたん")["talks"]), (0, 1))
        h.act("discuss_forget", {})
        self.assertEqual(h.discuss.memory.get("めたん")["talks"], 0)
        h.discuss.on = True
        self.assertFalse(h.act("discuss_forget", {})["ok"])        # 会話中は消せない

    def test_no_device(self):
        h = admin.Hub(); h.engine = FakeEng()
        self.assertFalse(h.act("volume", {"v": 100})["ok"])

    def test_volume_clamped(self):
        h, s = fresh()
        h.act("volume", {"v": 999}); h.act("volume", {"v": 1}); h.act("volume", {"delta": -20})
        self.assertEqual(s.sent, [{"t": "volume", "v": 255}, {"t": "volume", "v": 20}, {"t": "volume", "delta": -20}])

    def test_lang_validation(self):
        h, s = fresh()
        self.assertFalse(h.act("lang", {"code": "fr"})["ok"])
        self.assertTrue(h.act("lang", {"code": "zh"})["ok"])
        self.assertEqual(s.pair_b, "zh")

    def test_topic_and_say(self):
        h, s = fresh()
        h.act("topic", {"text": "  医療 "})
        self.assertEqual((s.topic, h.engine.topic_override, s.history), ("医療", "医療", []))
        self.assertFalse(h.act("say", {"text": "  "})["ok"])
        h.act("say", {"text": "こんにちは", "lang": "ja"})
        self.assertEqual(s.said, [("こんにちは", "ja")])

    def test_engine_mode(self):
        h, _ = fresh()
        self.assertTrue(h.act("engine", {"mode": "local"})["ok"])
        self.assertEqual([c[0] for c in h.engine.llm], ["このMac"])
        self.assertTrue(h.act("engine", {"mode": "s109"})["ok"])
        self.assertEqual([c[0] for c in h.engine.llm], ["s109 GPU"])
        h.act("engine", {"mode": "auto"})
        self.assertEqual(len(h.engine.llm), 2)
        self.assertFalse(h.act("engine", {"mode": "nope"})["ok"])

    def test_restart_whitelist(self):
        h, _ = fresh()
        self.assertFalse(h.act("restart", {"service": "ssh"})["ok"])

    def test_state_and_latency(self):
        h, _ = fresh()
        for x in (1.0, 3.0, 2.0):
            h.add_latency(x)
        h.add_turn({"ts": "2026-01-01T00:00:00", "from": "ja", "to": "en", "orig": "a", "trans": "b"})
        sys.modules["__main__"].short_model = lambda n: n
        st = h.state()
        self.assertTrue(st["connected"])
        self.assertEqual(st["vol"], 120)
        self.assertEqual(st["lat_med"], 2.0)
        self.assertEqual(st["turns"][0]["trans"], "b")
        self.assertIn("このサーバー", FakeEng.last_llm.replace("このMac", "このサーバー") + "このサーバー")

    def test_pipeline_state(self):
        h, s0 = fresh()
        sys.modules["__main__"].short_model = lambda n: n
        h.stage("stt", "active", sec=2.0)
        h.stage("stt", "done", ms=180, text="こんにちは")
        h.stage("play", "active", until=__import__("time").time() + 5)
        h.mic(0.1, s0.dev_id)
        st = h.state()
        self.assertEqual(st["stages"]["stt"]["state"], "done")
        self.assertEqual(st["stages"]["play"]["state"], "active")
        self.assertTrue(st["mic"]["live"])
        self.assertEqual(st["models"]["stt"]["name"], "fake-stt")
        self.assertEqual(len(st["models"]["llm"]), 2)
        self.assertIn("聞き取り", st["events"][0]["msg"])

    def test_new_controls(self):
        h, s = fresh()
        self.assertEqual(h.act("neck", {"dir": "left"}), {"ok": True})
        self.assertEqual(s.sent[-1], {"t": "servo", "speed": 300, "dpan": -90})
        h.act("neck", {"dir": "center"})
        self.assertEqual((s.sent[-1]["pan"], s.sent[-1]["tilt"]), (0, 200))
        self.assertFalse(h.act("neck", {"dir": "spin"})["ok"])
        self.assertTrue(h.act("face", {"state": "happy"})["ok"])
        self.assertFalse(h.act("face", {"state": "evil"})["ok"])
        h.act("track", {"on": False}); h.act("listen", {"on": False})
        self.assertEqual((h.track, h.listen), (False, False))
        h.act("speed", {"scale": 9}); self.assertEqual(h.engine.speed_scale, 1.5)
        h.act("speed", {"scale": 0}); self.assertEqual(h.engine.speed_scale, 0.7)
        h.add_turn({"ts": "x", "from": "ja", "to": "en", "orig": "a", "trans": "b"})
        h.act("clear", {}); self.assertEqual(h.turns, [])

    def test_dead_session_dropped(self):
        h, s = fresh()
        s.alive = False
        self.assertIsNone(h.current())

    def test_html_has_endpoints(self):
        html = open(os.path.join(HERE, "..", "bridge", "admin.html"), encoding="utf-8").read()
        for ep in ("volume", "lang", "topic", "say", "engine", "restart", "shot", "bright"):
            self.assertIn(f"api('{ep}'", html, ep)




class MultiDeviceTests(unittest.TestCase):
    def two(self):
        h = admin.Hub(); h.engine = FakeEng()
        a, b = FakeSess(), FakeSess()
        h.add_session(a); h.add_session(b)
        return h, a, b

    def test_all_by_default(self):
        h, a, b = self.two()
        h.act("volume", {"v": 100})
        self.assertEqual(a.sent[-1]["v"], 100)
        self.assertEqual(b.sent[-1]["v"], 100)

    def test_select_one_device(self):
        h, a, b = self.two()
        self.assertTrue(h.act("device_select", {"id": b.dev_id})["ok"])
        h.act("volume", {"v": 60})
        self.assertEqual(len(a.sent), 0)
        self.assertEqual(b.sent[-1]["v"], 60)
        h.act("lang", {"code": "zh"})
        self.assertIsNone(a.pair_b)
        self.assertEqual(b.pair_b, "zh")
        self.assertEqual(h.state()["view_dev"], b.dev_id)

    def test_device_in_body_overrides(self):
        h, a, b = self.two()
        h.act("say", {"text": "やあ", "lang": "ja", "device": a.dev_id})
        self.assertEqual(a.said, [("やあ", "ja")])
        self.assertEqual(b.said, [])

    def test_devices_list_and_rename_forget(self):
        h, a, b = self.two()
        d = h.state()["devices"]
        self.assertEqual({x["id"] for x in d if x["online"]}, {a.dev_id, b.dev_id})
        self.assertTrue(h.act("device_rename", {"id": a.dev_id, "name": "リビング"})["ok"])
        self.assertFalse(h.act("device_forget", {"id": a.dev_id})["ok"])      # 接続中は消せない
        a.alive = False
        self.assertTrue(h.act("device_forget", {"id": a.dev_id})["ok"])
        self.assertNotIn(a.dev_id, [x["id"] for x in h.state()["devices"]])

    def test_topic_per_device(self):
        h, a, b = self.two()
        h.act("topic", {"text": "医療", "device": a.dev_id})
        self.assertEqual((a.topic, b.topic), ("医療", ""))
        self.assertEqual(admin.REG.get(a.dev_id).get("topic"), "医療")


if __name__ == "__main__":
    unittest.main()


class VoicePerDeviceTests(unittest.TestCase):
    def setUp(self):
        class Tts: voices = {"ja": "voicevox:3"}
        self.h = admin.Hub()
        self.h.engine = FakeEng()
        self.h.engine.tts = Tts()
        self.h.engine.voice_ja = "voicevox:3"
        self.a, self.b = FakeSess(), FakeSess()
        self.h.add_session(self.a); self.h.add_session(self.b)
        for x in (self.a, self.b):
            admin.REG.touch(x.dev_id, "10.0.0.1")
            x.voice = None

    def test_one_device_only(self):
        self.h.act("device_select", {"id": self.b.dev_id})
        self.assertTrue(self.h.act("voice", {"id": 2})["ok"])
        self.assertEqual(self.b.voice, "voicevox:2")
        self.assertIsNone(self.a.voice)
        self.assertEqual(admin.REG.get(self.b.dev_id)["voice"], "voicevox:2")
        self.assertEqual(self.h.engine.voice_ja, "voicevox:3")          # 既定は変わらない
        self.assertEqual(self.h.state()["voice"], "2")

    def test_all_sets_everyone_and_default_only_on_load(self):
        self.h.act("voice", {"id": 8})
        self.assertEqual((self.a.voice, self.b.voice, self.h.engine.voice_ja), ("voicevox:8",) * 3)
        self.h.act("device_select", {"id": self.a.dev_id})
        self.h.act("voice", {"id": 1})
        self.h.act("voice", {"id": 5, "default": True})                  # 起動時の復元: 個別の声は触らない
        self.assertEqual(self.a.voice, "voicevox:1")
        self.assertEqual(self.h.engine.voice_ja, "voicevox:5")

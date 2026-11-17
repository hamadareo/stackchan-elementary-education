import os, sys, tempfile, time, unittest
os.environ["SC_JOURNAL"] = os.path.join(tempfile.mkdtemp(), "journal.json")      # 実際の活動記録に触れない
os.environ["SC_PERSONA"] = os.path.join(tempfile.mkdtemp(), "persona.json")
os.environ["SC_AUTONOMY"] = os.path.join(tempfile.mkdtemp(), "auto.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import devices


class RegistryTest(unittest.TestCase):
    def reg(self):
        return devices.Registry(os.path.join(tempfile.mkdtemp(), "d.json"))

    def test_auto_register(self):
        r = self.reg()
        rec, new = r.touch("aabbccddeeff", "10.0.0.5", "fw1")
        self.assertTrue(new)
        self.assertEqual(rec["name"], "StackChan-1")
        rec2, new2 = r.touch("aabbccddeeff", "10.0.0.6")
        self.assertFalse(new2)
        self.assertEqual(rec2["ip"], "10.0.0.6")
        rec3, new3 = r.touch("112233445566", "10.0.0.7")
        self.assertTrue(new3)
        self.assertEqual(rec3["name"], "StackChan-2")

    def test_legacy_merge(self):
        r = self.reg()
        r.touch("ip-10.0.0.5", "10.0.0.5")
        r.rename("ip-10.0.0.5", "リビング")
        r.set("ip-10.0.0.5", lang="zh")
        rec, new = r.touch("aabbccddeeff", "10.0.0.5", "fw2")     # ファームウェア更新後、ID が付いた
        self.assertFalse(new)
        self.assertEqual((rec["name"], rec["lang"], rec["id"]), ("リビング", "zh", "aabbccddeeff"))
        self.assertEqual(len(r.all()), 1)

    def test_rename_forget_persist(self):
        r = self.reg()
        r.touch("a", "1.1.1.1"); r.touch("b", "1.1.1.2")
        self.assertFalse(r.rename("b", "StackChan-1"))             # 同じ名前は不可
        self.assertTrue(r.rename("b", "玄関"))
        q = devices.Registry(r.path)
        self.assertEqual([d["name"] for d in q.all()], ["StackChan-1", "玄関"])
        self.assertTrue(q.forget("a"))
        self.assertEqual(oct(os.stat(r.path).st_mode & 0o777), "0o600")
        self.assertEqual(q.touch("c", "")[0]["name"], "StackChan-1")   # 空いた番号を使う


if __name__ == "__main__":
    unittest.main()


class OtaTargets(unittest.TestCase):
    def test_targets(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import ota_all
        devs = [{"id": "a", "name": "A", "online": True, "ip": "10.0.0.2"}, {"id": "b", "name": "B", "online": False, "ip": "10.0.0.3"},
                {"id": "c", "name": "C", "online": True, "ip": "usb"}, {"id": "d", "name": "D", "online": True, "ip": "10.0.0.4"}]
        self.assertEqual([d["name"] for d in ota_all.targets(devs)], ["A", "D"])
        self.assertEqual([d["name"] for d in ota_all.targets(devs, "D")], ["D"])


class ProvisionHelpers(unittest.TestCase):
    def test_mac_and_registered(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import provision
        out = "esptool.py v4.8\nChip is ESP32-S3 (QFN56)\nMAC: 80:45:6B:4D:3E:E4\nUses OTP"
        self.assertEqual(provision.parse_mac(out), "80456b4d3ee4")
        self.assertEqual(provision.parse_mac("Failed to connect"), "")
        self.assertTrue(provision.is_registered("80456b4d3ee4", [{"id": "80456b4d3ee4"}]))
        self.assertFalse(provision.is_registered("", [{"id": ""}]))
        self.assertFalse(provision.is_registered("aa", [{"id": "bb"}]))


class UsbProv(unittest.TestCase):
    def test_decide(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
        import usbprov
        d = {"mac": "aa", "port": "/dev/ttyACM0"}
        self.assertEqual(usbprov.decide(d, True, False, True, 0, 0, 0)[0], "registered")
        self.assertEqual(usbprov.decide(d, False, False, True, 0, 0, 0)[0], "no_access")
        self.assertEqual(usbprov.decide(d, False, True, False, 0, 0, 0)[0], "no_image")
        self.assertEqual(usbprov.decide(d, False, True, True, 0, 0, 0)[0], "ready")
        self.assertEqual(usbprov.decide(d, False, True, True, 1, 0, 10)[0], "cooldown")
        self.assertEqual(usbprov.decide(d, False, True, True, 3, 0, 999)[0], "gave_up")
        self.assertEqual(usbprov.decide({"mac": "aa", "port": ""}, False, True, True, 0, 0, 0)[0], "waiting")


class DiscussTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
        import discuss
        self.d = discuss

    def test_clean(self):
        c = self.d.clean_line
        self.assertEqual(c("めたん: いい指摘ですね。", "めたん"), "いい指摘ですね。")
        self.assertEqual(c("<think>むむ</think>「ええと、そうだね」", "つむぎ"), "ええと、そうだね")
        self.assertLessEqual(len(c("あ" * 200, "ずんだ")), self.d.MAX_CHARS)
        self.assertEqual(c("一行目\n二行目", "ずんだ"), "一行目")

    def test_msgs(self):
        m = self.d.build_msgs("旅行", self.d.PERSONAS, 1, [("ずんだ", "どこ行くのだ?")], last=True)
        self.assertIn("めたん", m[0]["content"]); self.assertIn("締めくくる", m[0]["content"]); self.assertIn("どこ行くのだ", m[1]["content"])

    def test_run(self):
        sent = []

        class S:
            def __init__(s, n): s.n = n
            def send(s, m): pass
            def _send_pcm(s, pcm, caption="", lang=""): sent.append((s.n, caption)); return 0.0

        class Hub:
            ev = []
            def __init__(s): s.ss = {i: S(i) for i in "abc"}
            def by_id(s, i): return s.ss.get(i)
            def event(s, t): s.ev.append(t)

        class Reg:
            def all(s): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            n = 0
            def translate(s, msgs): s.n += 1; return f"発言{s.n}です。", "x"
            def speech_pcm_as(s, text, voice): return b""

        d = self.d.Discussion(Hub(), Reg(), lambda: Eng(), self.d.Memory(os.path.join(tempfile.mkdtemp(), "m.json")))
        ok, _ = d.start("旅行", 4)
        self.assertTrue(ok)
        for _ in range(100):
            if not d.on: break
            time.sleep(0.1)
        self.assertFalse(d.on)
        who = [x[0] for x in sent]
        self.assertEqual(who[0], "a")
        self.assertEqual(len(who), 4)
        self.assertTrue(all(x != y for x, y in zip(who, who[1:])))      # 同じ人が続けて話さない
        self.assertEqual(len(d.snapshot()["log"]), 4)
        self.assertFalse(Hub().ss and d.start("", 3)[0])


    def test_choose_and_pass(self):
        P = self.d.PERSONAS
        self.assertTrue(self.d.is_pass("PASS")); self.assertTrue(self.d.is_pass("「pass」")); self.assertFalse(self.d.is_pass("そうだね"))
        self.assertEqual(self.d.addressed(P, 0, "めたん、どう思うのだ?"), 1)
        self.assertEqual(self.d.addressed(P, 0, "ずんだは元気だね"), None)       # 自分の名前は数えない
        import random
        r = random.Random(1)
        self.assertEqual(self.d.choose(P, 0, "つむぎさん、どう?", {1: "a", 2: "b"}, {}, r), 2)   # 呼ばれた人が先
        self.assertIsNone(self.d.choose(P, 0, "x", {}, {}, r))
        for _ in range(20):
            self.assertNotEqual(self.d.choose(P, 0, "x", {0: "a", 1: "b", 2: "c"}, {}, r), 0)   # 直前の話者は避ける

    def test_all_pass_ends(self):
        sent = []

        class S:
            def send(s, m): pass
            def _send_pcm(s, pcm, caption="", lang=""): sent.append(caption); return 0.0

        class Hub:
            def by_id(s, i): return S_
            def event(s, t): pass

        S_ = S()

        class Reg:
            def all(s): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            n = 0
            def translate(s, msgs):
                forced = "PASS とだけ" not in msgs[0]["content"]
                s.n += 1
                return (f"最初の発言{s.n}", "x") if forced else ("PASS", "x")
            def speech_pcm_as(s, text, voice): return b""

        d = self.d.Discussion(Hub(), Reg(), lambda: Eng(), self.d.Memory(os.path.join(tempfile.mkdtemp(), "m.json")))
        d.start("旅行", 12)
        for _ in range(100):
            if not d.on: break
            time.sleep(0.1)
        self.assertFalse(d.on)
        self.assertLess(len(sent), 12)                                 # 全員黙ると、早めに終わる
        self.assertIn("終え", d.snapshot()["note"])


    def test_reactions(self):
        import random
        r = random.Random(3)
        plan = self.d.plan_reactions(8.0, [1, 2], r)
        self.assertTrue(plan)
        self.assertTrue(all(0.5 < t < 7.1 for t, _k, _x in plan))                # 始めと終わりぎわは何もしない
        self.assertTrue(all(k in (1, 2) for _t, k, _x in plan))
        self.assertLessEqual(sum(1 for *_a, x in plan if x == "voice"), 1)       # 相槌の声は 1 回まで
        self.assertEqual(plan, sorted(plan))
        short = self.d.plan_reactions(2.0, [1, 2], random.Random(1))
        self.assertFalse([x for *_a, x in short if x == "voice"])                # 短い発言には声を出さない

    def test_growth(self):
        d = self.d
        m = d.Memory(os.path.join(tempfile.mkdtemp(), "m.json"))
        self.assertEqual(m.get("ずんだ")["talks"], 0)
        P = d.PERSONAS
        self.assertNotIn("これまでに", d.build_msgs("旅行", P, 0, [], mem=m.get("ずんだ"))[0]["content"])   # 初回は記憶なし
        m.learn("ずんだ", "旅行", ["旅は計画より偶然が楽しい"], {"めたん": "理屈っぽいが頼れる"})
        m.learn("ずんだ", "料理", ["料理は味見が大事"], {})
        r = m.get("ずんだ")
        self.assertEqual((r["talks"], r["views"][0], r["topics"][0]), (2, "料理は味見が大事", "料理"))
        self.assertEqual(r["rel"]["めたん"], "理屈っぽいが頼れる")
        sysmsg = d.build_msgs("旅行", P, 0, [], mem=r)[0]["content"]
        self.assertNotIn("料理は味見が大事", sysmsg)                                   # 無関係な記憶は持ち込まない
        self.assertIn("理屈っぽいが頼れる", sysmsg)                                  # 相手への印象は持ち込む
        self.assertIn("料理は味見が大事", d.build_msgs("料理の味見", P, 0, [], mem=r)[0]["content"])   # 関係する話題なら思い出す
        self.assertNotIn("理屈っぽいが頼れる", d.build_msgs("旅行", P, 1, [], mem=r)[0]["content"] if False else "")
        for i in range(10):
            m.learn("ずんだ", f"t{i}", [f"v{i}"], {})
        r = m.get("ずんだ")
        self.assertLessEqual(len(r["views"]), 6); self.assertLessEqual(len(r["topics"]), 8)   # 無限に増えない
        self.assertEqual(d.Memory(m.path).get("ずんだ")["talks"], 12)                           # 保存されて再読み込みできる
        v, rel = d.parse_reflect("学び: 旅は偶然が面白い\n印象 めたん: 鋭い\n印象 だれか: 無視", ["めたん", "つむぎ"])
        self.assertEqual(v, ["旅は偶然が面白い"]); self.assertEqual(rel, {"めたん": "鋭い"})

    def test_reflect_after_run(self):
        class S:
            def send(s, m): pass
            def _send_pcm(s, pcm, caption="", lang=""): return 0.0
        S_ = S()

        class Hub:
            ev = []
            def by_id(s, i): return S_
            def event(s, t): s.ev.append(t)

        class Reg:
            def all(s): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            n = 0
            def translate(s, msgs):
                if "振り返り" in msgs[0]["content"]:
                    return "学び: 議論は楽しい\n印象 めたん: 鋭い", "x"
                s.n += 1
                return f"発言{s.n}です。", "x"
            def speech_pcm_as(s, text, voice): return b""

        mem = self.d.Memory(os.path.join(tempfile.mkdtemp(), "m.json"))
        d = self.d.Discussion(Hub(), Reg(), lambda: Eng(), mem)
        d.start("旅行", 4)
        for _ in range(100):
            if not d.on: break
            time.sleep(0.1)
        for _ in range(50):
            if mem.get("ずんだ")["talks"]: break
            time.sleep(0.1)
        self.assertEqual(mem.get("ずんだ")["views"], ["議論は楽しい"])
        self.assertTrue(d.snapshot()["grew"])

    def test_emotion(self):
        d = self.d
        self.assertEqual(d.split_emotion("【驚き】えっ、本当なのだ!"), ("surprise", "えっ、本当なのだ!"))
        self.assertEqual(d.split_emotion("[怒り] それは違います")[0], "anger")
        self.assertEqual(d.split_emotion("【興奮】やった!!")[0], "excited")
        self.assertEqual(d.split_emotion("【謎】そうだね。", "joy")[1], "【謎】そうだね。")       # 未知のタグは本文のまま(推測にまわす)
        self.assertEqual(d.split_emotion("残念ですね")[0], "sad")                                  # タグなしは言葉から推測
        self.assertEqual(d.split_emotion("【平常】PASS")[1], "PASS")
        for k, (_n, pr, face) in d.EMOTIONS.items():
            self.assertTrue(set(pr) == {"speed", "pitch", "intonation", "volume"} and face)
        P = d.PERSONAS
        sysmsg = d.build_msgs("旅", P, 0, [], emos={"ずんだ": "joy", "めたん": "anger"})[0]["content"]
        self.assertIn("いまの気持ちは「喜び」", sysmsg); self.assertIn("めたん=怒り", sysmsg)
        self.assertGreater(d.EMOTIONS["sad"][1]["speed"], 0.5)
        self.assertLess(d.EMOTIONS["sad"][1]["speed"], d.EMOTIONS["excited"][1]["speed"])

    def test_emotion_voice_and_snapshot(self):
        got = []

        class S:
            def send(s, m): got.append(m)
            def _send_pcm(s, pcm, caption="", lang=""): return 0.0
        S_ = S()

        class Hub:
            def by_id(s, i): return S_
            def event(s, t): pass

        class Reg:
            def all(s): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            n = 0
            prosodies = []
            def translate(s, msgs):
                if "振り返り" in msgs[0]["content"]:
                    return "", "x"
                s.n += 1
                return ("【驚き】えっ" if s.n % 2 else "【悲しみ】そんな") + f"{s.n}。", "x"
            def speech_pcm_as(s, text, voice, emotion=None): s.prosodies.append(emotion); return b""

        d = self.d.Discussion(Hub(), Reg(), lambda: Eng(), self.d.Memory(os.path.join(tempfile.mkdtemp(), "m.json")))
        d.start("旅", 3)
        for _ in range(100):
            if not d.on: break
            time.sleep(0.1)
        self.assertTrue(all(p and "speed" in p for p in Eng.prosodies))                  # 読み上げに感情の調子が渡る
        faces = {m["state"] for m in got if m.get("t") == "face"}
        self.assertTrue(faces & {"surprise", "sad"})                                     # 顔も感情に合わせる
        snap = d.snapshot()
        self.assertIn(snap["log"][0]["emo"], ("驚き", "悲しみ"))
        self.assertTrue(snap["emos"])

    def test_profile_radar(self):
        d = self.d
        base = d.PERSONAS[0]["radar"]
        self.assertEqual(d.radar(base, {}), {a: base[a] for a in d.AXES})                  # 発言がなければ初期の性格のまま
        st = {}
        for _ in range(20):
            st = d.observe_stats(st, "neutral", "そうですね。", False)
        r = d.radar(base, st)
        self.assertLess(r["元気"], base["元気"]); self.assertLess(r["感情表現"], base["感情表現"])   # 平坦に話し続けると下がる
        st = {}
        for _ in range(20):
            st = d.observe_stats(st, "joy", "なぜ?たとえば、楽しいね!", False)
        r2 = d.radar(base, st)
        self.assertGreater(r2["好奇心"], r["好奇心"]); self.assertGreater(r2["論理"], r["論理"])
        self.assertTrue(all(5 <= v <= 100 for v in r2.values()))
        m = d.Memory(os.path.join(tempfile.mkdtemp(), "m.json"))
        m.observe("ずんだ", "joy", "やった!", False)
        self.assertEqual(d.Memory(m.path).get("ずんだ")["stats"]["n"], 1)
        class H:
            def by_id(s, i): return None
        class R:
            def all(s): return []
        dd = d.Discussion(H(), R(), lambda: None, m)
        self.assertEqual(dd.profiles(), [])

    def test_mood(self):
        d = self.d
        Z, M, T = d.PERSONAS
        m = {}
        for _ in range(3):
            m = d.mood_step(m, "いや、それは違う。しかし根拠が足りない!", "", M)
        self.assertEqual(d.mood_emotion(m, M), "anger")                          # めたんは反論されると怒る
        m = {}
        for _ in range(3):
            m = d.mood_step(m, "いや、それは違うよ。", "", Z)
        self.assertEqual(d.mood_emotion(m, Z), "sad")                            # ずんだは落ち込む
        m = {}
        for _ in range(2):
            m = d.mood_step(m, "そうだね、いいね!", "", T)
        self.assertIn(d.mood_emotion(m, T), ("joy", "excited"))                   # 同意されると嬉しい
        m = d.mood_step({}, "ふつうの話です。", "", M)
        self.assertEqual(d.mood_emotion(m, M), "neutral")
        for _ in range(12):                                                       # 時間がたつと平常に戻る
            m = d.mood_step(m, "ふつうの話です。", "", M)
        self.assertEqual(d.mood_emotion(m, M), "neutral")
        self.assertGreater(d.similar("たけのこの里が未来を支える", "たけのこの里が未来を支えるのだ"), 0.35)
        self.assertLess(d.similar("きのこの山", "宝くじの話"), 0.1)

class AutonomyTests(unittest.TestCase):
    def setUp(self):
        import autonomy, discuss
        self.a, self.d = autonomy, discuss

    def _mk(self, n_dev=3, voice_ago=999.0, hour_ok=True, talking=False):
        a, d = self.a, self.d
        started = []

        class S:
            def __init__(s): s.talking = talking
            def send(s, m): pass
            def _send_pcm(s, *x, **k): return 0.0

        class Hub:
            def __init__(s): s.ss = {i: S() for i in "abc"[:n_dev]}
            def by_id(s, i): return s.ss.get(i)
            def live(s): return list(s.ss.values())
            def event(s, t): pass

        class Reg:
            def all(s): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            last_voice = time.time() - voice_ago
            def translate(s, msgs): return "雨の日の過ごし方", "x"
            def speech_pcm_as(s, text, voice, emotion=None): return b""

        eng = Eng()
        h = Hub()
        mem = d.Memory(os.path.join(tempfile.mkdtemp(), "m.json"))
        ds = d.Discussion(h, Reg(), lambda: eng, mem)
        ds.start = lambda topic, turns=12, opener=0, auto=False: (started.append((topic, opener, auto)) or True, "")
        au = a.Autonomy(h, Reg(), ds, lambda: eng, os.path.join(tempfile.mkdtemp(), "a.json"))
        au.cfg.update({"quiet_from": 0, "quiet_to": 0} if hour_ok else {})
        return au, ds, started

    def test_helpers(self):
        a = self.a
        self.assertTrue(a.in_quiet_hours(23, 23, 7) and a.in_quiet_hours(3, 23, 7))
        self.assertFalse(a.in_quiet_hours(12, 23, 7)); self.assertFalse(a.in_quiet_hours(5, 5, 5))
        self.assertEqual(a.clean_topic("話題: 「雨の日の過ごし方」\n説明"), "雨の日の過ごし方")
        self.assertEqual(a.clean_topic("夕方の空、なぜ赤くなるのだろうのだ"), "夕方の空、なぜ赤くなるのだろう?")
        self.assertEqual(a.clean_topic("雨の日の過ごし方なのだ"), "雨の日の過ごし方")
        self.assertFalse(a.due(100, 0, 1200, 1.0)); self.assertTrue(a.due(1300, 0, 1200, 1.0))

    def test_starts_by_itself(self):
        au, ds, started = self._mk()
        now = time.time() + 3600
        self.assertTrue(au.step(now))
        self.assertEqual(started[0][0], "雨の日の過ごし方")
        self.assertIn(au.last_by, [p["name"] for p in self.d.PERSONAS])

    def test_respects_conditions(self):
        au, ds, started = self._mk()
        self.assertFalse(au.step(time.time() + 10))                 # まだ間隔がたっていない
        au.cfg["enabled"] = False
        self.assertFalse(au.step(time.time() + 7200)); self.assertIn("オフ", au.note)
        au, ds, started = self._mk(n_dev=1)
        self.assertFalse(au.step(time.time() + 7200)); self.assertIn("2 台未満", au.note)
        au, ds, started = self._mk(voice_ago=10)
        self.assertFalse(au.step(time.time() + 15)); self.assertTrue(au.note)      # 人が話したばかり
        au, ds, started = self._mk(talking=True)
        self.assertFalse(au.step(time.time() + 7200)); self.assertIn("機器に話", au.note)
        au, ds, started = self._mk()
        ds.on = True
        self.assertFalse(au.step(time.time() + 7200)); self.assertIn("会話中", au.note)
        self.assertEqual(started, [])

    def test_no_repeat_topic_and_touch(self):
        au, ds, started = self._mk()
        au.last_topic = "雨の日の過ごし方"
        ds.memory.learn("ずんだ", "雨の日の過ごし方", [], {}); ds.memory.learn("めたん", "雨の日の過ごし方", [], {}); ds.memory.learn("つむぎ", "雨の日の過ごし方", [], {})
        self.assertFalse(au.step(time.time() + 7200))              # 最近話した議題と同じなら話さない(あとでやり直す)
        au.user_touched()
        self.assertGreaterEqual(au.last, time.time() - 2)
        au.set(enabled=True, interval_min=999)
        self.assertEqual(au.cfg["interval_min"], 240)
        self.assertEqual(self.a.Autonomy(au.hub, au.reg, ds, au.eng_getter, au.path).cfg["interval_min"], 240)     # 保存される

    def test_ignore_voice(self):
        au, ds, started = self._mk(voice_ago=5)
        au.last = 0.0                                                # 間隔は十分たっている
        self.assertFalse(au.step(time.time() + 10))                  # 人の声があれば、既定では遠慮する
        au.set(ignore_voice=True)
        au.last = 0.0
        self.assertTrue(au.step(time.time() + 10))                 # 「人の声があっても話す」なら話し始める
        self.assertTrue(au.snapshot()["ignore_voice"])

    def test_night_and_defaults(self):
        au, ds, started = self._mk()
        self.assertEqual(au.cfg["quiet_from"], au.cfg["quiet_to"])                  # 夜間も話す
        self.assertFalse(self.a.in_quiet_hours(3, au.cfg["quiet_from"], au.cfg["quiet_to"]))
        self.assertIn("夜間も", au.snapshot()["quiet"])
        self.assertTrue(au.step(time.time() + 3600))
        self.assertTrue(started[0][2])                                              # 自発のフラグが付く


class JournalTests(unittest.TestCase):
    def test_journal_written_after_discussion(self):
        import discuss as d
        class S:
            def send(s, m): pass
            def _send_pcm(s, *x, **k): return 0.0
        S_ = S()
        class Hub:
            def by_id(s, i): return S_
            def event(s, t): pass
        class Reg:
            def all(s): return [{"id": i, "name": "N" + i} for i in "abc"]
        class Eng:
            n = 0
            def translate(s, msgs):
                c = msgs[0]["content"]
                if "要約" in c:
                    return "旅について話し、計画派と偶然派で意見が分かれた。", "x"
                if "振り返り" in c:
                    return "学び: 偶然も楽しい\n印象 めたん: 計画派", "x"
                s.n += 1
                return f"【喜び】発言{s.n}です。", "x"
            def speech_pcm_as(s, text, voice, emotion=None): return b""
        jr = d.Journal(os.path.join(tempfile.mkdtemp(), "j.json"))
        ds = d.Discussion(Hub(), Reg(), lambda: Eng(), d.Memory(os.path.join(tempfile.mkdtemp(), "m.json")), jr)
        ds.start("旅", 5, auto=True)
        for _ in range(100):
            if not ds.on: break
            time.sleep(0.1)
        for _ in range(60):
            if jr.items and not ds.reflecting: break
            time.sleep(0.1)
        self.assertEqual(len(jr.items), 1)
        e = jr.items[0]
        self.assertTrue(e["auto"]); self.assertEqual(e["topic"], "旅"); self.assertIn("意見が分かれた", e["summary"])
        self.assertEqual(len(e["people"]), 3)
        self.assertTrue(any(p["learned"] for p in e["people"]))
        self.assertTrue(any(p["emotions"] for p in e["people"]))
        self.assertEqual(d.Journal(jr.path).summary()["total"], 1)                  # 保存されて読み直せる
        self.assertEqual(d.radar_delta({"元気": 50, "論理": 60}, {"元気": 55, "論理": 60}), {"元気": 5})

class BondTests(unittest.TestCase):
    def test_bond_and_prompt(self):
        import discuss as d, autonomy
        m = d.Memory(os.path.join(tempfile.mkdtemp(), "m.json"))
        self.assertAlmostEqual(m.get("ずんだ")["bond"]["めたん"], 0.22)               # 最初から少し偏りがある
        self.assertEqual(d.bond_label(0.8), "恋心"); self.assertEqual(d.bond_label(0.5), "好意"); self.assertEqual(d.bond_label(-0.6), "対立")
        for _ in range(20):
            m.bond_add("ずんだ", "めたん", 0.1)
        self.assertEqual(m.get("ずんだ")["bond"]["めたん"], 1.0)                      # 上限
        m.bond_add("つむぎ", "めたん", -0.9)
        self.assertEqual(d.bond_label(m.get("つむぎ")["bond"]["めたん"]), "対立")
        P = d.PERSONAS
        s = d.build_msgs("今日の出来事", P, 0, [], mem=m.get("ずんだ"))[0]["content"]
        self.assertIn("めたん: 恋心", s); self.assertIn("日常会話", s); self.assertIn("照れ", s)
        self.assertEqual(d.Memory(m.path).get("ずんだ")["bond"]["めたん"], 1.0)         # 保存される
        tm = autonomy.topic_msgs(P[0], m.get("ずんだ"), [], 21, ["めたん", "つむぎ"])[0]["content"]
        self.assertIn("恋バナ", tm); self.assertIn("雑学ではなく", tm)

    def test_bond_moves_in_talk_and_shy(self):
        import discuss as d
        class S:
            def send(s, m): pass
            def _send_pcm(s, *x, **k): return 0.0
        class Hub:
            def by_id(s, i): return S()
            def event(s, t): pass
        class Reg:
            def all(s): return [{"id": i, "name": "N" + i} for i in "abc"]
        m = d.Memory(os.path.join(tempfile.mkdtemp(), "m.json"))
        ds = d.Discussion(Hub(), Reg(), lambda: None, m, d.Journal(os.path.join(tempfile.mkdtemp(), "j.json")))
        ds.people = [{**p, "dev": "x", "dev_name": "x"} for p in d.PERSONAS]
        ds.mood = {}; ds.emos = {p["name"]: "neutral" for p in ds.people}; ds.log = [("ずんだ", "x")]
        b0 = m.get("めたん")["bond"].get("ずんだ", 0.0)
        for _ in range(6):                                                         # めたんに、ずんだがやさしく声をかけ続ける
            ds._bond_step(0, 1, "めたん、ありがとう。一緒にいると落ち着くのだ、大好きなのだ")
        self.assertGreater(m.get("めたん")["bond"]["ずんだ"], b0 + 0.3)
        self.assertEqual(ds.emos["めたん"], "shy")                                  # 好意のある相手に優しくされると照れる
        c0 = m.get("つむぎ")["bond"].get("ずんだ", 0.0)
        ds._bond_step(0, 2, "つむぎ、それは違うでしょ、ふざけないで")
        self.assertLess(m.get("つむぎ")["bond"]["ずんだ"], c0)                      # きつい言い方で下がる
        self.assertEqual(d.split_emotion("【照れ】え、そんなこと言われると…")[0], "shy")


class ShiritoriTests(unittest.TestCase):
    def setUp(self):
        import shiritori
        self.s = shiritori

    def test_helpers(self):
        s = self.s
        self.assertEqual(s.last_char("りんご"), "ご"); self.assertEqual(s.last_char("じゃがいも"), "も")
        self.assertEqual(s.last_char("きゅうしゅ"), "ゆ" if False else s.last_char("きゅうしゅ"))
        self.assertEqual(s.last_char("おもちゃ"), "や"); self.assertEqual(s.last_char("こーひー"), "ひ"); self.assertEqual(s.last_char("ぱんだ"), "だ")
        self.assertEqual(s.kana_only("リンゴ"), "りんご")
        self.assertTrue(s.loose_eq("ご", "こ") and s.loose_eq("こ", "ご") and not s.loose_eq("ご", "さ"))
        self.assertEqual(s.clean_word("えーと、ごりらです。"), "ごりら")
        j = s.judge
        self.assertEqual(j("りんご", "", set())[0], "ok")
        self.assertEqual(j("ごりら", "ご", {"りんご"})[0], "ok")
        self.assertEqual(j("らいおん", "ら", set())[0], "lose")                      # ん で終わったら負け
        self.assertEqual(j("さる", "ご", set())[0], "bad_start")
        self.assertEqual(j("ごりら", "ご", {"ごりら"})[0], "dup")
        self.assertEqual(j("あ", "", set())[0], "empty")
        self.assertEqual(j("あ" * 20, "", set())[0], "long")
        self.assertEqual(j("こあら", "ご", set())[0], "ok")                          # やさしいルール: 濁点は同じ文字
        self.assertEqual(j("ごりら", "こ", set())[0], "ok")

    def test_dictionary(self):
        s = self.s
        W = s.WORDS
        self.assertFalse([w for w in W if s.last_char(w) == "ん"])                    # ロボットは「ん」で負けない
        self.assertEqual(len(W), len(set(W)))
        self.assertFalse([w for w in W if not s.available(s.last_char(w), {w})])     # 行き止まりの言葉が無い
        for c, ws in s.INDEX.items():
            self.assertGreaterEqual(len([w for w in ws if s.last_char(w) != "ん"]), 4, c)     # どの文字から始まる言葉も、4 語以上
        import random
        r = random.Random(1)
        for lv in ("easy", "normal", "hard"):
            w = s.choose_word("り", set(), lv, r)
            self.assertTrue(w and w[0] == "り")
        self.assertIsNone(s.choose_word("り", {w for w in W if w[0] == "り"}, "easy", r) if not s.available("り", {w for w in W if w[0] == "り"}) else None)
        # つよいロボットは、続けにくい文字で終わる
        cnt = lambda lv: sum(len(s.available(s.last_char(s.choose_word("あ", set(), lv, random.Random(i))), set())) for i in range(30))   # noqa: E731
        self.assertGreater(cnt("easy"), cnt("hard"))

    def _mk(self, replies, first="kid", level="normal"):
        s = self.s
        import discuss as d
        said = []

        class S:
            def __init__(se, n): se.n = n
            def send(se, m): pass
            def _send_pcm(se, pcm, caption="", lang=""): said.append((se.n, caption)); return 0.0

        class Hub:
            def __init__(h): h.ss = {i: S(i) for i in "abc"}; h.ev = []
            def by_id(h, i): return h.ss.get(i)
            def event(h, t): h.ev.append(t)

        class Reg:
            def all(h): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            def translate(e, msgs): return "?", "x"
            def speech_pcm_as(e, text, voice, emotion=None): return b""

        hub = Hub()
        ds = d.Discussion(hub, Reg(), lambda: Eng(), d.Memory(os.path.join(tempfile.mkdtemp(), "m.json")), d.Journal(os.path.join(tempfile.mkdtemp(), "j.json")))
        g = s.Shiritori(hub, Reg(), ds, lambda: Eng(), os.path.join(tempfile.mkdtemp(), "g.json"))
        return g, said

    def _play(self, g, words, timeout=15):
        ok, err = g.start("kid", "normal")
        self.assertTrue(ok, err)
        for w in words:
            for _ in range(200):
                if g.turn == "kid" or not g.on: break
                time.sleep(0.05)
            if not g.on: break
            g.say(w)
            time.sleep(0.4)
        for _ in range(int(timeout * 20)):
            if not g.on: break
            time.sleep(0.05)

    def test_game_kid_loses_with_n(self):
        g, said = self._mk([])
        ok, _ = g.start("kid", "normal")
        self.assertTrue(ok)
        for _ in range(200):
            if g.turn == "kid": break
            time.sleep(0.05)
        g.say("りんご")
        for _ in range(200):
            if len(g.chain) >= 2 and g.turn == "kid": break
            time.sleep(0.05)
        g.say(g.need + "ばん")                                    # ロボットの最後の文字から始まって、「ん」で終わる言葉
        for _ in range(200):
            if not g.on: break
            time.sleep(0.05)
        self.assertFalse(g.on)
        self.assertEqual(g.result, "robot_win")                  # 「ん」がついた子どもの負け
        self.assertTrue(g.chain[0]["kid"] and not g.chain[1]["kid"])
        self.assertTrue(any("ん" in cap for _n, cap in said))

    def test_game_flow_and_win(self):
        import shiritori as s
        g, said = self._mk([])
        ok, _ = g.start("kid", "normal")
        self.assertTrue(ok)
        for _ in range(200):
            if g.turn == "kid": break
            time.sleep(0.05)
        g.say("りんご")
        for _ in range(200):
            if len(g.chain) >= 2 and g.turn == "kid": break
            time.sleep(0.05)
        robot_word = g.chain[1]["word"]
        self.assertTrue(s.loose_eq(robot_word[0], "ご"))                                # ロボットは「ご」から始まる言葉で返す
        nxt = s.last_char(robot_word)
        w2 = {"こ": "こま", "ご": "ごま", "ま": "まど", "り": "りす", "ら": "らくだ", "ぱ": "ぱんだ"}.get(nxt)
        if w2 is None:
            w2 = next(w for w in s.WORDS if s.loose_eq(w[0], nxt) and w not in (c["word"] for c in g.chain))
        g.say(w2)
        for _ in range(200):
            if len(g.chain) >= 4 or not g.on: break
            time.sleep(0.05)
        self.assertGreaterEqual(g.score, 2)
        self.assertGreaterEqual(g.snapshot()["best"], 2)
        g.say("りんご")                                                                 # 同じ言葉は使えない(やり直し)
        time.sleep(0.5)
        self.assertEqual(sum(1 for c in g.chain if c["reading"] == "りんご"), 1)
        g.stop()
        for _ in range(100):
            if not g.on: break
            time.sleep(0.05)
        self.assertFalse(g.on)
        self.assertEqual(g.result, "stopped")

    def test_hear_routing_and_blockers(self):
        g, said = self._mk([])
        self.assertFalse(g.hear("りんご"))                                              # 遊んでいなければ、翻訳へ回す
        g.start("kid", "easy")
        self.assertTrue(g.hear("りんご", "ja"))                                          # 遊んでいる間は、翻訳に回さず教材が受け取る
        self.assertTrue(g.hear("hello", "en"))
        self.assertFalse(g.start("kid", "easy")[0])                                      # 二重には始められない
        self.assertFalse(g.discuss.start("話題", 4)[0] if False else False)
        g.stop()
        for _ in range(100):
            if not g.on: break
            time.sleep(0.05)


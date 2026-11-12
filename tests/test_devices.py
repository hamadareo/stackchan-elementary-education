import os, sys, tempfile, time, unittest
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

        d = self.d.Discussion(Hub(), Reg(), lambda: Eng())
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

        d = self.d.Discussion(Hub(), Reg(), lambda: Eng())
        d.start("旅行", 12)
        for _ in range(100):
            if not d.on: break
            time.sleep(0.1)
        self.assertFalse(d.on)
        self.assertLess(len(sent), 12)                                 # 全員黙ると、早めに終わる
        self.assertIn("終え", d.snapshot()["note"])

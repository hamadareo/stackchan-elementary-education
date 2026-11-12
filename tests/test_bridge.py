"""ブリッジの純粋ロジックの試験(端末・サーバー不要)。実行: bridge/.venv/bin/python -m unittest discover -s tests -v"""
import os, re, sys, textwrap, unittest

SRC = open(os.path.join(os.path.dirname(__file__), "..", "bridge", "stackchan_bridge.py"), encoding="utf-8").read()


def block(start: str, end: str) -> dict:
    ns = {"re": re}
    exec(textwrap.dedent(SRC[SRC.index(start):SRC.index(end)]), ns)
    return ns


class VolumeCommands(unittest.TestCase):
    ns = block("    _VOL_UP", "    def _voice_cmd")

    def kind(self, t):
        n = self.ns
        for k, rx in (("max", n["_VOL_MAX"]), ("min", n["_VOL_MIN"]), ("up", n["_VOL_UP"]), ("down", n["_VOL_DN"])):
            if rx.search(t):
                return k
        return None

    def test_commands(self):
        for t, k in [("音量を上げて", "up"), ("もっと小さい声で", "down"), ("うるさい", "down"), ("ボリューム最大", "max"),
                     ("ボリュームミュート", "min"), ("volume down", "down"), ("turn it up", "up"), ("louder please", "up")]:
            self.assertEqual(self.kind(t), k, t)

    def test_normal_sentences_not_matched(self):
        for t in ["今日は学会の発表です", "I like music", "音楽が好きです", "How are you?"]:
            self.assertIsNone(self.kind(t), t)


class LangCommands(unittest.TestCase):
    ns = block("    _LANG_CMD", "    def set_pair_b")

    def test_lang(self):
        rx = self.ns["_LANG_CMD"]
        for t in ["中国語モード", "英語にして", "Switch to Korean", "韓国語に切り替えて"]:
            self.assertTrue(rx.search(t), t)
        for t in ["英語を勉強しています", "中国語は難しいです", "I speak English"]:
            self.assertFalse(rx.search(t), t)


class TopicCommands(unittest.TestCase):
    ns = block("    _TOPIC_CMD", "    def _voice_cmd")

    def test_topic(self):
        rx = self.ns["_TOPIC_CMD"]
        self.assertEqual(rx.match("話題は医療です").group(1), "医療")
        self.assertEqual(rx.match("テーマは機械学習について").group(1), "機械学習")
        self.assertIsNone(rx.match("今日の話題はニュースです"))      # 文頭が話題でなければ通訳対象
        self.assertTrue(self.ns["_TOPIC_CLEAR"].search("話題をリセット"))


class Mood(unittest.TestCase):
    ns = block("    _HAPPY", "    def _mood")

    def test_mood(self):
        h, s = self.ns["_HAPPY"], self.ns["_SAD"]
        self.assertTrue(h.search("ありがとう"))
        self.assertTrue(h.search("That's awesome"))
        self.assertTrue(s.search("ごめんなさい"))
        self.assertFalse(h.search("今日は晴れです"))


class Hallucination(unittest.TestCase):
    def test_hallu_regex(self):
        m = re.search(r'_HALLU = re\.compile\((.*?)\, re\.I\)', SRC, re.S)
        self.assertIsNotNone(m)
        rx = re.compile(eval('(' + m.group(1) + ')'), re.I)
        for t in ["ご視聴ありがとうございました", "you", "Thank you.", "Thanks for watching!"]:
            self.assertTrue(rx.match(t), t)
        for t in ["今日はいい天気です", "Thank you for your help with the project"]:
            self.assertFalse(rx.match(t), t)


class SplitReady(unittest.TestCase):
    def setUp(self):
        i = SRC.index("    _END = ")
        j = SRC.index("    def _emit")
        ns = {"re": re}
        exec("class T:\n" + SRC[i:j], ns)
        self.t = ns["T"]()

    def test_first_short_sentence_goes_early(self):
        out, rest = self.t._split_ready("Hello. Today, I will speak", True, "en")
        self.assertEqual(out, ["Hello."])
        self.assertTrue(rest.strip().startswith("Today"))

    def test_waits_for_more_when_period_at_end(self):
        out, rest = self.t._split_ready("Hello.", True, "en")
        self.assertEqual(out, [])

    def test_japanese(self):
        out, rest = self.t._split_ready("こんにちは。今日は", True, "ja")
        self.assertEqual(out, ["こんにちは。"])

    def test_later_short_sentences_are_merged_not_split(self):
        out, rest = self.t._split_ready("Ok. Yes.", False, "en")
        self.assertEqual(out, [])


class FaceDetect(unittest.TestCase):
    def test_yunet_on_snapshot(self):
        try:
            import cv2
        except Exception:
            self.skipTest("opencv なし")
        model = os.path.join(os.path.dirname(__file__), "..", "bridge", "models", "face_detection_yunet_2023mar.onnx")
        self.assertTrue(os.path.exists(model), "YuNet モデルがありません")
        det = cv2.FaceDetectorYN.create(model, "", (320, 240), 0.6, 0.3, 50)
        import numpy as np
        _, f = det.detect(np.zeros((240, 320, 3), np.uint8))      # 真っ黒な画像では顔なし
        self.assertTrue(f is None or len(f) == 0)



class TrackingLogic(unittest.TestCase):
    """顔追従の制御(合成した検出結果で、首の指示の向き・不感帯・待ち時間を確認)。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
        sys.path.insert(0, os.environ.get("TSUYAKU_SRC", "/Users/work/tsuyaku/src"))
        try:
            import numpy as np
            import stackchan_bridge as sb
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"ブリッジを import できない: {e}")
        cls.np, cls.sb = np, sb

    def make(self, det):
        np, sb = self.np, self.sb
        sess = sb.Session.__new__(sb.Session)
        sent = []
        sess.send = sent.append

        class Face:
            cv2 = None
            def decode(self, j): return np.full((240, 320, 3), 100, np.uint8)
            def detect(self, img): return det[0]
            def find(self, img):
                r = det[0]
                return None if r is None else (160 + r[0] * 160 - 20, 120 + r[1] * 120 - 20, 40.0, 40.0, None)
            rel = staticmethod(lambda f, shape: det[0])
        sess.eng = type("E", (), {"face": Face(), "last_voice": 0})()
        sess._trk = {"calib": {"pan_px": -0.65, "tilt_px": 0.6}, "busy": False, "seen": 1e12, "react": 1e12,
                     "settle": 0.0, "n": 0, "hit": 0, "t": 1e12, "look_t": 1e12}
        sess._auto_bright = lambda img, now: None
        return sess, sent

    def servo(self, sent): return [m for m in sent if m.get("t") == "servo"]

    def test_face_on_right_pans_positive_and_down_tilts_negative(self):
        det = [(0.5, 0.4, 0.3)]
        sess, sent = self.make(det)
        sess.on_frame(b"x")
        m = self.servo(sent)[0]
        self.assertGreater(m["dpan"], 0)      # 右にいる顔 → 画像を左へ → pan 正(校正値 pan_px<0 と整合)
        self.assertLess(m["dtilt"], 0)        # 下にいる顔 → 画像を上へ → tilt 負

    def test_face_on_left(self):
        sess, sent = self.make([(-0.5, 0.0, 0.3)])
        sess.on_frame(b"x")
        self.assertLess(self.servo(sent)[0]["dpan"], 0)

    def test_deadband(self):
        sess, sent = self.make([(0.05, 0.05, 0.3)])
        sess.on_frame(b"x")
        self.assertEqual(self.servo(sent), [])

    def test_settle_blocks_back_to_back_commands(self):
        sess, sent = self.make([(0.6, 0.0, 0.3)])
        sess.on_frame(b"x"); sess.on_frame(b"x")
        self.assertEqual(len(self.servo(sent)), 1)

    def test_clamped(self):
        sess, sent = self.make([(1.0, 1.0, 0.3)])
        sess.on_frame(b"x")
        m = self.servo(sent)[0]
        self.assertLessEqual(abs(m["dpan"]), 350)
        self.assertLessEqual(abs(m["dtilt"]), 250)

    def test_no_face_no_command(self):
        sess, sent = self.make([None])
        sess.on_frame(b"x")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()


class HalluTest(unittest.TestCase):
    def test_clean(self):
        import stackchan_bridge as b
        c = b.clean_hallucination
        self.assertEqual(c("ご視聴ありがとうございました"), "")
        self.assertEqual(c("それでは、ご視聴ありがとうございました"), "それでは")
        self.assertEqual(c("見に行ったらいい。ナンバーマル。ご視聴あり"), "見に行ったらいい。ナンバーマル")
        self.assertEqual(c("おやすみなさいご視聴ありがとうございました"), "おやすみなさい")
        self.assertEqual(c("ヨドッグヨドッグヨドッグヨドッグヨ"), "")
        self.assertEqual(c("请不吝点赞 订阅 订阅 转发 打赏支持明镜与点点栏目"), "")
        self.assertEqual(c("ん", 0.4), "")
        self.assertEqual(c("8", 0.4), "8")
        self.assertEqual(c("アイスクリームってなんていうんですか"), "アイスクリームってなんていうんですか")
        self.assertEqual(c("Thank you for watching"), "")


class HalluPunctTest(unittest.TestCase):
    def test_keep_question(self):
        import stackchan_bridge as b
        self.assertEqual(b.clean_hallucination("これでいいのかな?"), "これでいいのかな?")
        self.assertEqual(b.clean_hallucination("不快点。"), "不快点。")


class SessionVoiceDefault(unittest.TestCase):
    def test_voice_attr_defined(self):
        # identify() の前に読み上げが走っても落ちない(Session.voice の既定は None)。identify でも台帳の声を入れる
        self.assertIn("class Session:\n    voice = None", SRC)
        self.assertIn('self.voice = rec.get("voice") or None', SRC)


class NoDuplicateMethods(unittest.TestCase):
    def test_no_duplicate_defs_in_classes(self):
        # 同じクラスに同名メソッドが 2 つあると、後ろの定義が前を黙って上書きする(声の不具合の原因だった)
        import ast
        for fn in ("stackchan_bridge.py", "admin.py", "discuss.py", "devices.py", "usbprov.py"):
            tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "..", "bridge", fn), encoding="utf-8").read())
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
                dup = {x for x in names if names.count(x) > 1}
                self.assertFalse(dup, f"{fn}:{cls.name} に重複: {dup}")

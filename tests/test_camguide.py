"""カメラ距離・見え方の案内(camguide)と、じゃんけん・あっち向いてホイでの指示の試験。実機・実画像なしで、合成の画像と顔の検出結果で確かめる。"""
import os
import queue
import random
import sys
import tempfile
import threading
import time
import unittest

os.environ["SC_JOURNAL"] = os.path.join(tempfile.mkdtemp(), "journal.json")
os.environ["SC_PERSONA"] = os.path.join(tempfile.mkdtemp(), "persona.json")
os.environ["SC_AUTONOMY"] = os.path.join(tempfile.mkdtemp(), "auto.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np

import camguide as cg
import fun
import hand as hand_mod
import janken as jk
import playfx

playfx.Stage.FAST = True
W, H = 320, 240


def face_row(x, y, w, h, eye=None):
    """YuNet の行のまね: x,y,w,h,右目(x,y),左目(x,y),鼻(x,y)...。eye=目の間隔(省略は顔幅の 0.4)。"""
    e = eye if eye is not None else w * 0.4
    cx, cy = x + w / 2, y + h * 0.4
    return [x, y, w, h, cx - e / 2, cy, cx + e / 2, cy, cx, cy + h * 0.25, 0, 0, 0, 0, 0.9]


def face_at(ratio, cx=0.0, eye=None):
    """画像の横幅に対する顔幅 ratio、画面中心からのずれ cx(-1..1)の顔の検出結果 (x, y, w, h, row)。"""
    w = ratio * W
    x = (cx + 1.0) / 2.0 * W - w / 2.0
    y = H / 2 - w / 2
    return (x, y, w, w, face_row(x, y, w, w, eye))


def reading(ratio=0.3, cx=0.0, bright=120.0, cut=0.0, eye=None, face=True):
    if not face:
        return {"face": False, "bright": bright, "ratio": 0.0, "cx": 0.0, "cut": 0.0, "eye": None}
    return {"face": True, "bright": bright, "ratio": ratio, "cx": cx, "cut": cut, "eye": eye}


class FakeFace:
    """Face のまね。find は、決めた順に結果を返す(尽きたら最後のまま)。decode は、明るさ bright の合成画像。"""

    def __init__(self, script, bright=120):
        self.cv2 = object()
        self.script = list(script)
        self.bright = bright
        self.calls = 0

    def decode(self, _j):
        return np.full((H, W, 3), self.bright, np.uint8)

    def find(self, _img):
        self.calls += 1
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0]


class DecideTests(unittest.TestCase):
    def test_ratio_boundaries(self):
        d = lambda r, **k: cg.decide([reading(ratio=r, **k)])           # noqa: E731
        self.assertEqual(d(cg.NEAR_RATIO), "ok")                         # ちょうどの値は ok(超えたら near)
        self.assertEqual(d(cg.NEAR_RATIO + 0.001), "near")
        self.assertEqual(d(cg.FAR_RATIO), "ok")
        self.assertEqual(d(cg.FAR_RATIO - 0.001), "far")
        self.assertEqual(d(0.3), "ok")

    def test_side_and_cut(self):
        self.assertEqual(cg.decide([reading(cx=cg.SIDE_CX)]), "ok")
        self.assertEqual(cg.decide([reading(cx=cg.SIDE_CX + 0.01)]), "side")
        self.assertEqual(cg.decide([reading(cx=-cg.SIDE_CX - 0.01)]), "side")
        self.assertEqual(cg.decide([reading(cut=cg.CUT_FRAC + 0.01)]), "side")           # 顔の枠が画像からはみ出している
        self.assertEqual(cg.decide([reading(cut=cg.CUT_FRAC)]), "ok")

    def test_none_dark_unknown(self):
        self.assertEqual(cg.decide([]), "unknown")                                       # 画像なし=判断しない
        self.assertEqual(cg.decide([reading(face=False, bright=100)]), "none")
        self.assertEqual(cg.decide([reading(face=False, bright=cg.DARK_MEAN - 1)]), "dark")
        self.assertEqual(cg.decide([reading(face=False, bright=cg.DARK_MEAN)]), "none")
        self.assertEqual(cg.decide([reading(bright=10)]), "ok")                          # 暗くても顔が見えていれば、指示しない

    def test_majority_and_median(self):
        rs = [reading(face=False), reading(), reading()]                                 # 3 枚中 2 枚に顔 → 顔あり
        self.assertEqual(cg.decide(rs), "ok")
        rs = [reading(face=False), reading(face=False), reading()]                       # 顔が少数 → 見えない
        self.assertEqual(cg.decide(rs), "none")
        rs = [reading(ratio=0.7), reading(ratio=0.3), reading(ratio=0.31)]               # 1 枚だけ近くても、中央値で ok
        self.assertEqual(cg.decide(rs), "ok")
        rs = [reading(ratio=0.7), reading(ratio=0.72), reading(ratio=0.3)]
        self.assertEqual(cg.decide(rs), "near")
        # 半分ちょうどに顔: 見えるほう
        self.assertEqual(cg.decide([reading(face=False), reading()]), "ok")

    def test_need_eyes(self):
        small_eye = reading(eye=cg.MIN_EYE_PX - 0.5)
        self.assertEqual(cg.decide([small_eye]), "ok")                                   # 手のためなら、顔があればよい
        self.assertEqual(cg.decide([small_eye], need_eyes=True), "side")                 # 顔の向きを読むには、目の間隔が要る
        self.assertEqual(cg.decide([reading(eye=cg.MIN_EYE_PX)], need_eyes=True), "ok")
        self.assertEqual(cg.decide([reading(eye=None)], need_eyes=True), "ok")           # Haar など、目の位置が無いときは、決めつけない

    def test_face_reading(self):
        r = cg.face_reading(face_at(0.5, 0.0), (H, W, 3), 99)
        self.assertTrue(r["face"])
        self.assertAlmostEqual(r["ratio"], 0.5, places=3)
        self.assertAlmostEqual(r["cx"], 0.0, places=3)
        self.assertEqual(r["cut"], 0.0)
        self.assertAlmostEqual(cg.face_reading(face_at(0.2, 0.5), (H, W, 3), 0)["cx"], 0.5, places=3)
        r = cg.face_reading((-30.0, 50.0, 100.0, 100.0, None), (H, W, 3), 0)               # 左に 30px はみ出し
        self.assertAlmostEqual(r["cut"], 0.3, places=3)
        self.assertIsNone(r["eye"])
        self.assertFalse(cg.face_reading(None, (H, W, 3), 50)["face"])

    def test_hand_reason(self):
        self.assertEqual(cg.hand_reason("ok"), "hand")
        self.assertEqual(cg.hand_reason("side"), "hand")
        self.assertEqual(cg.hand_reason("near"), "near")
        self.assertEqual(cg.hand_reason("far"), "far")
        self.assertEqual(cg.hand_reason("dark"), "dark")
        self.assertEqual(cg.hand_reason("none"), "hidden")                               # 手が近すぎて顔まで隠れた
        self.assertIsNone(cg.hand_reason("unknown"))


class AnalyzeTests(unittest.TestCase):
    def test_synthetic_frames(self):
        for ratio, cx, want in [(0.3, 0.0, "ok"), (0.7, 0.0, "near"), (0.05, 0.0, "far"), (0.3, 0.9, "side")]:
            r = cg.analyze(FakeFace([face_at(ratio, cx)]), [b"x"] * 3)
            self.assertEqual(r["state"], want, (ratio, cx))
            self.assertEqual(r["label"], cg.LABEL[want])
            self.assertEqual(r["n"], 3)

    def test_dark_and_none(self):
        self.assertEqual(cg.analyze(FakeFace([None], bright=10), [b"x"] * 2)["state"], "dark")
        self.assertEqual(cg.analyze(FakeFace([None], bright=150), [b"x"] * 2)["state"], "none")

    def test_unusable(self):
        self.assertEqual(cg.analyze(None, [b"x"])["state"], "unknown")                    # 顔検出なし
        f = FakeFace([face_at(0.3)])
        f.cv2 = None
        self.assertEqual(cg.analyze(f, [b"x"])["state"], "unknown")
        self.assertEqual(cg.analyze(FakeFace([face_at(0.3)]), [])["state"], "unknown")    # 画像なし

    def test_bad_frame_does_not_stop(self):
        class Bad(FakeFace):
            def decode(self, j):
                if j == b"bad":
                    raise ValueError("x")
                return super().decode(j)
        r = cg.analyze(Bad([face_at(0.3)]), [b"bad", b"ok", b"ok"])
        self.assertEqual((r["state"], r["n"]), ("ok", 2))

    def test_last_frames_only(self):
        f = FakeFace([face_at(0.3)])
        cg.analyze(f, [b"x"] * 20, last=4)
        self.assertEqual(f.calls, 4)


class CoachTests(unittest.TestCase):
    def mk(self, cooldown=10.0, max_same=2):
        self.t = 100.0
        return cg.Coach(lambda: self.t, cooldown, max_same)

    def test_ok_and_unknown_need_nothing(self):
        c = self.mk()
        self.assertEqual(c.next("ok"), "ok")
        self.assertEqual(c.next("unknown"), "ok")
        self.assertFalse(c.skip)
        c.next("unknown")
        self.assertTrue(c.skip)                                                          # 見る手段がないのが続く: 確認を省く

    def test_cooldown_and_giveup(self):
        c = self.mk()
        self.assertEqual(c.next("near"), "say")
        self.t += 9.9
        self.assertEqual(c.next("near"), "wait")                                         # クールダウン中は、言わない
        self.t += 0.2
        self.assertEqual(c.next("near"), "say")                                          # 2 回目
        self.t += 10.1
        self.assertEqual(c.next("near"), "giveup")                                       # 3 回目はなく、声だけにする
        self.assertTrue(c.gave_up)
        self.assertEqual(c.next("near"), "muted")
        self.assertEqual(c.next("ok"), "muted")
        self.assertTrue(c.skip)

    def test_states_counted_separately_and_fix_resets(self):
        c = self.mk(cooldown=0)
        self.assertEqual([c.next("near"), c.next("far"), c.next("near"), c.next("far")], ["say"] * 4)
        self.assertEqual(c.next("near"), "giveup")
        c = self.mk(cooldown=0)
        self.assertEqual([c.next("near"), c.next("near")], ["say", "say"])
        c.next("ok")
        c.next("ok")                                                                     # 続けて 2 回 ok = 直った → 数え直し
        self.assertEqual(c.next("near"), "say")


class LinesTests(unittest.TestCase):
    def test_all_states_have_lines_and_board(self):
        for st in ("near", "far", "none", "dark", "side", "hand", "hidden"):
            big, foot = cg.board_text(st)
            self.assertTrue(big and foot, st)
            self.assertLessEqual(len(big.encode()), 31, st)                              # 機器の画面の大きな字の入れ物(32 バイト)に収まる
            self.assertLessEqual(len(foot.encode()), 95, st)
            for name in ("ずんだ", "めたん", "つむぎ"):
                for i in range(6):
                    t = cg.line(name, st, lambda k, o, i=i: o[i % len(o)])
                    self.assertTrue(t and "{" not in t and "}" not in t, (st, name, t))
        for kind in ("jan", "face"):
            for name in ("ずんだ", "めたん", "つむぎ"):
                t = cg.giveup_line(name, kind)
                self.assertTrue("{" not in t and "こえ" in t, (kind, t))
            big, foot = cg.giveup_board(kind)
            self.assertTrue(big and foot)
        self.assertLessEqual(len(cg.TITLE.encode()), 47)

    def test_character_tone(self):
        zun = cg.line("ずんだ", "near", lambda k, o: o[1])                               # 「ちかいみたい{d}」
        met = cg.line("めたん", "near", lambda k, o: o[1])
        self.assertIn("なのだ", zun)
        self.assertIn("です", met)

    def test_warm_plan_includes_guides(self):
        import warm
        plan = {t for _p, t, _e, _r in warm.build_plan(["ずんだ"])}
        for t in cg.all_lines("ずんだ"):
            for ch in playfx.split_chunks(t.strip()):                    # 先読みは、文ごと
                self.assertIn(playfx.yomi(ch.strip()), plan)


class Rig:
    """Janken を、声・画面・機器なしで動かす道具(話した台詞と表示を記録する)。"""

    def mk(self, script, cooldown=0.0, max_same=2):
        class Sess:
            hand_cb = None

            def send(self, _m):
                pass

        sess = Sess()

        class Hub:
            janken_cam = True

            def by_id(self, _d):
                return sess

            def event(self, *_a):
                pass

        g = jk.Janken(Hub(), None, None, lambda: None, os.path.join(tempfile.mkdtemp(), "g.json"))
        g.robots = [{"id": "d1", "name": "ずんだ", "dev_name": "x", "voice": "voicevox:3"}]
        g.pick = lambda k, o: o[0]
        g._coach_obj = cg.Coach(g.clock, cooldown, max_same)
        self.said, self.boards, self.clears = [], [], []
        g._speak = lambda eng, i, text, emo="joy", **kw: self.said.append(text)
        g._board = lambda **kw: self.boards.append(kw)
        g._board_clear = lambda: self.clears.append(1)
        g._cam_grab = lambda *a, **k: [b"x"]
        self.eng = type("Eng", (), {})()
        self.eng.face = FakeFace(script)
        self.sess = sess
        return g


class PrecheckTests(unittest.TestCase, Rig):
    def setUp(self):
        self._saved = (cg.FIX_WAIT_S, cg.RECHECK_S, cg.PRE_MAX_S)
        cg.FIX_WAIT_S, cg.RECHECK_S, cg.PRE_MAX_S = 0.02, 0.02, 5.0
        self.addCleanup(lambda: setattr(cg, "FIX_WAIT_S", self._saved[0]) or setattr(cg, "RECHECK_S", self._saved[1]) or setattr(cg, "PRE_MAX_S", self._saved[2]))

    def test_good_position_is_silent(self):
        g = self.mk([face_at(0.3)])
        g._cam_precheck(self.eng)
        self.assertEqual((self.said, self.boards, self.clears), ([], [], []))
        self.assertEqual(g.cam_state, "ok")
        self.assertEqual(g.cam_label, "ちょうどいい")
        self.assertFalse(g._cam_need)                                                    # 毎回は確認しない

    def test_too_close_instructs_by_voice_and_screen_then_clears(self):
        g = self.mk([face_at(0.7), face_at(0.3)])
        g._cam_precheck(self.eng)
        self.assertEqual(len(self.said), 1)
        self.assertIn("ちかすぎ", self.said[0])
        self.assertEqual(self.boards[0]["title"], cg.TITLE)
        self.assertEqual(self.boards[0]["a"], ("", "さがってね"))
        self.assertEqual(len(self.clears), 1)                                            # 直ったら、画面の指示を消す
        self.assertEqual(g.cam_state, "ok")

    def test_too_far_dark_none_side(self):
        for script, bright, key in [([face_at(0.05), face_at(0.3)], 120, "とおい"), ([None, face_at(0.3)], 5, "くらい"), ([None, face_at(0.3)], 120, "みえない"),
                                     ([face_at(0.3, 0.95), face_at(0.3)], 120, "まんなか")]:
            g = self.mk(script)
            self.eng.face.bright = bright
            g._cam_precheck(self.eng)
            self.assertEqual(len(self.said), 1, key)
            self.assertIn(key, self.said[0])

    def test_gives_up_after_two_same_instructions_and_goes_voice_only(self):
        g = self.mk([face_at(0.05)])                                                     # ずっと遠い
        g._cam_precheck(self.eng)
        self.assertEqual(len(self.said), 3)
        self.assertIn("とおい", self.said[0])
        self.assertIn("こえ", self.said[2])                                              # 3 回目は「声でおしえてね」
        self.assertTrue(g._coach_obj.gave_up)
        self.assertFalse(g.cam_enabled())                                                # 以後は声だけ
        self.assertTrue(g.snapshot()["cam_muted"])
        self.assertEqual(self.boards[-1]["a"], ("", "こえで おしえてね"))
        g._cam_need = True
        n = len(self.said)
        g._cam_precheck(self.eng)
        self.assertEqual(len(self.said), n)                                              # もう言わない

    def test_cooldown_prevents_repeating(self):
        g = self.mk([face_at(0.05)], cooldown=0.3, max_same=5)
        t0 = time.time()
        threading.Timer(0.35, g._stop.set).start()
        g._cam_precheck(self.eng)
        self.assertLessEqual(len(self.said), 2)                                          # 0.35 秒のあいだに、指示は 1〜2 回(連発しない)
        self.assertGreaterEqual(len(self.said), 1)
        self.assertLess(time.time() - t0, 1.5)

    def test_stop_leaves_immediately(self):
        g = self.mk([face_at(0.05)], cooldown=0.0, max_same=99)
        cg.FIX_WAIT_S = 30.0                                                             # 指示のあとの待ちが長くても
        orig = g._speak
        g._speak = lambda *a, **k: (orig(*a, **k), g._stop.set())                          # 指示を言いおえたところで、中止
        t0 = time.time()
        g._cam_precheck(self.eng)
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(len(self.said), 1)

    def test_no_face_detector_or_frames_means_no_instruction(self):
        g = self.mk([face_at(0.7)])
        self.eng.face = None
        g._cam_precheck(self.eng)
        g._cam_need = True
        g._cam_grab = lambda *a, **k: []                                                 # 画像が来ない(古いファームなど)
        self.eng.face = FakeFace([face_at(0.7)])
        g._cam_precheck(self.eng)
        self.assertEqual(self.said, [])
        self.assertEqual(g.cam_state, "")

    def test_camera_off_skips(self):
        g = self.mk([face_at(0.7)])
        g.hub.janken_cam = False
        g._cam_precheck(self.eng)
        self.assertEqual(self.said, [])

    def test_face_kind_needs_readable_eyes(self):
        g = self.mk([face_at(0.3, eye=3.0), face_at(0.3)])                               # 目の間隔が小さすぎる(横向き)
        g._cam_precheck(self.eng, "face")
        self.assertEqual(len(self.said), 1)
        self.assertIn("まんなか", self.said[0])
        g = self.mk([face_at(0.3, eye=3.0)])
        g._cam_precheck(self.eng, "jan")                                                  # 手のためなら、問題にしない
        self.assertEqual(self.said, [])
        self.assertEqual(g.cam_state, "ok")

    def test_face_need_flag_is_separate(self):
        g = self.mk([face_at(0.3)])
        g._cam_precheck(self.eng, "jan")
        self.assertFalse(g._cam_need)
        self.assertTrue(g._face_need)
        g._cam_precheck(self.eng, "face")
        self.assertFalse(g._face_need)

    def test_cam_grab_restores_callback(self):
        g = self.mk([face_at(0.3)])
        del g.__dict__["_cam_grab"]                                                       # 本物を使う
        got = []
        self.sess.hand_cb = got.append                                                     # 先に受け口があった(あっち向いてホイの画像ためなど)
        threading.Timer(0.05, lambda: self.sess.hand_cb(b"a")).start()
        threading.Timer(0.1, lambda: self.sess.hand_cb(b"b")).start()
        threading.Timer(0.15, lambda: self.sess.hand_cb(b"c")).start()
        frames = g._cam_grab(2.0, 3)
        self.assertEqual(frames, [b"a", b"b", b"c"])
        self.assertEqual(got, [b"a", b"b", b"c"])                                         # もとの受け口にも渡る
        self.assertEqual(self.sess.hand_cb, got.append)                                   # 終わったらもとに戻す


class WaitHandGuideTests(unittest.TestCase, Rig):
    def setUp(self):
        self._orig = (hand_mod.HandRecognizer.available, hand_mod.recognize_jpegs, jk.CAM_WINDOW, cg.FIX_WAIT_S)
        hand_mod.HandRecognizer.available = staticmethod(lambda: True)
        jk.CAM_WINDOW = 0.3
        self.results = []                                                                  # 窓ごとの手の認識結果
        hand_mod.recognize_jpegs = lambda fr: ((self.results.pop(0) if len(self.results) > 1 else self.results[0]), [None] * len(fr))
        self.addCleanup(self._restore)

    def _restore(self):
        hand_mod.HandRecognizer.available, hand_mod.recognize_jpegs, jk.CAM_WINDOW, cg.FIX_WAIT_S = self._orig

    def feed(self, g, every=0.05, n=200):
        def run():
            for _ in range(n):
                time.sleep(every)
                cb = self.sess.hand_cb
                if cb:
                    cb(b"x")
        threading.Thread(target=run, daemon=True).start()

    def test_no_eng_keeps_old_behaviour(self):
        g = self.mk([face_at(0.7)])
        self.results = [None]
        self.feed(g)
        g.q.put("ぐー")
        self.assertEqual(g._wait_hand(), ("ぐー", None))
        self.assertEqual(self.said, [])

    def test_hand_missing_because_too_close_instructs_then_reads_hand(self):
        g = self.mk([face_at(0.7)])                                                         # 顔が大きすぎる(近すぎ)
        self.results = [None, "pa"]                                                         # 1 回目の窓は手なし、やり直しで ぱー
        self.feed(g)
        r = g._wait_hand(self.eng)
        self.assertEqual(r, ("", "pa"))
        self.assertEqual(len(self.said), 1)
        self.assertIn("ちかすぎ", self.said[0])
        self.assertEqual(self.boards[0]["a"], ("", "さがってね"))
        self.assertEqual(g.cam_state, "near")
        self.assertIsNone(self.sess.hand_cb)

    def test_hand_missing_with_face_ok_asks_to_show_hand(self):
        g = self.mk([face_at(0.3)])
        self.results = [None, "gu"]
        self.feed(g)
        self.assertEqual(g._wait_hand(self.eng), ("", "gu"))
        self.assertIn("てが みえない", self.said[0])
        self.assertEqual(g.cam_state, "hand")

    def test_face_missing_means_hand_too_close(self):
        g = self.mk([None])
        self.results = [None, "choki"]
        self.feed(g)
        self.assertEqual(g._wait_hand(self.eng), ("", "choki"))
        self.assertIn("ちかすぎる", self.said[0])                                             # 「てが ちかすぎるかも」
        self.assertEqual(self.boards[0]["a"], ("", "てを はなして"))

    def test_voice_wins_any_time(self):
        g = self.mk([face_at(0.7)])
        self.results = [None]
        self.feed(g)
        threading.Timer(0.9, lambda: g.q.put("ちょき")).start()                              # 指示のあとで、声で言った
        text, cam = g._wait_hand(self.eng)
        self.assertEqual((text, cam), ("ちょき", None))
        self.assertGreaterEqual(len(self.said), 1)

    def test_repeated_failure_gives_up_then_voice_only(self):
        g = self.mk([face_at(0.05)], max_same=1)
        self.results = [None]
        self.feed(g)
        threading.Timer(1.6, lambda: g.q.put("ぱー")).start()
        text, cam = g._wait_hand(self.eng)
        self.assertEqual((text, cam), ("ぱー", None))
        self.assertTrue(g._coach_obj.gave_up)
        self.assertIn("こえ", self.said[-1])
        self.assertFalse(g.cam_enabled())

    def test_stop_during_wait(self):
        g = self.mk([face_at(0.7)])
        self.results = [None]
        self.feed(g)
        threading.Timer(0.1, lambda: (g._stop.set(), g.q.put(None))).start()
        t0 = time.time()
        self.assertEqual(g._wait_hand(self.eng), (None, None))
        self.assertLess(time.time() - t0, 1.0)
        self.assertIsNone(self.sess.hand_cb)

    def test_cam_miss_marks_recheck(self):
        g = self.mk([face_at(0.3)])
        g._cam_need = False
        self.results = [None]
        self.feed(g)
        g._coach_obj = cg.Coach(g.clock, 999, 0)                                              # 指示は出さない設定(クールダウン長)
        g._coach_obj.last = g.clock()
        threading.Timer(0.8, lambda: g.q.put("ぐー")).start()
        g._wait_hand(self.eng)
        self.assertTrue(g._cam_need)                                                          # 次の「ぽん」の前に、位置を確かめなおす


class SnapshotTests(unittest.TestCase, Rig):
    def test_janken_snapshot_keys(self):
        g = self.mk([face_at(0.3)])
        s = g.snapshot()
        for k in ("cam_state", "cam_label", "cam_muted"):
            self.assertIn(k, s)
        self.assertEqual((s["cam_state"], s["cam_label"], s["cam_muted"]), ("", "", False))
        g._cam_set("near")
        s = g.snapshot()
        self.assertEqual((s["cam_state"], s["cam_label"]), ("near", "近すぎ"))
        g._cam_reset()
        self.assertEqual(g.snapshot()["cam_state"], "")


class HoiGuideTests(unittest.TestCase):
    """あっち向いてホイを実際に動かして、指示 → 直ったら進む / 直らなければ声だけで進む を確かめる。"""

    def setUp(self):
        import test_quiz as tq
        self.tq = tq
        self.base = tq.PlayBase("_wait")
        self.saved = (cg.FIX_WAIT_S, cg.RECHECK_S, cg.COOLDOWN_S, cg.GRAB_S, jk.Janken._cam_grab)
        cg.FIX_WAIT_S, cg.RECHECK_S, cg.COOLDOWN_S, cg.GRAB_S = 0.02, 0.02, 0.0, 0.05
        jk.Janken._cam_grab = lambda self, *a, **k: [b"x"]
        self.addCleanup(self._restore)

    def _restore(self):
        cg.FIX_WAIT_S, cg.RECHECK_S, cg.COOLDOWN_S, cg.GRAB_S, jk.Janken._cam_grab = self.saved

    def _start(self, script):
        qh_all = self.base._mk(rnd=self.tq.FixedRandom(1))
        gh, jh, qh, said, sent, raws = qh_all
        eng_cls = type(qh.eng_getter())
        face = FakeFace(script)
        eng_cls.face = face
        self.assertTrue(qh.start("hoi", "", ["a"])[0])
        return qh, qh.games["a"], said, sent, face

    def test_far_then_fixed_continues(self):
        qh, g, said, sent, face = self._start([face_at(0.05), face_at(0.3)])
        self.assertTrue(self.base._wait(lambda: any("ちかく" in c or "ちかづいて" in c or "こっちに" in c for _n, c in said)))
        self.base.step(qh, g, "watch", "した")                                                  # 直ったので、ロボットが ゆびを さし、声で答えられる
        self.assertEqual(g.cam_state, "ok")
        self.assertTrue(any(m.get("title") == cg.TITLE for _n, m in sent if m.get("t") == "board"))
        self.assertFalse(g._coach_obj.gave_up)
        self.assertTrue(self.base._wait(lambda: g.phase == "ask_dir" and g.turn == "kid"))        # つぎは 子どもが ゆびを さす番。待っているところで中止
        qh.stop("a")
        self.assertTrue(self.base._wait(lambda: not g.on))

    def test_cannot_fix_goes_voice_only_and_game_continues(self):
        qh, g, said, sent, face = self._start([face_at(0.7)])                                   # ずっと近すぎ
        self.assertTrue(self.base._wait(lambda: g._coach_obj.gave_up))
        self.assertTrue(any("こえ" in c for _n, c in said))
        self.base.step(qh, g, "watch", "した")                                                   # 声で遊びを続けられる(ロボットは うえを さした → よけた)
        self.assertTrue(self.base._wait(lambda: any(w in c for _n, c in said for w in ("よけ", "セーフ", "むかなかった", "ざんねん"))))
        snap = g.snapshot()
        self.assertTrue(snap["cam_muted"])
        self.assertEqual(snap["cam_state"], "near")
        qh.stop("a")
        self.assertTrue(self.base._wait(lambda: not g.on))

    def test_stop_during_guidance_ends_game_quickly(self):
        qh, g, said, sent, face = self._start([face_at(0.7)])
        cg.FIX_WAIT_S = 30.0
        self.assertTrue(self.base._wait(lambda: any("ちかすぎ" in c or "ちかい" in c or "ちかづきすぎ" in c for _n, c in said)))
        t0 = time.time()
        qh.stop("a")
        self.assertTrue(self.base._wait(lambda: not g.on, 100))
        self.assertLess(time.time() - t0, 3.0)


if __name__ == "__main__":
    unittest.main()

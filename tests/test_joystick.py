import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import joystick as js


class PadTests(unittest.TestCase):
    def mk(self):
        self.n = 0
        self.ev = []
        p = js.Pad(lambda k: (self.ev.append(k), setattr(self, "n", self.n + (1 if k == "push" else 0))))
        for _ in range(15):
            p.feed(510, 515, 0)                                  # 中心合わせ
        return p

    def test_parse(self):
        self.assertEqual(js.parse("J,512,498,0\r\n"), (512, 498, 0))
        self.assertEqual(js.parse("J,1,2,1"), (1, 2, 1))
        for bad in ("", "J,1,2", "X,1,2,3", "J,a,2,0", "J,2000,2,0"):
            self.assertIsNone(js.parse(bad), bad)

    def test_push_any_direction_jumps_once_until_released(self):
        for x, y in ((1000, 515), (10, 515), (510, 1000), (510, 10)):
            p = self.mk(); self.n = 0
            p.feed(x, y, 0); p.feed(x, y, 0); p.feed(x, y, 0)
            self.assertEqual(self.n, 1, (x, y))                  # 倒しっぱなしでも 1 回
            p.feed(510, 515, 0); p.feed(x, y, 0)
            self.assertEqual(self.n, 2, (x, y))                  # 戻して、また倒すと 2 回

    def test_small_wobble_is_ignored(self):
        p = self.mk()
        for x in (480, 540, 530, 500):
            p.feed(x, 515, 0)
        self.assertEqual(self.n, 0)

    def test_button_press_jumps_once(self):
        p = self.mk()
        p.feed(510, 515, 1); p.feed(510, 515, 1); p.feed(510, 515, 0); p.feed(510, 515, 1)
        self.assertEqual(self.n, 2)

    def test_no_jump_during_calibration(self):
        n = []
        p = js.Pad(lambda k: n.append(k))
        for _ in range(10):
            p.feed(1000, 1000, 1)
        self.assertEqual(n, [])

    def test_jump_goes_only_to_chari_players(self):
        sent = []

        class G:
            def __init__(self, on, kind): self.on, self.kind = on, kind
            def _dev(self, cmd): sent.append(cmd)

        class Q: games = {"a": G(True, "chari"), "b": G(True, "riddle"), "c": G(False, "chari")}

        class H: quiz = Q()
        self.assertEqual(js.Joystick(H()).jump(), 1)
        self.assertEqual(sent, ["jump"])

    def test_direction_names(self):
        for x, y, want in ((1000, 515, "right"), (10, 515, "left"), (510, 1000, "down"), (510, 10, "up")):
            p = self.mk(); self.ev.clear()
            p.feed(x, y, 0)
            self.assertEqual(self.ev, ["push", want], (x, y))
        p = self.mk(); self.ev.clear(); p.feed(510, 515, 1)
        self.assertEqual(self.ev, ["push", "button"])


class HubTests(unittest.TestCase):
    def mk(self, kinds):
        said, sent = [], []

        class S:
            def __init__(self, d): self.dev_id, self.n = d, d
            def send(self, m): sent.append((self.dev_id, m))

        class Les:
            def current(_, dev): return kinds.get(dev, "")

        class Hub1:
            def say(_, w, dev): said.append((dev, w)); return True

        class H:
            lesson = Les()
            game = janken = quiz = None
            def live(_): return [S(d) for d in kinds]
        h = H(); h.janken = Hub1()
        return js.Joystick(h), said, sent

    def test_janken_directions_become_words(self):
        j, said, _ = self.mk({"a": "janken", "b": ""})
        j.handle("left"); j.handle("up"); j.handle("right"); j.handle("down")
        self.assertEqual(said, [("a", "ぐー"), ("a", "ちょき"), ("a", "ぱー")])

    def test_gamepad_buttons_become_words_per_game(self):
        j, said, _ = self.mk({"a": "janken", "b": "omikuji", "c": "riddle", "d": "shiritori"})
        j.press(0); j.press(1); j.press(2)
        self.assertEqual(said, [("a", "ぐー"), ("b", "やる"), ("c", "ヒント"),
                                ("a", "ちょき"), ("b", "やめる"), ("c", "わからない"), ("a", "ぱー")])

    def test_unmapped_kind_or_key_does_nothing(self):
        j, said, _ = self.mk({"a": "shiritori", "b": "janken"})
        j.handle("button"); j.handle("up")
        self.assertEqual(said, [("b", "ちょき")])

    def test_stick_moves_neck_only_of_idle_devices(self):
        j, _, sent = self.mk({"a": "janken", "b": ""})
        j.tilt_gain = 1.0
        j.move(0.5, -0.5)
        self.assertEqual([d for d, _ in sent], ["b"])
        m = sent[0][1]
        self.assertEqual((m["t"], m["vx"], m["vy"]), ("joy", 0.5, -0.5))      # 倒し量に比例した速さ(位置ではない)

    def test_neck_move_pauses_face_tracking(self):
        j, _, sent = self.mk({"b": ""})
        s = j.hub.live()[0]
        j.hub.live = lambda: [s]
        j.move(0.5, 0.0)
        self.assertGreater(getattr(s, "_manual_until", 0), j.clock())

    def test_recenters_when_held_still_off_center(self):
        moves = []
        p = js.Pad(lambda k: None, lambda x, y: moves.append((x, y)))
        for _ in range(15):
            p.feed(512, 512, 0)
        for _ in range(js.STILL_N + 5):
            p.feed(512, 800, 0)                                # 手を離したのに、中心がずれたまま(ドリフト)
        self.assertEqual(moves[-1], (0.0, 0.0))
        self.assertAlmostEqual(p.cy, 800, delta=1)

    def test_pad_calls_move_only_outside_deadzone(self):
        moves = []
        p = js.Pad(lambda k: None, lambda x, y: moves.append((x, y)))
        for _ in range(15):
            p.feed(512, 512, 0)
        p.feed(540, 512, 0)                                    # 0.05: 不感帯
        self.assertEqual(moves, [])
        p.feed(700, 512, 0)
        self.assertEqual(len(moves), 1)
        self.assertGreater(moves[0][0], 0.2)
        self.assertAlmostEqual(moves[0][1], 0.0, places=2)
        p.feed(512, 512, 0)                                    # 戻した: 止める合図(0, 0)を 1 回
        p.feed(512, 512, 0)
        self.assertEqual(moves[-1], (0.0, 0.0))
        self.assertEqual(len(moves), 2)


if __name__ == "__main__":
    unittest.main()
